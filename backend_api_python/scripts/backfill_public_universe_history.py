"""Backfill point-in-time membership for supported public universes.

The importer reconstructs S&P 500 and Nasdaq-100 membership from published
historical source records. It never invents membership before the requested
start date and keeps locally collected snapshots authoritative from their
first date.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.db import get_db_connection
from scripts.refresh_public_universe_snapshots import US_SECTOR_UNIVERSES


GITHUB_API = "https://api.github.com/repos/datasets/s-and-p-500-companies"
RAW_BASE = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies"
SOURCE_PATH = "data/constituents.csv"
SOURCE_URL = "https://github.com/datasets/s-and-p-500-companies"
SOURCE_LICENSE = "ODC-PDDL-1.0"
NASDAQ100_SOURCE_URL = "https://github.com/jmccarrell/n100tickers"
NASDAQ100_SOURCE_LICENSE = "MIT"
NASDAQ100_RAW_BASE = (
    "https://raw.githubusercontent.com/jmccarrell/n100tickers/main/"
    "src/nasdaq_100_ticker_history"
)
IMPORTER_MARKER = "point_in_time_snapshots_v1"
EXPECTED_MEMBER_RANGE = (500, 510)
NASDAQ100_MEMBER_RANGE = (95, 110)
SUPPORTED_HISTORY_UNIVERSES = (
    "sp500",
    "nasdaq100",
    *US_SECTOR_UNIVERSES,
)


@dataclass(frozen=True)
class CommitRef:
    sha: str
    observed_at: datetime


@dataclass(frozen=True)
class Snapshot:
    observed_on: date
    commit: str
    members: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class MembershipInterval:
    symbol: str
    name: str
    valid_from: date
    valid_to: date | None
    rank: int | None
    source_commit: str
    metadata: dict[str, Any]


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _utc_boundary(day: date, *, end: bool) -> str:
    value = datetime.combine(
        day,
        datetime_time.max if end else datetime_time.min,
        tzinfo=timezone.utc,
    )
    return value.isoformat().replace("+00:00", "Z")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "User-Agent": "QuantDinger/5.7.1 universe-history-importer",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = str(os.getenv("GITHUB_TOKEN") or "").strip()
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def _request(session: requests.Session, url: str, *, params: dict | None = None) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=45)
            if response.status_code == 403 and "rate limit" in response.text.lower():
                raise RuntimeError(
                    "GitHub API rate limit reached. Set GITHUB_TOKEN and retry."
                )
            response.raise_for_status()
            return response
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}: {last_error}") from last_error


def _commit_from_payload(item: dict[str, Any]) -> CommitRef:
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
    raw_date = str(committer.get("date") or "")
    observed_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    return CommitRef(sha=str(item.get("sha") or ""), observed_at=observed_at)


def list_source_commits(
    session: requests.Session,
    start: date,
    through: date,
) -> list[CommitRef]:
    endpoint = f"{GITHUB_API}/commits"
    baseline_payload = _request(
        session,
        endpoint,
        params={"path": SOURCE_PATH, "until": _utc_boundary(start, end=True), "per_page": 1},
    ).json()
    if not baseline_payload:
        raise RuntimeError(f"no source snapshot exists on or before {start.isoformat()}")

    commits = [_commit_from_payload(baseline_payload[0])]
    page = 1
    while True:
        payload = _request(
            session,
            endpoint,
            params={
                "path": SOURCE_PATH,
                "since": _utc_boundary(start, end=False),
                "until": _utc_boundary(through, end=True),
                "per_page": 100,
                "page": page,
            },
        ).json()
        if not payload:
            break
        commits.extend(_commit_from_payload(item) for item in payload)
        if len(payload) < 100:
            break
        page += 1

    unique = {item.sha: item for item in commits if item.sha}
    return sorted(unique.values(), key=lambda item: (item.observed_at, item.sha))


def parse_constituents_csv(content: str) -> dict[str, dict[str, Any]]:
    members: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(csv.DictReader(io.StringIO(content)), start=1):
        symbol = str(row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        members[symbol] = {
            "name": str(row.get("Security") or row.get("Name") or "").strip(),
            "rank": rank,
            "sector": str(row.get("GICS Sector") or row.get("Sector") or "").strip(),
            "sub_industry": str(row.get("GICS Sub-Industry") or "").strip(),
            "date_added": str(row.get("Date added") or "").strip(),
        }
    return members


def download_snapshots(
    session: requests.Session,
    commits: Iterable[CommitRef],
    start: date,
) -> list[Snapshot]:
    snapshots_by_day: dict[date, Snapshot] = {}
    for item in commits:
        response = _request(session, f"{RAW_BASE}/{item.sha}/{SOURCE_PATH}")
        members = parse_constituents_csv(response.text)
        minimum, maximum = EXPECTED_MEMBER_RANGE
        if not minimum <= len(members) <= maximum:
            raise RuntimeError(
                f"source snapshot {item.sha[:12]} has {len(members)} members; "
                f"expected {minimum}-{maximum}"
            )
        observed_on = max(start, item.observed_at.date())
        snapshots_by_day[observed_on] = Snapshot(observed_on, item.sha, members)
    return [snapshots_by_day[day] for day in sorted(snapshots_by_day)]


def filter_snapshots_by_sector(snapshots: list[Snapshot], sector: str) -> list[Snapshot]:
    filtered = [
        Snapshot(
            observed_on=snapshot.observed_on,
            commit=snapshot.commit,
            members={
                symbol: member
                for symbol, member in snapshot.members.items()
                if str(member.get("sector") or "").strip() == sector
            },
        )
        for snapshot in snapshots
    ]
    return [snapshot for snapshot in filtered if snapshot.members]


def _ticker_members(tickers: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {
        symbol: {
            "name": symbol,
            "rank": rank,
            "sector": "",
            "sub_industry": "",
            "date_added": "",
        }
        for rank, symbol in enumerate(sorted({str(item).strip().upper() for item in tickers}), start=1)
        if symbol
    }


def _apply_ticker_changes(tickers: set[str], changes: dict[str, Any]) -> None:
    tickers.difference_update(str(item).strip().upper() for item in changes.get("difference", ()))
    tickers.update(str(item).strip().upper() for item in changes.get("union", ()))


def download_nasdaq100_snapshots(
    session: requests.Session,
    start: date,
    through: date,
) -> list[Snapshot]:
    if start < date(2007, 2, 1):
        raise RuntimeError("nasdaq100 history is supported from 2007-02-01")

    snapshots: list[Snapshot] = []
    for year in range(start.year, through.year + 1):
        url = f"{NASDAQ100_RAW_BASE}/n100-ticker-changes-{year}.yaml"
        payload = yaml.safe_load(_request(session, url).text) or {}
        if int(payload.get("year") or 0) != year:
            raise RuntimeError(f"invalid Nasdaq-100 history file for {year}")
        tickers = {str(item).strip().upper() for item in payload.get("tickers_on_Jan_1", ())}
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}

        baseline_day = start if year == start.year else date(year, 1, 1)
        for raw_day in sorted(changes):
            effective_day = date.fromisoformat(str(raw_day))
            if effective_day <= baseline_day:
                _apply_ticker_changes(tickers, changes[raw_day] or {})
        snapshots.append(
            Snapshot(
                baseline_day,
                f"n100tickers:{baseline_day.isoformat()}",
                _ticker_members(tickers),
            )
        )

        for raw_day in sorted(changes):
            effective_day = date.fromisoformat(str(raw_day))
            if effective_day <= baseline_day or effective_day > through:
                continue
            _apply_ticker_changes(tickers, changes[raw_day] or {})
            snapshots.append(
                Snapshot(
                    effective_day,
                    f"n100tickers:{effective_day.isoformat()}",
                    _ticker_members(tickers),
                )
            )

    minimum, maximum = NASDAQ100_MEMBER_RANGE
    invalid = [item for item in snapshots if not minimum <= len(item.members) <= maximum]
    if invalid:
        item = invalid[0]
        raise RuntimeError(
            f"Nasdaq-100 snapshot {item.observed_on} has {len(item.members)} members; "
            f"expected {minimum}-{maximum}"
        )
    return snapshots


def build_intervals(snapshots: list[Snapshot]) -> list[MembershipInterval]:
    if not snapshots:
        return []
    active: dict[str, MembershipInterval] = {}
    completed: list[MembershipInterval] = []

    first = snapshots[0]
    for symbol, member in first.members.items():
        active[symbol] = MembershipInterval(
            symbol=symbol,
            name=str(member.get("name") or ""),
            valid_from=first.observed_on,
            valid_to=None,
            rank=int(member.get("rank") or 0) or None,
            source_commit=first.commit,
            metadata={
                "sector": member.get("sector") or "",
                "sub_industry": member.get("sub_industry") or "",
                "date_added": member.get("date_added") or "",
            },
        )

    previous_symbols = set(first.members)
    for snapshot in snapshots[1:]:
        current_symbols = set(snapshot.members)
        for symbol in sorted(previous_symbols - current_symbols):
            interval = active.pop(symbol)
            if snapshot.observed_on > interval.valid_from:
                completed.append(replace(interval, valid_to=snapshot.observed_on))
        for symbol in sorted(current_symbols - previous_symbols):
            member = snapshot.members[symbol]
            active[symbol] = MembershipInterval(
                symbol=symbol,
                name=str(member.get("name") or ""),
                valid_from=snapshot.observed_on,
                valid_to=None,
                rank=int(member.get("rank") or 0) or None,
                source_commit=snapshot.commit,
                metadata={
                    "sector": member.get("sector") or "",
                    "sub_industry": member.get("sub_industry") or "",
                    "date_added": member.get("date_added") or "",
                },
            )
        previous_symbols = current_symbols

    completed.extend(active.values())
    return sorted(completed, key=lambda item: (item.symbol, item.valid_from))


def clip_intervals(
    intervals: Iterable[MembershipInterval],
    authoritative_from: date | None,
) -> list[MembershipInterval]:
    planned = []
    for interval in intervals:
        if authoritative_from is not None and interval.valid_from >= authoritative_from:
            continue
        valid_to = interval.valid_to
        if authoritative_from is not None and (valid_to is None or valid_to > authoritative_from):
            valid_to = authoritative_from
        if valid_to is not None and valid_to <= interval.valid_from:
            continue
        planned.append(replace(interval, valid_to=valid_to))
    return planned


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def import_history(
    code: str,
    start: date,
    through: date,
    intervals: list[MembershipInterval],
    *,
    apply: bool,
    source_url: str = SOURCE_URL,
    source_license: str = SOURCE_LICENSE,
) -> dict[str, Any]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, metadata_json FROM qd_universes WHERE code = ? AND is_system = TRUE",
            (code,),
        )
        universe = cur.fetchone() or {}
        universe_id = int(universe.get("id") or 0)
        if not universe_id:
            raise RuntimeError(f"unknown system universe: {code}")

        if apply:
            cur.execute("LOCK TABLE qd_universe_members IN SHARE ROW EXCLUSIVE MODE")

        cur.execute(
            """
            SELECT valid_from, metadata_json
            FROM qd_universe_members
            WHERE universe_id = ?
            ORDER BY valid_from
            """,
            (universe_id,),
        )
        rows = cur.fetchall() or []
        local_dates = [
            row.get("valid_from")
            for row in rows
            if _json_object(row.get("metadata_json")).get("history_importer") != IMPORTER_MARKER
        ]
        authoritative_from = min(local_dates) if local_dates else None
        planned = clip_intervals(intervals, authoritative_from)
        symbols = {item.symbol for item in planned}

        result = {
            "universe": code,
            "requested_from": start.isoformat(),
            "source_through": through.isoformat(),
            "authoritative_local_from": (
                authoritative_from.isoformat() if authoritative_from else None
            ),
            "intervals": len(planned),
            "symbols": len(symbols),
            "apply": apply,
            "source": source_url,
            "license": source_license,
        }
        if not apply:
            db.rollback()
            cur.close()
            return result
        if not planned and authoritative_from is not None and authoritative_from <= start:
            result["status"] = "already_covered"
            db.rollback()
            cur.close()
            return result

        cur.execute(
            """
            DELETE FROM qd_universe_members
            WHERE universe_id = ?
              AND metadata_json ->> 'history_importer' = ?
            """,
            (universe_id, IMPORTER_MARKER),
        )
        for interval in planned:
            metadata = {
                **interval.metadata,
                "history_importer": IMPORTER_MARKER,
                "history_source": source_url,
                "history_license": source_license,
                "source_commit": interval.source_commit,
            }
            cur.execute(
                """
                INSERT INTO qd_universe_members
                  (universe_id, market, symbol, name, market_type, valid_from,
                   valid_to, member_rank, source_version, metadata_json)
                VALUES (?, 'USStock', ?, ?, 'spot', ?, ?, ?, ?, ?)
                """,
                (
                    universe_id,
                    interval.symbol,
                    interval.name,
                    interval.valid_from,
                    interval.valid_to,
                    interval.rank,
                    interval.source_commit,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

        universe_metadata = _json_object(universe.get("metadata_json"))
        universe_metadata["history_backfill"] = {
            "from": start.isoformat(),
            "through": through.isoformat(),
            "source": source_url,
            "license": source_license,
            "method": IMPORTER_MARKER,
        }
        cur.execute(
            """
            UPDATE qd_universes
            SET status = 'active', metadata_json = ?, updated_at = NOW()
            WHERE id = ?
            """,
            (json.dumps(universe_metadata, ensure_ascii=False), universe_id),
        )
        db.commit()
        cur.close()
        return result


def backfill_universe_history(
    code: str,
    start: date,
    through: date,
    *,
    apply: bool,
    snapshot_cache: dict[tuple[str, date, date], tuple[list[Snapshot], int]] | None = None,
) -> dict[str, Any]:
    if code not in SUPPORTED_HISTORY_UNIVERSES:
        raise ValueError(f"unsupported historical universe: {code}")
    if start > through:
        raise ValueError("start must be on or before through")
    session = _session()
    if code == "sp500" or code in US_SECTOR_UNIVERSES:
        cache_key = ("sp500", start, through)
        cached = snapshot_cache.get(cache_key) if snapshot_cache is not None else None
        if cached is None:
            commits = list_source_commits(session, start, through)
            base_snapshots = download_snapshots(session, commits, start)
            source_records = len(commits)
            if snapshot_cache is not None:
                snapshot_cache[cache_key] = (base_snapshots, source_records)
        else:
            base_snapshots, source_records = cached
        snapshots = base_snapshots
        if code in US_SECTOR_UNIVERSES:
            snapshots = filter_snapshots_by_sector(snapshots, US_SECTOR_UNIVERSES[code])
            if not snapshots:
                raise RuntimeError(f"no sector members found for {US_SECTOR_UNIVERSES[code]}")
        source_url = SOURCE_URL
        source_license = SOURCE_LICENSE
    else:
        snapshots = download_nasdaq100_snapshots(session, start, through)
        source_url = NASDAQ100_SOURCE_URL
        source_license = NASDAQ100_SOURCE_LICENSE
        source_records = len(snapshots)
    intervals = build_intervals(snapshots)
    result = import_history(
        code,
        start,
        through,
        intervals,
        apply=apply,
        source_url=source_url,
        source_license=source_license,
    )
    result.update({
        "source_records": source_records,
        "snapshots": len(snapshots),
        "requested_through": through.isoformat(),
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill observed point-in-time public-universe membership history."
    )
    parser.add_argument("--universe", choices=SUPPORTED_HISTORY_UNIVERSES, default="sp500")
    parser.add_argument("--from", dest="start", type=parse_iso_date, required=True)
    parser.add_argument("--through", type=parse_iso_date, default=date.today())
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the planned intervals. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    if args.start > args.through:
        parser.error("--from must be on or before --through")

    result = backfill_universe_history(
        args.universe,
        args.start,
        args.through,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.apply:
        print("Dry run only. Add --apply after reviewing the summary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

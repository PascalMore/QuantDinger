import json
from datetime import date

import pytest

from scripts.backfill_public_universe_history import (
    MembershipInterval,
    Snapshot,
    backfill_universe_history,
    build_intervals,
    clip_intervals,
    download_nasdaq100_snapshots,
    filter_snapshots_by_sector,
    parse_constituents_csv,
)
from scripts.refresh_public_universe_snapshots import backfill_current_snapshot_as_fixed


def _member(name: str, rank: int) -> dict:
    return {
        "name": name,
        "rank": rank,
        "sector": "Sector",
        "sub_industry": "Industry",
        "date_added": "",
    }


def test_parse_constituents_supports_old_and_current_column_names():
    old = parse_constituents_csv("Symbol,Name,Sector\nABC,Alpha,Industrials\n")
    current = parse_constituents_csv(
        "Symbol,Security,GICS Sector,GICS Sub-Industry,Date added\n"
        "XYZ,Xylophone,Technology,Software,2023-01-02\n"
    )

    assert old["ABC"]["name"] == "Alpha"
    assert old["ABC"]["sector"] == "Industrials"
    assert current["XYZ"]["name"] == "Xylophone"
    assert current["XYZ"]["sub_industry"] == "Software"


def test_build_intervals_tracks_removal_and_readdition_without_lookahead():
    snapshots = [
        Snapshot(date(2023, 1, 1), "a" * 40, {"AAA": _member("A", 1), "BBB": _member("B", 2)}),
        Snapshot(date(2023, 3, 1), "b" * 40, {"BBB": _member("B", 1), "CCC": _member("C", 2)}),
        Snapshot(date(2023, 5, 1), "c" * 40, {"AAA": _member("A", 1), "CCC": _member("C", 2)}),
    ]

    intervals = build_intervals(snapshots)
    keyed = {(item.symbol, item.valid_from): item for item in intervals}

    assert keyed[("AAA", date(2023, 1, 1))].valid_to == date(2023, 3, 1)
    assert keyed[("AAA", date(2023, 5, 1))].valid_to is None
    assert keyed[("BBB", date(2023, 1, 1))].valid_to == date(2023, 5, 1)
    assert keyed[("CCC", date(2023, 3, 1))].valid_to is None


def test_filter_snapshots_by_sector_keeps_point_in_time_membership():
    snapshots = [
        Snapshot(
            date(2023, 1, 1),
            "abc",
            {
                "A": {"sector": "Energy"},
                "B": {"sector": "Financials"},
            },
        ),
        Snapshot(
            date(2023, 2, 1),
            "def",
            {
                "A": {"sector": "Energy"},
                "C": {"sector": "Energy"},
            },
        ),
        Snapshot(
            date(2023, 3, 1),
            "ghi",
            {
                "A": {"sector": "Oil & Gas Exploration & Production"},
                "C": {"sector": "Integrated Oil & Gas"},
            },
        ),
    ]

    filtered = filter_snapshots_by_sector(snapshots, "Energy")

    assert [set(item.members) for item in filtered] == [{"A"}, {"A", "C"}]
    assert [item.commit for item in filtered] == ["abc", "def"]


def test_clip_intervals_stops_before_locally_collected_history():
    intervals = [
        MembershipInterval("AAA", "A", date(2023, 1, 1), None, 1, "a", {}),
        MembershipInterval("BBB", "B", date(2023, 1, 1), date(2024, 1, 1), 2, "a", {}),
        MembershipInterval("CCC", "C", date(2025, 1, 1), None, 3, "b", {}),
    ]

    clipped = clip_intervals(intervals, date(2024, 7, 18))

    assert [(item.symbol, item.valid_to) for item in clipped] == [
        ("AAA", date(2024, 7, 18)),
        ("BBB", date(2024, 1, 1)),
    ]


def test_nasdaq_history_uses_effective_dates(monkeypatch):
    responses = {
        "2023": """
year: 2023
tickers_on_Jan_1: [AAA, BBB, CCC]
changes:
  '2023-02-01':
    difference: [AAA]
    union: [DDD]
  '2023-06-01':
    difference: [BBB]
    union: [EEE]
""",
    }

    class _Response:
        def __init__(self, text):
            self.text = text

    monkeypatch.setattr(
        "scripts.backfill_public_universe_history._request",
        lambda _session, url: _Response(responses[url.rsplit("-", 1)[-1].split(".", 1)[0]]),
    )
    monkeypatch.setattr(
        "scripts.backfill_public_universe_history.NASDAQ100_MEMBER_RANGE",
        (3, 3),
    )

    snapshots = download_nasdaq100_snapshots(object(), date(2023, 3, 1), date(2023, 12, 31))

    assert [item.observed_on for item in snapshots] == [date(2023, 3, 1), date(2023, 6, 1)]
    assert set(snapshots[0].members) == {"BBB", "CCC", "DDD"}
    assert set(snapshots[1].members) == {"CCC", "DDD", "EEE"}


def test_backfill_rejects_unverified_history_sources():
    with pytest.raises(ValueError, match="unsupported historical universe"):
        backfill_universe_history(
            "russell1000",
            date(2023, 1, 1),
            date(2026, 1, 1),
            apply=True,
        )


def test_backfill_function_runs_supported_nasdaq_history(monkeypatch):
    snapshots = [Snapshot(date(2023, 1, 1), "source", {"AAA": _member("A", 1)})]
    monkeypatch.setattr(
        "scripts.backfill_public_universe_history._session",
        lambda: object(),
    )
    monkeypatch.setattr(
        "scripts.backfill_public_universe_history.download_nasdaq100_snapshots",
        lambda *_args: snapshots,
    )
    captured = {}

    def fake_import(code, start, through, intervals, **kwargs):
        captured.update({
            "code": code,
            "start": start,
            "through": through,
            "intervals": intervals,
            **kwargs,
        })
        return {"universe": code, "apply": kwargs["apply"]}

    monkeypatch.setattr(
        "scripts.backfill_public_universe_history.import_history",
        fake_import,
    )

    result = backfill_universe_history(
        "nasdaq100",
        date(2023, 1, 1),
        date(2023, 12, 31),
        apply=True,
    )

    assert captured["code"] == "nasdaq100"
    assert captured["apply"] is True
    assert len(captured["intervals"]) == 1
    assert result["snapshots"] == 1


def test_current_snapshot_can_be_backfilled_as_a_fixed_universe(monkeypatch):
    statements = []

    class Cursor:
        def __init__(self):
            self.result = None

        def execute(self, sql, params=None):
            statements.append((" ".join(sql.split()), params))
            if "SELECT id, metadata_json" in sql:
                self.result = {"id": 7, "metadata_json": {"snapshot_only": True}}
            elif "SELECT COUNT(*) AS member_count" in sql:
                self.result = {"member_count": 31}

        def fetchone(self):
            return self.result

        def close(self):
            return None

    class Database:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.committed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.committed = True

        def rollback(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    database = Database()
    monkeypatch.setattr(
        "scripts.refresh_public_universe_snapshots.get_db_connection",
        lambda: database,
    )

    result = backfill_current_snapshot_as_fixed(
        "us_etf",
        date(2023, 1, 1),
        date(2026, 10, 24),
    )

    assert result == {
        "universe": "us_etf",
        "history_from": "2023-01-01",
        "snapshot_as_of": "2026-10-24",
        "members": 31,
        "mode": "fixed_current_constituents",
        "apply": True,
    }
    assert database.committed is True
    assert any("DELETE FROM qd_universe_members" in sql for sql, _ in statements)
    assert any(params == (date(2023, 1, 1), 7) for _, params in statements)
    metadata_params = next(
        params for sql, params in statements
        if "UPDATE qd_universes" in sql
    )
    metadata = json.loads(metadata_params[0])
    assert metadata["history_mode"] == "fixed_current_constituents"
    assert metadata["history_assumed_from"] == "2023-01-01"

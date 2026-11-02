from __future__ import annotations

import inspect
from pathlib import Path

from flask import g

from app.routes import fast_analysis as fast_analysis_routes
from app.routes import indicator as indicator_routes
from app.routes import portfolio as portfolio_routes
from app.routes import strategy as strategy_routes
from app.services import analysis_memory as analysis_memory_module
from app.services import indicator_params as indicator_params_module
from app.services import portfolio_monitor as portfolio_monitor_module
from app.services.analysis_memory import AnalysisMemory


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor


class _SingleRowCursor:
    def __init__(self, row=None):
        self.row = row
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        pass


def test_indicator_params_query_is_scoped_to_owner_or_approved_public_listing(monkeypatch):
    cursor = _SingleRowCursor()
    monkeypatch.setattr(
        indicator_params_module,
        "get_db_connection",
        lambda: _Connection(cursor),
    )

    result = indicator_params_module.get_indicator_params(91, user_id=7)

    assert result is None
    sql, params = cursor.statements[0]
    assert "user_id = %s" in sql
    assert "publish_to_community = 1" in sql
    assert "review_status = 'approved'" in sql
    assert params == (91, 7)


def test_indicator_params_route_uses_current_user_and_hides_foreign_ids(app, monkeypatch):
    captured = {}

    def get_params(indicator_id, user_id):
        captured.update(indicator_id=indicator_id, user_id=user_id)
        return None

    monkeypatch.setattr(indicator_params_module, "get_indicator_params", get_params)
    with app.test_request_context("/api/indicator/getIndicatorParams?indicator_id=91"):
        g.user_id = 7
        response, status = inspect.unwrap(indicator_routes.get_indicator_params)()

    assert status == 404
    assert response.get_json()["msg"] == "indicator_not_found"
    assert captured == {"indicator_id": 91, "user_id": 7}


class _PerformanceCursor(_SingleRowCursor):
    def __init__(self):
        super().__init__()
        self.query_index = 0

    def execute(self, sql, params=None):
        super().execute(sql, params)
        self.query_index += 1

    def fetchone(self):
        return {
            "total": 1,
            "correct": 1,
            "avg_return": 2,
            "avg_confidence": 75,
            "buy_count": 1,
            "sell_count": 0,
            "hold_count": 0,
            "helpful_count": 1,
            "feedback_count": 1,
        }


def test_analysis_performance_queries_are_scoped_to_current_user(monkeypatch):
    cursor = _PerformanceCursor()
    monkeypatch.setattr(
        analysis_memory_module,
        "get_db_connection",
        lambda: _Connection(cursor),
    )
    service = AnalysisMemory.__new__(AnalysisMemory)

    result = service.get_performance_stats(
        user_id=7,
        market="Crypto",
        symbol="BTC/USDT",
        days=30,
    )

    assert result["total_analyses"] == 1
    assert len(cursor.statements) == 2
    for sql, params in cursor.statements:
        assert "user_id = %s" in sql
        assert params == (7, "Crypto", "BTC/USDT", 30)


def test_confidence_calibration_query_is_scoped_to_current_user(monkeypatch):
    cursor = _SingleRowCursor()
    monkeypatch.setattr(
        analysis_memory_module,
        "get_db_connection",
        lambda: _Connection(cursor),
    )
    service = AnalysisMemory.__new__(AnalysisMemory)

    assert service.get_adjusted_confidence(
        75,
        user_id=7,
        market="Crypto",
        symbol="BTC/USDT",
    ) == 75

    sql, params = cursor.statements[0]
    assert "user_id = %s" in sql
    assert params == (7, "Crypto", "BTC/USDT", 90)


def test_analysis_performance_route_forwards_authenticated_user(app, monkeypatch):
    captured = {}

    class _Memory:
        @staticmethod
        def get_performance_stats(**kwargs):
            captured.update(kwargs)
            return {"total_analyses": 0}

    monkeypatch.setattr(fast_analysis_routes, "get_analysis_memory", lambda: _Memory())
    with app.test_request_context(
        "/api/fast-analysis/performance?market=Crypto&symbol=BTC/USDT&days=14"
    ):
        g.user_id = 7
        response = inspect.unwrap(fast_analysis_routes.get_performance)()

    assert response.get_json()["code"] == 1
    assert captured == {
        "user_id": 7,
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "days": 14,
    }


def test_strategy_delete_checks_ownership_before_runtime_state(app, monkeypatch):
    class _StrategyService:
        @staticmethod
        def get_strategy(strategy_id, user_id=None):
            assert strategy_id == 91
            assert user_id == 7
            return None

    class _Executor:
        @staticmethod
        def is_running(strategy_id):
            raise AssertionError("runtime state must not be queried for a foreign strategy")

    monkeypatch.setattr(strategy_routes, "get_strategy_service", lambda: _StrategyService())
    monkeypatch.setattr(strategy_routes, "get_trading_executor", lambda: _Executor())
    with app.test_request_context("/api/strategies/91", method="DELETE"):
        g.user_id = 7
        response, status = inspect.unwrap(strategy_routes.delete_strategy)(91)

    assert status == 404
    assert response.get_json()["msg"] == "strategyV2.strategyNotFound"


def test_portfolio_alert_rejects_foreign_position_before_insert(app, monkeypatch):
    cursor = _SingleRowCursor()
    monkeypatch.setattr(
        portfolio_routes,
        "get_db_connection",
        lambda: _Connection(cursor),
    )
    payload = {
        "position_id": 91,
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "alert_type": "pnl_above",
        "threshold": 1,
    }
    with app.test_request_context("/api/portfolio/alerts", method="POST", json=payload):
        g.user_id = 7
        response, status = inspect.unwrap(portfolio_routes.add_alert)()

    assert status == 404
    assert response.get_json()["msg"] == "position_not_found"
    assert all("INSERT INTO qd_position_alerts" not in sql for sql, _ in cursor.statements)
    assert cursor.statements[0][1] == (91, 7)


def test_portfolio_alert_list_joins_positions_with_same_user(app, monkeypatch):
    cursor = _SingleRowCursor()
    monkeypatch.setattr(
        portfolio_routes,
        "get_db_connection",
        lambda: _Connection(cursor),
    )
    with app.test_request_context("/api/portfolio/alerts"):
        g.user_id = 7
        response = inspect.unwrap(portfolio_routes.get_alerts)()

    assert response.get_json()["data"] == []
    sql, params = cursor.statements[0]
    assert "a.position_id = p.id AND a.user_id = p.user_id" in sql
    assert params == (7,)


def test_position_alert_worker_joins_positions_with_same_user(monkeypatch):
    cursor = _SingleRowCursor()
    monkeypatch.setattr(
        portfolio_monitor_module,
        "get_db_connection",
        lambda: _Connection(cursor),
    )
    monkeypatch.setattr(portfolio_monitor_module, "KlineService", lambda: object())
    monkeypatch.setattr(portfolio_monitor_module, "SignalNotifier", lambda: object())

    portfolio_monitor_module._check_position_alerts()

    sql, _params = cursor.statements[0]
    assert "a.position_id = p.id AND a.user_id = p.user_id" in sql


def test_position_alert_migration_enforces_composite_ownership():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations" / "20261023_position_alert_ownership.sql").read_text(
        encoding="utf-8"
    )
    init_sql = (root / "migrations" / "init.sql").read_text(encoding="utf-8")
    db_source = (root / "app" / "utils" / "db.py").read_text(encoding="utf-8")

    assert "SET position_id = NULL" in migration
    assert "owned_position.user_id = alert.user_id" in migration
    assert "FOREIGN KEY (position_id, user_id)" in migration
    assert "FOREIGN KEY (position_id, user_id)" in init_sql
    assert "position-alert-ownership-20261023" in db_source

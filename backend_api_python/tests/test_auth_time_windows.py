from contextlib import contextmanager

from app.services.email_service import EmailService
from app.services.security_service import SecurityService


class ScriptedCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append((query, params))

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)

    def close(self):
        return None


class ScriptedConnection:
    def __init__(self, rows=None):
        self.cursor_instance = ScriptedCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _patch_connection(monkeypatch, module_path, connection):
    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(module_path, fake_connection)


def test_verification_code_fifth_failure_locks_code(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_MAX_ATTEMPTS", "5")
    connection = ScriptedConnection(
        rows=[
            {"failed_attempts": 4},
            {
                "id": 7,
                "stored_code": "654321",
                "attempts": 4,
                "is_valid": True,
            },
            {"attempts": 5},
            {"failed_attempts": 5},
        ]
    )
    _patch_connection(
        monkeypatch,
        "app.services.email_service.get_db_connection",
        connection,
    )

    verified, message = EmailService().verify_code(
        "user@example.com",
        "000000",
        "login",
    )

    assert verified is False
    assert "Too many failed attempts" in message
    statements = connection.cursor_instance.statements
    assert "FOR UPDATE" in statements[1][0]
    assert "NOW()" in statements[1][0]
    assert "COALESCE(attempts, 0) + 1" in statements[2][0]
    assert "RETURNING attempts" in statements[2][0]
    assert connection.commits == 1


def test_verification_code_cannot_be_used_after_attempt_limit(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_MAX_ATTEMPTS", "5")
    connection = ScriptedConnection(
        rows=[
            {"failed_attempts": 0},
            {
                "id": 7,
                "stored_code": "654321",
                "attempts": 5,
                "is_valid": True,
            }
        ]
    )
    _patch_connection(
        monkeypatch,
        "app.services.email_service.get_db_connection",
        connection,
    )

    verified, message = EmailService().verify_code(
        "user@example.com",
        "654321",
        "login",
    )

    assert verified is False
    assert "Too many failed attempts" in message
    assert len(connection.cursor_instance.statements) == 2
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_verification_code_attempt_limit_spans_reissued_codes(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("VERIFICATION_CODE_LOCK_MINUTES", "30")
    connection = ScriptedConnection(rows=[{"failed_attempts": 5}])
    _patch_connection(
        monkeypatch,
        "app.services.email_service.get_db_connection",
        connection,
    )

    verified, message = EmailService().verify_code(
        "user@example.com",
        "654321",
        "login",
    )

    assert verified is False
    assert "Too many failed attempts" in message
    query, params = connection.cursor_instance.statements[0]
    assert "SUM(attempts)" in query
    assert "NOW() - make_interval(mins => ?)" in query
    assert params == ("user@example.com", "login", 30)
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_verification_code_expiration_uses_database_clock(monkeypatch):
    connection = ScriptedConnection()
    _patch_connection(
        monkeypatch,
        "app.services.email_service.get_db_connection",
        connection,
    )
    service = EmailService()
    monkeypatch.setattr(service, "generate_code", lambda: "123456")

    service.create_verification_code(
        "user@example.com",
        "login",
        "203.0.113.20",
    )

    insert_query, params = connection.cursor_instance.statements[-1]
    assert "NOW() + make_interval(mins => ?)" in insert_query
    assert params[3] == service.code_expire_minutes
    assert connection.commits == 1


def test_login_block_window_uses_database_clock(monkeypatch):
    monkeypatch.setenv("SECURITY_ACCOUNT_MAX_ATTEMPTS", "5")
    connection = ScriptedConnection(
        rows=[{"remaining_seconds": 120}]
    )
    _patch_connection(
        monkeypatch,
        "app.services.security_service.get_db_connection",
        connection,
    )

    blocked, remaining = SecurityService().is_blocked(
        "user@example.com",
        "account",
    )

    assert blocked is True
    assert remaining == 120
    query, params = connection.cursor_instance.statements[0]
    assert "threshold_events" in query
    assert "MAX(block_started_at)" in query
    assert params == ("user@example.com", "account", 90, 60, 5, 30)
    assert connection.rollbacks == 1


def test_ip_block_outlives_short_attempt_window(monkeypatch):
    monkeypatch.setenv("SECURITY_IP_MAX_ATTEMPTS", "10")
    monkeypatch.setenv("SECURITY_IP_WINDOW_MINUTES", "5")
    monkeypatch.setenv("SECURITY_IP_BLOCK_MINUTES", "15")
    connection = ScriptedConnection(rows=[{"remaining_seconds": 540}])
    _patch_connection(
        monkeypatch,
        "app.services.security_service.get_db_connection",
        connection,
    )

    blocked, remaining = SecurityService().is_blocked(
        "203.0.113.20",
        "ip",
    )

    assert blocked is True
    assert remaining == 540
    _, params = connection.cursor_instance.statements[0]
    assert params == ("203.0.113.20", "ip", 20, 5, 10, 15)


def test_verification_send_limits_use_database_clock(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_IP_HOURLY_LIMIT", "10")
    connection = ScriptedConnection(rows=[{"count": 0}, {"count": 10}])
    _patch_connection(
        monkeypatch,
        "app.services.security_service.get_db_connection",
        connection,
    )

    allowed, message = SecurityService().can_send_verification_code(
        "user@example.com",
        "203.0.113.20",
    )

    assert allowed is False
    assert "Too many verification code requests" in message
    email_query, email_params = connection.cursor_instance.statements[0]
    ip_query, ip_params = connection.cursor_instance.statements[1]
    assert "NOW() - make_interval(secs => ?)" in email_query
    assert email_params == ("user@example.com", 60)
    assert "NOW() - INTERVAL '1 hour'" in ip_query
    assert ip_params == ("203.0.113.20",)
    assert connection.rollbacks == 1

from contextlib import contextmanager

from app.services.security_service import SecurityService


class FakeCursor:
    def __init__(self, count=0, fail=False):
        self.count = count
        self.fail = fail
        self.statements = []

    def execute(self, query, params=None):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.statements.append((query, params))

    def fetchone(self):
        return {"count": self.count}

    def close(self):
        return None


class FakeConnection:
    def __init__(self, count=0, fail=False):
        self.cursor_instance = FakeCursor(count=count, fail=fail)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _patch_connection(monkeypatch, connection):
    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(
        "app.services.security_service.get_db_connection",
        fake_connection,
    )


def test_registration_slot_is_reserved_atomically(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_IP_HOURLY_LIMIT", "3")
    connection = FakeConnection(count=2)
    _patch_connection(monkeypatch, connection)
    service = SecurityService()

    assert service.reserve_registration_slot(
        "203.0.113.20",
        user_agent="pytest",
        email="user@example.com",
    ) == (True, "allowed")

    queries = [query for query, _ in connection.cursor_instance.statements]
    assert any("pg_advisory_xact_lock" in query for query in queries)
    assert any(
        "NOW() - INTERVAL '1 hour'" in query
        for query in queries
        if "registration_rate_slot" in query and "SELECT" in query
    )
    assert any("registration_rate_slot" in query and "INSERT" in query for query in queries)
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_registration_slot_rejects_ip_at_hourly_limit(monkeypatch):
    monkeypatch.setenv("VERIFICATION_CODE_IP_HOURLY_LIMIT", "3")
    connection = FakeConnection(count=3)
    _patch_connection(monkeypatch, connection)
    service = SecurityService()

    allowed, message = service.reserve_registration_slot("203.0.113.20")

    assert allowed is False
    assert "Too many accounts" in message
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any(
        "INSERT" in query for query, _ in connection.cursor_instance.statements
    )


def test_registration_slot_fails_closed_when_database_is_unavailable(monkeypatch):
    connection = FakeConnection(fail=True)
    _patch_connection(monkeypatch, connection)
    service = SecurityService()

    allowed, message = service.reserve_registration_slot("203.0.113.20")

    assert allowed is False
    assert "temporarily unavailable" in message


class RejectingSecurity:
    def __init__(self):
        self.calls = []

    def validate_password_strength(self, _password):
        return True, "valid"

    def reserve_registration_slot(self, **kwargs):
        self.calls.append(kwargs)
        return False, "Too many accounts created from this IP. Try again later."


class AcceptingEmail:
    @staticmethod
    def is_valid_email(_email):
        return True

    @staticmethod
    def verify_code(_email, _code, _code_type):
        return True, "verified"


class NewUserLookup:
    created = False

    @staticmethod
    def get_user_by_email(_email):
        return None

    @staticmethod
    def get_user_by_username(_username):
        return None

    @classmethod
    def create_user(cls, **_kwargs):
        cls.created = True
        return 1


def _patch_registration_services(monkeypatch, security):
    monkeypatch.setattr(
        "app.services.security_service.get_security_service",
        lambda: security,
    )
    monkeypatch.setattr(
        "app.services.email_service.get_email_service",
        lambda: AcceptingEmail(),
    )
    monkeypatch.setattr(
        "app.services.user_service.get_user_service",
        lambda: NewUserLookup(),
    )
    monkeypatch.setattr(
        "app.services.billing_service.get_billing_service",
        lambda: object(),
    )


def test_code_login_rejects_new_account_when_ip_limit_is_reached(
    client,
    monkeypatch,
):
    security = RejectingSecurity()
    NewUserLookup.created = False
    _patch_registration_services(monkeypatch, security)
    monkeypatch.setenv("ENABLE_REGISTRATION", "true")

    response = client.post(
        "/api/auth/login-code",
        json={"email": "bot@example.com", "code": "123456"},
        environ_base={"REMOTE_ADDR": "203.0.113.20"},
    )

    assert response.status_code == 429
    assert response.get_json()["msg"].startswith("Too many accounts")
    assert len(security.calls) == 1
    assert security.calls[0]["ip_address"] == "203.0.113.20"
    assert security.calls[0]["email"] == "bot@example.com"
    assert NewUserLookup.created is False


def test_password_registration_rejects_new_account_when_ip_limit_is_reached(
    client,
    monkeypatch,
):
    security = RejectingSecurity()
    NewUserLookup.created = False
    _patch_registration_services(monkeypatch, security)
    monkeypatch.setenv("ENABLE_REGISTRATION", "true")

    response = client.post(
        "/api/auth/register",
        json={
            "email": "bot@example.com",
            "code": "123456",
            "username": "bot_user",
            "password": "StrongPass123",
        },
        environ_base={"REMOTE_ADDR": "203.0.113.20"},
    )

    assert response.status_code == 429
    assert response.get_json()["msg"].startswith("Too many accounts")
    assert len(security.calls) == 1
    assert security.calls[0]["ip_address"] == "203.0.113.20"
    assert NewUserLookup.created is False

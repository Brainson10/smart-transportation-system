import os
import sys
import tempfile
from pathlib import Path

# Must run before anything imports `config`: importing app.py bootstraps the
# databases, and this guarantees that never touches the real ones.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="smart-transport-tests-")
os.environ["SMART_TRANSPORT_DATA_DIR"] = _SESSION_DATA_DIR
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GATE_PASSWORD", "gate123")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

CSRF = "test-csrf-token"


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    """Fresh, seeded databases for one test, plus reset in-memory state."""
    import config
    from backend import auth, db
    from gate import decision, sms

    monkeypatch.setattr(config, "VEHICLE_DB", tmp_path / "database.db")
    monkeypatch.setattr(config, "AUTHORITY_DB", tmp_path / "authority" / "authority.db")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(sms, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(sms, "SMS_LOG_FILE", tmp_path / "logs" / "sms_logs.txt")
    db.init_databases()

    auth.login_throttle.reset()
    decision.set_paused(False)
    decision.update_latest_result({})
    yield tmp_path
    decision.set_paused(False)


@pytest.fixture(scope="session")
def flask_app():
    # Imported lazily so the pure-function tests don't pay for torch/ultralytics.
    from app import app
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return app


@pytest.fixture
def client(flask_app, data_env):
    client = flask_app.test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = CSRF
    return client


def _set_session(client, **values):
    with client.session_transaction() as session:
        session.clear()
        session["_csrf_token"] = CSRF
        session.update(values)


@pytest.fixture
def admin_client(client):
    _set_session(client, admin_logged_in=True, admin_username="admin")
    return client


@pytest.fixture
def gate_client(client):
    _set_session(client, gate="Hostel Gate")
    return client

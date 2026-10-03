import config
from backend import auth


def test_password_hashing_round_trip():
    stored = auth.hash_password("s3cret")
    assert auth.is_password_hash(stored)
    assert auth.verify_password(stored, "s3cret")
    assert not auth.verify_password(stored, "wrong")


def test_unknown_user_never_verifies():
    assert not auth.verify_password(None, "anything")


def test_gate_password_from_config(monkeypatch):
    monkeypatch.setattr(config, "GATE_PASSWORD", "open-sesame")
    assert auth.check_gate_password("open-sesame")
    assert not auth.check_gate_password("gate123")
    assert not auth.check_gate_password(None)


def test_throttle_blocks_then_expires():
    clock = [0.0]
    throttle = auth.LoginThrottle(max_failures=3, window=60, time_fn=lambda: clock[0])
    for _ in range(3):
        assert throttle.retry_after("k") == 0
        throttle.record_failure("k")
    assert throttle.retry_after("k") == 60
    assert throttle.retry_after("other") == 0       # per key
    clock[0] = 61
    assert throttle.retry_after("k") == 0


def test_throttle_reset_on_success():
    throttle = auth.LoginThrottle(max_failures=2, window=60)
    throttle.record_failure("k")
    throttle.record_failure("k")
    throttle.reset("k")
    assert throttle.retry_after("k") == 0


def test_secret_key_is_generated_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", None)
    monkeypatch.setattr(config, "INSTANCE_DIR", tmp_path)
    first = auth._load_secret_key()
    assert len(first) == 64 and (tmp_path / "secret_key").exists()
    assert auth._load_secret_key() == first


def test_secret_key_from_environment_wins(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", "from-env")
    assert auth._load_secret_key() == "from-env"

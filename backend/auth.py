"""
Authentication, sessions and CSRF for the whole app. No extra dependencies:
werkzeug.security for password hashing, Flask's signed session for everything else.

What this replaces:
  * app.secret_key = "gate-secret" -- anyone with the source could forge a session.
  * admin passwords stored and compared in plaintext.
  * only /admin itself checked the login; every admin sub-page was open.
  * gate login checked a hardcoded password and then set NO session, so the gate
    dashboard, video stream and /gate API were reachable by anyone.
  * no CSRF protection, no login throttling, no session-fixation protection.
"""

import hmac
import logging
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config

log = logging.getLogger(__name__)

CSRF_SESSION_KEY = "_csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_HASH_PREFIXES = ("scrypt:", "pbkdf2:")


# =================================================
# PASSWORDS
# =================================================
def hash_password(password):
    # pbkdf2 works on every CPython build; scrypt depends on the OpenSSL linked.
    return generate_password_hash(password, method="pbkdf2:sha256")


def is_password_hash(value):
    return isinstance(value, str) and value.startswith(_HASH_PREFIXES)


_DUMMY_HASH = None


def verify_password(stored, candidate):
    global _DUMMY_HASH
    if not stored:
        # Unknown user: still spend the hashing time, so response timing doesn't
        # reveal which usernames exist.
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password")
        check_password_hash(_DUMMY_HASH, str(candidate or ""))
        return False
    if candidate is None:
        return False
    if not is_password_hash(stored):
        # Only reachable before the startup migration has run.
        return hmac.compare_digest(str(stored), str(candidate))
    return check_password_hash(stored, candidate)


def check_gate_password(candidate):
    """Shared gate-operator password, compared in constant time."""
    return hmac.compare_digest(str(candidate or ""), str(config.GATE_PASSWORD))


# =================================================
# SESSION STATE
# =================================================
def _norm(value):
    return (value or "").strip().lower()


def is_admin():
    return bool(session.get("admin_logged_in"))


def current_gate():
    return session.get("gate")


def login_admin(username):
    # clear() first: a pre-login session id must not survive login (fixation).
    session.clear()
    session.permanent = True
    session["admin_logged_in"] = True
    session["admin_username"] = username


def login_gate(location):
    session.clear()
    session.permanent = True
    session["gate"] = location


def logout():
    session.clear()


# =================================================
# ACCESS DECORATORS
# =================================================
def admin_required(view):
    """Admin pages: redirect to the admin login when not signed in."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin.admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def _gate_allowed(gate_arg):
    if is_admin():
        return True
    gate = current_gate()
    if not gate:
        return False
    return gate_arg is None or _norm(gate) == _norm(gate_arg)


def gate_required(view):
    """Gate pages: the operator signed in to THIS gate, or any admin."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _gate_allowed(kwargs.get("gate")):
            return redirect(url_for("gate_routes.gate_login"))
        return view(*args, **kwargs)
    return wrapper


def gate_api_required(view):
    """Gate JSON API and video stream: 401 instead of a redirect."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _gate_allowed(kwargs.get("gate")):
            return jsonify({"status": "error", "message": "Gate login required"}), 401
        return view(*args, **kwargs)
    return wrapper


# =================================================
# CSRF
# =================================================
def generate_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _csrf_protect():
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method in SAFE_METHODS:
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        log.warning("CSRF check failed for %s %s", request.method, request.path)
        abort(400, description="The form expired or was not sent from this site. "
                               "Go back, reload the page and try again.")


# =================================================
# LOGIN THROTTLING (in-memory, per scope + client address)
# =================================================
class LoginThrottle:
    def __init__(self, max_failures=None, window=None, time_fn=time.monotonic):
        self.max_failures = config.LOGIN_MAX_FAILURES if max_failures is None else max_failures
        self.window = config.LOGIN_LOCKOUT_SECONDS if window is None else window
        self._time = time_fn
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key):
        """Seconds until another attempt is allowed; 0 when not blocked."""
        with self._lock:
            now = self._time()
            recent = self._recent(key, now)
            if len(recent) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - recent[0])))

    def record_failure(self, key):
        with self._lock:
            now = self._time()
            self._recent(key, now)
            self._failures.setdefault(key, []).append(now)

    def reset(self, key=None):
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_throttle = LoginThrottle()


def throttle_key(scope):
    return (scope, request.remote_addr or "unknown")


# =================================================
# APP WIRING
# =================================================
def _load_secret_key():
    if config.SECRET_KEY:
        return config.SECRET_KEY
    path = config.INSTANCE_DIR / "secret_key"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.info("generated a new session secret key at %s", path)
    return key


def init_app(app):
    app.secret_key = _load_secret_key()
    app.config.setdefault("CSRF_ENABLED", True)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.SESSION_LIFETIME_HOURS),
    )
    app.before_request(_csrf_protect)
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.context_processor
    def _auth_context():
        return {"is_admin": is_admin(), "current_gate": current_gate()}

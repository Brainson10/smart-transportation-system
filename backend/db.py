"""
One database layer for the whole app.

Before this module existed, four files each carried their own copy of
get_vehicle_db()/get_authority_db(), decision.py opened and closed connections by
hand (leaking them on any exception), and nothing created the schema: `*.db` is
gitignored and init_db/ built different, unused tables, so a fresh clone crashed
on its first query with "no such table: vehicles".

init_databases() is idempotent and runs at startup. It creates every table the
app uses, upgrades older DBs in place, seeds demo data only into EMPTY tables,
and applies a few one-time data migrations -- backing the DB file up first,
because the DB files are untracked and a backup is the only undo.

Paths are read from `config` at CALL time, so tests can point them at temp files.
"""

import logging
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime

import config

log = logging.getLogger(__name__)


# =================================================
# CONNECTIONS
# =================================================
def _connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def vehicle_db():
    """Vehicles, locations, violations, admins. Always closed, even on error."""
    with closing(_connect(config.VEHICLE_DB)) as conn:
        yield conn


@contextmanager
def authority_db():
    """Accident data, predictions, cameras."""
    with closing(_connect(config.AUTHORITY_DB)) as conn:
        yield conn


def paginate(conn, sql, params=(), page=1, per_page=25):
    """Run `sql` one page at a time. Returns (rows, page, pages, total)."""
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    pages = max(1, -(-total // per_page))
    page = min(max(1, int(page or 1)), pages)
    rows = conn.execute(
        f"{sql} LIMIT ? OFFSET ?", (*params, per_page, (page - 1) * per_page)
    ).fetchall()
    return rows, page, pages, total


# =================================================
# SCHEMA
# =================================================
VEHICLE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT,
        owner TEXT,
        type TEXT,
        status TEXT,
        role TEXT,
        violation_count INTEGER DEFAULT 0,
        phone TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        prohibited_start TEXT,
        prohibited_end TEXT,
        violation_count INTEGER DEFAULT 0
    )""",
    # timestamp stays UTC (CURRENT_TIMESTAMP); read it with datetime(..,'localtime').
    """CREATE TABLE IF NOT EXISTS violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_number TEXT,
        location TEXT,
        violation_type TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_violations_location ON violations(location)",
    "CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations(timestamp)",
]

AUTHORITY_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS accident_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        segment TEXT UNIQUE,
        curve INTEGER,
        junction INTEGER,
        visibility INTEGER,
        accident_count INTEGER DEFAULT 0,
        lane_width INTEGER DEFAULT 1,
        traffic_density INTEGER DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        segment TEXT,
        predicted_risk TEXT,
        explanation TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        confidence INTEGER,
        curve INTEGER,
        junction INTEGER,
        visibility INTEGER,
        lane_width INTEGER,
        traffic_density INTEGER,
        accident_count INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS cameras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        location TEXT,
        zone TEXT,
        status TEXT,
        coverage TEXT
    )""",
]

# Columns that were added to older DBs over time with ALTER TABLE.
VEHICLE_COLUMNS = {
    "vehicles": [("role", "TEXT"), ("violation_count", "INTEGER DEFAULT 0"), ("phone", "TEXT")],
}
AUTHORITY_COLUMNS = {
    "accident_data": [("lane_width", "INTEGER DEFAULT 1"), ("traffic_density", "INTEGER DEFAULT 1")],
    "predictions": [("confidence", "INTEGER"), ("curve", "INTEGER"), ("junction", "INTEGER"),
                    ("visibility", "INTEGER"), ("lane_width", "INTEGER"),
                    ("traffic_density", "INTEGER"), ("accident_count", "INTEGER")],
}

# =================================================
# DEMO SEED (only ever written into EMPTY tables)
# -------------------------------------------------
# Copied from the original local DBs, so a fresh clone runs the same demo.
# Visibility is already on the 0/1/2 scale the model and the Add Road form use.
# =================================================
SEED_ADMIN = ("admin", "admin123")

# Phone numbers are deliberately fake (9000000xxx): this file is committed, and
# the simulated-SMS log would otherwise collect real people's numbers.
SEED_VEHICLES = [
    # number, owner, type, role, status, phone
    ("MN01AB1234", "Demo Student A", "Bike", "Student", "ACTIVE", "9000000001"),
    ("MN01CD5678", "Demo Student B", "Car", "Student", "ACTIVE", "9000000002"),
    ("MN01EF9012", "Demo Student C", "Car", "Student", "ACTIVE", "9000000003"),
    ("MN01EM9999", "Campus Ambulance", "Car", "Emergency Vehicle", "ACTIVE", "9000000004"),
    ("UP19EQ1001", "Demo Visitor", "Car", "Student", "ACTIVE", "9000000005"),
]

SEED_LOCATIONS = [
    ("Hostel Gate", "22:00", "06:00"), ("Academic Block", "20:00", "07:00"),
    ("Library Gate", "23:00", "05:00"), ("Academic Gate", "20:00", "07:00"),
    ("Main gate", "21:00", "05:00"), ("North Gate Road", "09:00", "06:00"),
    ("Workshop Road", "18:30", "08:00"), ("South Gate Road", "22:00", "05:00"),
    ("Cafeteria Road", "23:30", "05:30"), ("Hostel Lane A", "01:00", "05:00"),
    ("Hostel Lane B", "01:00", "05:00"), ("Parking Area Road", "00:00", "06:00"),
    ("Academic Block Road", "10:00", "00:30"), ("Auditorium Road", "21:30", "06:00"),
]

SEED_CAMERAS = [
    # name, location, zone, status, coverage
    ("Camera 1", "Main Gate", "Main Gate Road", "ONLINE", "Entry lane"),
    ("Camera 2", "Parking Area", "Parking Road", "ONLINE", "Parking lot"),
    ("Camera 3", "Loading Dock", "Service Road", "ONLINE", "Service lane"),
    ("Camera 4", "Hostel Gate", "Hostel Road", "ONLINE", "Hostel entry"),
    ("Camera 5", "Academic Block", "Academic Road", "ONLINE", "Main corridor"),
    ("Camera 6", "Library Area", "Library Road", "ONLINE", "Library entrance"),
    ("Camera 7", "Sports Complex", "Sports Road", "ONLINE", "Playground perimeter"),
    ("Camera 8", "Cafeteria", "Cafeteria Lane", "OFFLINE", "Student movement"),
    ("Camera 9", "North Gate", "North Gate Road", "OFFLINE", "Outer approach"),
    ("Camera 10", "South Gate", "South Gate Road", "ONLINE", "Exit lane"),
]

SEED_ACCIDENT_DATA = [
    # segment, curve, junction, visibility(0-2), lane_width, traffic_density, accident_count
    ("North Gate Road", 1, 1, 0, 1, 1, 9),
    ("South Gate Road", 0, 1, 1, 1, 1, 5),
    ("Academic Block Road", 0, 0, 2, 1, 1, 1),
    ("Hostel Lane A", 1, 0, 1, 1, 1, 6),
    ("Hostel Lane B", 1, 0, 1, 1, 1, 4),
    ("Cafeteria Road", 0, 1, 1, 1, 1, 3),
    ("Parking Area Road", 0, 0, 2, 1, 1, 2),
    ("Workshop Road", 1, 1, 0, 1, 1, 8),
    ("Auditorium Road", 0, 0, 2, 1, 1, 0),
    ("Medical Center Road", 1, 0, 1, 1, 1, 5),
]


def bin_visibility(value):
    """Old data stored visibility as a 0-1 float; the model and form use 0/1/2."""
    value = float(value)
    if value in (0.0, 1.0, 2.0):
        return int(value)
    if value < 0.4:
        return 0
    if value < 0.7:
        return 1
    return 2


def normalize_plate_text(number):
    return "".join(str(number or "").upper().split())


# =================================================
# BOOTSTRAP HELPERS
# =================================================
def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(conn, spec):
    for table, columns in spec.items():
        existing = _columns(conn, table)
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                log.info("schema: added %s.%s", table, name)


def _is_empty(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def backup_database(path):
    """Copy a DB file to BACKUP_DIR before migrating it. Returns the backup path."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / f"{path.stem}-{stamp}{path.suffix}"
    # SQLite's online-backup API gives a consistent copy even if another
    # connection (e.g. the detection thread) is mid-write.
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    log.warning("backed up %s -> %s before migrating it", path.name, target)
    return target


def _create_unique_index(conn, name, table, expr, dup_sql):
    """Create a UNIQUE index unless existing rows would violate it (never delete data)."""
    duplicates = conn.execute(dup_sql).fetchall()
    if duplicates:
        log.warning("not creating %s: duplicate values exist %s -- resolve them by hand",
                    name, [tuple(d) for d in duplicates])
        return False
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({expr})")
    return True


# =================================================
# MIGRATIONS (each is a no-op once applied)
# =================================================
def _pending_vehicle_migrations(conn):
    from backend.auth import is_password_hash

    pending = {}
    plaintext = [r["id"] for r in conn.execute("SELECT id, password FROM admins")
                 if not is_password_hash(r["password"])]
    if plaintext:
        pending["hash_admin_passwords"] = plaintext

    plates = [(r["id"], normalize_plate_text(r["number"]))
              for r in conn.execute("SELECT id, number FROM vehicles")
              if r["number"] != normalize_plate_text(r["number"])]
    if plates:
        pending["normalize_plates"] = plates

    types = [(r["id"], r["type"].strip().title())
             for r in conn.execute("SELECT id, type FROM vehicles WHERE type IS NOT NULL")
             if r["type"] != r["type"].strip().title()]
    if types:
        pending["normalize_types"] = types

    legacy_types = [r["id"] for r in conn.execute(
        "SELECT id FROM violations WHERE violation_type = 'PROHIBITED_TIME_ENTRY'")]
    if legacy_types:
        pending["violation_type_spelling"] = legacy_types

    # Violations recorded with a differently-cased location ('academic block road')
    # are folded into the canonical locations.name spelling.
    locations = conn.execute("""
        SELECT v.id, l.name FROM violations v
        JOIN locations l ON LOWER(TRIM(v.location)) = LOWER(l.name)
        WHERE v.location != l.name
    """).fetchall()
    if locations:
        pending["canonical_locations"] = [(r[0], r[1]) for r in locations]
    return pending


def _apply_vehicle_migrations(conn, pending):
    from backend.auth import hash_password

    if "hash_admin_passwords" in pending:
        for admin_id in pending["hash_admin_passwords"]:
            row = conn.execute("SELECT password FROM admins WHERE id=?", (admin_id,)).fetchone()
            conn.execute("UPDATE admins SET password=? WHERE id=?",
                         (hash_password(row["password"] or ""), admin_id))
        log.info("migration: hashed %d plaintext admin password(s)",
                 len(pending["hash_admin_passwords"]))
    if "violation_type_spelling" in pending:
        conn.executemany("UPDATE violations SET violation_type = 'Prohibited time entry' WHERE id = ?",
                         [(i,) for i in pending["violation_type_spelling"]])
        log.info("migration: merged %d legacy violation_type spelling(s)",
                 len(pending["violation_type_spelling"]))
    for key, sql in (("normalize_plates", "UPDATE vehicles SET number=? WHERE id=?"),
                     ("normalize_types", "UPDATE vehicles SET type=? WHERE id=?"),
                     ("canonical_locations", "UPDATE violations SET location=? WHERE id=?")):
        if key in pending:
            conn.executemany(sql, [(value, row_id) for row_id, value in pending[key]])
            log.info("migration: %s updated %d row(s)", key, len(pending[key]))


def _pending_authority_migrations(conn):
    rows = conn.execute("SELECT id, visibility FROM accident_data "
                        "WHERE visibility IS NOT NULL AND visibility NOT IN (0, 1, 2)").fetchall()
    return {"bin_visibility": [(r["id"], bin_visibility(r["visibility"])) for r in rows]} if rows else {}


def _apply_authority_migrations(conn, pending):
    if "bin_visibility" in pending:
        conn.executemany("UPDATE accident_data SET visibility=? WHERE id=?",
                         [(v, i) for i, v in pending["bin_visibility"]])
        log.info("migration: binned visibility for %d accident_data row(s)",
                 len(pending["bin_visibility"]))


# =================================================
# ENTRY POINT
# =================================================
def init_vehicle_db():
    from backend.auth import hash_password

    with vehicle_db() as conn:
        with conn:
            for statement in VEHICLE_SCHEMA:
                conn.execute(statement)
            _add_missing_columns(conn, VEHICLE_COLUMNS)

        pending = _pending_vehicle_migrations(conn)
        if pending:
            backup_database(config.VEHICLE_DB)
            with conn:
                _apply_vehicle_migrations(conn, pending)

        with conn:
            _create_unique_index(
                conn, "idx_vehicles_number", "vehicles", "number",
                "SELECT number, COUNT(*) FROM vehicles GROUP BY number HAVING COUNT(*) > 1")
            _create_unique_index(
                conn, "idx_locations_name_nocase", "locations", "name COLLATE NOCASE",
                "SELECT LOWER(name), COUNT(*) FROM locations GROUP BY LOWER(name) HAVING COUNT(*) > 1")

            if _is_empty(conn, "admins"):
                conn.execute("INSERT INTO admins (username, password) VALUES (?, ?)",
                             (SEED_ADMIN[0], hash_password(SEED_ADMIN[1])))
                log.info("seed: created demo admin '%s'", SEED_ADMIN[0])
            if _is_empty(conn, "vehicles"):
                conn.executemany(
                    "INSERT INTO vehicles (number, owner, type, role, status, phone, violation_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0)", SEED_VEHICLES)
                log.info("seed: %d demo vehicles", len(SEED_VEHICLES))
            if _is_empty(conn, "locations"):
                conn.executemany(
                    "INSERT INTO locations (name, prohibited_start, prohibited_end) VALUES (?, ?, ?)",
                    SEED_LOCATIONS)
                log.info("seed: %d demo locations", len(SEED_LOCATIONS))


def init_authority_db():
    with authority_db() as conn:
        with conn:
            for statement in AUTHORITY_SCHEMA:
                conn.execute(statement)
            _add_missing_columns(conn, AUTHORITY_COLUMNS)

        pending = _pending_authority_migrations(conn)
        if pending:
            backup_database(config.AUTHORITY_DB)
            with conn:
                _apply_authority_migrations(conn, pending)

        with conn:
            if _is_empty(conn, "cameras"):
                conn.executemany(
                    "INSERT INTO cameras (name, location, zone, status, coverage) VALUES (?, ?, ?, ?, ?)",
                    SEED_CAMERAS)
                log.info("seed: %d demo cameras", len(SEED_CAMERAS))
            if _is_empty(conn, "accident_data"):
                conn.executemany(
                    "INSERT INTO accident_data (segment, curve, junction, visibility, lane_width, "
                    "traffic_density, accident_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    SEED_ACCIDENT_DATA)
                log.info("seed: %d demo road segments", len(SEED_ACCIDENT_DATA))
        needs_predictions = (_is_empty(conn, "predictions")
                             and not _is_empty(conn, "accident_data"))

    if needs_predictions:
        try:
            from authority.predict import run_predictions
            run_predictions()
        except Exception as exc:
            log.warning("could not generate initial risk predictions: %s", exc)


def init_databases():
    init_vehicle_db()
    init_authority_db()

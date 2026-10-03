"""
Schema bootstrap and migrations. *.db is gitignored, so before init_databases()
a fresh clone had no tables at all and crashed on the first request.
"""

import logging
import sqlite3

import pytest

import config
from backend import db
from backend.auth import is_password_hash


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VEHICLE_DB", tmp_path / "database.db")
    monkeypatch.setattr(config, "AUTHORITY_DB", tmp_path / "authority" / "authority.db")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    return tmp_path


def _count(path, table):
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_fresh_install_is_seeded(paths):
    db.init_databases()
    assert _count(config.VEHICLE_DB, "vehicles") == len(db.SEED_VEHICLES)
    assert _count(config.VEHICLE_DB, "locations") == len(db.SEED_LOCATIONS)
    assert _count(config.AUTHORITY_DB, "cameras") == len(db.SEED_CAMERAS)
    assert _count(config.AUTHORITY_DB, "predictions") == len(db.SEED_ACCIDENT_DATA)
    with sqlite3.connect(config.VEHICLE_DB) as conn:
        assert is_password_hash(conn.execute("SELECT password FROM admins").fetchone()[0])
    assert not config.BACKUP_DIR.exists()          # nothing to migrate, nothing backed up


def test_seed_contains_no_real_phone_numbers():
    assert all(phone.startswith("90000000") for *_, phone in db.SEED_VEHICLES)


def test_init_is_idempotent(paths):
    db.init_databases()
    db.init_databases()
    assert _count(config.VEHICLE_DB, "vehicles") == len(db.SEED_VEHICLES)
    assert not config.BACKUP_DIR.exists()


def test_deleting_all_vehicles_does_not_reseed_them_while_running(paths):
    db.init_databases()
    with sqlite3.connect(config.VEHICLE_DB) as conn:
        conn.execute("DELETE FROM vehicles")
    # Only an EMPTY table is seeded, so this is the documented behaviour on restart.
    db.init_databases()
    assert _count(config.VEHICLE_DB, "vehicles") == len(db.SEED_VEHICLES)


def _legacy_vehicle_db(path):
    """The schema as the original project created it, with its data quirks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE vehicles (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT,
                                   owner TEXT, type TEXT, status TEXT);
            CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
                                    prohibited_start TEXT, prohibited_end TEXT,
                                    violation_count INTEGER DEFAULT 0);
            CREATE TABLE violations (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_number TEXT,
                                     location TEXT, violation_type TEXT,
                                     timestamp DATETIME DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE admins (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
                                 password TEXT);
            INSERT INTO vehicles (number, owner, type, status) VALUES ('mn01 ab1234', 'A', 'car', 'ACTIVE');
            INSERT INTO locations (name) VALUES ('Academic Block Road');
            INSERT INTO violations (vehicle_number, location, violation_type)
                VALUES ('MN01AB1234', 'academic block road', 'PROHIBITED_TIME_ENTRY');
            INSERT INTO admins (username, password) VALUES ('admin', 'plaintext-pw');
        """)


def _legacy_authority_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE accident_data (id INTEGER PRIMARY KEY AUTOINCREMENT, segment TEXT UNIQUE,
                                        curve INTEGER, junction INTEGER, visibility REAL,
                                        accident_count INTEGER);
            INSERT INTO accident_data (segment, curve, junction, visibility, accident_count)
                VALUES ('A', 1, 1, 0.25, 9), ('B', 0, 1, 0.55, 5), ('C', 0, 0, 0.85, 1);
        """)


def test_legacy_databases_are_upgraded_with_backup(paths):
    _legacy_vehicle_db(config.VEHICLE_DB)
    _legacy_authority_db(config.AUTHORITY_DB)
    db.init_databases()

    with sqlite3.connect(config.VEHICLE_DB) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vehicles)")}
        assert {"role", "phone", "violation_count"} <= cols
        assert conn.execute("SELECT number, type FROM vehicles").fetchone() == ("MN01AB1234", "Car")
        assert conn.execute("SELECT location, violation_type FROM violations").fetchone() == \
            ("Academic Block Road", "Prohibited time entry")
        assert is_password_hash(conn.execute("SELECT password FROM admins").fetchone()[0])
        # Existing data is never replaced by seed data.
        assert conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 1

    with sqlite3.connect(config.AUTHORITY_DB) as conn:
        assert [r[0] for r in conn.execute("SELECT visibility FROM accident_data ORDER BY segment")] \
            == [0, 1, 2]

    backups = sorted(p.name for p in config.BACKUP_DIR.iterdir())
    assert any(n.startswith("database-") for n in backups)
    assert any(n.startswith("authority-") for n in backups)
    # The backup holds the pre-migration data.
    backup = next(config.BACKUP_DIR.glob("database-*.db"))
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT password FROM admins").fetchone()[0] == "plaintext-pw"


def test_migrations_do_not_run_twice(paths):
    _legacy_vehicle_db(config.VEHICLE_DB)
    db.init_databases()
    first = set(config.BACKUP_DIR.iterdir())
    db.init_databases()
    assert set(config.BACKUP_DIR.iterdir()) == first


def test_duplicate_plates_skip_unique_index_without_deleting(paths, caplog):
    _legacy_vehicle_db(config.VEHICLE_DB)
    with sqlite3.connect(config.VEHICLE_DB) as conn:
        conn.execute("INSERT INTO vehicles (number, owner, type, status) "
                     "VALUES ('MN01AB1234', 'Dup', 'Car', 'ACTIVE')")
    with caplog.at_level(logging.WARNING):
        db.init_databases()
    assert "idx_vehicles_number" in caplog.text
    assert _count(config.VEHICLE_DB, "vehicles") == 2


@pytest.mark.parametrize("value,expected", [
    (0.25, 0), (0.39, 0), (0.4, 1), (0.55, 1), (0.69, 1), (0.7, 2), (0.9, 2),
    (0, 0), (1, 1), (2, 2),          # already on the 0/1/2 scale: unchanged
])
def test_bin_visibility(value, expected):
    assert db.bin_visibility(value) == expected


def test_paginate(paths):
    db.init_databases()
    with db.vehicle_db() as conn:
        rows, page, pages, total = db.paginate(
            conn, "SELECT number FROM vehicles ORDER BY number", (), page=2, per_page=2)
        assert (page, pages, total, len(rows)) == (2, 3, 5, 2)
        _, page, _, _ = db.paginate(conn, "SELECT number FROM vehicles", (), page=99, per_page=2)
        assert page == 3

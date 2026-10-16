"""
Numbers for the admin dashboard, straight from gate_events and violations.

All grouping is done in LOCAL time (timestamps are stored UTC), so "today" and
"09:00" mean what the campus clock says. An event the operator re-checked
(final_decision = 'RECHECKED') is superseded by the follow-up event and is not
counted twice.
"""

from datetime import date, timedelta

from backend.db import vehicle_db

# The decision that stuck: the operator's call when they made one.
EFFECTIVE = ("CASE WHEN final_decision IN ('ALLOW ENTRY', 'DENY ENTRY') "
             "THEN final_decision ELSE decision END")
COUNTED = "COALESCE(final_decision, '') != 'RECHECKED'"


def _chart(rows, value_key="value"):
    """
    Rows -> chart dict. `pct` is each bar's height as a share of the top axis
    tick (not of the peak), so bars line up with the gridlines. Only the first
    peak is flagged, so the chart carries one direct label, not one per tie.
    """
    rows = [dict(r) for r in rows]
    peak = max((r[value_key] for r in rows), default=0)
    ticks = _ticks(peak)
    top = ticks[-1] or 1
    labelled = False
    for row in rows:
        row["pct"] = round(100.0 * row[value_key] / top, 1)
        row["is_peak"] = bool(peak) and row[value_key] == peak and not labelled
        labelled = labelled or row["is_peak"]
    return {"rows": rows, "peak": peak, "total": sum(r[value_key] for r in rows),
            "ticks": ticks}


def _ticks(peak):
    """Clean y-axis ticks (0, mid, top) for a column chart."""
    if not peak:
        return [0]
    step = 1
    for candidate in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if peak / candidate <= 4:
            step = candidate
            break
    top = -(-peak // step) * step
    return list(range(0, top + 1, step))


def today_summary(conn):
    row = conn.execute(f"""
        SELECT COUNT(*) AS total,
               SUM({EFFECTIVE} = 'ALLOW ENTRY') AS allowed,
               SUM({EFFECTIVE} = 'DENY ENTRY') AS denied,
               SUM({EFFECTIVE} = 'MANUAL CHECK') AS manual,
               SUM(final_decision IN ('ALLOW ENTRY', 'DENY ENTRY')) AS overridden
        FROM gate_events
        WHERE DATE(created_at, 'localtime') = DATE('now', 'localtime') AND {COUNTED}
    """).fetchone()
    violations = conn.execute("""
        SELECT COUNT(*) FROM violations
        WHERE DATE(timestamp, 'localtime') = DATE('now', 'localtime')
          AND COALESCE(status, 'OPEN') != 'WAIVED'
    """).fetchone()[0]
    return {key: row[key] or 0 for key in ("total", "allowed", "denied", "manual", "overridden")} \
        | {"violations": violations}


def hourly_traffic(conn):
    counts = {int(r[0]): r[1] for r in conn.execute(f"""
        SELECT strftime('%H', created_at, 'localtime'), COUNT(*)
        FROM gate_events
        WHERE DATE(created_at, 'localtime') = DATE('now', 'localtime') AND {COUNTED}
        GROUP BY 1
    """)}
    return _chart([{"label": f"{h:02d}:00", "short": f"{h:02d}", "value": counts.get(h, 0)}
                   for h in range(24)])


def daily_violations(conn, days=14, today=None):
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    counts = {r[0]: r[1] for r in conn.execute("""
        SELECT DATE(timestamp, 'localtime'), COUNT(*) FROM violations
        WHERE DATE(timestamp, 'localtime') >= ? AND COALESCE(status, 'OPEN') != 'WAIVED'
        GROUP BY 1
    """, (start.isoformat(),))}
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        rows.append({"label": day.strftime("%a %d %b"), "short": day.strftime("%d"),
                     "value": counts.get(day.isoformat(), 0)})
    return _chart(rows)


def top_violators(conn, limit=5):
    rows = conn.execute("""
        SELECT v.vehicle_number AS label, COUNT(*) AS value, ve.owner AS owner, ve.id AS vehicle_id
        FROM violations v
        LEFT JOIN vehicles ve ON ve.number = v.vehicle_number
        WHERE COALESCE(v.status, 'OPEN') != 'WAIVED'
        GROUP BY v.vehicle_number
        ORDER BY value DESC, label
        LIMIT ?
    """, (limit,)).fetchall()
    return _chart(rows)


def busiest_gates(conn, days=7, limit=6):
    rows = conn.execute(f"""
        SELECT gate AS label, COUNT(*) AS value FROM gate_events
        WHERE DATE(created_at, 'localtime') >= DATE('now', 'localtime', ?) AND {COUNTED}
        GROUP BY gate ORDER BY value DESC, label LIMIT ?
    """, (f"-{days - 1} days", limit)).fetchall()
    return _chart(rows)


def latest_events(conn, limit=8):
    return [dict(r) for r in conn.execute(f"""
        SELECT id, gate, plate, {EFFECTIVE} AS decision, reason, confidence, source,
               datetime(created_at, 'localtime') AS local_time
        FROM gate_events WHERE {COUNTED}
        ORDER BY id DESC LIMIT ?
    """, (limit,))]


def dashboard():
    with vehicle_db() as conn:
        return {
            "today": today_summary(conn),
            "hourly": hourly_traffic(conn),
            "daily_violations": daily_violations(conn),
            "top_violators": top_violators(conn),
            "busiest_gates": busiest_gates(conn),
            "latest_events": latest_events(conn),
        }

"""CSV downloads for the admin lists (violations, gate activity)."""

import csv
import io
from datetime import datetime

from flask import Response

# A cell starting with one of these is run as a formula by Excel / Sheets
# ("CSV injection"); prefixing a quote makes it plain text.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def csv_response(basename, header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(value) for value in row])
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{basename}-{stamp}.csv"'})

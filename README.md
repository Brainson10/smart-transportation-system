# Smart Transportation System

An academic demo of a campus/gate access-control system built around Automatic
Number Plate Recognition (ANPR). A camera watches a gate, detects a vehicle's
number plate, looks it up against a registered-vehicle database, and decides
whether to allow entry, deny it, or flag it for manual review — with time-based
entry restrictions and SMS-style violation alerts along the way. An admin
dashboard surfaces vehicles, violations, cameras and an accident-risk model for
road segments.

**Demo video:** https://drive.google.com/file/d/1e7mGiu4BWBLlnO4YvMntVJKMfyfAjyiT/view?usp=drive_link

> For the live demo / simulation, "CCTV" is the laptop's own webcam. The
> camera source is a config value, not a hardcoded assumption — see
> [Camera source](#camera-source-webcam-video-file-or-real-cctv) below for
> pointing it at a real `rtsp://` camera instead.

---

## How it works

```
 laptop webcam / CCTV  ──▶  gate/capture.py   background capture thread,
                             │                 always holds only the newest frame
                             ▼
                      gate/camera.py          MJPEG stream (Flask) + a
                             │                 separate detection worker thread
                             ▼
                      gate/anpr.py            YOLOv8 plate detector
                             │                 + gate/ocr.py (EasyOCR / Tesseract)
                             ▼
                   gate/plate_utils.py        OCR-error repair, plate validation,
                             │                 confidence fusion, temporal voting
                             ▼
                      gate/decision.py        vehicle DB lookup, prohibited-time
                             │                 check, violations, SMS alert
                             ▼
                frontend/gate_dashboard.html  live feed + ANPR result, polling
                                               GET /gate/latest_result every 2s
```

The video stream and the detection worker are **separate threads**: the MJPEG
feed is always paced to a steady frame rate and never blocks on inference, so
the picture stays smooth no matter how long OCR takes. Detection runs on a
fixed interval (not only when something moves), so a vehicle that *stops* at
the gate is still read — not just one that's passing through.

---

## Project layout

| Path | Purpose |
|---|---|
| `app.py` | Flask app: routes, admin dashboard, `/video_feed/<gate>` |
| `config.py` | All paths + every CV/ANPR constant (env-var overridable) |
| `gate/capture.py` | Background camera capture thread (webcam / file / RTSP) |
| `gate/anpr.py` | YOLO plate detection + OCR orchestration + overlay drawing |
| `gate/ocr.py` | OCR engines (EasyOCR primary, Tesseract fallback) with real confidence |
| `gate/plate_utils.py` | Plate regex/validation, OCR-error repair, confidence fusion, vote tallying |
| `gate/camera.py` | MJPEG stream generator + the detection worker loop |
| `gate/decision.py` | Vehicle lookup, prohibited-time rule, violations, SMS, `/gate/*` API |
| `gate/sms.py` | **Simulated** SMS — appends to `logs/sms_logs.txt`; nothing is sent to a phone |
| `gate/models/license_plate_detector.pt` | Trained YOLOv8n plate detector (single class) |
| `authority/` | Separate ML model: accident-risk prediction for road segments |
| `backend/db.py` | Database layer: connections, schema bootstrap, demo seed, migrations, backups |
| `backend/auth.py` | Sessions, password hashing, CSRF, login throttling, access decorators |
| `backend/routes/` | Flask blueprints: admin, gate login, vehicles, violations, camera metadata |
| `frontend/` | Jinja2 templates + CSS for the dashboards |
| `tools/anpr_probe.py` | Offline CLI to test/tune detection on an image, video or webcam |
| `tests/` | pytest suite: plate logic, decision engine, DB bootstrap, routes, auth |

---

## Setup

Requires Python 3.11+ (developed on 3.13) and the native `tesseract` binary
(used as the OCR fallback engine).

```sh
# macOS
brew install tesseract

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**EasyOCR note:** `requirements.txt` already lists the correct install order,
but if you ever reinstall from scratch, do it in two steps — a plain
`pip install -r requirements.txt` can let `easyocr` pull in
`opencv-python-headless`, which installs a second, conflicting `cv2` and
silently breaks `opencv-python`:

```sh
pip install --no-deps easyocr==1.7.2
pip install -r requirements.txt
```

If EasyOCR can't be installed on your machine, set `ANPR_OCR_ENGINE=tesseract`
(see below) — the Tesseract path works standalone.

---

## Running

```sh
python app.py
```

On first start the app creates `database.db` and `authority/authority.db` and
fills them with demo data (the DB files are gitignored, so a fresh clone has
none). Existing databases are upgraded in place — see
[Data, migrations and backups](#data-migrations-and-backups).

Open **http://127.0.0.1:5000**:

- `/gate-login` — gate operator login: pick a location, password `gate123`
  (override with `GATE_PASSWORD`). The session is tied to that gate, so an
  operator can open only their own gate's dashboard and stream.
- `/admin-login` — admin login, `admin` / `admin123` for the seeded demo
  account. Every admin page requires this login; admins can also open any gate.

On the gate dashboard: hold a printed plate (or a phone showing a plate photo)
up to the webcam. Detection runs continuously — a bounding box with the read
plate and confidence appears on the stream, and the ANPR Result panel on the
right fills in once a plate locks in. Click **Next Vehicle** to resume
detection for the next vehicle.

---

## Camera source (webcam, video file, or real CCTV)

The simulation defaults to the laptop webcam (device `0`). Point it at
anything else with an environment variable — no code change:

```sh
# a saved clip, for a repeatable demo
ANPR_CAMERA_SOURCE=samples/gate.mp4 python app.py

# a real CCTV camera over RTSP
ANPR_CAMERA_SOURCE='rtsp://user:pass@192.168.1.50:554/stream1' python app.py
```

## ANPR configuration

Every detection constant lives in `config.py` and can be overridden by
environment variable. The defaults were tuned on this project's webcam; the
ones most worth adjusting for a different camera or demo:

| Variable | Default | What it does |
|---|---|---|
| `ANPR_CAMERA_SOURCE` | `0` | Webcam index, video file path, or `rtsp://` URL |
| `ANPR_OCR_ENGINE` | `easyocr` | `easyocr` or `tesseract` |
| `ANPR_DEVICE` | `cpu` | YOLO device: `cpu`, `mps`, `cuda` |
| `ANPR_EASYOCR_GPU` | `auto` | `auto` picks MPS/CUDA when available (~2.7x faster), or force `0`/`1` |
| `ANPR_YOLO_CONF` | `0.25` | Minimum YOLO box confidence to consider |
| `ANPR_MANUAL_CHECK_CONFIDENCE` | `70` | Below this, a read is routed to MANUAL CHECK instead of auto-decided |
| `ANPR_DETECT_INTERVAL` | `0.25` | Seconds between inference attempts |
| `ANPR_MOTION_ENABLED` | `false` | If enabled, motion only raises the inference rate — a still scene is still forcibly checked every `ANPR_MOTION_FORCE_INTERVAL` seconds |
| `ANPR_VOTE_WINDOW` / `ANPR_VOTE_THRESHOLD` | `6.0` / `2.0` | How long and how many confidence-weighted sightings are needed before a plate "locks" |
| `ANPR_LOG_LEVEL` | `INFO` | Set to `DEBUG` to see per-frame OCR output |

A locked detection's evidence (full frame + plate crop) is saved under
`logs/detections/<date>/`.

Camera health for any gate is visible at `GET /camera_status/<gate>`.

---

## Gate rules

- **Decision order:** no plate or confidence below `ANPR_MANUAL_CHECK_CONFIDENCE`
  → MANUAL CHECK; unregistered → DENY; blocked (admins can block/unblock on the
  Vehicles page) → DENY; registered during the location's prohibited window →
  DENY + violation + simulated SMS; otherwise ALLOW.
- **Emergency vehicles** (role `Emergency Vehicle`) are admitted during
  prohibited hours, with no violation and no SMS.
- **Prohibited windows** are local wall-clock times and may cross midnight
  (`22:00–06:00`). Equal start and end times are rejected; clear both fields
  to remove a restriction.
- Every decision freezes the gate until the operator clicks **Next Vehicle**.

## Security

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | generated | Signs session cookies. If unset, a random key is created once in `instance/secret_key` (gitignored). Set it explicitly for any shared deployment. |
| `GATE_PASSWORD` | `gate123` | Shared gate-operator password. |
| `FLASK_DEBUG` | off | `1` enables Flask debug mode. Never on a shared network: the debugger can run code. |
| `SESSION_COOKIE_SECURE` | off | `1` when serving over HTTPS. |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Bind address. |

- Admin passwords are stored as salted hashes (`pbkdf2:sha256`). Plaintext
  passwords from older databases are hashed automatically on startup.
- Every form and state-changing request carries a CSRF token; deletes, toggles
  and "Run AI" are POST-only.
- Five failed logins from one address within five minutes block further
  attempts for five minutes (in-memory, per process).

## Data, migrations and backups

- `SMART_TRANSPORT_DATA_DIR` moves the databases, `backups/` and `instance/`
  somewhere other than the project folder (handy for a clean demo or tests).
- Startup is idempotent: it creates missing tables and columns, seeds demo data
  **only into empty tables**, and applies one-time data fixes (hashing admin
  passwords, normalising plate numbers and vehicle types, merging differently
  cased violation locations, putting road visibility on the 0/1/2 scale).
- **Before any migration changes rows, the database is copied to
  `backups/<name>-<timestamp>.db`.** The DB files aren't in git, so that copy
  is the only undo.
- Violation timestamps are stored in UTC and shown in local time.

## Accident-risk model

`authority/model.pkl` is a decision tree predicting accident counts from five
road features (curve, junction, visibility, lane width, traffic density, all on
0/1/2 or 0/1 scales). Its "risk score" is the prediction scaled to 0–100, not
a confidence. Retrain after changing `accident_data`:

```sh
python -m authority.train_model    # prints before/after labels, refreshes predictions
```

It trains on one row per road segment (10 in the demo data), so treat it as a
demonstration of the workflow rather than a validated risk estimate.

---

## Testing

```sh
# full suite (runs against temporary databases, never the real ones; no camera needed)
python -m pytest tests/ -q

# offline tuning: run the real pipeline on a still image or video, no browser
python -m tools.anpr_probe --source path/to/plate.jpg
python -m tools.anpr_probe --source path/to/plate.jpg --dump-variants
python -m tools.anpr_probe --source samples/gate.mp4 --sweep-conf
```

`--sweep-conf` tabulates how many reads would pass at each confidence
threshold — that's how `ANPR_MANUAL_CHECK_CONFIDENCE` should be tuned for a
new camera or plate style. `--dump-variants` writes out every preprocessed
image OCR actually sees, which is the fastest way to tell why a read is
failing.

---

## Known limitations

- The `/admin/cameras` page manages camera *metadata* only (name, zone,
  status); it is not wired to the live detection pipeline, which is
  configured solely through `ANPR_CAMERA_SOURCE`.
- SMS is simulated: "sending" writes to `logs/sms_logs.txt` and never contacts
  a provider.
- Plate parsing targets the standard Indian format (`SS DD LL NNNN`) plus the
  Bharat (`BH`) series; other countries' plate formats are not recognised.
- The gate's pause state and latest result are shared by all gates in one
  process, and login throttling is in-memory and per process. Run a single
  worker (the built-in server, or `gunicorn -w 1 --threads 8 app:app`).
- Behind a reverse proxy every client shares the proxy's address, so the login
  throttle would apply to everyone at once.
- A few hand-inserted demo violations (ids 128–132 in the original database)
  may have been entered in local time and will display 5½ hours late.
- The `road_features` table and the `locations.violation_count` column from
  older databases are no longer used; counts come from the `violations` table.

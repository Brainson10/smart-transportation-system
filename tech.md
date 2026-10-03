# Smart Transportation System — Technical Overview

This document covers the architecture, every technology used and why we chose
it, and the problems we ran into building the system and how each was solved.
For setup and configuration, see [README.md](README.md). For the
non-technical overview, see [pitch.md](pitch.md).

---

## 1. Architecture

```
                         ┌──────────────────────────── Flask app (app.py) ─────────────────────────────┐
                         │                                                                               │
 Webcam / CCTV / file ──▶│ gate/capture.py      background thread, keeps only the newest frame           │
                         │        │                                                                      │
                         │        ├──▶ gate/camera.py  MJPEG stream ──▶ browser <img> (15 fps, no ML)     │
                         │        │                                                                      │
                         │        └──▶ gate/camera.py  detection worker (separate thread, ~4 Hz)          │
                         │                 │                                                             │
                         │                 ▼                                                             │
                         │          gate/anpr.py      YOLOv8n plate detection (Ultralytics / PyTorch)     │
                         │                 │                                                             │
                         │                 ▼                                                             │
                         │          gate/ocr.py       5 image variants → EasyOCR (or Tesseract)           │
                         │                 │                                                             │
                         │                 ▼                                                             │
                         │          gate/plate_utils.py  plate repair, confidence fusion, voting          │
                         │                 │                                                             │
                         │                 ▼                                                             │
                         │          gate/decision.py  rules: registered / blocked / prohibited hours      │
                         │                 │          → violation + SMS (simulated) + evidence image     │
                         │                 ▼                                                             │
                         │          latest result ◀── dashboard polls GET /gate/latest_result every 2 s  │
                         │                                                                               │
                         │ backend/auth.py   sessions, CSRF, password hashing, login throttling          │
                         │ backend/db.py     SQLite access, schema bootstrap, migrations, backups        │
                         │ backend/routes/   admin, gate login, vehicles, violations, cameras            │
                         │ authority/        accident-risk model (scikit-learn) for road segments        │
                         └───────────────────────────────────────────────────────────────────────────────┘
                                         │                                   │
                                   database.db                     authority/authority.db
                       (vehicles, locations, violations, admins)  (accident_data, predictions, cameras)
```

The key design decision is that **video streaming and inference run on
different threads**. The stream is paced to a fixed frame rate and never waits
on OCR, so the picture stays smooth regardless of how long a plate takes to
read.

---

## 2. Tech stack

### Core platform

| Technology | Version | Where | Why we chose it |
|---|---|---|---|
| **Python** | 3.13 | everything | One language for the web app, computer vision and machine learning; every library we needed has first-class Python support. |
| **Flask** | 3.1.2 | `app.py`, `backend/routes/` | Small and explicit. Blueprints let us split admin, gate, vehicle and violation routes cleanly, and streaming a `multipart/x-mixed-replace` MJPEG response is a few lines. Django would have brought an ORM, admin and auth we would have had to fight or bypass for a video-streaming app. |
| **Werkzeug** | 3.1.5 | Flask's server; `werkzeug.security` | Comes with Flask. We use its `pbkdf2:sha256` password hashing so we didn't need an extra auth dependency. |
| **Jinja2** | 3.1.6 | `frontend/templates/` | Flask's template engine; server-rendered pages keep the frontend simple (no build step). Autoescaping protects against HTML injection. |
| **SQLite** | 3.45 (built in) | `database.db`, `authority/authority.db` | Zero setup, a single file, ships with Python. Our write volume (a decision every few seconds per gate) is far below what SQLite handles. PostgreSQL would add a server to install and run for a campus demo with no benefit at this scale. |

### Computer vision and OCR

| Technology | Version | Where | Why we chose it |
|---|---|---|---|
| **OpenCV** (`opencv-python`) | 4.12 | capture, preprocessing, overlay, JPEG encoding | The standard for camera capture (webcam index, video file and RTSP through one API), image preprocessing (CLAHE, thresholding, deskew) and drawing the bounding-box overlay. |
| **Ultralytics YOLOv8n** | 8.4.7 | `gate/anpr.py`, `gate/models/license_plate_detector.pt` | A single-class (`license_plate`) detector, only 6 MB. The *nano* model runs in ~21 ms per frame on a laptop CPU, fast enough to detect continuously. Ultralytics gives a one-line inference API with confidence scores and NMS built in. A classical contour-based plate finder is much less reliable under varied angles and lighting. |
| **PyTorch** | 2.9.1 | backend for YOLO and EasyOCR | Required by both. On Apple-silicon Macs it uses the **MPS** GPU backend, which made OCR 2.7× faster. |
| **EasyOCR** (primary) | 1.7.2 | `gate/ocr.py` | A deep-learning OCR model that copes far better than Tesseract with low-resolution, blurry webcam plates, and returns a real per-read confidence. On a clean test plate it scored 96–99% where Tesseract scored 66–85%. |
| **Tesseract** + `pytesseract` (fallback) | 5.5.2 / 0.3.13 | `gate/ocr.py` | Kept as an automatic fallback: if EasyOCR can't be installed or fails to load, the gate still works. We read per-character confidence through `image_to_data` instead of `image_to_string`, which returns text only. |
| **NumPy** | 2.2.6 | throughout CV code | Frames are NumPy arrays; required by OpenCV, YOLO and EasyOCR. |

### Machine learning (road risk)

| Technology | Version | Where | Why we chose it |
|---|---|---|---|
| **scikit-learn** `DecisionTreeRegressor` | 1.8.0 | `authority/train_model.py` | A decision tree is explainable: we can say *why* a road is risky (curve, junction, low visibility, narrow lane, heavy traffic), which matters more to a safety officer than a black-box score. It also behaves sensibly on a very small dataset (max depth 5, at least 2 samples per leaf). |
| **pandas** | 3.0.0 | training and prediction | Loads training rows straight from SQLite and gives the model named feature columns, so a feature-order mistake raises an error instead of silently producing wrong predictions. |
| **joblib** | 1.5.3 | `authority/model.pkl` | scikit-learn's recommended way to save and load trained models. |

### Frontend

| Technology | Where | Why we chose it |
|---|---|---|
| **Server-rendered HTML + custom CSS** | most pages | No JavaScript build tooling; each page has its own stylesheet plus a shared `common.css`. |
| **Tailwind CSS (CDN)** | gate dashboard, login and error pages | Fast to build clean layouts without writing much CSS. |
| **Vanilla JavaScript** | gate dashboard | About 100 lines: polls the latest decision every 2 s and sends "Next Vehicle". No framework needed. |
| **MJPEG over HTTP** | `/video_feed/<gate>` | Plays in a plain `<img>` tag in every browser, with no WebRTC or player library. |

### Security (no extra packages)

| Mechanism | Implementation |
|---|---|
| Password storage | `werkzeug.security`, salted `pbkdf2:sha256`; old plaintext passwords are hashed automatically on startup |
| Sessions | Flask's signed cookie; secret key from `SECRET_KEY` or generated once into `instance/secret_key`; `HttpOnly`, `SameSite=Lax`, 8-hour lifetime |
| CSRF | Per-session token checked on every POST (form field or `X-CSRFToken` header) |
| Brute force | 5 failed logins per address in 5 minutes → 5-minute lockout |
| Access control | `admin_required` for admin pages; `gate_required` ties a gate operator's session to one gate |

### Testing and tooling

| Technology | Where | Why |
|---|---|---|
| **pytest** 8.3.5 | `tests/` (186 tests) | Plain functions and fixtures; Flask's test client exercises routes against temporary databases, never the real ones. |
| `tools/anpr_probe.py` | offline CLI | Runs the full ANPR pipeline on an image, video or webcam without the web app, printing every stage's output — how we tuned thresholds. |
| **gunicorn** 25.0.1 | deployment | Production WSGI server for hosting (the project was deployed to Render). Must run as one worker; see limitations. |

Two packages in `requirements.txt` — **matplotlib** and **polars** — are not
imported anywhere and can be removed.

---

## 3. How the ANPR pipeline works

**Capture** (`gate/capture.py`). One background thread per camera source
reads frames continuously and keeps only the newest one, with a sequence
number. `CAP_PROP_BUFFERSIZE = 1` keeps an RTSP stream near real time. If the
camera drops, it reconnects with exponential backoff (1 s up to 10 s). The
camera opens on the first request, not at import.

**Detection** (`gate/anpr.py`). YOLOv8n runs at 640 px with confidence ≥ 0.25.
Every box is padded by 4% (plate detectors crop tightly and clip the outer
characters), filtered by size, and read; the best-scoring read wins.

**OCR** (`gate/ocr.py`). Each plate crop is upscaled to at least 64 px tall,
then five variants are produced: grayscale, CLAHE contrast, Otsu threshold,
adaptive threshold, and a deskewed version. Variants are tried from cheapest
to most expensive and the loop stops early on a confident, well-formed read,
so a good plate usually needs one OCR call.

**Plate repair** (`gate/plate_utils.py`). Indian plates follow
`SS 00 L(LL) 0000` (state, RTO, series, number), plus the Bharat series
`00 BH 0000 LL`. Characters are corrected by position: letters in digit slots
become digits (`O→0`, `S→5`, `I→1`, `B→8`…) and the reverse. Readings that
need fewer corrections are preferred.

**Confidence** — a 0–100 score:

```
confidence = 100 × (0.30 × detector_conf + 0.50 × ocr_conf + 0.20 × format_score)
                 × (1 − 0.05 × number_of_corrected_characters)
```

Below **70** the gate shows MANUAL CHECK instead of deciding.

**Voting.** A plate must reach a confidence-weighted total of 2.0 from at
least 2 sightings within a 6-second window before the gate acts. Old votes
expire, and reads that differ by one character are merged.

**Detection trigger.** Inference runs about 4 times per second regardless of
motion. An optional motion mode only lowers the rate on a still scene and
still forces a check every 1.5 s.

**Decision** (`gate/decision.py`). Low confidence → MANUAL CHECK. Unregistered
→ DENY. Blocked → DENY. Inside the gate's prohibited window → DENY, record a
violation, send an SMS — except emergency vehicles, which are allowed. Otherwise
ALLOW. Every decision freezes the gate until the guard clicks Next Vehicle.

---

## 4. Data model

**`database.db`**

| Table | Purpose |
|---|---|
| `vehicles` | plate (unique, normalised), owner, type, role, status (`ACTIVE`/`BLOCKED`), phone, violation count |
| `locations` | gates and roads, with optional prohibited start/end (`HH:MM`, local time) |
| `violations` | plate, location, type, timestamp (stored in UTC, shown in local time) |
| `admins` | username and password hash |

**`authority/authority.db`**

| Table | Purpose |
|---|---|
| `accident_data` | per road segment: curve, junction, visibility, lane width, traffic density (0/1/2), recorded accident count |
| `predictions` | latest predicted risk level, risk score, explanation and features per segment |
| `cameras` | camera inventory (name, location, zone, coverage, status) |

Both databases are created and upgraded automatically on startup
(`backend/db.py`). Any migration that changes existing rows first copies the
database into `backups/`.

---

## 5. Problems we faced and how we solved them

### ANPR accuracy

**1. The confidence score was fake.**
*Problem:* The dashboard always showed **95%**. The code computed
`min(70 + len(plate) × 3, 95)`, and since the plate pattern only matched
10-character strings, the result was always 95. YOLO's and the OCR's real
confidence values were thrown away. As a result, the "low confidence → manual
check" rule could never fire.
*Fix:* The confidence formula in section 3, built from the detector, the OCR
and the plate format.
*Result:* A clean plate scores 85; a misread degraded plate scored 59 and was
correctly sent to manual check instead of being admitted.

**2. A vehicle that stopped at the gate was never read.**
*Problem:* OCR only ran when frame-to-frame motion was detected, and the
reference frame was updated on every frame. A car waiting at the barrier — the
sharpest, clearest view of its plate — produced no motion, so it was never
read. Plates were only read while moving, which is when they are blurriest.
*Fix:* Detection runs on a fixed interval; motion can only increase the rate.
*Result:* Verified with a completely motionless test video: the plate is read
and decided.

**3. OCR was reading the wrong image.**
*Problem:* The code built a cleaned, binarised plate image and then never used
it; it re-cropped the plate and sent a different, unprocessed image to
Tesseract.
*Fix:* Five preprocessing variants, all of which are actually tried and scored.

**4. OCR confuses look-alike characters.**
*Problem:* EasyOCR read our test plate `MN05AB1234` as `MNOSAB1234` with 99.6%
confidence — high confidence, wrong answer. Two different plate patterns in
the code also contradicted each other, which made plates with a one-letter
series (e.g. `MN06A1234`) impossible to accept.
*Fix:* One plate module with position-aware repair (section 3).
*Result:* `MNOSAB1234 → MN05AB1234`, `MNO1CD5678 → MN01CD5678`; one-letter,
three-letter and BH series plates are all accepted.

**5. A single bad frame could lock the gate.**
*Problem:* Plate "votes" were simple counters that never expired. A plate seen
once and again ten minutes later counted as two sightings; counts from one
vehicle leaked into the next.
*Fix:* Confidence-weighted votes in a 6-second window, at least two sightings
required.

**6. Our synthetic test plates weren't detected.**
*Problem:* When building a repeatable test video, YOLO found no plate in our
first drawn plate — it was too small in the frame and too unlike the real
photographs the model was trained on.
*Fix:* A larger, more realistic test plate (detected at 0.69–0.70 confidence),
checked to survive MP4 compression. Lesson: the detector only knows what real
plates look like, so test assets have to resemble them.

### Real-time performance and architecture

**7. Slow OCR froze the video.**
*Problem:* Detection ran inside the video-stream loop, so every OCR call
stalled the feed.
*Fix:* Separate threads for streaming and detection (section 1).
*Result:* The stream stays at ~15 fps; OCR time no longer affects it.

**8. Two browser tabs stole each other's frames.**
*Problem:* The camera was opened once at import time, and every viewer pulled
frames from the same object, so each tab saw only part of the video. In debug
mode Flask's reloader also opened the camera twice.
*Fix:* One capture thread per source that publishes only the newest frame;
viewers copy it instead of consuming it. The camera opens on first use.
*Result:* Two simultaneous viewers each received a continuous stream.

**9. CPU at 100% while idle.**
*Problem:* When paused for the guard, or when the camera failed, the loop
re-encoded and re-sent the same frame as fast as possible with no pause.
*Fix:* Every loop sleeps; the paused stream sends 2 frames per second.

**10. OCR was too slow on the CPU.**
*Problem:* EasyOCR took ~425 ms per plate on the laptop CPU (2.2 s for the
first call).
*Fix:* Run EasyOCR on the Apple GPU (MPS) when available, detected
automatically.
*Result:* ~153 ms per plate — 2.7× faster. YOLO itself only needs ~21 ms on the
CPU, so it stays there.

**11. Unlimited background threads.**
*Problem:* Every distinct name requested at `/video_feed/<name>` started a new
detection thread that never stopped.
*Fix:* Only real locations can open a stream (others return 404), and one
capture thread is shared per camera source.

### Dependencies and environment

**12. Installing EasyOCR broke OpenCV.**
*Problem:* EasyOCR 1.7.2 (released before Python 3.13) depends on
`opencv-python-headless`, which installs a second, conflicting `cv2` module
over the `opencv-python` we already use.
*Fix:* Install EasyOCR with `--no-deps`, then its other dependencies
separately (documented in `requirements.txt` and the README).

**13. EasyOCR might not be installable everywhere.**
*Problem:* With this many native dependencies, the install can fail on some
machines, which would take the whole gate down.
*Fix:* OCR sits behind one interface; if EasyOCR fails, the system falls back
to Tesseract automatically.

**14. A fresh copy of the project couldn't start.**
*Problem:* The database files are excluded from git (`*.db`), and the old
setup scripts created different, unused tables, so anyone cloning the project
got `no such table: vehicles` on the first page.
*Fix:* `backend/db.py` creates every table and seeds demo data on startup, and
upgrades older databases in place.

### Data correctness

**15. Prohibited hours were never enforced at gates.**
*Problem:* Before looking up a location, the code removed the word " gate"
from its name — so *Hostel Gate* was looked up as `hostel`, which doesn't
exist. All six gate-named locations, the very places operators log into, never
enforced their prohibited hours. A window with equal start and end times also
meant "prohibited all day".
*Fix:* Match the full name (case-insensitive); equal times are rejected.

**16. Violation times were 5½ hours off.**
*Problem:* SQLite's `datetime('now')` and `CURRENT_TIMESTAMP` are UTC, but
the pages displayed them as if they were local time. A violation logged at
11:26 IST appeared as 05:56, and "today's violations" used the wrong day
boundary.
*Fix:* Keep storing UTC and convert with `datetime(timestamp, 'localtime')`
when reading. The displayed time now matches the SMS log to the second.

**17. One location's violations were split in two.**
*Problem:* The gate stored the location name in lowercase, so the database
held both `academic block road` and `Academic Block Road`. The dashboard also
only listed road segments, so *Academic Gate* (123 violations) never appeared.
*Fix:* Violations use the location's stored spelling; counts are
case-insensitive; the overview lists every location. Existing rows were
merged by a one-time migration.

**18. Registered plates weren't validated.**
*Problem:* Plates were saved exactly as typed (`mn01 ab1234`), with no format
check and no duplicate check, so the camera's normalised read might never
match.
*Fix:* Plates are upper-cased, stripped of spaces, validated against the
plate format and must be unique. Typos are rejected rather than silently
"repaired".

### Security

**19. Admin pages were open to anyone.**
*Problem:* Only the dashboard itself checked the login. The vehicle, violation,
camera and road pages could be opened directly, and deleting a vehicle was a
plain link, so even a link preview or a browser prefetch could delete records.
*Fix:* Every admin page requires login; every change is a POST with a CSRF
token.

**20. The gate password protected nothing.**
*Problem:* The gate login checked the password and then redirected without
creating a session, so the dashboard URL worked for anyone.
*Fix:* A real session tied to the chosen gate; an operator can only open their
own gate's dashboard, stream and API.

**21. Plaintext passwords and a hardcoded secret.**
*Problem:* Admin passwords were stored and compared in plain text, and the
session-signing key was written in the source code, so anyone with the code
could forge a login.
*Fix:* Hashed passwords (migrated automatically), a generated secret key, and
login throttling.

**22. Debug mode left on.**
*Problem:* `app.run(debug=True)` enables the Werkzeug debugger, which can run
code from the browser.
*Fix:* Debug is off unless `FLASK_DEBUG=1` is set.

**23. Personal data in the repository.**
*Problem:* The simulated SMS log, containing real-looking phone numbers, was
committed to git.
*Fix:* The log is no longer tracked, and the demo seed data uses fake numbers
(`9000000xxx`). The old log still exists in earlier commits.

### Machine learning

**24. The risk model ignored most of its inputs.**
*Problem:* The model was trained on a join that produced only **4 rows**, giving
a one-split tree that could output just two values — every road's risk
depended only on whether it had a junction. It was also trained on visibility
values from 1–3 but asked to predict on values from 0.25–0.9. A road with zero
recorded accidents was rated MEDIUM; one with three was rated HIGH. Finally,
the "AI Confidence %" shown was just the prediction divided by 8, not a
confidence.
*Fix:* Train on all 10 labelled road segments with one consistent 0/1/2
encoding; relabel the number as "Risk score".
*Result:* A 4-leaf tree whose risk levels follow the accident counts (0–2
accidents → LOW, 8–9 → HIGH).

**25. "Add Road" did nothing.**
*Problem:* The form submitted to a route that ignored POST requests.
*Fix:* The route now validates the input, predicts the risk, and saves the road
and its prediction.

### Testing

**26. No automated tests.**
*Problem:* The project had none, so every fix risked breaking something else.
*Fix:* 186 pytest tests covering plate repair, voting, confidence, the
decision rules for every gate, database setup and migrations, access control,
CSRF, login throttling and every admin form. They run against temporary
databases, never the real ones.

---

## 6. Measured performance

All measured on an Apple-silicon MacBook with its built-in webcam.

| Stage | Time |
|---|---|
| Webcam capture | 1280×720 at ~17 fps |
| YOLOv8n plate detection (CPU) | ~21 ms per frame |
| EasyOCR, one plate (CPU) | ~425 ms |
| EasyOCR, one plate (Apple GPU / MPS) | ~153 ms |
| EasyOCR first call (model load) | ~2.2 s |
| Stream to browser | ~15 fps |
| Time to a decision on a clear plate | about 1 second (two sightings required) |

---

## 7. Known limitations and future work

- **Field accuracy unknown.** Tested with a webcam and test plates; it needs a
  trial on real traffic, including at night.
- **Single process.** The gate's pause state and latest result are shared, and
  login throttling is in memory, so the app runs as one process
  (`gunicorn -w 1 --threads 8`).
- **Cloud hosting.** A cloud server has no webcam; the live gate needs to run
  where the camera is, or read a CCTV stream over the network
  (`ANPR_CAMERA_SOURCE=rtsp://…`). Tesseract also needs its native binary
  installed on the host.
- **Simulated SMS.** Alerts are written to a file.
- **Small risk dataset.** 10 road segments; the model shows the method, not
  validated predictions.
- **Camera inventory** (`/admin/cameras`) is metadata only and doesn't control
  which camera the gate uses.
- **Plate formats.** Standard Indian and BH series only.

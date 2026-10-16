# Smart Transportation System Learning Guide

This guide teaches the project topic by topic. Read it alongside the code:
start with the big picture, then follow one request or one camera frame through
the system.

Related docs:

- `README.md`: setup, running, configuration, and demo notes
- `tech.md`: technical architecture and implementation problems solved
- `pitch.md`: non-technical project pitch

---

## 1. What This Project Does

The project is a smart transportation and campus gate-control system. A camera
looks at a gate, the app detects a vehicle number plate, reads the text, checks
the vehicle database, and decides whether the vehicle should be allowed,
denied, or manually checked.

It also includes an admin dashboard for vehicles, violations, cameras,
locations, prohibited entry times, and road accident-risk prediction.

Main features:

- Gate operator login
- Live camera stream in the browser
- Automatic Number Plate Recognition, usually called ANPR
- Vehicle database lookup
- Time-based gate restriction rules
- Violation recording
- Simulated SMS alerts
- Admin dashboard
- Road accident-risk prediction using machine learning
- Tests for backend logic, security, database setup, ANPR utilities, and routes

The project is an academic demo, but it uses real engineering ideas: web
development, authentication, databases, computer vision, OCR, machine learning,
threading, configuration, testing, and deployment concepts.

---

## 2. Project Structure

Important files and folders:

| Path | Meaning |
|---|---|
| `app.py` | Creates the Flask app, registers routes, initializes databases, and exposes main pages |
| `config.py` | Central place for settings such as database paths, camera source, OCR engine, YOLO thresholds, and security options |
| `backend/db.py` | SQLite database layer, schema creation, migrations, seed data, backups, pagination |
| `backend/auth.py` | Login sessions, password hashing, CSRF protection, access decorators, login throttling |
| `backend/routes/` | Flask blueprints for admin login, gate login, vehicles, violations, and camera management |
| `gate/capture.py` | Background camera reader thread that keeps only the newest frame |
| `gate/camera.py` | MJPEG stream generator and separate ANPR detection worker |
| `gate/anpr.py` | YOLO plate detection plus OCR orchestration |
| `gate/ocr.py` | OCR preprocessing and EasyOCR/Tesseract engines |
| `gate/plate_utils.py` | Plate cleaning, validation, OCR repair, confidence scoring, temporal voting |
| `gate/decision.py` | Gate decision rules and `/gate/*` JSON APIs |
| `gate/sms.py` | Simulated SMS logging |
| `authority/` | Accident-risk ML model, training, and prediction |
| `frontend/templates/` | Jinja2 HTML templates |
| `frontend/static/` | CSS files |
| `tests/` | pytest test suite |
| `tools/anpr_probe.py` | Offline ANPR testing tool |

---

## 3. High-Level Architecture

The system has two major areas:

1. Web application
2. Computer vision and machine learning pipeline

The web app handles users, pages, forms, sessions, databases, and APIs.

The vision pipeline handles camera frames, plate detection, OCR, confidence
scoring, voting, and gate decisions.

The main flow:

```text
Camera or webcam
    -> gate/capture.py
    -> gate/camera.py
    -> gate/anpr.py
    -> gate/ocr.py
    -> gate/plate_utils.py
    -> gate/decision.py
    -> database.db
    -> browser dashboard
```

The most important design choice is that video streaming and ANPR detection run
in separate threads. This keeps the live video smooth even when OCR is slow.

---

## 4. Python Concepts Used

Python is the main language. The project uses these Python concepts heavily:

| Concept | Where it appears | Why it matters |
|---|---|---|
| Modules and packages | `backend/`, `gate/`, `authority/` | Keeps code organized by responsibility |
| Functions | Almost every file | Encapsulates reusable behavior |
| Classes | `CameraStream`, `DetectionState`, OCR engines, `PlateVoter` | Holds related state and behavior |
| Context managers | `with vehicle_db() as conn` | Opens and closes database connections safely |
| Decorators | `@admin_required`, `@gate_required`, `@app.route` | Adds behavior around functions |
| Dataclasses | `PlateDetection`, `OcrResult`, `PlateCandidate` | Lightweight structured data |
| Threading | camera capture and detection workers | Allows background work while Flask serves requests |
| Environment variables | `config.py` | Lets behavior change without editing code |
| Logging | `config.setup_logging()` | Better than print statements for debugging running systems |

Example from the project:

```python
@app.route("/admin")
@admin_required
def admin_dashboard():
    ...
```

This means:

- `@app.route("/admin")` connects the function to the `/admin` URL.
- `@admin_required` checks whether the user is logged in as admin before the
  function runs.

---

## 5. Flask Web App Concepts

Flask is a lightweight Python web framework. In this project it handles routes,
templates, sessions, responses, redirects, JSON APIs, and streaming.

Important Flask ideas:

| Flask concept | Meaning in this project |
|---|---|
| App object | `app = Flask(...)` in `app.py` creates the web application |
| Route | A URL connected to a Python function |
| Blueprint | A group of related routes, such as vehicle routes or gate routes |
| Request | Data sent by the browser, such as form fields |
| Response | HTML, JSON, redirect, error page, or video stream sent back |
| Template | HTML with dynamic values inserted by Jinja2 |
| Session | Signed browser cookie storing login state |
| Error handler | Custom response when something goes wrong |

Example route categories:

- `/`: home page
- `/admin-login`: admin login
- `/admin`: admin dashboard
- `/admin/vehicles`: vehicle management
- `/gate-login`: gate operator login
- `/gate/dashboard/<gate>`: gate dashboard for a specific location
- `/video_feed/<gate>`: live MJPEG camera stream
- `/gate/latest_result`: JSON endpoint polled by the dashboard
- `/gate/resume_detection`: called when the operator clicks Next Vehicle

---

## 6. Templates, HTML, CSS, and JavaScript

The frontend is mostly server-rendered. That means Flask prepares HTML on the
server and sends it to the browser.

Technologies:

- HTML for page structure
- CSS for styling
- Jinja2 for inserting backend values into HTML
- Tailwind CDN on some pages for utility classes
- Vanilla JavaScript on the gate dashboard for polling and actions

The gate dashboard does two important browser-side things:

1. Shows the video stream using an `<img>` tag whose source is `/video_feed/<gate>`.
2. Calls `/gate/latest_result` every 2 seconds to update the plate, confidence,
   decision, reason, violation count, and SMS status.

This avoids a heavy frontend framework. For this project, simple JavaScript is
enough.

---

## 7. SQLite Database Concepts

SQLite stores data in local `.db` files. It is good for demos and smaller apps
because it does not require a separate database server.

This project uses two databases:

| Database | Purpose |
|---|---|
| `database.db` | vehicles, locations, violations, admins |
| `authority/authority.db` | road accident data, predictions, cameras |

Main tables in `database.db`:

| Table | Purpose |
|---|---|
| `vehicles` | registered vehicle number, owner, type, role, status, phone, violation count |
| `locations` | gate or road names and prohibited time windows |
| `violations` | records denied prohibited-time entries |
| `admins` | admin usernames and password hashes |

Main tables in `authority/authority.db`:

| Table | Purpose |
|---|---|
| `accident_data` | road feature values and accident counts |
| `predictions` | latest ML risk output |
| `cameras` | camera inventory |

Important database concepts used:

- `CREATE TABLE IF NOT EXISTS`: create tables only when missing
- `ALTER TABLE`: add missing columns to older databases
- `UNIQUE`: prevent duplicate names or plate numbers
- `INDEX`: speed up common searches
- `with conn:`: transaction block
- `row_factory = sqlite3.Row`: access columns by name
- Pagination: load one page of rows instead of everything at once
- Backup before migration: copy the database before changing existing rows

---

## 8. Authentication and Security Concepts

Security is handled in `backend/auth.py`.

Important concepts:

| Concept | Meaning |
|---|---|
| Password hashing | Store a one-way hash, not plaintext passwords |
| Salt | Randomness added to password hashes so equal passwords do not produce identical stored values |
| Session | Browser cookie signed by Flask to remember login state |
| Secret key | Used to sign session cookies |
| CSRF token | Prevents another site from secretly submitting forms as the logged-in user |
| Access decorators | Functions such as `admin_required` and `gate_required` protect pages |
| Login throttling | Too many failed attempts temporarily blocks more attempts |
| Session fixation protection | `session.clear()` before login removes old session state |

The admin login uses the `admins` table.

The gate login uses a shared gate password from `GATE_PASSWORD`, defaulting to
`gate123` for the demo.

Admin users can access all gates. Gate operators can access only their own
gate.

---

## 9. Configuration Concepts

`config.py` keeps settings in one place. Many settings can be changed with
environment variables.

Examples:

| Setting | Meaning |
|---|---|
| `ANPR_CAMERA_SOURCE` | webcam index, video file, or RTSP camera URL |
| `ANPR_OCR_ENGINE` | `easyocr` or `tesseract` |
| `ANPR_DEVICE` | CPU, Apple MPS, or CUDA device for YOLO |
| `ANPR_YOLO_CONF` | minimum YOLO detection confidence |
| `ANPR_DETECT_INTERVAL` | time between detection attempts |
| `ANPR_MANUAL_CHECK_CONFIDENCE` | below this score, route to manual check |
| `SMART_TRANSPORT_DATA_DIR` | where runtime databases and instance files live |
| `SECRET_KEY` | session signing key |
| `SESSION_COOKIE_SECURE` | use secure cookies when serving over HTTPS |

Why configuration matters:

- The same code can run with a webcam, video file, or real CCTV.
- Tests can use temporary databases.
- Deployment can use stronger secrets and different host/port settings.
- Thresholds can be tuned without editing source code.

---

## 10. Camera and Streaming Concepts

Camera handling lives mostly in `gate/capture.py` and `gate/camera.py`.

The project uses OpenCV to read camera frames:

```python
cap = cv2.VideoCapture(source)
```

The source can be:

- `0` for laptop webcam
- a video file path
- an `rtsp://` CCTV stream

`CameraStream` runs in a daemon thread. It reads frames continuously and stores
only the newest frame.

Why keep only the newest frame?

For a gate system, old frames are not useful. If OCR is slow, the detector
should skip stale frames and work on the latest view.

The browser stream uses MJPEG:

```text
multipart/x-mixed-replace
```

This sends a continuous sequence of JPEG images over HTTP. The browser can show
it in a normal `<img>` tag.

---

## 11. Threading Concepts

Threading lets the app do background work.

Threads used here:

| Thread | Job |
|---|---|
| Capture thread | Reads camera frames continuously |
| Detection worker thread | Runs YOLO, OCR, voting, and decisions |
| Flask request threads | Serve browser requests |

Why not run everything in one loop?

YOLO and OCR can be slow. If they ran inside the video stream loop, the live
feed would freeze whenever OCR takes time.

The project uses locks because multiple threads read and write shared data.

Examples:

- `threading.Lock()` protects latest gate results
- `threading.Condition()` lets code wait for a new camera frame
- `threading.Event()` tells a worker thread when to stop

---

## 12. Computer Vision Concepts

Computer vision means extracting information from images.

In this project, OpenCV is used for:

- Reading frames from camera/video/RTSP
- Resizing and preprocessing plate crops
- Converting images to grayscale
- Thresholding
- Deskewing
- Drawing bounding boxes
- Encoding frames as JPEG

Important image concepts:

| Concept | Meaning |
|---|---|
| Frame | One image from the camera |
| Pixel | One tiny color point in the image |
| Crop | A smaller image cut from the full frame |
| Bounding box | Rectangle around detected plate |
| Grayscale | Image with brightness only, no color |
| Thresholding | Convert image into mostly black/white |
| CLAHE | Contrast enhancement useful for poor lighting |
| Deskewing | Rotating slightly to straighten text |

The pipeline first detects where the plate is, then crops that region, then
sends the crop to OCR.

---

## 13. YOLO Object Detection

YOLO is an object detection model. It finds objects in an image and returns
bounding boxes with confidence scores.

This project uses Ultralytics YOLOv8n with a trained license plate detector:

```text
gate/models/license_plate_detector.pt
```

YOLO output:

- box coordinates
- confidence score
- detected class

In this project the class is effectively license plate.

`gate/anpr.py` loads the model lazily. Lazy loading means the heavy model is
loaded only when needed, not when the module is imported.

Why YOLO instead of simple image processing?

License plates can appear at different angles, sizes, lighting conditions, and
backgrounds. A trained detector is usually more reliable than hand-written
contour rules.

---

## 14. OCR Concepts

OCR means Optical Character Recognition. It turns an image of text into actual
characters.

This project supports two OCR engines:

| OCR engine | Role |
|---|---|
| EasyOCR | Primary OCR engine |
| Tesseract | Fallback OCR engine |

The OCR code tries multiple image variants:

- grayscale
- contrast-enhanced
- Otsu threshold
- adaptive threshold
- deskewed

Why multiple variants?

OCR is sensitive to blur, shadows, contrast, glare, and angle. One version of
the crop may be easier for OCR than another.

The system stops early when it gets a confident and well-formed plate, so it
does not waste time on every variant when the first result is already good.

---

## 15. Number Plate Validation and Repair

Plate text is handled in `gate/plate_utils.py`.

The project supports:

- Standard Indian plates such as `MN01AB1234`
- Plates with one-letter or three-letter series
- Bharat series such as `22BH1234AB`

OCR often confuses similar-looking characters:

| OCR mistake | Likely fix in digit position |
|---|---|
| `O` or `Q` | `0` |
| `I`, `L`, or `J` | `1` |
| `Z` | `2` |
| `S` | `5` |
| `B` | `8` |

The key idea is position-aware repair.

Example:

```text
Raw OCR: MNOSAB1234
Expected plate pattern: MN 05 AB 1234
Fixed plate: MN05AB1234
```

The code does not blindly replace every character. It checks whether a position
should contain a letter or digit, then repairs based on that position.

---

## 16. Confidence Scoring

The final confidence is not just OCR confidence. It is a fused score from:

- YOLO detection confidence
- OCR confidence
- plate format score
- penalty for repaired characters

Formula concept:

```text
final confidence =
    weighted YOLO score
  + weighted OCR score
  + weighted plate-format score
  - penalty for substitutions
```

Why this matters:

A plate can have high OCR confidence but still be wrong. For example, OCR may
confidently read a zero as the letter O. Format checking and repair penalties
make the decision more realistic.

If confidence is below `ANPR_MANUAL_CHECK_CONFIDENCE`, the gate result becomes
`MANUAL CHECK`.

---

## 17. Temporal Voting

One frame should not be enough to decide. A single bad OCR frame could misread a
plate.

`PlateVoter` solves this by requiring multiple sightings within a short time
window.

Important ideas:

- Each read adds a confidence-weighted vote.
- Old votes expire.
- At least two sightings are needed.
- Similar plates differing by one character can be merged.
- Once a plate locks, the vote state resets.

This makes the system more stable than deciding from one frame.

---

## 18. Gate Decision Rules

The decision engine is in `gate/decision.py`.

Decision order:

1. If there is no valid plate, show `MANUAL CHECK`.
2. If confidence is too low, show `MANUAL CHECK`.
3. If the plate is not registered, show `DENY ENTRY`.
4. If the vehicle is blocked, show `DENY ENTRY`.
5. If the vehicle enters during prohibited hours, show `DENY ENTRY`, record a
   violation, and simulate SMS.
6. If the vehicle is an emergency vehicle, allow it during prohibited hours.
7. Otherwise, show `ALLOW ENTRY`.

Every final decision pauses detection. The gate operator clicks Next Vehicle to
resume.

This is important because the same vehicle may stay in front of the camera. The
system should not repeatedly create decisions or violations for one vehicle.

---

## 19. Prohibited Time Windows

Locations can have prohibited entry windows such as:

```text
22:00 to 06:00
```

This crosses midnight.

The logic must handle two cases:

| Window type | Example | Rule |
|---|---|---|
| Same day | `09:00` to `17:00` | now must be between start and end |
| Crosses midnight | `22:00` to `06:00` | now is after start or before end |

Equal start and end times are treated as no restriction.

---

## 20. Violations and SMS

When a registered, non-emergency vehicle enters during prohibited time:

1. A row is inserted into `violations`.
2. The vehicle's violation count is increased.
3. A simulated SMS is written to `logs/sms_logs.txt`.

The SMS is simulated. No real message is sent.

This is good for demos because it shows the behavior without needing a paid SMS
provider or real phone numbers.

---

## 21. Machine Learning for Road Risk

The `authority/` module predicts road accident risk.

Files:

| File | Purpose |
|---|---|
| `authority/train_model.py` | Trains the model from `accident_data` |
| `authority/predict.py` | Loads the model and predicts risk |
| `authority/model.pkl` | Saved trained model |

The model uses:

- scikit-learn
- `DecisionTreeRegressor`
- pandas
- joblib

Input features:

| Feature | Meaning |
|---|---|
| `curve` | Whether the road is curved |
| `junction` | Whether a junction exists |
| `visibility` | low, medium, or good visibility |
| `lane_width` | narrow, medium, or wide |
| `traffic_density` | low, medium, or high traffic |

The model predicts accident count. That predicted count is converted into:

- `LOW`
- `MEDIUM`
- `HIGH`

The displayed risk score is not a statistical confidence. It is a scaled
prediction score.

Important ML lesson:

This is a workflow demonstration, not a scientifically validated model. The
demo dataset is small, so the output should be treated as an example of how ML
could be integrated into a dashboard.

---

## 22. Testing Concepts

Tests use pytest.

The tests cover:

- Plate normalization and validation
- Confidence scoring
- Temporal voting
- Gate decision rules
- Authentication
- CSRF protection
- Login throttling
- Database bootstrap and migrations
- Admin features
- Route behavior
- Authority model behavior

Important testing ideas:

| Concept | Meaning |
|---|---|
| Unit test | Tests one small function or class |
| Integration test | Tests multiple parts together, such as Flask route plus database |
| Fixture | Reusable test setup |
| Temporary database | Tests do not touch the real demo database |
| Monkeypatch | Temporarily replace config or behavior during a test |
| Regression test | Protects against a bug returning later |

Run tests:

```sh
python -m pytest tests/ -q
```

---

## 23. Deployment Concepts

For local development:

```sh
python app.py
```

For production-style hosting, the project includes `gunicorn`.

Important deployment ideas:

- Use a real `SECRET_KEY`.
- Do not enable Flask debug mode on a shared network.
- Use HTTPS and `SESSION_COOKIE_SECURE=1`.
- Keep database files and logs outside committed source code.
- Use one worker process for this app unless shared state is redesigned.
- Real CCTV can be used by setting `ANPR_CAMERA_SOURCE` to an RTSP URL.

Why one worker?

The app has in-memory state for latest gate result, pause status, camera stream
registry, and detection workers. Multiple worker processes would not
automatically share that memory.

---

## 24. How One Vehicle Moves Through the System

Step-by-step:

1. The operator opens `/gate-login`.
2. Flask checks the selected gate and password.
3. The operator opens `/gate/dashboard/<gate>`.
4. The browser loads `/video_feed/<gate>` in an image tag.
5. `gate/capture.py` starts reading camera frames.
6. `gate/camera.py` starts a detection worker.
7. The worker sends frames to `gate/anpr.py`.
8. YOLO detects possible plate boxes.
9. Each plate crop goes to OCR.
10. OCR text is cleaned and repaired by `gate/plate_utils.py`.
11. Confidence is calculated.
12. `PlateVoter` waits for enough matching sightings.
13. When a plate locks, evidence images may be saved.
14. `gate/decision.py` checks the vehicle database.
15. The rule engine returns allow, deny, or manual check.
16. The dashboard polls `/gate/latest_result` and shows the result.
17. Detection pauses.
18. The operator clicks Next Vehicle.
19. `/gate/resume_detection` resets state and detection continues.

---

## 25. Common Terms

| Term | Simple meaning |
|---|---|
| ANPR | Automatic Number Plate Recognition |
| OCR | Reading text from an image |
| YOLO | Object detector that finds plates in frames |
| MJPEG | Streaming many JPEG images over HTTP |
| RTSP | Common protocol for CCTV camera streams |
| Frame | One camera image |
| Bounding box | Rectangle around detected object |
| Confidence | Score estimating trust in a detection/read |
| Voting | Combining multiple frame reads before deciding |
| Flask | Python web framework |
| Blueprint | Group of Flask routes |
| Jinja2 | HTML template engine |
| SQLite | File-based database |
| CSRF | Attack where another site submits forms as you |
| Hashing | One-way password storage |
| Migration | Updating an old database schema/data |
| Fixture | pytest setup helper |
| Model pickle | Saved machine learning model file |

---

## 26. Suggested Study Order

If you are learning this from zero, use this order:

1. Read `README.md` to understand what the app does.
2. Run the app and click through the home page, admin login, and gate login.
3. Read `app.py` to see how Flask routes are wired.
4. Read `backend/auth.py` to understand sessions and security.
5. Read `backend/db.py` to understand the database schema.
6. Read `frontend/templates/gate_dashboard.html` to understand the live gate UI.
7. Read `gate/capture.py` to understand camera input.
8. Read `gate/camera.py` to understand streaming and detection workers.
9. Read `gate/anpr.py` to understand YOLO plus OCR.
10. Read `gate/ocr.py` to understand preprocessing and OCR engines.
11. Read `gate/plate_utils.py` to understand plate validation, confidence, and voting.
12. Read `gate/decision.py` to understand the rule engine.
13. Read `authority/predict.py` and `authority/train_model.py` for ML.
14. Read tests related to each topic after reading the source file.

---

## 27. Mini Learning Exercises

Try these to understand the project deeply:

1. Change `ANPR_MANUAL_CHECK_CONFIDENCE` and observe how the decision behavior changes.
2. Add a new vehicle from the admin page and test whether it is allowed.
3. Block a vehicle and check that it is denied.
4. Change a gate's prohibited time and test the rule.
5. Add a new road and inspect the predicted risk.
6. Run `python -m pytest tests/test_plate_utils.py -q`.
7. Run `python -m pytest tests/test_decision.py -q`.
8. Read one failing test by intentionally changing a small rule, then revert it.
9. Use a video file as `ANPR_CAMERA_SOURCE`.
10. Compare EasyOCR and Tesseract by changing `ANPR_OCR_ENGINE`.

---

## 28. Important Design Lessons From This Project

1. Keep configuration separate from code.
2. Do not block the video stream with slow ML/OCR work.
3. Treat camera frames as real-time data; stale frames are often useless.
4. Validate OCR output using domain knowledge.
5. Use multiple signals for confidence instead of trusting one number.
6. Require repeated evidence before making an automated decision.
7. Store passwords as hashes, never plaintext.
8. Protect forms with CSRF tokens.
9. Keep database setup idempotent so fresh clones run cleanly.
10. Tests are documentation for expected behavior.
11. Demo ML models should be clearly described as demos when data is small.
12. Separate responsibilities into modules so the project stays understandable.

---

## 29. Quick Code Map

Use this map when you want to find a feature quickly:

| Question | Look here |
|---|---|
| Where is the app created? | `app.py` |
| Where are routes registered? | `app.py` |
| Where is admin login? | `backend/routes/admin_routes.py` |
| Where is gate login? | `backend/routes/gate_routes.py` |
| Where are sessions checked? | `backend/auth.py` |
| Where are tables created? | `backend/db.py` |
| Where is the camera opened? | `gate/capture.py` |
| Where is the video stream generated? | `gate/camera.py` |
| Where does YOLO run? | `gate/anpr.py` |
| Where does OCR run? | `gate/ocr.py` |
| Where are plates normalized? | `gate/plate_utils.py` |
| Where are gate decisions made? | `gate/decision.py` |
| Where are violations inserted? | `gate/decision.py` |
| Where is simulated SMS written? | `gate/sms.py` |
| Where is road risk predicted? | `authority/predict.py` |
| Where is the ML model trained? | `authority/train_model.py` |
| Where are tests? | `tests/` |

---

## 30. One-Sentence Summary

This project combines a Flask web app, SQLite databases, secure login,
real-time camera streaming, YOLO plate detection, OCR, rule-based gate
decisions, simulated alerts, and a small machine-learning road-risk module into
one smart transportation demo.

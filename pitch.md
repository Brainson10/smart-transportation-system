# Smart Transportation System

**A campus gate that reads number plates by itself, follows the campus's
entry rules, and asks a human when it isn't sure.**

Demo video: https://drive.google.com/file/d/1e7mGiu4BWBLlnO4YvMntVJKMfyfAjyiT/view?usp=drive_link

---

## The problem

At most campus gates, entry control is still manual:

- A guard looks at each vehicle and decides whether it belongs, often from
  memory or a sticker on the windscreen.
- Entry rules such as "no vehicles on hostel roads after 10 pm" exist on paper
  but are hard to enforce consistently, especially at night.
- When something goes wrong there is little or no record: which vehicle,
  which gate, what time.
- Road-safety planning for campus roads happens without any data-driven view
  of which segments are risky.

## What we built

Two connected modules on one web platform.

**1. Gate Security (ANPR).** A camera at the gate (a laptop webcam for the
demo; a CCTV stream in a real deployment) feeds a live dashboard. The
system:

1. finds the number plate in the video with a trained YOLOv8 detector;
2. reads the characters with deep-learning OCR;
3. corrects common OCR mistakes using the structure of Indian plates;
4. checks the plate against the registered-vehicle database and the gate's
   prohibited hours;
5. shows the guard a decision — **ALLOW**, **DENY** or **MANUAL CHECK** — with
   the plate it read and how confident it is;
6. records violations, saves an image of the plate as evidence, and sends an
   alert to the owner (simulated SMS in the demo).

**2. Authority Dashboard.** Administrators manage vehicles, gates, cameras and
prohibited hours, review every violation, and see an AI risk score for each
campus road segment based on its geometry and accident history.

## How a vehicle goes through the gate

```
 Vehicle stops at gate
        │
        ▼
 Camera ──▶ plate found ──▶ plate read ──▶ errors corrected ──▶ seen in several frames?
                                                                       │
        ┌──────────────────────────────────────────────────────────────┘
        ▼
 Registered?  ── no ──▶  DENY  (unregistered)
        │ yes
 Blocked?     ── yes ─▶  DENY  (blocked vehicle)
        │ no
 Prohibited hours at this gate?
        │ yes ── emergency vehicle? ── yes ─▶ ALLOW (exemption)
        │                            └─ no ──▶ DENY + violation + SMS alert
        │ no
        ▼
      ALLOW
```

If the system's confidence in the plate is low at any point, it does not
guess: the gate shows **MANUAL CHECK** and the guard decides.

## What makes it different

**It is honest about uncertainty.** Every decision carries a real confidence
score combining how sure the detector is that it found a plate, how sure the
OCR is about each character, and how well the text matches a valid plate
format. Low-confidence reads go to a human. In our testing, a small, blurry
plate was misread as `NH01CJ5370`; instead of letting the wrong vehicle in, the
system scored it 59% and sent it to manual check.

**It knows what a plate looks like.** OCR engines confuse `O`/`0`, `S`/`5`,
`I`/`1` and `B`/`8`. Our system knows which positions on an Indian plate must
be letters and which must be digits, and repairs the read accordingly. A real
OCR output `MNO1CD5678` becomes the correct `MN01CD5678`.

**It reads vehicles that stop.** Detection runs continuously, not only when
something moves, so a car waiting at the barrier — the clearest view of its
plate — is always read.

**It doesn't trust a single frame.** A plate must be seen consistently across
several frames within a few seconds before the gate acts on it, which filters
out one-off misreads.

**It enforces real campus rules.** Per-gate prohibited hours (including
windows that cross midnight), blocked vehicles, and an exemption for
emergency vehicles such as ambulances.

**It is CCTV-ready.** Switching from the laptop webcam to a real IP camera is
one setting (`ANPR_CAMERA_SOURCE=rtsp://…`); no code changes.

**It keeps evidence.** Every decision saves the frame and the plate crop with a
timestamp, and every violation is logged and searchable.

## Results from our testing

| Measure | Result |
|---|---|
| Plate detection (YOLOv8n, laptop CPU) | ~21 ms per frame |
| Plate reading (EasyOCR on the laptop's Apple GPU) | ~150 ms per plate, 2.7× faster than on CPU |
| Live video to the guard | steady ~15 fps, unaffected by OCR time |
| Stationary vehicle at the gate | detected and decided (previously missed) |
| Confidence on a clear test plate | 85%, correctly allowed |
| Misread on a degraded plate | 59%, correctly routed to manual check |
| Automated tests | 186 passing |

These numbers come from a laptop webcam and printed or on-screen test plates,
not from a field trial with real traffic.

## Live demo (about 3 minutes)

1. **Home page** — live counts of registered vehicles, locations, analysed
   road segments and recorded violations.
2. **Gate login** — choose *Hostel Gate*, enter the gate password.
3. **Hold a plate up to the webcam** — a green box appears around it with the
   plate text and confidence; after a moment the dashboard shows the decision.
   Click **Next Vehicle**.
4. **Show a blurred or partly covered plate** — the system answers
   **MANUAL CHECK** instead of guessing.
5. **Admin login** — block the same vehicle on the Vehicles page, return to the
   gate, show the plate again: **DENY — Blocked vehicle**.
6. **Prohibited hours** — set the current time as prohibited for the gate and
   show a registered plate: **DENY**, a violation is recorded, and the SMS log
   shows the alert. Show the ambulance plate: **ALLOW** with the
   emergency exemption.
7. **Road risk** — open *Roads*, add a road segment, and see its predicted
   risk level and the reasons (curve, junction, low visibility…).

## Who it's for

- **Campus security teams** — fewer manual checks, consistent rule
  enforcement day and night, and a searchable record.
- **Campus administration** — one place to manage vehicles, gates, hours and
  cameras, with violation history per location.
- **Planning / safety officers** — a starting point for prioritising road
  improvements by risk.

## What's next

- Run on real CCTV at one gate and measure accuracy on real traffic, day and
  night.
- Connect a real SMS provider for owner alerts.
- Track each gate's state separately so several gates can run from one server.
- Grow the road-accident dataset so the risk model can be properly validated.
- Support plate formats beyond the standard Indian and BH series.

## Honest limitations

- Tested with a webcam and test plates, not yet with live traffic.
- SMS alerts are simulated (written to a log file).
- The road-risk model is trained on 10 road segments, so it demonstrates the
  approach rather than giving validated predictions.
- Designed to run as a single server process.

## Built with

Python · Flask · SQLite · OpenCV · YOLOv8 (Ultralytics, PyTorch) · EasyOCR ·
Tesseract · scikit-learn · Tailwind CSS · pytest

See [tech.md](tech.md) for the full technical write-up.

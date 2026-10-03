"""
Offline ANPR tuning harness.

Runs the real pipeline over a still image, a video file or the live webcam
WITHOUT Flask or a browser, printing what each stage actually produced. This is
how thresholds get tuned and how a bad read is reproduced from a saved clip.

    python -m tools.anpr_probe --source samples/plate.jpg
    python -m tools.anpr_probe --source samples/plate.jpg --dump-variants
    python -m tools.anpr_probe --source 0 --limit 60 --save-dir out/
    python -m tools.anpr_probe --source samples/gate.mp4 --sweep-conf
    python -m tools.anpr_probe --source samples/plate.jpg --engine tesseract
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

log = logging.getLogger("anpr_probe")


def _report(index, result, elapsed_ms):
    print(f"\n--- frame {index}  ({elapsed_ms:.0f} ms)  status={result['status']} ---")
    if not result["detections"]:
        print("  no plate boxes")
        return
    for det in result["detections"]:
        print(f"  box={det.bbox} det_conf={det.det_conf:.3f}")
        print(f"    raw={det.raw_text!r}  plate={det.plate}  variant={det.variant}")
        print(f"    ocr_conf={det.ocr_conf:.1f}  format={det.format_score:.2f}  "
              f"subs={det.substitutions}  FUSED={det.confidence}")
    print(f"  => {result['vehicle_number']} @ {result['confidence']}%")


def _dump_variants(crop, out_dir, index):
    """Write each preprocessed crop so you can SEE what the OCR engine sees."""
    from gate import ocr
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, image in ocr.preprocess_variants(crop):
        path = out_dir / f"frame{index:04d}_{name}.png"
        cv2.imwrite(str(path), image)
        print(f"    wrote {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANPR offline probe")
    parser.add_argument("--source", default=None,
                        help="image path, video path, webcam index, or rtsp URL")
    parser.add_argument("--limit", type=int, default=1,
                        help="frames to process for video/webcam sources")
    parser.add_argument("--engine", default=None, help="easyocr | tesseract")
    parser.add_argument("--conf", type=float, default=None, help="override YOLO_CONF")
    parser.add_argument("--save-dir", default=None, help="write annotated frames here")
    parser.add_argument("--dump-variants", action="store_true",
                        help="write every preprocessed crop as a PNG")
    parser.add_argument("--sweep-conf", action="store_true",
                        help="tabulate how many reads pass each confidence threshold")
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(levelname)-7s %(name)s: %(message)s")

    if args.engine:
        config.OCR_ENGINE = args.engine
    if args.conf is not None:
        config.YOLO_CONF = args.conf

    from gate.anpr import annotate, run_anpr

    source = config.resolve_camera_source(args.source)
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    is_still = isinstance(source, str) and Path(source).suffix.lower() in (
        ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"
    )

    frames = []
    if is_still:
        image = cv2.imread(source)
        if image is None:
            parser.error(f"cannot read image {source!r}")
        frames = [image]
    else:
        from gate import capture
        stream = capture.CameraStream(source=source, name="probe").start()
        deadline = time.monotonic() + 20.0
        last_seq = -1
        while len(frames) < max(1, args.limit) and time.monotonic() < deadline:
            frame, seq = stream.read_new(last_seq, timeout=2.0)
            if frame is None:
                continue
            last_seq = seq
            frames.append(frame)
        stream.stop()
        if not frames:
            parser.error(f"no frames captured from {source!r}")

    summary, confidences = [], []
    for index, frame in enumerate(frames):
        started = time.monotonic()
        result = run_anpr(frame)
        elapsed = (time.monotonic() - started) * 1000.0
        _report(index, result, elapsed)

        for det in result["detections"]:
            if det.readable:
                confidences.append(det.confidence)
            if args.dump_variants and det.crop is not None:
                _dump_variants(det.crop, (save_dir or Path("out")) / "variants", index)

        if save_dir:
            path = save_dir / f"frame{index:04d}.jpg"
            cv2.imwrite(str(path), annotate(frame.copy(), result["detections"]))
            print(f"  annotated -> {path}")

        summary.append({
            "frame": index, "status": result["status"],
            "plate": result["vehicle_number"], "confidence": result["confidence"],
            "raw_text": result["raw_text"], "det_conf": result["det_conf"],
            "ocr_conf": result["ocr_conf"], "ms": round(elapsed, 1),
        })

    readable = [s for s in summary if s["plate"]]
    print(f"\n=== {len(readable)}/{len(summary)} frames produced a valid plate "
          f"(engine={config.OCR_ENGINE}, device={config.YOLO_DEVICE}) ===")
    if summary:
        print(f"    mean inference: {sum(s['ms'] for s in summary) / len(summary):.0f} ms")

    if args.sweep_conf and confidences:
        # This is how MANUAL_CHECK_CONFIDENCE gets its final value.
        print("\n--- confidence threshold sweep ---")
        for threshold in (50, 60, 70, 80, 90):
            passing = sum(1 for c in confidences if c >= threshold)
            flag = "  <-- current" if threshold == config.MANUAL_CHECK_CONFIDENCE else ""
            print(f"    >= {threshold}: {passing}/{len(confidences)} reads auto-accepted{flag}")

    if args.json:
        print(json.dumps(summary, indent=2))

    return 0 if readable else 1


if __name__ == "__main__":
    raise SystemExit(main())

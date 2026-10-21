#!/usr/bin/env python
"""ONE-COMMAND JUDGE DEMO - give the system one MP4, get a full demo.

    python scripts/judge_demo.py --video "PATH_TO_VIDEO"
    python scripts/judge_demo.py --video "PATH_TO_VIDEO" --csv "PATH_TO_FORM_CSV"

Nothing here re-implements the pipeline: every step calls the code that
already exists.

    STEP 1  dependencies, buffalo_l weights, database, encryption key
    STEP 2  gallery empty/incomplete -> scripts/setup_student_database.py
            (Google Form CSV + local/Drive videos; inaccessible Drive videos are
            reported per student and never turn into fake embeddings)
    STEP 3  gallery validation: enrolled / reference embeddings / missing
    STEP 4  scripts/test_cctv.py::run_recognition() -
            SCRFD -> quality gate -> ArcFace -> cosine (threshold + margin) ->
            K-of-N confirmation -> attendance ledger (one mark per student)
    STEP 5-7  name/reg/similarity/confirmation/timestamp per person, UNKNOWN
            stays UNKNOWN, duplicates impossible
    STEP 8  eval/reports/judge_demo/{attendance.csv, observations.csv,
            summary.json, report.html, frames/, annotated_video.mp4}

Honesty rules (enforced in code, not just in wording):

  * accuracy is produced ONLY by the existing identity-level evaluator and
    ONLY when --ground-truth is supplied; otherwise the report says
    "Accuracy: NOT CALCULATED - NO GROUND TRUTH".
  * when the supplied clip turns out to be one of the enrollment sources the
    whole run is labelled "DEMO / SAME-SOURCE VIDEO" (see detect_same_source).
  * faces below threshold are printed as UNKNOWN / NOT MARKED and are never
    attached to a student; a single frame never marks attendance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for _p in (str(_REPO), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SAME_SOURCE_LABEL = "DEMO / SAME-SOURCE VIDEO"
SAME_SOURCE_NOTE = (
    "Recognition demonstrated on supplied video. If enrollment and test "
    "footage originate from the same recording session, performance should "
    "not be interpreted as real-world CCTV accuracy."
)
NO_GT_NOTE = (
    "Evaluation metrics not calculated because no independent ground truth "
    "was provided."
)
NO_GT_ACCURACY = "Accuracy: NOT CALCULATED \u2014 NO GROUND TRUTH"

DEFAULT_OUT_DIR = Path("eval/reports/judge_demo")
ENROLLMENT_DIR = Path("data/enrollment")
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg"}


class DemoError(RuntimeError):
    """User-facing problem - printed as a message, never a traceback."""


# --------------------------------------------------------------- step 1: pre
def _mask_url(url: str) -> str:
    """Hide any credentials embedded in DATABASE_URL."""
    if "://" in url:
        head, tail = url.split("://", 1)
        if "@" in tail:
            return f"{head}://****@{tail.split('@', 1)[1]}"
    return url


def check_environment() -> list[tuple[str, str, str]]:
    """(check, status, detail) - raises DemoError when the demo cannot run."""
    checks: list[tuple[str, str, str]] = []

    # 1. dependencies
    missing: list[str] = []
    for mod in ("cv2", "numpy", "sqlalchemy", "fastapi", "pydantic", "insightface"):
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - report whatever failed
            missing.append(f"{mod} ({exc})")
    if missing:
        raise DemoError(
            "missing dependencies: " + ", ".join(missing)
            + "  ->  pip install -r requirements.txt"
        )
    checks.append(("dependencies", "OK", "cv2, numpy, sqlalchemy, fastapi, insightface"))

    # 2. model weights
    from app.config import settings

    root = Path(settings.insightface_root).expanduser()
    model_dir = root / "models" / settings.face_model_name
    onnx = sorted(model_dir.glob("*.onnx")) if model_dir.is_dir() else []
    if not onnx:
        raise DemoError(
            f"model unavailable: no .onnx weights in {model_dir}. "
            "Download the model once (needs network), e.g.:\n"
            "       python -c \"from insightface.app import FaceAnalysis; "
            f"FaceAnalysis(name='{settings.face_model_name}', "
            f"root='{root.as_posix()}').prepare(ctx_id=-1)\""
        )
    checks.append(
        ("model", "OK", f"{settings.face_model_name}: {len(onnx)} ONNX file(s) in {model_dir}")
    )

    # 3. database
    try:
        from app.db import Base, SessionLocal, engine as db_engine
        from app.models import Student

        Base.metadata.create_all(db_engine)
        db = SessionLocal()
        try:
            n_students = db.query(Student).count()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        raise DemoError(
            f"database unavailable: {exc} (DATABASE_URL={_mask_url(settings.database_url)})"
        ) from exc
    checks.append(
        ("database", "OK", f"{_mask_url(settings.database_url)} - {n_students} student row(s)")
    )

    # 4. encryption key
    if settings.embedding_encryption_key:
        checks.append(("encryption key", "OK", "EMBEDDING_ENCRYPTION_KEY is set"))
    elif settings.environment == "production":
        raise DemoError(
            "EMBEDDING_ENCRYPTION_KEY is missing while ENVIRONMENT=production "
            "(the API hard-fails at startup without it)"
        )
    else:
        checks.append(
            (
                "encryption key",
                "WARN",
                "EMBEDDING_ENCRYPTION_KEY not set - embeddings stored "
                "unencrypted (development only)",
            )
        )
    return checks


def print_checks(checks: list[tuple[str, str, str]]) -> None:
    print("STEP 1  environment")
    for name, status, detail in checks:
        print(f"  [{status:4}] {name:16} {detail}")


# ------------------------------------------------------------ step 2: roster
def resolve_form_csv(csv_arg: Path | None) -> Path | None:
    """--csv > DEFAULT_FORM_CSV > the single data/enrollment/*.csv > None."""
    from app.config import settings

    if csv_arg is not None:
        if not csv_arg.is_file():
            raise DemoError(f"CSV not found: {csv_arg}")
        return csv_arg
    if settings.default_form_csv:
        configured = Path(settings.default_form_csv)
        if not configured.is_file():
            raise DemoError(
                f"configured DEFAULT_FORM_CSV not found: {configured} (pass --csv)"
            )
        return configured
    if ENROLLMENT_DIR.is_dir():
        found = sorted(ENROLLMENT_DIR.glob("*.csv"))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise DemoError(
                "several enrollment CSVs in "
                f"{ENROLLMENT_DIR}/ - pass --csv explicitly: "
                + ", ".join(str(p) for p in found)
            )
    return None


def enrolled_students() -> list[dict]:
    from app.db import SessionLocal
    from app.models import Student

    db = SessionLocal()
    try:
        return [
            {
                "student_id": s.id,
                "name": s.name,
                "registration_no": s.registration_no,
            }
            for s in db.query(Student).all()
        ]
    finally:
        db.close()


def gallery_stats() -> dict:
    from app.matching import EmbeddingIndex

    index = EmbeddingIndex()
    index.refresh()
    return {"students": index.student_count, "embeddings": index.embedding_count}


def missing_enrollment(csv_path: Path, enrolled_regs: set[str]) -> tuple[list[dict], int, int]:
    """(students in the CSV with no gallery entry, valid rows, invalid rows)."""
    from app.formcsv import FormImportError, parse_form_csv

    try:
        rows = parse_form_csv(csv_path, require_link=False, check_duplicates=False)
    except FormImportError as exc:
        raise DemoError(str(exc)) from exc
    valid = [r for r in rows if r.registration_no and not r.errors]
    invalid = [r for r in rows if r.errors]
    missing = [
        {"name": r.name, "registration_no": r.registration_no}
        for r in valid
        if r.registration_no not in enrolled_regs
    ]
    return missing, len(valid), len(invalid)


def run_enrollment(csv_path: Path, videos_dir: Path | None, use_rpc: bool) -> int:
    """STEP 2 - the existing enrollment pipeline, unchanged."""
    import setup_student_database

    argv = ["--csv", str(csv_path)]
    if videos_dir is not None:
        argv += ["--videos-dir", str(videos_dir)]
    if use_rpc:
        argv.append("--rpc")
    return setup_student_database.main(argv)


# ------------------------------------------------- same-source (honest labelling)
def enrollment_video_candidates(videos_dir: Path | None) -> list[Path]:
    roots = [ENROLLMENT_DIR]
    if videos_dir is not None:
        roots.append(videos_dir)
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out += [
                p
                for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS
            ]
    return out


def _file_digest(path: Path, limit: int = 64 * 1024 * 1024) -> str:
    """SHA-256 of the whole file, or of head+tail for big files."""
    h = hashlib.sha256()
    size = path.stat().st_size
    try:
        if size <= limit:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        else:
            with path.open("rb") as fh:
                h.update(fh.read(4 << 20))
                fh.seek(max(0, size - (4 << 20)))
                h.update(fh.read(4 << 20))
    except OSError:
        return ""
    return f"{size}:{h.hexdigest()}"


def _fingerprints(path: Path, samples: int = 4) -> list:
    """A few 32x32 grayscale frames spread over the clip."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out = []
    try:
        for i in range(1, samples + 1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * i / (samples + 1))))
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, (32, 32))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype("float32")
            out.append(gray)
    finally:
        cap.release()
    return out


def _near_identical(video: Path, candidates: list[Path], tol: float = 6.0) -> Path | None:
    """True when the clip was cut out of an enrollment video (same pixels)."""
    import numpy as np

    try:
        ours = _fingerprints(video)
    except Exception:  # noqa: BLE001 - a bad clip must not kill the demo
        return None
    if not ours:
        return None
    for cand in candidates:
        if cand.resolve() == video.resolve():
            continue
        try:
            theirs = _fingerprints(cand)
        except Exception:  # noqa: BLE001
            continue
        if not theirs:
            continue
        for a in ours:
            for b in theirs:
                diff = float(np.mean(np.abs(a - b)))
                if diff <= tol:
                    return cand
    return None


def detect_same_source(
    video: Path, candidates: list[Path]
) -> tuple[bool, str]:
    """Is the supplied clip one of the enrollment sources? (path/bytes/pixels)"""
    if not candidates:
        return False, ""
    v = video.resolve()
    for cand in candidates:
        try:
            if cand.resolve() == v:
                return True, "the supplied video is itself an enrollment source"
        except OSError:  # pragma: no cover - unreadable path
            continue
    try:
        v_digest = _file_digest(v)
    except OSError:
        return False, ""
    if v_digest:
        for cand in candidates:
            try:
                if cand.is_file() and _file_digest(cand) == v_digest:
                    return True, f"byte-identical to an enrollment source ({cand.name})"
            except OSError:  # pragma: no cover
                continue
    hit = _near_identical(video, candidates)
    if hit is not None:
        return True, f"frame-for-frame match with an enrollment source ({hit.name})"
    return False, ""


# ------------------------------------------------------------- step 8: outputs
def _esc(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def best_similarities(ctx: dict) -> dict[str, float]:
    """Best cosine similarity seen per registration number."""
    best: dict[str, float] = {}
    for row in ctx.get("observations", []):
        reg = row.get("predicted") or ""
        if not reg or reg == "UNKNOWN":
            continue
        score = float(row.get("score") or 0.0)
        if score > best.get(reg, -1.0):
            best[reg] = score
    for row in ctx.get("attendance", []):
        reg = row["registration_no"]
        score = float(row.get("match") or 0.0)
        if score > best.get(reg, -1.0):
            best[reg] = score
    return best


def build_summary(args: argparse.Namespace, ctx: dict, extra: dict) -> dict:
    """Everything summary.json / report.html need (no invented numbers)."""
    enrolled: list[dict] = extra["enrolled"]
    present_regs = {a["registration_no"] for a in ctx["attendance"]}
    enrolled_regs = {s["registration_no"] for s in enrolled}

    if ctx.get("roster"):
        absent = [
            r
            for r in ctx["roster"]
            if r["status"] == "ABSENT" and r["registration_no"] in enrolled_regs
        ]
        absent_source = "form CSV roster x gallery"
    else:
        absent = [
            {"name": s["name"], "registration_no": s["registration_no"],
             "status": "ABSENT", "timestamp": "", "match": ""}
            for s in enrolled
            if s["registration_no"] not in present_regs
        ]
        absent_source = "gallery (no form CSV supplied)"

    metrics = ctx.get("metrics") or {}
    gt_path = args.ground_truth
    evaluation = (
        {
            "status": "calculated",
            "ground_truth": str(gt_path),
            "note": ctx.get("note", ""),
            "metrics": metrics,
        }
        if metrics and gt_path
        else {
            "status": "not_calculated",
            "ground_truth": None,
            "reason": "no independent ground truth was provided",
            "accuracy": NO_GT_ACCURACY,
        }
    )

    best = best_similarities(ctx)
    files = {
        "attendance_csv": str(Path(ctx["out_dir"]) / "attendance.csv"),
        "observations_csv": str(Path(ctx["out_dir"]) / "observations.csv"),
        "report_html": str(Path(ctx["out_dir"]) / "report.html"),
        "summary_json": str(Path(ctx["out_dir"]) / "summary.json"),
        "frames_dir": ctx["frames_dir"],
        "annotated_video": ctx.get("annotated_video", ""),
    }
    if Path(ctx["out_dir"], "roster.csv").is_file():
        files["roster_csv"] = str(Path(ctx["out_dir"]) / "roster.csv")
    if Path(ctx["out_dir"], "metrics.json").is_file():
        files["metrics_json"] = str(Path(ctx["out_dir"]) / "metrics.json")

    limitations: list[str] = []
    if extra["same_source"]:
        limitations.append(
            "DEMO / SAME-SOURCE VIDEO: " + extra["same_source_reason"]
            + " - this is a demonstration, not an independent CCTV accuracy benchmark."
        )
    limitations.append(SAME_SOURCE_NOTE)
    if evaluation["status"] != "calculated":
        limitations.append(NO_GT_ACCURACY + " (pass --ground-truth frame,reg to compute it).")
    if extra["missing"]:
        regs = ", ".join(m["registration_no"] for m in extra["missing"])
        limitations.append(
            f"{len(extra['missing'])} student(s) from the CSV have no enrollment "
            f"({regs}) - Drive video inaccessible or no usable face - so they "
            "cannot be recognised and no embedding was created for them."
        )
    if not ctx["attendance"]:
        limitations.append(
            "nobody reached the confirmation rule (k of n within the window) - "
            "no attendance was marked."
        )
    if ctx.get("unknown_faces"):
        limitations.append(
            f"{ctx['unknown_faces']} face observation(s) stayed UNKNOWN and were "
            "not marked (rejection is preferred over a wrong identity)."
        )
    if files["annotated_video"] and not Path(files["annotated_video"]).is_file():
        limitations.append("annotated video could not be written; annotated frames were.")
    if not files["annotated_video"]:
        limitations.append("annotated video disabled (--no-annotated-video).")
    if not extra["csv"]:
        limitations.append(
            "no form CSV - present/absent is computed against the gallery only."
        )

    return {
        "command": extra["command"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": str(args.video),
        "video_name": args.video.name,
        "duration_s": ctx["clip_seconds"],
        "fps": ctx["fps"],
        "frames_total": ctx["total_frames"],
        "frames_processed": ctx["frames_decoded"],
        "frames_sampled": ctx["frames_sampled"],
        "resolution": [ctx["width"], ctx["height"]],
        "sampling_fps": ctx["sampling_fps"],
        "faces_detected": ctx["faces"],
        "known_faces": ctx["faces"] - ctx["unknown_faces"],
        "unknown_faces": ctx["unknown_faces"],
        "students_marked_present": len(ctx["attendance"]),
        "students_absent": len(absent),
        "absent_source": absent_source,
        "gallery": {
            "students": ctx["students"],
            "embeddings": ctx["embeddings"],
            "missing_enrollment": extra["missing"],
            "missing_count": len(extra["missing"]),
        },
        "rules": {
            "threshold": ctx["threshold"],
            "margin": ctx["margin"],
            "confirm_k": ctx["confirm_k"],
            "confirm_n": ctx["confirm_n"],
            "confirm_window_s": ctx["confirm_window"],
            "min_frames_rule": f"{ctx['confirm_k']} frames / {ctx['confirm_window']} s",
        },
        "same_source": extra["same_source"],
        "same_source_label": SAME_SOURCE_LABEL if extra["same_source"] else "",
        "same_source_reason": extra["same_source_reason"],
        "same_source_note": SAME_SOURCE_NOTE,
        "evaluation": evaluation,
        "attendance": [
            {
                "registration_no": a["registration_no"],
                "name": a["name"],
                "status": a["status"],
                "timestamp": a["timestamp"],
                "match_at_confirmation": float(a["match"]),
                "best_similarity": round(
                    best.get(a["registration_no"], float(a["match"])), 4
                ),
                "confirmed_frames": a["frames"],
            }
            for a in ctx["attendance"]
        ],
        "absent": [
            {
                "registration_no": r["registration_no"],
                "name": r["name"],
                "status": "ABSENT",
            }
            for r in absent
        ],
        "files": files,
        "limitations": limitations,
    }


def write_summary(out_dir: Path, summary: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "summary.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_report_html(out_dir: Path, summary: dict, ctx: dict) -> Path:
    """The single dashboard the judge opens (5 required sections)."""
    metrics = (summary["evaluation"].get("metrics") or {}) if summary["evaluation"]["status"] == "calculated" else {}
    best = best_similarities(ctx)

    # 2. attendance table
    att_rows = "".join(
        "<tr>"
        f"<td>{_esc(a['registration_no'])}</td>"
        f"<td>{_esc(a['name'])}</td>"
        f"<td class='ok'>{_esc(a['status'])}</td>"
        f"<td>{_esc(a['timestamp'])}</td>"
        f"<td>{a['best_similarity']:.4f}</td>"
        "</tr>"
        for a in summary["attendance"]
    ) or "<tr><td colspan=5>nobody confirmed</td></tr>"
    absent_rows = "".join(
        f"<tr><td>{_esc(a['registration_no'])}</td><td>{_esc(a['name'])}</td>"
        "<td class='bad'>ABSENT</td><td>-</td><td>-</td></tr>"
        for a in summary["absent"]
    )

    # 3. recognised identities + unknown detections
    per_reg: dict[str, dict] = {}
    for row in ctx["observations"]:
        reg = row["predicted"]
        if reg == "UNKNOWN":
            continue
        slot = per_reg.setdefault(
            reg, {"reg": reg, "name": "", "obs": 0, "best": -1.0, "confirmed": "NO"})
        slot["obs"] += 1
        slot["best"] = max(slot["best"], float(row["score"]))
    for a in ctx["attendance"]:
        slot = per_reg.setdefault(
            a["registration_no"],
            {"reg": a["registration_no"], "name": a["name"], "obs": 0,
             "best": -1.0, "confirmed": "NO"},
        )
        slot["name"] = a["name"]
        slot["confirmed"] = "YES - PRESENT"
    known_rows = "".join(
        f"<tr><td>{_esc(s['reg'])}</td><td>{_esc(s['name']) or '-'}</td>"
        f"<td>{s['obs']}</td><td>{s['best']:.4f}</td>"
        f"<td class='{'ok' if s['confirmed'].startswith('YES') else 'warn'}\">"
        f"{_esc(s['confirmed'])}</td></tr>"
        for s in sorted(per_reg.values(), key=lambda x: -x["best"])
    ) or "<tr><td colspan=5>no known identity matched</td></tr>"

    unknown_rows_obs = [r for r in ctx["observations"] if r["predicted"] == "UNKNOWN"]
    unknown_rows = "".join(
        f"<tr><td>{r['frame']}</td><td>{r['time_s']:.2f}</td>"
        f"<td>{r['score']:.4f}</td><td>{_esc(r['bbox'])}</td></tr>"
        for r in unknown_rows_obs[:50]
    ) or "<tr><td colspan=4>no unknown faces detected</td></tr>"
    unk_scores = [float(r["score"]) for r in unknown_rows_obs]
    unk_range = (
        f"min {min(unk_scores):.4f} / max {max(unk_scores):.4f}"
        if unk_scores
        else "-"
    )

    # 5. evaluation
    if summary["evaluation"]["status"] == "calculated":
        metric_rows = "".join(
            f"<tr><td>{_esc(k)}</td><td>{v if isinstance(v, int) else f'{v:.4f}'}</td></tr>"
            for k, v in metrics.items()
        )
        evaluation_html = (
            f"<p class='ok'><b>Accuracy: "
            f"{float(metrics.get('accuracy', 0.0)):.4f} - calculated from "
            "ground truth with the identity-level evaluator</b></p>"
            f"<p>Ground truth: {_esc(summary['evaluation']['ground_truth'])}</p>"
            f"<table><tr><th>metric</th><th>value</th></tr>{metric_rows}</table>"
            f"<p class='muted'>{_esc(summary['evaluation'].get('note', ''))}</p>"
        )
    else:
        evaluation_html = (
            f"<p class='bad'><b>{_esc(NO_GT_ACCURACY)}</b></p>"
            f"<p>{_esc(NO_GT_NOTE)}</p>"
            "<p class='muted'>Face counts are never used as accuracy. "
            "Supply <code>--ground-truth frame,reg</code> to run the "
            "identity-level evaluator.</p>"
        )

    same_banner = (
        f"<p class='bad'><b>{SAME_SOURCE_LABEL}</b><br>"
        f"{_esc(summary['same_source_reason'])}</p>"
        if summary["same_source"]
        else ""
    )
    absent_line = (
        f"<p><b>Students absent:</b> {summary['students_absent']} "
        f"<span class='muted'>({_esc(summary['absent_source'])})</span></p>"
    )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Judge demo - {_esc(summary['video_name'])}</title>
<style>
 body{{font-family:Arial,Helvetica,sans-serif;margin:32px;color:#222;max-width:1100px}}
 table{{border-collapse:collapse;width:100%;margin-bottom:24px}}
 th,td{{border:1px solid #ddd;padding:7px 10px;text-align:left;font-size:14px}}
 th{{background:#2e7d32;color:#fff}}
 tr:nth-child(even){{background:#f6f6f6}}
 .ok{{color:#2e7d32;font-weight:bold}}
 .bad{{color:#c62828;font-weight:bold}}
 .warn{{color:#e65100;font-weight:bold}}
 .muted{{color:#666;font-size:13px}}
 .banner{{background:#fff8e1;border:1px solid #ffe082;padding:12px 16px;border-radius:6px}}
 .pipeline{{background:#e8f5e9;border:1px solid #a5d6a7;padding:14px 16px;border-radius:6px;
           font-family:Consolas,monospace;font-size:15px;line-height:1.9}}
 h1{{margin-bottom:4px}} h2{{margin-top:30px;border-bottom:2px solid #2e7d32;padding-bottom:4px}}
 code{{background:#f1f1f1;padding:1px 5px;border-radius:3px}}
</style></head><body>

<h1>&#127909; AI Video Attendance - Judge Demo</h1>
<p class="muted">generated {_esc(summary['generated_at'])}</p>

<p class="banner">{same_banner}
<b>Video:</b> {_esc(summary['video_name'])}<br>
<b>Gallery:</b> {summary['gallery']['students']} students /
{summary['gallery']['embeddings']} reference embeddings - threshold
{summary['rules']['threshold']}, margin {summary['rules']['margin']},
confirmation {summary['rules']['confirm_k']} of last
{summary['rules']['confirm_n']} within {summary['rules']['confirm_window_s']} s<br>
<b>Note:</b> {_esc(summary['same_source_note'])}</p>

<h2>1. Demo summary</h2>
<table>
<tr><th>field</th><th>value</th></tr>
<tr><td>Video</td><td>{_esc(summary['video'])}</td></tr>
<tr><td>Duration</td><td>{summary['duration_s']:.1f} s
 ({summary['frames_total']} frames @ {summary['fps']:.2f} fps,
 {summary['resolution'][0]}x{summary['resolution'][1]})</td></tr>
<tr><td>Frames processed</td><td>{summary['frames_processed']}
 (decoded) / {summary['frames_sampled']} sampled at {summary['sampling_fps']} fps</td></tr>
<tr><td>Faces detected</td><td>{summary['faces_detected']}</td></tr>
<tr><td>Known faces</td><td class="ok">{summary['known_faces']}</td></tr>
<tr><td>Unknown faces</td><td class="bad">{summary['unknown_faces']}</td></tr>
<tr><td>Students marked present</td><td class="ok">{summary['students_marked_present']}</td></tr>
<tr><td>Students absent</td><td class="bad">{summary['students_absent']}</td></tr>
<tr><td>Students enrolled</td><td>{summary['gallery']['students']}</td></tr>
<tr><td>Reference embeddings</td><td>{summary['gallery']['embeddings']}</td></tr>
<tr><td>Students missing enrollment</td><td class="warn">{summary['gallery']['missing_count']}</td></tr>
</table>
{absent_line}
{('<p class="warn">Missing enrollment: ' + _esc(', '.join(m['registration_no'] for m in summary['gallery']['missing_enrollment'])) + '</p>') if summary['gallery']['missing_enrollment'] else ''}

<h2>2. Attendance</h2>
<table><tr><th>Registration No.</th><th>Name</th><th>Status</th><th>Time</th>
<th>Best Similarity</th></tr>
{att_rows}{absent_rows}</table>

<h2>3. Recognition results</h2>
<p class="muted">Known identities (confirmation requires
{summary['rules']['confirm_k']} of the last {summary['rules']['confirm_n']}
observations inside {summary['rules']['confirm_window_s']} s - a single frame
never marks attendance):</p>
<table><tr><th>Registration No.</th><th>Name</th><th>Observations</th>
<th>Best similarity</th><th>Confirmed</th></tr>
{known_rows}</table>

<p class="muted">Unknown detections: {summary['unknown_faces']} observation(s),
similarity {unk_range} - never marked, never mapped to a student:</p>
<table><tr><th>frame</th><th>time (s)</th><th>similarity</th><th>bbox</th></tr>
{unknown_rows}</table>
<p class="muted">showing the first 50 - full file: observations.csv</p>

<h2>4. System pipeline</h2>
<div class="pipeline">
Video<br>
&darr;<br>
SCRFD face detection (InsightFace buffalo_l)<br>
&darr;<br>
Face quality gate (sharpness / brightness / size / pose)<br>
&darr;<br>
ArcFace 512-d embedding<br>
&darr;<br>
Cosine similarity (threshold {summary['rules']['threshold']} + margin
{summary['rules']['margin']})<br>
&darr;<br>
Multi-frame confirmation ({summary['rules']['min_frames_rule']})<br>
&darr;<br>
Attendance ledger (one mark per student - duplicates impossible)
</div>

<h2>5. Evaluation</h2>
{evaluation_html}

<h2>Files</h2>
<ul>
<li><a href="attendance.csv">attendance.csv</a></li>
<li><a href="observations.csv">observations.csv</a></li>
<li><a href="summary.json">summary.json</a></li>
{'<li><a href="roster.csv">roster.csv</a></li>' if 'roster_csv' in summary['files'] else ''}
{'<li><a href="metrics.json">metrics.json</a></li>' if 'metrics_json' in summary['files'] else ''}
<li><a href="annotated_video.mp4">annotated_video.mp4</a></li>
<li>frames/ ({len(list(Path(ctx['frames_dir']).glob('*.jpg'))) if Path(ctx['frames_dir']).is_dir() else 0} annotated frames)</li>
</ul>

<h2>Known limitations</h2>
<ul>{''.join(f'<li>{_esc(x)}</li>' for x in summary['limitations'])}</ul>

</body></html>"""
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path


# ------------------------------------------------------------------ terminal
def print_gallery_block(summary: dict) -> None:
    print("\nStudent Gallery:")
    print(f"Enrolled: {summary['gallery']['students']}")
    print(f"Embeddings: {summary['gallery']['embeddings']}")
    print(f"Students missing enrollment: {summary['gallery']['missing_count']}")


def print_final_report(summary: dict, ctx: dict) -> None:
    print("\n" + "=" * 40 + " ATTENDANCE " + "=" * 40)
    if not summary["attendance"]:
        print("  (nobody confirmed - no attendance marked)")
    for a in summary["attendance"]:
        print(f"{a['registration_no']}  {a['name']}   {a['status']}   "
              f"{a['timestamp'][11:19] if len(a['timestamp']) > 19 else a['timestamp']}   "
              f"{a['best_similarity']:.2f}")
    for a in summary["absent"]:
        print(f"{a['registration_no']}  {a['name']}   ABSENT")
    print("-" * 92)
    print(f"Students Present: {summary['students_marked_present']}")
    print(f"Students Absent : {summary['students_absent']}  ({summary['absent_source']})")

    # recognised but never confirmed -> shown as VERIFYING, never as present
    confirmed = {a["registration_no"] for a in summary["attendance"]}
    pending: dict[str, dict] = {}
    for row in ctx["observations"]:
        reg = row["predicted"]
        if reg == "UNKNOWN" or reg in confirmed:
            continue
        current = pending.get(reg)
        if current is None or float(row["score"]) > float(current["score"]):
            pending[reg] = row   # keep the best similarity, not the last one
    if pending:
        print("\nMATCHED, NOT CONFIRMED (still VERIFYING - not marked):")
        for reg, row in sorted(pending.items(), key=lambda kv: -float(kv[1]["score"])):
            print(f"  {reg}  similarity {float(row['score']):.2f}  "
                  f"confirmed: NO  attendance: NOT MARKED")

    unknown = [r for r in ctx["observations"] if r["predicted"] == "UNKNOWN"]
    if unknown:
        scores = [float(r["score"]) for r in unknown]
        print(f"\nUNKNOWN: {len(unknown)} observation(s) - "
              f"similarity {min(scores):.2f}-{max(scores):.2f} - attendance: NOT MARKED")
        for row in unknown[:5]:
            print(f"  frame {row['frame']:>6}  similarity {float(row['score']):.2f}")

    if summary["evaluation"]["status"] == "calculated":
        m = summary["evaluation"]["metrics"]
        print("\nEVALUATION (identity-level, ground truth supplied)")
        for key in ("accuracy", "precision", "recall", "f1", "far", "frr",
                    "unknown_rejection_rate"):
            if key in m:
                print(f"  {key:24} {m[key]:.4f}")
        print(f"  {'correct/total':24} {m.get('correct')}/{m.get('total')}")
    else:
        print(f"\n{NO_GT_ACCURACY}")
        print(f"  {NO_GT_NOTE}")

    print("\n" + "=" * 40 + " OUTPUT FILES " + "=" * 40)
    print(f"Attendance : {summary['files']['attendance_csv']}")
    print(f"Report     : {summary['files']['report_html']}")
    print(f"Summary    : {summary['files']['summary_json']}")
    print(f"Observations: {summary['files']['observations_csv']}")
    print(f"Frames     : {summary['files']['frames_dir']}")
    if summary["files"].get("annotated_video"):
        print(f"Annotated  : {summary['files']['annotated_video']}")


def print_acceptance(summary: dict) -> None:
    print("\n" + "=" * 40 + " DEMO ACCEPTANCE " + "=" * 39)
    items = [
        ("Exact command used", summary["command"]),
        ("Students enrolled", summary["gallery"]["students"]),
        ("Faces detected", summary["faces_detected"]),
        ("Known recognitions", summary["known_faces"]),
        ("Unknowns", summary["unknown_faces"]),
        ("Students marked present", summary["students_marked_present"]),
        ("Attendance report path", summary["files"]["attendance_csv"]),
        ("HTML report path", summary["files"]["report_html"]),
        ("Annotated video path",
         summary["files"].get("annotated_video") or "(not written)"),
        ("Remaining limitations", f"{len(summary['limitations'])} (see report.html)"),
    ]
    for i, (label, value) in enumerate(items, 1):
        print(f"{i:2}. {label}: {value}")
    for i, text in enumerate(summary["limitations"], 1):
        print(f"      - {text}")


# ----------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="judge_demo.py",
        description="One-command judge demo: one MP4 -> recognition, "
        "attendance, annotated video and an HTML report.",
    )
    ap.add_argument("--video", required=True, type=Path, help="MP4/classroom video")
    ap.add_argument(
        "--csv", type=Path, default=None,
        help="Google Form enrollment CSV (default: DEFAULT_FORM_CSV, else the "
        "single data/enrollment/*.csv)",
    )
    ap.add_argument(
        "--ground-truth", type=Path, default=None,
        help="CSV frame,reg - WITHOUT it no accuracy is ever computed",
    )
    ap.add_argument(
        "--videos-dir", type=Path, default=None,
        help="folder of local enrollment videos <reg>.mp4 (bypasses Google Drive)",
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--sampling-fps", type=float, default=5.0,
                    help="recognise at N fps (5 is comfortable on CPU)")
    ap.add_argument("--confirm-k", type=int, default=3)
    ap.add_argument("--confirm-n", type=int, default=5)
    ap.add_argument("--confirm-window", type=float, default=10.0)
    ap.add_argument("--camera-name", default="judge-demo")
    ap.add_argument("--camera-type", default="entry", choices=["entry", "exit", "both"])
    ap.add_argument("--annotate-every", type=float, default=1.0)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--start-time", default=None, help="ISO time of frame 0")
    ap.add_argument("--rpc", action="store_true",
                    help="use the shared Redis inference service (default: local FaceEngine)")
    ap.add_argument("--no-annotated-video", action="store_true",
                    help="skip annotated_video.mp4")
    ap.add_argument("--same-source", action="store_true",
                    help="force the DEMO / SAME-SOURCE VIDEO label when you know "
                    "the clip comes from the same session as enrollment")
    ap.add_argument("--debug", action="store_true", help="show the traceback")
    return ap


def run_demo(args: argparse.Namespace) -> int:
    video: Path = args.video
    command = (
        f"{Path(sys.executable).name} scripts/judge_demo.py --video \"{video}\""
        + (f" --csv \"{args.csv}\"" if args.csv else "")
        + (f" --ground-truth \"{args.ground_truth}\"" if args.ground_truth else "")
    )

    # ---- STEP 1 ------------------------------------------------------------
    if not video.is_file():
        raise DemoError(
            f"video not found: {video} - pass an existing MP4 with --video"
        )
    print("=" * 40 + " AI ATTENDANCE - JUDGE DEMO " + "=" * 40)
    print(f"Video: {video.name}")
    print_checks(check_environment())

    # ---- STEP 2 ------------------------------------------------------------
    csv_path = resolve_form_csv(args.csv)
    enrolled = enrolled_students()
    enrolled_regs = {s["registration_no"] for s in enrolled}
    missing: list[dict] = []
    csv_rows = 0
    if csv_path is not None:
        missing, csv_rows, invalid_rows = missing_enrollment(csv_path, enrolled_regs)
        if invalid_rows:
            print(f"  [WARN] enrollment CSV: {invalid_rows} row(s) with invalid data")

    stats = gallery_stats()
    if stats["students"] == 0 or missing:
        if csv_path is None:
            raise DemoError(
                "no enrolled students and no enrollment CSV found - "
                "pass --csv \"path/to/form.csv\" (and --videos-dir if the "
                "videos are local)"
            )
        if stats["students"] == 0:
            print("\nSTEP 2  gallery is empty - running the existing enrollment pipeline")
        else:
            print(
                f"\nSTEP 2  gallery incomplete - enrolling {len(missing)} "
                "missing student(s) with the existing enrollment pipeline"
            )
        print(f"  csv      : {csv_path}")
        print(f"  videos   : {args.videos_dir or '(Google Drive links only)'}")
        rc = run_enrollment(csv_path, args.videos_dir, args.rpc)
        if rc != 0:
            raise DemoError(
                f"enrollment pipeline failed (exit {rc}) - see the output above "
                "(inaccessible Drive videos are reported per student and skipped)"
            )
        stats = gallery_stats()
    else:
        print("\nSTEP 2  gallery already complete - enrollment not needed")

    enrolled = enrolled_students()
    enrolled_regs = {s["registration_no"] for s in enrolled}
    if csv_path is not None:
        missing, csv_rows, _ = missing_enrollment(csv_path, enrolled_regs)
    if stats["students"] == 0 or stats["embeddings"] == 0:
        raise DemoError(
            "no enrolled students in the gallery - enrollment produced nothing "
            "(no usable face / no accessible video). Check the enrollment output above."
        )

    print("\nSTEP 3  gallery validation")
    print(f"  Students enrolled       : {stats['students']}")
    print(f"  Reference embeddings    : {stats['embeddings']}")
    print(f"  Students missing enrollment: {len(missing)}"
          + ("" if not missing else
             "  -> " + ", ".join(m["registration_no"] for m in missing)))
    if csv_path is not None:
        print(f"  Enrollment CSV rows     : {csv_rows} ({csv_path})")

    # ---- same-source labelling -------------------------------------------
    same_source = bool(args.same_source)
    reason = "labelled by the operator (--same-source)" if same_source else ""
    if not same_source:
        same_source, reason = detect_same_source(
            video, enrollment_video_candidates(args.videos_dir)
        )
    if same_source:
        print(f"\n  [{SAME_SOURCE_LABEL}] {reason}")
        print(f"  {SAME_SOURCE_NOTE}")
    else:
        print(f"\n  Note: {SAME_SOURCE_NOTE}")

    # ---- STEP 4-7: recognition -------------------------------------------
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    annotated = None if args.no_annotated_video else out_dir / "annotated_video.mp4"

    argv2 = [
        "--video", str(video),
        "--out-dir", str(out_dir),
        "--sampling-fps", str(args.sampling_fps),
        "--confirm-k", str(args.confirm_k),
        "--confirm-n", str(args.confirm_n),
        "--confirm-window", str(args.confirm_window),
        "--camera-name", args.camera_name,
        "--camera-type", args.camera_type,
        "--annotate-every", str(args.annotate_every),
        "--judge-labels",
        "--progress",
    ]
    if csv_path is not None:
        argv2 += ["--expected", str(csv_path)]
    if args.ground_truth is not None:
        if not args.ground_truth.is_file():
            raise DemoError(f"ground truth CSV not found: {args.ground_truth}")
        argv2 += ["--ground-truth", str(args.ground_truth)]
    if annotated is not None:
        argv2 += ["--annotated-video", str(annotated)]
    if args.max_frames:
        argv2 += ["--max-frames", str(args.max_frames)]
    if args.start_time:
        argv2 += ["--start-time", str(args.start_time)]
    if args.rpc:
        argv2.append("--rpc")

    print("\nSTEP 4  processing video (SCRFD -> quality gate -> ArcFace -> "
          "cosine -> confirmation)")
    import test_cctv

    try:
        cctv_args = test_cctv.build_parser().parse_args(argv2)
        ctx = test_cctv.run_recognition(cctv_args)
    except test_cctv.CCTVError as exc:
        raise DemoError(str(exc)) from exc

    # ---- STEP 8: outputs --------------------------------------------------
    summary = build_summary(args, ctx, {
        "enrolled": enrolled,
        "missing": missing,
        "csv": csv_path,
        "same_source": same_source,
        "same_source_reason": reason,
        "command": command,
    })
    write_summary(out_dir, summary)
    write_report_html(out_dir, summary, ctx)

    print_final_report(summary, ctx)
    print_acceptance(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_demo(args)
    except DemoError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nERROR: interrupted by user", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - never dump a bare traceback
        if args.debug:
            raise
        print(f"\nFATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("       re-run with --debug for the full traceback", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

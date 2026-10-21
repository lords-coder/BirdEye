"""Judge demo (`scripts/judge_demo.py`) - one-command orchestration tests.

The heavy pieces (model, DB writes, video decode) are stubbed with fakes that
return exactly what the real ``test_cctv.run_recognition`` returns, so these
tests check the *orchestration and the honesty rules*:

  - files are produced (summary.json, report.html, attendance.csv, ...)
  - no accuracy is ever reported without ground truth
  - the demo labels itself when the clip is an enrollment source
  - an unconfirmed match is never shown as present
  - enrollment is (re)run through the existing pipeline when the gallery is
    empty/incomplete, and an enrollment failure is reported, not swallowed
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


judge_demo = _load("judge_demo")
test_cctv = _load("test_cctv")

REG_A = "900000001"
REG_B = "900000002"


# ------------------------------------------------------------------ helpers
def _default_ctx(out_dir: Path) -> dict:
    """Same shape as test_cctv.run_recognition's return value."""
    attendance = [
        {
            "name": "Test Student",
            "registration_no": REG_A,
            "status": "PRESENT",
            "timestamp": "2026-10-06 10:32:14",
            "time": "10:32:14",
            "clip_time_s": 3.8,
            "match": "0.7400",
            "frames": 3,
            "track_id": 1,
            "student_id": 1,
            "db_action": "created",
        }
    ]
    observations = [
        {"frame": 10, "time_s": 0.33, "track_id": 1, "bbox": [10, 10, 80, 80],
         "expected": "", "predicted": REG_A, "score": 0.78, "result": "MATCH"},
        {"frame": 20, "time_s": 0.66, "track_id": 1, "bbox": [11, 10, 81, 80],
         "expected": "", "predicted": REG_A, "score": 0.76, "result": "MATCH"},
        {"frame": 30, "time_s": 1.00, "track_id": 1, "bbox": [12, 10, 82, 80],
         "expected": "", "predicted": REG_A, "score": 0.74, "result": "MATCH"},
        {"frame": 40, "time_s": 1.33, "track_id": 2, "bbox": [300, 10, 380, 80],
         "expected": "", "predicted": "UNKNOWN", "score": 0.31, "result": "UNKNOWN"},
        {"frame": 50, "time_s": 1.66, "track_id": 2, "bbox": [301, 10, 381, 80],
         "expected": "", "predicted": "UNKNOWN", "score": 0.29, "result": "UNKNOWN"},
        {"frame": 60, "time_s": 2.00, "track_id": 2, "bbox": [302, 10, 382, 80],
         "expected": "", "predicted": "UNKNOWN", "score": 0.33, "result": "UNKNOWN"},
    ]
    return {
        "clip_label": "real camera footage (demo.mp4) - no ground truth supplied, "
                      "no accuracy claim",
        "note": "Enrollment videos must never be used as the accuracy test set.",
        "students": 2,
        "embeddings": 10,
        "confirm_k": 3,
        "confirm_n": 5,
        "confirm_window": 10.0,
        "frames_decoded": 60,
        "frames_sampled": 30,
        "faces": 6,
        "unknown_faces": 3,
        "tracks": 2,
        "attendance": attendance,
        "roster": [
            {"name": "Test Student", "registration_no": REG_A,
             "status": "PRESENT", "timestamp": "2026-10-06 10:32:14",
             "match": "0.7400"},
            {"name": "Second Student", "registration_no": REG_B,
             "status": "ABSENT", "timestamp": "", "match": ""},
        ],
        "observations": observations,
        "metrics": {},
        "gt_frames_skipped": 0,
        "video": "demo.mp4",
        "video_name": "demo.mp4",
        "out_dir": str(out_dir),
        "frames_dir": str(out_dir / "frames"),
        "annotated_video": str(out_dir / "annotated_video.mp4"),
        "start_wall": "2026-10-06T10:32:14+00:00",
        "clip_seconds": 10.0,
        "fps": 30.0,
        "total_frames": 300,
        "width": 640,
        "height": 640,
        "sampling_fps": 5.0,
        "threshold": 0.40,
        "margin": 0.10,
        "expected_count": 2,
    }


def _default_extra(args, same_source: bool = False, reason: str = "") -> dict:
    return {
        "enrolled": [
            {"student_id": 1, "name": "Test Student", "registration_no": REG_A},
            {"student_id": 2, "name": "Second Student", "registration_no": REG_B},
        ],
        "missing": [],
        "csv": None,
        "same_source": same_source,
        "same_source_reason": reason,
        "command": 'python scripts/judge_demo.py --video "demo.mp4"',
    }


def _stub_recognition(monkeypatch, ctx_factory=None) -> dict:
    """Swap the video-processing step for a fake with the real CLI parser."""
    calls: dict = {}

    def _run(args):
        calls["args"] = args
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "attendance.csv").write_text(
            "name,registration_no,status,timestamp,time,clip_time_s,match,frames,"
            "track_id,student_id,db_action\n", encoding="utf-8",
        )
        (out / "observations.csv").write_text(
            "frame,time_s,track_id,bbox,expected,predicted,score,result\n",
            encoding="utf-8",
        )
        if args.annotated_video is not None:
            Path(args.annotated_video).parent.mkdir(parents=True, exist_ok=True)
            Path(args.annotated_video).write_bytes(b"\x00\x00\x00\x18ftypmp42")
        ctx = ctx_factory(out) if ctx_factory else _default_ctx(out)
        return ctx

    fake_mod = types.ModuleType("test_cctv")
    fake_mod.build_parser = test_cctv.build_parser
    fake_mod.CCTVError = test_cctv.CCTVError
    fake_mod.run_recognition = _run
    monkeypatch.setitem(sys.modules, "test_cctv", fake_mod)
    return calls


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Point every environment lookup at tmp_path (no real CSV/Drive scan)."""
    empty = tmp_path / "enrollment_dir"
    empty.mkdir()
    monkeypatch.setattr(judge_demo, "ENROLLMENT_DIR", empty)
    monkeypatch.setattr(judge_demo, "enrollment_video_candidates", lambda v: [])
    return empty


@pytest.fixture()
def ctx():
    return _default_ctx(Path("eval/reports/judge_demo"))


def _args(*extra: str):
    return judge_demo.build_parser().parse_args(["--video", "demo.mp4", *extra])


# ------------------------------------------------------- resolve_form_csv
def test_resolve_form_csv_explicit_missing(tmp_path) -> None:
    with pytest.raises(judge_demo.DemoError, match="CSV not found"):
        judge_demo.resolve_form_csv(tmp_path / "nope.csv")


def test_resolve_form_csv_auto_detects_single(tmp_path, monkeypatch) -> None:
    root = tmp_path / "enroll"
    root.mkdir()
    only = root / "form.csv"
    only.write_text("name,registration\n", encoding="utf-8")
    monkeypatch.setattr(judge_demo, "ENROLLMENT_DIR", root)
    assert judge_demo.resolve_form_csv(None) == only


def test_resolve_form_csv_ambiguous_lists_candidates(tmp_path, monkeypatch) -> None:
    root = tmp_path / "enroll"
    root.mkdir()
    (root / "a.csv").write_text("x\n", encoding="utf-8")
    (root / "b.csv").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(judge_demo, "ENROLLMENT_DIR", root)
    with pytest.raises(judge_demo.DemoError, match="--csv"):
        judge_demo.resolve_form_csv(None)


def test_resolve_form_csv_none_when_nothing_configured(tmp_path, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(judge_demo, "ENROLLMENT_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(settings, "default_form_csv", "")
    assert judge_demo.resolve_form_csv(None) is None


# ------------------------------------------------------------- CLI errors
def test_missing_video_is_a_user_facing_message(tmp_path, capsys) -> None:
    rc = judge_demo.main(["--video", str(tmp_path / "nope.mp4")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "video not found" in captured.err
    assert "Traceback" not in captured.err


def test_missing_ground_truth_file_is_reported(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 1, "embeddings": 2})
    monkeypatch.setattr(judge_demo, "enrolled_students",
                        lambda: [{"student_id": 1, "name": "T",
                                  "registration_no": REG_A}])
    monkeypatch.setattr(judge_demo, "missing_enrollment", lambda c, r: ([], 1, 0))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a video")
    rc = judge_demo.main([
        "--video", str(video),
        "--ground-truth", str(tmp_path / "missing_gt.csv"),
        "--out-dir", str(tmp_path / "out"),
    ])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ground truth CSV not found" in captured.err


# --------------------------------------------------- honesty: no fake numbers
def test_report_without_ground_truth_has_no_accuracy(ctx, tmp_path) -> None:
    args = _args()
    summary = judge_demo.build_summary(args, ctx, _default_extra(args))
    out = tmp_path / "out"
    out.mkdir()
    path = judge_demo.write_report_html(out, summary, ctx)
    html = path.read_text(encoding="utf-8")

    assert summary["evaluation"]["status"] == "not_calculated"
    assert summary["evaluation"]["accuracy"] == judge_demo.NO_GT_ACCURACY
    assert "accuracy" not in summary["evaluation"].get("metrics", {})
    assert judge_demo.NO_GT_ACCURACY in html          # "NOT CALCULATED — NO GROUND TRUTH"
    assert judge_demo.NO_GT_NOTE in html
    # face counts are never presented as accuracy
    assert "Accuracy: 0." not in html
    for heading in ("1. Demo summary", "2. Attendance", "3. Recognition results",
                    "4. System pipeline", "5. Evaluation"):
        assert heading in html
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_report_with_ground_truth_reports_the_metrics(ctx, tmp_path) -> None:
    args = _args("--ground-truth", str(tmp_path / "gt.csv"))
    ctx["metrics"] = {
        "accuracy": 0.9804, "precision": 1.0, "recall": 0.9714, "f1": 0.9855,
        "far": 0.0, "frr": 0.0286, "unknown_rejection_rate": 1.0,
        "correct": 50, "total": 51, "false_accepts": 0, "false_rejects": 1,
        "wrong_identities": 0,
    }
    summary = judge_demo.build_summary(args, ctx, _default_extra(args))
    out = tmp_path / "out"
    out.mkdir()
    html = judge_demo.write_report_html(out, summary, ctx).read_text(encoding="utf-8")

    assert summary["evaluation"]["status"] == "calculated"
    assert summary["evaluation"]["metrics"]["accuracy"] == pytest.approx(0.9804)
    assert "Accuracy: 0.9804" in html
    assert judge_demo.NO_GT_NOTE not in html


def test_absent_students_come_from_the_gallery_when_no_csv(ctx) -> None:
    args = _args()
    summary = judge_demo.build_summary(args, ctx, _default_extra(args))
    assert summary["students_marked_present"] == 1
    assert summary["students_absent"] == 1          # REG_B in the roster, enrolled
    assert summary["absent"][0]["registration_no"] == REG_B
    assert summary["unknown_faces"] == 3
    assert summary["faces_detected"] == 6
    assert summary["known_faces"] == 3


# ------------------------------------------------------------ same-source
def test_same_source_detects_the_video_itself(tmp_path) -> None:
    video = tmp_path / "TEST000001.mp4"
    video.write_bytes(b"abc")
    same, reason = judge_demo.detect_same_source(video, [video])
    assert same is True
    assert "enrollment source" in reason


def test_same_source_detects_identical_bytes(tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"same-bytes-here")
    other = tmp_path / "enrolled_source.mp4"
    other.write_bytes(b"same-bytes-here")
    same, reason = judge_demo.detect_same_source(video, [other])
    assert same is True
    assert "byte-identical" in reason


def test_same_source_is_false_for_different_files(tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"aaaa")
    other = tmp_path / "source.mp4"
    other.write_bytes(b"bbbb")
    same, _ = judge_demo.detect_same_source(video, [other])
    assert same is False


def test_same_source_label_appears_in_report(ctx, tmp_path) -> None:
    args = _args("--same-source")
    summary = judge_demo.build_summary(
        args, ctx, _default_extra(args, True, "the supplied video is itself an "
                                               "enrollment source"))
    html = judge_demo.write_report_html(tmp_path, summary, ctx).read_text(encoding="utf-8")
    assert summary["same_source"] is True
    assert judge_demo.SAME_SOURCE_LABEL in html
    assert judge_demo.SAME_SOURCE_NOTE in html


# ----------------------------------------------------------- label format
def test_judge_labels_show_confirmed_unknown_and_verifying() -> None:
    from app.confirm import UNKNOWN

    present, green = test_cctv.label_lines(
        {"name": "Test Student", "reg": REG_A, "score": 0.78, "status": "PRESENT"},
        style="judge")
    assert present == ["KNOWN:", "Test Student", REG_A, "0.78", "PRESENT"]
    assert green == (0, 200, 0)

    pending, orange = test_cctv.label_lines(
        {"name": "Test Student", "reg": REG_A, "score": 0.62, "status": "PENDING"},
        style="judge")
    assert pending == ["VERIFYING...", "Test Student", "0.62"]
    assert orange == (0, 165, 255)
    assert "PRESENT" not in pending                  # never claim present early

    weak, red = test_cctv.label_lines(
        {"name": UNKNOWN, "reg": UNKNOWN, "score": 0.31, "status": "NOT MARKED"},
        style="judge")
    assert weak == ["UNKNOWN", "0.31"]
    assert red == (0, 0, 255)


def test_classic_labels_keep_the_masked_registration() -> None:
    from app.confirm import UNKNOWN

    lines, _ = test_cctv.label_lines(
        {"name": "Test Student", "reg": REG_A, "score": 0.78, "status": "PENDING"})
    assert lines == ["Test Student | ...001 | 0.78 | PENDING"]
    unknown_lines, _ = test_cctv.label_lines(
        {"name": UNKNOWN, "reg": UNKNOWN, "score": 0.31, "status": "NOT MARKED"})
    assert unknown_lines == ["UNKNOWN - NOT MARKED"]


# -------------------------------------------------------- unconfirmed = absent
def test_unconfirmed_match_is_never_marked_present(ctx, capsys) -> None:
    args = _args()
    ctx["attendance"] = []            # matched but never reached k-of-n
    summary = judge_demo.build_summary(args, ctx, _default_extra(args))
    capsys.readouterr()
    judge_demo.print_final_report(summary, ctx)
    out = capsys.readouterr().out

    assert summary["students_marked_present"] == 0
    assert "nobody confirmed - no attendance marked" in out
    assert "MATCHED, NOT CONFIRMED" in out
    assert f"{REG_A}  similarity 0.78  confirmed: NO  attendance: NOT MARKED" in out
    assert "UNKNOWN: 3 observation(s)" in out


# ------------------------------------------------------- full (stubbed) run
def test_demo_run_writes_every_output(tmp_path, monkeypatch, capsys, isolated) -> None:
    calls = _stub_recognition(monkeypatch)
    video = tmp_path / "classroom_demo.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    out = tmp_path / "judge_demo"

    monkeypatch.setattr(judge_demo, "check_environment",
                        lambda: [("dependencies", "OK", "stub"),
                                 ("model", "OK", "stub"),
                                 ("database", "OK", "stub"),
                                 ("encryption key", "OK", "stub")])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 2, "embeddings": 10})
    monkeypatch.setattr(
        judge_demo, "enrolled_students",
        lambda: [{"student_id": 1, "name": "Test Student", "registration_no": REG_A},
                 {"student_id": 2, "name": "Second Student",
                  "registration_no": REG_B}],
    )
    monkeypatch.setattr(judge_demo, "missing_enrollment", lambda c, r: ([], 2, 0))

    rc = judge_demo.main([
        "--video", str(video),
        "--out-dir", str(out),
        "--sampling-fps", "5",
        "--same-source",
    ])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    # 1. every required file exists
    summary_path = out / "summary.json"
    assert summary_path.is_file()
    assert (out / "report.html").is_file()
    assert (out / "attendance.csv").is_file()
    assert (out / "observations.csv").is_file()
    assert (out / "annotated_video.mp4").is_file()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["students_marked_present"] == 1
    assert summary["gallery"]["students"] == 2
    assert summary["gallery"]["embeddings"] == 10
    assert summary["same_source"] is True
    assert summary["evaluation"]["status"] == "not_calculated"
    assert isinstance(summary["evaluation"]["accuracy"], str)

    html = (out / "report.html").read_text(encoding="utf-8")
    assert judge_demo.NO_GT_ACCURACY in html
    assert judge_demo.SAME_SOURCE_LABEL in html

    # 2. the recognition step got the demo's flags (full reg + bar + video)
    args = calls["args"]
    assert args.judge_labels is True
    assert args.progress is True
    assert args.sampling_fps == 5.0
    assert Path(args.out_dir) == out
    assert Path(args.annotated_video) == out / "annotated_video.mp4"
    assert args.ground_truth is None

    # 3. terminal: banner, counts, acceptance list
    assert "AI ATTENDANCE - JUDGE DEMO" in captured.out
    assert "Students Present: 1" in captured.out
    assert "Students Absent : 1" in captured.out
    assert judge_demo.NO_GT_ACCURACY in captured.out
    assert "1. Exact command used:" in captured.out
    assert "10. Remaining limitations:" in captured.out
    # never print a made-up accuracy
    assert "accuracy: 0." not in captured.out.lower()


def test_demo_uses_ground_truth_when_supplied(tmp_path, monkeypatch, capsys,
                                              isolated) -> None:
    calls = _stub_recognition(
        monkeypatch,
        ctx_factory=lambda out: {**_default_ctx(out), "metrics": {
            "accuracy": 0.9804, "precision": 1.0, "recall": 0.9714, "f1": 0.9855,
            "far": 0.0, "frr": 0.0286, "unknown_rejection_rate": 1.0,
            "correct": 50, "total": 51}},
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    gt = tmp_path / "gt.csv"
    gt.write_text("frame,reg\n0,900000001\n", encoding="utf-8")
    out = tmp_path / "judge_demo"

    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 1, "embeddings": 5})
    monkeypatch.setattr(judge_demo, "enrolled_students",
                        lambda: [{"student_id": 1, "name": "Test Student",
                                  "registration_no": REG_A}])
    monkeypatch.setattr(judge_demo, "missing_enrollment", lambda c, r: ([], 1, 0))

    rc = judge_demo.main(["--video", str(video), "--out-dir", str(out),
                          "--ground-truth", str(gt)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["evaluation"]["status"] == "calculated"
    assert summary["evaluation"]["metrics"]["accuracy"] == pytest.approx(0.9804)
    assert calls["args"].ground_truth == gt
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "Accuracy: 0.9804" in html
    assert "EVALUATION (identity-level, ground truth supplied)" in captured.out


# ------------------------------------------------------- enrollment step
def test_demo_enrolls_through_the_existing_pipeline(tmp_path, monkeypatch,
                                                    capsys, isolated) -> None:
    csv_path = tmp_path / "form.csv"
    csv_path.write_text("name,registration,video\nT,900000001,http://x\n",
                        encoding="utf-8")
    _stub_recognition(monkeypatch)

    stats = iter([{"students": 0, "embeddings": 0},
                  {"students": 1, "embeddings": 5}])
    enrolled = iter([[], [{"student_id": 1, "name": "Test Student",
                           "registration_no": REG_A}]])
    missing = iter([
        ([{"name": "Test Student", "registration_no": REG_A}], 1, 0),
        ([], 1, 0),
    ])
    calls: dict = {}

    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats", lambda: next(stats))
    monkeypatch.setattr(judge_demo, "enrolled_students", lambda: next(enrolled))
    monkeypatch.setattr(judge_demo, "missing_enrollment",
                        lambda c, r: next(missing))

    def _enroll(csv, videos_dir, use_rpc):
        calls.update({"csv": csv, "videos_dir": videos_dir, "rpc": use_rpc})
        return 0

    monkeypatch.setattr(judge_demo, "run_enrollment", _enroll)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    out = tmp_path / "judge_demo"
    rc = judge_demo.main(["--video", str(video), "--csv", str(csv_path),
                          "--out-dir", str(out)])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    assert calls["csv"] == csv_path          # the existing pipeline was used
    assert "gallery is empty - running the existing enrollment pipeline" in captured.out
    assert "STEP 3  gallery validation" in captured.out
    assert "Students enrolled       : 1" in captured.out
    assert "Reference embeddings    : 5" in captured.out


def test_enrollment_failure_stops_with_a_clear_message(tmp_path, monkeypatch,
                                                      capsys, isolated) -> None:
    csv_path = tmp_path / "form.csv"
    csv_path.write_text("name,registration,video\nT,900000001,http://x\n",
                        encoding="utf-8")

    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 0, "embeddings": 0})
    monkeypatch.setattr(judge_demo, "enrolled_students", lambda: [])
    monkeypatch.setattr(judge_demo, "missing_enrollment",
                        lambda c, r: ([{"name": "T", "registration_no": REG_A}], 1, 0))
    monkeypatch.setattr(judge_demo, "run_enrollment", lambda *a, **k: 1)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    rc = judge_demo.main(["--video", str(video), "--csv", str(csv_path),
                          "--out-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "enrollment pipeline failed (exit 1)" in captured.err


def test_empty_gallery_without_csv_is_reported(tmp_path, monkeypatch, capsys,
                                               isolated) -> None:
    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 0, "embeddings": 0})
    monkeypatch.setattr(judge_demo, "enrolled_students", lambda: [])

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    rc = judge_demo.main(["--video", str(video),
                          "--out-dir", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no enrolled students and no enrollment CSV found" in captured.err
    assert "pass --csv" in captured.err


def test_missing_enrollment_students_are_reported(tmp_path, monkeypatch, capsys,
                                                  isolated) -> None:
    """A student whose Drive video is inaccessible is reported, never invented."""
    calls = _stub_recognition(monkeypatch)
    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 1, "embeddings": 5})
    monkeypatch.setattr(judge_demo, "enrolled_students",
                        lambda: [{"student_id": 1, "name": "Test Student",
                                  "registration_no": REG_A}])
    monkeypatch.setattr(
        judge_demo, "missing_enrollment",
        lambda c, r: ([{"name": "Unreachable Student",
                        "registration_no": "900000009"}], 2, 0),
    )
    # the existing enrollment pipeline is re-run for the missing student, but it
    # cannot reach the Drive video - it reports it and continues (exit 0)
    monkeypatch.setattr(judge_demo, "run_enrollment", lambda *a, **k: 0)

    csv_path = tmp_path / "form.csv"
    csv_path.write_text("name,registration,video\nT,900000001,http://x\n",
                        encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    out = tmp_path / "judge_demo"
    rc = judge_demo.main(["--video", str(video), "--csv", str(csv_path),
                          "--out-dir", str(out)])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["gallery"]["missing_count"] == 1
    assert summary["gallery"]["missing_enrollment"][0]["registration_no"] == "900000009"
    assert all(a["registration_no"] != "900000009" for a in summary["attendance"])
    assert any("900000009" in lim for lim in summary["limitations"])
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "Missing enrollment: 900000009" in html
    assert "Students missing enrollment" in html
    assert "900000009" not in [a["registration_no"] for a in summary["attendance"]]


def test_duplicate_students_appear_once_in_the_attendance_table(tmp_path,
                                                                monkeypatch,
                                                                capsys, isolated) -> None:
    """One student = one attendance record in one demo session."""
    _stub_recognition(monkeypatch)
    monkeypatch.setattr(judge_demo, "check_environment", lambda: [])
    monkeypatch.setattr(judge_demo, "gallery_stats",
                        lambda: {"students": 1, "embeddings": 5})
    monkeypatch.setattr(judge_demo, "enrolled_students",
                        lambda: [{"student_id": 1, "name": "Test Student",
                                  "registration_no": REG_A}])
    monkeypatch.setattr(judge_demo, "missing_enrollment", lambda c, r: ([], 1, 0))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    out = tmp_path / "judge_demo"
    rc = judge_demo.main(["--video", str(video), "--out-dir", str(out)])
    capsys.readouterr()

    assert rc == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    regs = [a["registration_no"] for a in summary["attendance"]]
    assert regs == [REG_A]
    assert len(regs) == len(set(regs))
    assert summary["students_marked_present"] == len(regs)

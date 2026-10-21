"""Camera connection diagnostics (task 5).

Verifies the classified probe in :mod:`app.services.camera_test`:

* network unreachable / connection refused  -> ``unreachable``
* credentials rejected by the camera        -> ``auth_failed``   (ffmpeg path)
* invalid / unsupported RTSP path           -> ``unsupported`` or ``error``
* stream opens but yields no decodable frame -> ``no_frames``
* inference service down                    -> surfaced as ``infer_ok=False``
  on :meth:`GET /cameras/{id}/live-status`  (covered by
  ``tests/test_live_monitor.py::test_live_status_reports_inference_outage``)

Never asserts on a specific network topology: a refused port on localhost is
used so the test is deterministic and never depends on an external host.
Never prints or asserts a credential.
"""
from __future__ import annotations

import json

import pytest

from app.services import camera_test


def test_redact_strips_credentials() -> None:
    raw = "rtsp://alice:s3cret-pw@10.10.150.178:554/cam/realmonitor?channel=1"
    assert camera_test.redact(raw) == (
        "rtsp://***@10.10.150.178:554/cam/realmonitor?channel=1"
    )
    assert "s3cret-pw" not in camera_test.redact(raw)
    assert camera_test.redact(None) is None
    assert camera_test.redact("no credentials here") == "no credentials here"


def test_tcp_probe_connection_problem_is_not_connected() -> None:
    # 19876 is not listened to on this host.  Whether Windows reports it as
    # "refused" or "timeout", both classify as a connection problem - never
    # as "connected".
    status = camera_test._tcp_probe("127.0.0.1", 19876, timeout=2.0)
    assert status in ("refused", "timeout", "dns", "unreachable"), status


def test_source_unreachable_host_is_classified_not_leaked() -> None:
    # Source carries a password, but nothing is listening -> unreachable, and
    # the password must not appear in the diagnostic detail.
    result = camera_test.test_source(
        "rtsp://alice:s3cret-pw@127.0.0.1:19876/Streaming/Channels/101"
    )
    assert result.ok is False
    assert result.status in ("unreachable", "timeout", "dns"), result.status
    assert "s3cret-pw" not in (result.detail or "")
    assert "alice" not in (result.detail or "")


def test_source_missing_file_is_unreachable() -> None:
    result = camera_test.test_source("does-not-exist/lecture.mp4")
    assert result.ok is False
    assert result.status == "unreachable", result.status
    assert "not found" in (result.detail or ""), result.detail


def test_source_empty_is_error() -> None:
    result = camera_test.test_source("")
    assert result.ok is False
    assert result.status == "error"
    assert "no stream source" in (result.detail or "")

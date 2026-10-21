"""Application configuration.

All settings come from environment variables (or a local `.env` file).
Field names are case-insensitive in env vars, e.g. ``RECOGNITION_THRESHOLD``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "AI Video Attendance API"
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"  # json | text
    cors_origins: str = "*"
    data_dir: str = "./data"  # student reference photos live here
    # Judge demo: enrollment CSV used when scripts/judge_demo.py gets no --csv.
    # Empty -> auto-detect data/enrollment/*.csv (set DEFAULT_FORM_CSV to pin it).
    default_form_csv: str = ""
    version: str = "1.0.0"

    # -------------------------------------------------------------- database
    database_url: str = (
        "postgresql+psycopg://attendance:attendance@localhost:5432/attendance"
    )

    # ---------------------------------------------------------------- redis
    redis_url: str = "redis://localhost:6379/0"

    # ----------------------------------------------------------------- auth
    auth_required: bool = True
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    admin_username: str = "admin"
    admin_password: str = "admin"

    # ------------------------------------------------------ security knobs
    # Readiness phase (SEC-05 / SEC-10).  Defaults are deliberately
    # generous for the admin console; tune per deployment.
    max_upload_bytes: int = 25 * 1024 * 1024  # request bodies above this -> 413
    auth_rate_limit_per_minute: int = 10  # login attempts per (ip, user); 0 = off

    # ----------------------------------------------------------- face model
    face_model_name: str = "buffalo_l"
    insightface_root: str = "~/.insightface"
    cuda_device_index: int = 0
    force_cpu: bool = False  # skip CUDA entirely (for debugging)
    det_sizes: str = "640x640"  # comma separated WxH, e.g. "640x640" or "128x128,640x640"
    det_thresh: float = 0.5
    max_faces_per_frame: int = 20
    max_embedding_batch: int = 64  # faces per GPU call when batching embeddings
    enable_batched_detection: bool = True  # cross-frame batched SCRFD (falls back automatically)

    # ---------------------------------------------------------- recognition
    # Single threshold used EVERYWHERE (API, pipeline, demo, tests).  Validated
    # 2026-10-04 from printed cosine scores (eval/enroll_and_calibrate.py +
    # eval/diag_threshold.py, buffalo_l CPU, 6 enrolled students):
    #   different-person max = 0.1246 | same-person photo min = 0.9111
    #   genuine video-face min vs gallery = 0.5030 (median 0.9553)
    #   -> 0.40 sits inside (0.1246, 0.5030): 0.275 above the worst impostor,
    #      0.103 below the weakest genuine video match.
    recognition_threshold: float = 0.40  # cosine similarity threshold
    # Best-vs-second-best margin (duplicate identities excluded, see matching).
    # Runner-up over DIFFERENT identities never exceeded 0.1246 while genuine
    # bests are >= 0.5030 (gap 0.378); 0.10 is conservative inside that gap.
    recognition_margin: float = 0.10
    # Two students whose galleries are >= 0.90 similar are the same human
    # (measured: duplicated photo folders score 0.9998) -> excluded from the
    # runner-up so the margin rule does not reject genuine matches.
    duplicate_identity_sim: float = 0.90
    dedup_window_seconds: int = 120  # same student + camera within window => one event
    dedup_log_all: bool = False  # still write RecognitionLog rows on dedup hits
    log_unknown_faces: bool = True  # log unknown faces for alerting (audit)
    # Camera-level cooldown for unknown-face events: at most one unknown
    # event per camera per window, regardless of how many distinct unknown
    # faces appear.  Prevents the monitoring panel from being flooded when
    # a corridor sees steady foot traffic.  The per-face quantised hash
    # dedup above still applies on top for multi-person frames.
    unknown_cooldown_s: int = 60

    # ------------------------------------------------------------- alerting
    # Match accepted but with cosine below this -> `low_confidence` alert.
    # Sits just above the measured genuine video minimum (0.5030) so it flags
    # borderline acceptances for human review (see recognition_threshold).
    alert_low_confidence: float = 0.55
    # Same (student, camera) low-confidence alerts suppressed within window.
    alert_dedup_window_s: int = 300
    # Period-deviation grace windows (minutes) used by
    # services.alerts.evaluate_period_deviations.
    absent_grace_minutes: int = 15
    late_grace_minutes: int = 5
    early_exit_minutes: int = 10

    # -------------------------------------------------- camera management
    # Connection test (POST /cameras/test-connection): TCP connect + one
    # decoded frame, classified into connected/auth_failed/timeout/...
    camera_connect_timeout_s: float = 8.0
    camera_snapshot_interval_s: float = 1.5  # pacing for /cameras/{id}/snapshot cache
    # MJPEG preview: max FPS advertised to browsers (server paces reads anyway).
    camera_stream_fps: int = 6
    # Shared live monitor (pipeline-published annotated frames):
    # how long a published frame stays valid, monitor pacing, and the
    # downscaled width used for the browser stream (bandwidth vs detail).
    live_frame_ttl_s: int = 15
    # MJPEG delivery: max parts per second handed to a browser.  Delivery is
    # change-driven (see iter_live_parts), so this is a CEILING, not a rate
    # that duplicate frames get padded out to.
    live_mjpeg_fps: int = 20
    live_frame_width: int = 640
    live_frame_quality: int = 70
    # Display decoupling: the capture thread taps decoded frames for the
    # browser at LIVE_PUBLISH_FPS, independently of the recognition cadence
    # (per-camera sampling_rate) and of inference latency.  This is what
    # makes the video smooth without inflating GPU load.  0 disables the tap
    # and restores the legacy inference-gated display (one frame per batch).
    live_publish_fps: int = 15
    # MJPEG delivery: poll the frame bus this often and emit a part only
    # when the published frame actually changed; LIVE_MJPEG_KEEPALIVE_S
    # bounds how long a client sees no part when the source is idle/stale,
    # and LIVE_MJPEG_FPS stays the maximum emit rate.
    live_mjpeg_poll_ms: int = 30
    live_mjpeg_keepalive_s: float = 1.0

    # ----------------------------------------------------- presence engine
    # Persistent per-student OUTSIDE/ENTRY_PENDING/INSIDE/EXIT_PENDING/
    # NEEDS_REVIEW state machine (services/presence.py).  The camera-level
    # K-of-N gate (CONFIRMATION_FRAMES/WINDOW_S) already confirms each
    # sighting; these knobs add the state-transition layer on top.
    presence_tracking_enabled: bool = True
    # Sighted observations required to cross ENTRY_PENDING->INSIDE (and
    # EXIT_PENDING->OUTSIDE).  1 = the K-of-N confirmed sighting itself
    # completes the transition (legacy-compatible); raise to 2+ for
    # doorway placements that need an extra beat of evidence.
    presence_transition_observations: int = 1
    # Same-student presence-review events suppressed within this window.
    presence_review_dedup_s: int = 300

    # ----------------------------------------------- attendance policy
    # Per-period threshold for automatic PRESENT: confirmed presence must
    # reach max(configured minutes, configured ratio x scheduled length).
    # Default policy example (decision register): 30 of 60 minutes.
    attendance_default_required_minutes: int = 30
    attendance_default_required_ratio: float = 0.0  # 0 = minutes-only mode
    # Manual override reasons are validated server-side at this minimum
    # length after whitespace stripping (blank reasons are rejected).
    override_reason_min_length: int = 5

    # -------------------------------------------------------------- runtime
    # TTL for camera runtime state keys (starting/running/stopping) and task
    # ids; they are deleted on clean shutdown, so this is only a safety net.
    camera_state_ttl_s: int = 86400
    # Bounded startup: a dispatched task must have decoded its first frame
    # within this window, otherwise the pipeline reports FAILED with the real
    # capture error instead of sitting in STARTING (or claiming RUNNING with
    # no frames at all).  Must exceed the RTSP open timeout plus a margin.
    camera_startup_timeout_s: float = 30.0
    # How long a FAILED state stays readable by the UI before it self-clears,
    # and how long the human-readable diagnostic stays available.  A failed
    # camera is immediately startable again (FAILED is not a blocking state).
    camera_failed_state_ttl_s: int = 600
    camera_error_ttl_s: int = 3600
    min_reference_photos: int = 3
    max_reference_photos: int = 5
    # quality gate for occlusion/blur/low-light (Req 5)
    face_quality_enabled: bool = True
    face_min_sharpness: float = 30.0  # Laplacian variance
    face_min_brightness: float = 30.0
    face_max_brightness: float = 230.0
    face_min_area: int = 800  # bbox area in pixels
    face_min_width: int = 80  # minimum face width in pixels
    face_max_yaw: float = 30.0  # max absolute yaw (degrees)
    face_max_pitch: float = 30.0  # max absolute pitch (degrees)
    face_max_roll: float = 30.0  # max absolute roll (degrees)
    face_min_det_score: float = 0.6  # minimum detection confidence

    # ---------------------------------------------------------- encryption
    # base64 url-safe 32-byte key (Fernet) used to encrypt embeddings at rest.
    # Generate: `python scripts/generate_key.py`
    # REQUIRED in production - startup fails hard without it (no JWT fallback).
    embedding_encryption_key: str | None = None

    # -------------------------------------------------------- registration
    registration_no_pattern: str = r"^[A-Za-z0-9_-]{6,12}$"  # alphanumeric + underscore + hyphen, 6-12 chars
    # K-of-N confirmation before state change (camera pipeline gate for
    # every attendance event; enrollment captures use the same values as
    # separate photo frames).  Defaults follow the acceptance criterion in
    # CONTRIBUTING.md ("3 frames / 10 s"), approved as authoritative.
    confirmation_frames: int = 3
    confirmation_window_s: float = 10.0

    # ------------------------------------------------- shared inference RPC
    inference_mode: str = "redis"  # redis (shared GPU service) | inprocess (worker-local)
    inference_queue_key: str = "infer:requests"
    inference_response_ttl_s: int = 30
    inference_request_timeout_s: float = 15.0
    max_batch_frames: int = 32  # max frames per engine call (across all cameras)
    inference_batch_wait_ms: int = 25  # max time to wait while filling a GPU batch

    # -------------------------------------------------------------- pipeline
    default_sampling_rate: int = 5  # process every Nth decoded frame
    frame_queue_size: int = 8  # bounded decode queue (drop-oldest)
    batch_size: int = 8  # frames per RPC sent by one camera worker
    batch_flush_ms: int = 120  # max time to fill a camera-side batch
    jpeg_quality: int = 85
    rtsp_transport: str = "tcp"
    rtsp_open_timeout_ms: int = 10_000
    rtsp_read_timeout_ms: int = 10_000
    reconnect_initial_delay_s: float = 2.0
    reconnect_max_delay_s: float = 30.0
    camera_slots: int = 8  # dedicated celery worker slots for camera streams
    heartbeat_ttl_s: int = 30  # pipeline heartbeat TTL (unhealthy if expired)

    # ---------------------------------------------------------------- time
    local_timezone: str = "UTC"

    # ---------------------------------------------------------------- seed
    seed_video_path: str = "./samples/videos/lecture.mp4"

    # ---------------------------------------------------------- redis keys
    events_channel: str = "events:live"
    events_recent_key: str = "events:recent"
    events_recent_limit: int = 200
    gpu_status_key: str = "gpu:status"
    students_version_key: str = "students:version"
    running_cameras_key: str = "cameras:running"

    # ------------------------------------------------------------ validators
    @field_validator("det_sizes")
    @classmethod
    def _validate_det_sizes(cls, v: str) -> str:
        for part in v.split(","):
            part = part.strip().lower()
            if not part:
                continue
            w, _, h = part.partition("x")
            if not (w.isdigit() and h.isdigit()):
                raise ValueError(f"det_sizes must look like '640x640', got {v!r}")
        return v

    @field_validator("embedding_encryption_key")
    @classmethod
    def _validate_encryption_key(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:  # `EMBEDDING_ENCRYPTION_KEY=` in .env means "unset"
            return None
        import base64
        try:
            decoded = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
            if len(decoded) != 32:
                raise ValueError("EMBEDDING_ENCRYPTION_KEY must decode to exactly 32 bytes")
        except Exception as e:
            raise ValueError(f"EMBEDDING_ENCRYPTION_KEY must be valid base64: {e}")
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret_production(cls, v: str) -> str:
        if v == "change-me-in-production" and os.getenv("ENVIRONMENT") == "production":
            raise ValueError("JWT_SECRET must be changed from default in production")
        return v

    @model_validator(mode="after")
    def _require_encryption_key_in_production(self) -> "Settings":
        """Hard-fail at startup when production has no encryption key.

        There is NO fallback (in particular NOT the JWT secret): silently
        deriving the at-rest key from a shared secret previously allowed a
        JWT leak to decrypt every stored face embedding.
        """
        if (
            not self.embedding_encryption_key
            and self.environment.strip().lower() in {"production", "prod"}
        ):
            raise ValueError(
                "EMBEDDING_ENCRYPTION_KEY is required when ENVIRONMENT=production. "
                "Generate one with `python scripts/generate_key.py` and put it in "
                ".env (base64 url-safe, decodes to exactly 32 bytes). "
                "Refusing to start: there is no fallback key."
            )
        return self

    @model_validator(mode="after")
    def _production_hardening_checks(self) -> "Settings":
        """Refuse shipped defaults in production (SEC-04 / SEC-11).

        Readiness phase: the same fail-fast philosophy as the encryption-key
        check, applied to the two remaining shipped secrets/open settings.
        """
        if self.environment.strip().lower() in {"production", "prod"}:
            if self.cors_origins.strip() == "*":
                raise ValueError(
                    "CORS_ORIGINS='*' is not allowed when ENVIRONMENT=production; "
                    "list the explicit origins of the admin console "
                    "(comma-separated)."
                )
            if self.admin_password in {"", "admin"}:
                raise ValueError(
                    "ADMIN_PASSWORD must be changed from the shipped default "
                    "when ENVIRONMENT=production."
                )
        return self

    # ------------------------------------------------------------- helpers
    @property
    def cors_origins_list(self) -> list[str]:
        value = self.cors_origins.strip()
        if value == "*":
            return ["*"]
        return [o.strip() for o in value.split(",") if o.strip()]

    @property
    def photos_root(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def det_size_list(self) -> list[tuple[int, int]]:
        sizes: list[tuple[int, int]] = []
        for part in self.det_sizes.split(","):
            part = part.strip().lower()
            if not part:
                continue
            w, _, h = part.partition("x")
            sizes.append((int(w), int(h)))
        return sizes or [(640, 640)]

    @property
    def slot_queue_prefix(self) -> str:
        return "camera_slot_"

    def slot_queue(self, slot: int) -> str:
        return f"{self.slot_queue_prefix}{slot}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

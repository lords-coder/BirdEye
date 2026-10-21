"""Pydantic request/response schemas (OpenAPI models shown in Swagger)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CameraType = Literal["entry", "exit", "both"]
SessionStatus = Literal["ongoing", "completed"]


# --------------------------------------------------------------------- auth
class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    role: str | None = None  # super_admin | admin


class RegisterRequest(BaseModel):
    """Self-service sign-up: always creates a *pending* admin account."""

    username: str = Field(
        min_length=3,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.-]{3,100}$",
        examples=["priya.n"],
    )
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    # Only `admin` is self-registerable; super_admin is never self-assigned.
    requested_role: Literal["admin"] = "admin"


class RegisteredOut(BaseModel):
    id: int
    username: str
    status: str
    requested_role: str
    detail: str


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class ForgotPasswordOut(BaseModel):
    accepted: bool = True
    detail: str


class MeOut(BaseModel):
    id: int | None = None
    username: str
    role: str
    status: str = "active"
    display_name: str | None = None
    security_level: int = 0


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    role: str
    status: str
    security_level: int
    approved_at: datetime | None = None
    approved_by: str | None = None
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    """Super-admin-only credential / role change.  All fields optional."""

    username: str | None = Field(
        default=None, min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]{3,100}$"
    )
    password: str | None = Field(default=None, min_length=8, max_length=200)
    role: Literal["super_admin", "admin", "teacher"] | None = None
    security_level: int | None = Field(default=None, ge=1, le=5)
    status: Literal["pending", "active", "rejected", "suspended"] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "UserUpdate":
        if all(
            getattr(self, f) is None
            for f in ("username", "password", "role", "security_level", "status")
        ):
            raise ValueError("at least one field must be provided")
        return self


class UserApprove(BaseModel):
    security_level: int = Field(default=3, ge=1, le=5)


class PasswordRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    reason: str | None = None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class PasswordResolveRequest(BaseModel):
    """Super admin action on a forgotten-password request."""

    action: Literal["reset", "reject"]
    new_password: str | None = Field(default=None, min_length=8, max_length=200)

    @model_validator(mode="after")
    def _password_for_reset(self) -> "PasswordResolveRequest":
        if self.action == "reset" and not self.new_password:
            raise ValueError("new_password is required when action is 'reset'")
        return self


class UserListResponse(BaseModel):
    items: list[UserOut] = Field(default_factory=list)
    total: int = 0


class PasswordRequestListResponse(BaseModel):
    items: list[PasswordRequestOut] = Field(default_factory=list)
    total: int = 0


# ----------------------------------------------------------------- students
class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    registration_no: str
    section: str | None = None
    photo_paths: list[str] = Field(default_factory=list)
    embedding_count: int = 0
    # Only populated when ?include_embeddings=true
    embeddings: list[list[float]] | None = None
    created_at: datetime
    updated_at: datetime


class StudentSummary(BaseModel):
    """Compact student projection used inside attendance/event payloads."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    registration_no: str
    section: str | None = None


class StudentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    registration_no: str | None = Field(default=None, min_length=1, max_length=64)
    section: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _at_least_one(self) -> "StudentUpdate":
        if all(
            getattr(self, f) is None
            for f in ("name", "registration_no", "section")
        ):
            raise ValueError("at least one field must be provided")
        return self


# ------------------------------------------------------------------ cameras
def _check_rtsp_scheme(value: str | None) -> str | None:
    """Only ``rtsp://`` / ``rtsps://`` sources may be stored.

    The value is echoed back to the web console and fed to the frame reader,
    so accepting arbitrary schemes (``javascript:``, ``file:``, ``data:``,
    ...) would be a stored-XSS / local-file vector.
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    low = v.lower()
    if not (low.startswith("rtsp://") or low.startswith("rtsps://")):
        raise ValueError("rtsp_url must start with rtsp:// or rtsps://")
    return v


def redact_rtsp_url(value: str | None) -> str | None:
    """Strip any userinfo (``user:pass@``) from an RTSP URL for display.

    Used on every camera response so a legacy row that stored inline
    credentials can never echo them back to a browser or an export.
    """
    if not value:
        return value
    for scheme in ("rtsp://", "rtsps://"):
        if value.lower().startswith(scheme):
            rest = value[len(scheme):]
            if "@" in rest.split("/", 1)[0]:
                rest = rest.split("@", 1)[1]
                return scheme + "…" + rest
            return value
    return value


CameraRole = Literal["classroom_interior", "doorway_exterior"]


class CameraBase(BaseModel):
    """Camera fields shared by input and output schemas (no input rules)."""

    name: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    rtsp_url: str | None = Field(default=None, max_length=500)
    file_path: str | None = Field(default=None, max_length=500)
    type: CameraType = "entry"
    sampling_rate: int = Field(
        default=5,
        ge=1,
        le=600,
        description="Process every Nth decoded frame (5 ≈ 6 FPS from a 30 FPS stream)",
    )
    # ------------------------------------------------ structured connection
    host: str | None = Field(default=None, max_length=255, description="IP or hostname")
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=128)
    stream_path: str | None = Field(
        default=None, max_length=255, description="RTSP path, e.g. /Streaming/Channels/101"
    )
    camera_role: CameraRole | None = Field(
        default=None,
        description="Physical purpose: classroom interior vs doorway/exterior",
    )
    room: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    enabled: bool = True

    @field_validator("rtsp_url")
    @classmethod
    def _rtsp_scheme(cls, v: str | None) -> str | None:
        return _check_rtsp_scheme(v)


class CameraCreate(CameraBase):
    # Write-only secret: accepted here, encrypted at rest, NEVER serialized
    # back (CameraOut exposes `has_password` instead).
    password: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _needs_source(self) -> "CameraCreate":
        if not self.rtsp_url and not self.file_path and not self.host:
            raise ValueError("provide host, rtsp_url or file_path")
        if self.password and not self.username:
            raise ValueError("username is required when a password is set")
        if self.port is not None and not self.host:
            raise ValueError("host is required when port is set")
        return self


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    rtsp_url: str | None = Field(default=None, max_length=500)
    file_path: str | None = Field(default=None, max_length=500)
    type: CameraType | None = None
    sampling_rate: int | None = Field(default=None, ge=1, le=600)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=128)
    # Omit the field entirely to keep the stored password; send null to
    # clear it (credential rotation without re-exposing the old value).
    password: str | None = Field(default=None, max_length=200)
    clear_password: bool = False
    stream_path: str | None = Field(default=None, max_length=255)
    camera_role: CameraRole | None = None
    room: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None

    @field_validator("rtsp_url")
    @classmethod
    def _rtsp_scheme(cls, v: str | None) -> str | None:
        return _check_rtsp_scheme(v)


class CameraRuntime(BaseModel):
    state: Literal["stopped", "starting", "running", "stopping", "unhealthy"] = "stopped"
    running: bool = False
    slot: int | None = None
    task_id: str | None = None
    last_heartbeat_at: datetime | None = None


class CameraOut(CameraBase):
    # NOTE: derives from CameraBase, NOT CameraCreate - the create-time
    # "must have a source" validator must not re-run when *reading* rows
    # (a legacy row without a source would otherwise fail response
    # validation and turn a GET into a 500).
    model_config = ConfigDict(from_attributes=True)

    id: int
    # True when a stream password is stored; the value itself is never
    # returned by any endpoint.
    has_password: bool = False
    # Server-composed connection display URL, credentials redacted.
    display_url: str | None = None
    last_frame_at: datetime | None = None
    last_test_status: str | None = None
    last_tested_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    runtime: CameraRuntime = Field(default_factory=CameraRuntime)


class CameraTestRequest(BaseModel):
    """Test a connection: by id (stored config) or ad-hoc fields (pre-save)."""

    camera_id: int | None = None
    # Ad-hoc probe (never persisted): only host/port/user/password/path.
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=200)
    stream_path: str | None = Field(default=None, max_length=255)
    rtsp_url: str | None = Field(default=None, max_length=500)
    file_path: str | None = Field(default=None, max_length=500)

    @field_validator("rtsp_url")
    @classmethod
    def _rtsp_scheme(cls, v: str | None) -> str | None:
        return _check_rtsp_scheme(v)

    @model_validator(mode="after")
    def _has_target(self) -> "CameraTestRequest":
        if self.camera_id is None and not (
            self.host or self.rtsp_url or self.file_path
        ):
            raise ValueError("camera_id or a connection target is required")
        return self


class CameraTestResult(BaseModel):
    """Classified connection-test outcome.  ``detail`` is always
    credential-redacted."""

    ok: bool
    status: Literal[
        "connected", "auth_failed", "unreachable", "timeout",
        "no_frames", "unsupported", "error", "disabled",
    ]
    detail: str
    latency_ms: float | None = None
    frame_size: str | None = None
    tested_at: datetime


class StartStopResponse(BaseModel):
    camera_id: int
    state: str
    slot: int | None = None
    task_id: str | None = None
    detail: str | None = None
    # Bounded startup: seconds the worker has to reach RUNNING, and the
    # recorded failure reason when a start attempt failed.
    startup_deadline_s: float | None = None
    error: str | None = None


# --------------------------------------------------------------- attendance
class AttendanceSessionOut(BaseModel):
    id: int
    student_id: int
    camera_in_id: int | None = None
    camera_out_id: int | None = None
    camera_in_name: str | None = None
    camera_out_name: str | None = None
    entry_time: datetime
    exit_time: datetime | None = None
    date: date
    status: SessionStatus
    total_duration: int | None = None  # seconds; None while ongoing
    period_id: int | None = None
    period_name: str | None = None
    source: Literal["auto", "manual"] = "auto"
    review_status: Literal["confirmed", "unresolved", "needs_review"] = "confirmed"


class SessionTotals(BaseModel):
    entries: int = 0
    exits: int = 0
    total_seconds: int = 0
    first_entry: datetime | None = None
    last_exit: datetime | None = None


class StudentAttendanceOut(BaseModel):
    student: StudentSummary
    date: date
    sessions: list[AttendanceSessionOut] = Field(default_factory=list)
    totals: SessionTotals = Field(default_factory=SessionTotals)


class SummaryRow(BaseModel):
    student_id: int
    name: str
    registration_no: str
    section: str | None = None
    entries: int = 0
    exits: int = 0
    first_entry: datetime | None = None
    last_exit: datetime | None = None
    total_seconds: int = 0
    ongoing: bool = False


class SummaryTotals(BaseModel):
    present_students: int = 0
    ongoing_sessions: int = 0
    completed_sessions: int = 0
    total_seconds: int = 0


class SummaryOut(BaseModel):
    date: date
    timezone: str
    students: list[SummaryRow] = Field(default_factory=list)
    totals: SummaryTotals = Field(default_factory=SummaryTotals)


class LiveSessionOut(BaseModel):
    session_id: int
    student: StudentSummary
    entry_time: datetime
    date: date
    elapsed_seconds: int
    camera_in_id: int | None = None
    camera_in_name: str | None = None


class LiveOut(BaseModel):
    server_time: datetime
    count: int
    sessions: list[LiveSessionOut] = Field(default_factory=list)


class RecognitionLogOut(BaseModel):
    id: int
    student_id: int | None = None
    student_name: str | None = None
    camera_id: int | None = None
    camera_name: str | None = None
    timestamp: datetime
    confidence_score: float | None = None
    gpu_inference_time_ms: float | None = None


# ------------------------------------------------------------------ periods
class PeriodBaseFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_time: time
    end_time: time
    days_bitmask: int = Field(default=127, ge=0, le=127, description="Bitmask Mon=1<<0 ... Sun=1<<6; 127=all days")
    section: str | None = Field(default=None, max_length=64, description="NULL = all sections")
    course_id: int | None = Field(
        default=None, description="Course that owns this lecture (teacher-portal scoping)"
    )
    required_minutes: int | None = Field(
        default=None, ge=1,
        description="Minutes of confirmed presence needed; NULL = global default",
    )
    required_ratio: float | None = Field(
        default=None, gt=0, le=1,
        description="Fraction of scheduled length needed; NULL = unused",
    )

    @model_validator(mode="after")
    def _check_range(self) -> "PeriodBaseFields":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class PeriodCreate(PeriodBaseFields):
    pass


class PeriodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    start_time: time | None = None
    end_time: time | None = None
    days_bitmask: int | None = Field(default=None, ge=0, le=127)
    section: str | None = Field(default=None, max_length=64)
    course_id: int | None = None
    clear_course: bool = False
    required_minutes: int | None = Field(default=None, ge=1)
    required_ratio: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _at_least_one(self) -> "PeriodUpdate":
        if all(
            getattr(self, f) is None
            for f in (
                "name", "start_time", "end_time", "days_bitmask", "section",
                "course_id", "required_minutes", "required_ratio",
            )
        ) and not self.clear_course:
            raise ValueError("at least one field must be provided")
        return self

    @model_validator(mode="after")
    def _check_range_update(self) -> "PeriodUpdate":
        if self.start_time is not None and self.end_time is not None and self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    start_time: time
    end_time: time
    days_bitmask: int
    section: str | None = None
    course_id: int | None = None
    course_code: str | None = None
    course_name: str | None = None
    teacher_user_id: int | None = None
    teacher_username: str | None = None
    required_minutes: int | None = None
    required_ratio: float | None = None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ courses
class CourseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    section: str | None = Field(default=None, max_length=64)
    teacher_user_id: int | None = None


class CourseUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    section: str | None = Field(default=None, max_length=64)
    teacher_user_id: int | None = None
    clear_teacher: bool = False

    @model_validator(mode="after")
    def _at_least_one(self) -> "CourseUpdate":
        if all(
            getattr(self, f) is None
            for f in ("code", "name", "section", "teacher_user_id")
        ) and not self.clear_teacher:
            raise ValueError("at least one field must be provided")
        return self


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    section: str | None = None
    teacher_user_id: int | None = None
    teacher_username: str | None = None
    teacher_display_name: str | None = None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------- per-period attendance
class OverrideCreate(BaseModel):
    """Teacher/admin manual attendance correction.

    Both fields are required: an explicit PRESENT/ABSENT selection and a
    non-empty reason (re-validated server-side after stripping).
    """

    status: Literal["PRESENT", "ABSENT"]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _reason_not_blank(self) -> "OverrideCreate":
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        return self


class OverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: int
    period_id: int
    student_id: int
    date: date
    previous_effective: Literal["PRESENT", "ABSENT"] | None = None
    new_effective: Literal["PRESENT", "ABSENT"]
    reason: str
    actor_user_id: int | None = None
    actor_username: str
    actor_role: str
    created_at: datetime


class AttendanceRecordOut(BaseModel):
    """Derived per-student period result: machine status preserved, effective
    status = latest override when present."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    period_id: int
    period_name: str | None = None
    student: StudentSummary
    date: date
    presence_seconds: int = 0
    threshold_seconds: int = 0
    machine_status: str
    effective_status: Literal["PRESENT", "ABSENT"] | None = None
    overridden: bool = False
    override_reason: str | None = None
    overridden_by: str | None = None
    overridden_at: datetime | None = None
    # The student's intervals clipped to this period's scheduled window.
    intervals: list[AttendanceSessionOut] = Field(default_factory=list)
    computed_at: datetime


class AttendanceRecordsOut(BaseModel):
    period_id: int
    period_name: str
    date: date
    window_start: datetime
    window_end: datetime
    threshold_seconds: int
    is_past: bool
    is_running: bool
    records: list[AttendanceRecordOut] = Field(default_factory=list)


# --------------------------------------------------- student history view
class HistoryRow(BaseModel):
    """One confirmed presence interval with full context."""

    session_id: int
    student: StudentSummary
    entry_time: datetime
    exit_time: datetime | None = None
    duration_seconds: int
    date: date
    status: str
    unresolved: bool = False
    review_status: str = "confirmed"
    source: str = "auto"
    camera_in_id: int | None = None
    camera_in_name: str | None = None
    camera_out_id: int | None = None
    camera_out_name: str | None = None
    period_id: int | None = None
    period_name: str | None = None


class HistoryOut(BaseModel):
    total: int = 0
    limit: int = 50
    offset: int = 0
    items: list[HistoryRow] = Field(default_factory=list)
    totals: SessionTotals = Field(default_factory=SessionTotals)


# ------------------------------------------------------------- presence UI
class PresenceStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    student_id: int
    student_name: str | None = None
    registration_no: str | None = None
    state: str
    since: datetime | None = None
    last_seen_at: datetime | None = None
    last_camera_id: int | None = None
    last_camera_name: str | None = None
    pending_observations: int = 0
    open_session_id: int | None = None
    review_reason: str | None = None


class PresenceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: int
    student_name: str | None = None
    from_state: str | None = None
    to_state: str
    camera_id: int | None = None
    camera_name: str | None = None
    event_time: datetime
    confidence: float | None = None
    session_id: int | None = None
    reason: str | None = None
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class PresenceReviewRequest(BaseModel):
    action: Literal["confirm", "dismiss"]
    note: str | None = Field(default=None, max_length=200)


# ------------------------------------------------------------------ system
class HealthCheck(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, str] = Field(default_factory=dict)


class GpuStatusOut(BaseModel):
    service: Literal["online", "offline"] = "offline"
    updated_at: datetime | None = None
    provider: str | None = None
    providers: list[str] = Field(default_factory=list)
    model: str | None = None
    batched_detection: bool | None = None
    device_name: str | None = None
    utilization_percent: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    queue_length: int = 0
    queue_key: str | None = None
    inference_mode: str = "redis"
    throughput: dict = Field(default_factory=dict)
    cameras_running: int = 0
    detail: str | None = None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    severity: str
    payload: dict = Field(default_factory=dict)
    camera_id: int | None = None
    student_id: int | None = None
    period_id: int | None = None
    created_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


class OkResponse(BaseModel):
    ok: bool = True
    detail: str | None = None

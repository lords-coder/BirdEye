"""SQLAlchemy ORM models (PostgreSQL in production, SQLite in tests)."""

from __future__ import annotations

from datetime import date, datetime, time
from urllib.parse import quote

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


from .crypto import decrypt_embeddings


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_students_name_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    registration_no: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # List of 512-dim L2-normalisable float vectors (one per reference photo).
    # Stored encrypted at rest.
    embeddings: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    # Reference photo paths, stored relative to settings.data_dir.
    photo_paths: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    sessions: Mapped[list["AttendanceSession"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def embedding_list(self) -> list[list[float]]:
        """Get decrypted embeddings as list of float lists."""
        if not self.embeddings:
            return []
        try:
            return decrypt_embeddings(self.embeddings)
        except Exception:
            return []

    @property
    def embedding_count(self) -> int:
        return len(self.embedding_list)


class Camera(Base):
    """Registered camera.

    Two ways to configure a stream source (both may coexist):

    * **structured** - ``host``/``port``/``username``/``password_encrypted``/
      ``stream_path``; the RTSP URL is composed server-side only and the
      password is Fernet-encrypted at rest (see ``app.crypto.encrypt_text``).
      Passwords are NEVER returned through API responses.
    * **legacy** - a pre-built ``rtsp_url`` or local ``file_path`` (kept for
      existing rows, demo videos and the test suite).

    ``camera_role`` records the physical purpose (classroom interior vs
    doorway/exterior); ``type`` keeps the session semantics the pipeline has
    always used (entry opens, exit closes, both does either).
    """

    __tablename__ = "cameras"
    __table_args__ = (
        CheckConstraint(
            "type IN ('entry', 'exit', 'both')", name="ck_cameras_type"
        ),
        CheckConstraint(
            "sampling_rate >= 1 AND sampling_rate <= 600",
            name="ck_cameras_sampling_rate",
        ),
        CheckConstraint(
            "camera_role IN ('classroom_interior', 'doorway_exterior')",
            name="ck_cameras_role",
        ),
        CheckConstraint(
            "port IS NULL OR (port >= 1 AND port <= 65535)",
            name="ck_cameras_port_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rtsp_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # entry -> open sessions, exit -> close sessions, both -> whichever applies
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="entry")
    # Process every Nth decoded frame (CPU sampling before GPU batching).
    sampling_rate: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    # ---------------- structured connection (credentials never leave the
    # server: password_encrypted is write-only, URL built on demand) -------
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Fernet ciphertext of the stream password (app.crypto.encrypt_text).
    password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stream_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Physical purpose; NULL on legacy rows (inferred from `type` when needed).
    camera_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Disabled cameras keep their configuration but are refused a pipeline
    # start (offline/retired hardware stays visible and editable).
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Health/telemetry written by the pipeline and the connection test.
    last_frame_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Redacted diagnostic (never contains credentials or a credentialed URL).
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    sessions_in: Mapped[list["AttendanceSession"]] = relationship(
        foreign_keys="AttendanceSession.camera_in_id",
        back_populates="camera_in",
    )
    sessions_out: Mapped[list["AttendanceSession"]] = relationship(
        foreign_keys="AttendanceSession.camera_out_id",
        back_populates="camera_out",
    )

    # -------------------------------------------------------- helpers
    @property
    def has_password(self) -> bool:
        """True when an encrypted stream password is stored (never its value)."""
        return bool(self.password_encrypted)

    def stream_password(self) -> str | None:
        """Decrypt the stored stream password for server-side use only.

        Used by the frame reader, the connection test and the preview
        endpoints - never by an API serializer.
        """
        if not self.password_encrypted:
            return None
        from .crypto import decrypt_text

        return decrypt_text(self.password_encrypted)

    def build_rtsp_url(self) -> str | None:
        """Compose the RTSP URL server-side from structured fields.

        Falls back to the legacy ``rtsp_url`` column for rows registered
        before structured configuration existed.  The result can contain
        credentials - it must never be sent to a browser or written to a log.
        """
        if self.host:
            port = self.port or 554
            path = (self.stream_path or "").strip()
            if path and not path.startswith("/"):
                path = "/" + path
            if self.username:
                password = self.stream_password() or ""
                if password:
                    auth = f"{quote(self.username, safe='')}:{quote(password, safe='')}@"
                else:
                    # Username without a stored password (e.g. metadata
                    # registered before the password was entered via the UI):
                    # omit the empty password field rather than emitting
                    # "user:@host", which some servers reject outright.
                    auth = f"{quote(self.username, safe='')}@"
            else:
                auth = ""
            return f"rtsp://{auth}{self.host}:{port}{path}"
        if self.rtsp_url:
            return self.rtsp_url
        return None


class Course(Base):
    """Course / class section taught by one teacher.

    The teacher-portal authorization unit: a teacher may only read and
    override attendance for periods linked (via ``Period.course_id``) to a
    course whose ``teacher_user_id`` matches the signed-in account.  Admins
    and super admins manage every course.
    """

    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint("length(trim(code)) > 0", name="ck_courses_code_nonempty"),
        CheckConstraint("length(trim(name)) > 0", name="ck_courses_name_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    teacher_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    periods: Mapped[list["Period"]] = relationship(back_populates="course")


class Period(Base):
    """Timetable period definition for per-period attendance."""

    __tablename__ = "periods"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_periods_name_nonempty"),
        CheckConstraint(
            "start_time < end_time", name="ck_periods_time_range"
        ),
        CheckConstraint(
            "days_bitmask >= 0 AND days_bitmask <= 127",
            name="ck_periods_days_range",
        ),
        CheckConstraint(
            "required_minutes IS NULL OR required_minutes >= 1",
            name="ck_periods_required_minutes",
        ),
        CheckConstraint(
            "required_ratio IS NULL OR (required_ratio > 0 AND required_ratio <= 1)",
            name="ck_periods_required_ratio",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    # bitmask Mon=1<<0 ... Sun=1<<6 ; 127 = all days
    days_bitmask: Mapped[int] = mapped_column(Integer, nullable=False, default=127, server_default="127")
    # NULL = applies to all sections; otherwise exact section match
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Course/teacher scoping for the teacher portal (NULL = unassigned period,
    # visible to admins only).
    course_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Attendance policy override for this period.  NULL falls back to the
    # global defaults (ATTENDANCE_DEFAULT_REQUIRED_MINUTES / _RATIO).
    required_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    sessions: Mapped[list["AttendanceSession"]] = relationship(
        back_populates="period", passive_deletes=True
    )
    course: Mapped[Course | None] = relationship(back_populates="periods")


class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ongoing', 'completed')", name="ck_sessions_status"
        ),
        CheckConstraint(
            "source IN ('auto', 'manual')", name="ck_sessions_source"
        ),
        CheckConstraint(
            "review_status IN ('confirmed', 'unresolved', 'needs_review')",
            name="ck_sessions_review_status",
        ),
        Index("ix_sessions_student_date", "student_id", "date"),
        Index("ix_sessions_date_status", "date", "status"),
        Index("ix_sessions_period_id", "period_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    camera_in_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    camera_out_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    entry_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    exit_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Local (settings.local_timezone) calendar date of entry_time - all
    # queries for "attendance of day D" filter on this column.
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ongoing", server_default="ongoing"
    )
    # Seconds between entry and exit; while ongoing it is NULL and computed
    # on the fly by the summary endpoints.
    total_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-period attendance: nullable so old rows stay valid.
    # NOTE: no index=True here - __table_args__ already declares the named
    # index ix_sessions_period_id; a second, auto-named index on the same
    # column would exist in create_all-based databases but never in the
    # alembic-managed schema (schema drift + redundant write cost).
    period_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("periods.id", ondelete="SET NULL"), nullable=True
    )
    # How this interval came to exist: 'auto' = detected by the pipeline,
    # 'manual' = created or corrected by an operator (still audited).
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto", server_default="auto"
    )
    # Evidence quality: 'confirmed' = entry+exit observed; 'unresolved' =
    # still open with no observed exit (never invent an exit timestamp);
    # 'needs_review' = flagged by the presence engine or a manual fix.
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="confirmed", server_default="confirmed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    student: Mapped[Student] = relationship(back_populates="sessions")
    camera_in: Mapped[Camera | None] = relationship(
        foreign_keys=[camera_in_id], back_populates="sessions_in"
    )
    camera_out: Mapped[Camera | None] = relationship(
        foreign_keys=[camera_out_id], back_populates="sessions_out"
    )
    period: Mapped[Period | None] = relationship(back_populates="sessions")


class Alert(Base):
    """System alerts for deviations and anomalies (unknown faces, low confidence, etc.)."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high')", name="ck_alerts_severity"
        ),
        Index("ix_alerts_type", "type"),
        Index("ix_alerts_created_at", "created_at"),
        Index("ix_alerts_acknowledged", "acknowledged_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    # Arbitrary JSON payload: e.g. {camera_id, student_id, period_id, confidence}
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    camera_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    period_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("periods.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    """Console account (multi-user auth).

    Roles
    -----
    ``super_admin``  the ONLY role allowed to set/change a username or
                     password, approve/reject accounts and resolve
                     forgotten-password requests.
    ``admin``        regular operator: reads its own profile, never edits
                     credentials (its own included).

    Statuses
    --------
    ``pending``   registered, waiting for super-admin review (cannot log in)
    ``active``    approved by the super admin (may log in)
    ``rejected``  refused by the super admin
    ``suspended`` approved once, later disabled by the super admin
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'admin', 'teacher')", name="ck_users_role"
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'suspended')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "security_level >= 0 AND security_level <= 5",
            name="ck_users_security_level",
        ),
        CheckConstraint("length(trim(username)) >= 3", name="ck_users_username_len"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    # PBKDF2-SHA256 encoded string, see app.users.hash_password.
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    # 'env'    -> mirrors ADMIN_PASSWORD (rotation of .env keeps unlocking it)
    # 'manual' -> set by the super admin; .env no longer overrides it
    password_source: Mapped[str] = mapped_column(
        String(8), nullable=False, default="env", server_default="env"
    )
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    # 0 = not reviewed yet; 1 (low) .. 5 (highest) assigned on approval.
    security_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PasswordResetRequest(Base):
    """Forgotten-password request, routed to the super admin.

    Deliberately stores NO new password: only the super admin sets one, through
    ``POST /auth/admin/password-requests/{id}``.
    """

    __tablename__ = "password_reset_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved', 'rejected')",
            name="ck_reset_requests_status",
        ),
        Index("ix_reset_requests_username", "username"),
        Index("ix_reset_requests_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


class RecognitionLog(Base):
    """Raw audit log of every (deduplicated) recognition, including GPU latency."""

    __tablename__ = "recognition_logs"
    __table_args__ = (
        Index("ix_logs_student_ts", "student_id", "timestamp"),
        Index("ix_logs_camera_ts", "camera_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # NULL student_id => recognized as unknown (only written when
    # LOG_UNKNOWN_FACES=true).  SET NULL keeps the audit trail on delete.
    student_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    camera_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("cameras.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # Cosine similarity in [-1, 1]; matches require >= RECOGNITION_THRESHOLD.
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # End-to-end batch inference latency reported by the GPU service.
    gpu_inference_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class PresenceState(Base):
    """Persistent per-student classroom presence state machine.

    One row per student.  States:

    ``OUTSIDE``         no confirmed presence
    ``ENTRY_PENDING``   interior-camera evidence accumulating
    ``INSIDE``          confirmed presence (an open attendance session)
    ``EXIT_PENDING``    exit evidence accumulating (session still open)
    ``NEEDS_REVIEW``    an inconsistent observation (e.g. an exit sighted
                        with no active session) awaiting a human decision

    Driven by :func:`app.services.presence.observe`; every confirmed
    transition also writes a :class:`PresenceEvent` audit row.  The state is
    derived from camera evidence at *event* timestamps - never from worker
    processing time.
    """

    __tablename__ = "presence_states"
    __table_args__ = (
        CheckConstraint(
            "state IN ('OUTSIDE', 'ENTRY_PENDING', 'INSIDE', 'EXIT_PENDING', "
            "'NEEDS_REVIEW')",
            name="ck_presence_state",
        ),
        Index("ix_presence_states_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OUTSIDE", server_default="OUTSIDE"
    )
    # Event time (frame timestamp, UTC) of the observation that produced the
    # current state.
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last confirmed sighting of this student from ANY camera (event time).
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_camera_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    # Observations counted toward the current pending transition.
    pending_observations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    pending_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Open session this state is responsible for (INSIDE / EXIT_PENDING).
    open_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("attendance_sessions.id", ondelete="SET NULL"), nullable=True
    )
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    student: Mapped[Student] = relationship()


class PresenceEvent(Base):
    """Audit trail of every confirmed presence-state transition.

    Append-only: corrections create new rows, nothing is overwritten.  A
    non-null ``reviewed_by`` closes a ``NEEDS_REVIEW`` event.
    """

    __tablename__ = "presence_events"
    __table_args__ = (
        CheckConstraint(
            "to_state IN ('OUTSIDE', 'ENTRY_PENDING', 'INSIDE', 'EXIT_PENDING', "
            "'NEEDS_REVIEW')",
            name="ck_presence_events_to_state",
        ),
        CheckConstraint(
            "review_status IN ('open', 'confirmed', 'dismissed')",
            name="ck_presence_events_review",
        ),
        Index("ix_presence_events_student_ts", "student_id", "event_time"),
        Index("ix_presence_events_review", "review_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    camera_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True
    )
    # Frame/observation timestamp (UTC) - the event time, not processing time.
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("attendance_sessions.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="confirmed", server_default="confirmed"
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    student: Mapped[Student] = relationship()


class AttendanceRecord(Base):
    """Derived per-student, per-period attendance result.

    ``machine_status`` is the automatic calculation (PRESENT / ABSENT /
    IN_PROGRESS / SCHEDULED / NEEDS_REVIEW) and is never destroyed by an
    override; ``effective_status`` is the authoritative result, which equals
    the machine status until a teacher or admin override is recorded in
    :class:`AttendanceOverride`.  Recomputation updates presence numbers and
    the machine status but never silently clears an override.
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        CheckConstraint(
            "machine_status IN ('PRESENT', 'ABSENT', 'IN_PROGRESS', "
            "'SCHEDULED', 'NEEDS_REVIEW')",
            name="ck_records_machine_status",
        ),
        CheckConstraint(
            "effective_status IS NULL OR effective_status IN ('PRESENT', 'ABSENT')",
            name="ck_records_effective_status",
        ),
        CheckConstraint("presence_seconds >= 0", name="ck_records_presence_nonneg"),
        UniqueConstraint("period_id", "student_id", "date", name="uq_records_period_student_date"),
        Index("ix_records_period_date", "period_id", "date"),
        Index("ix_records_student_id", "student_id"),
        Index("ix_records_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("periods.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # Confirmed presence inside the scheduled window, clipped to boundaries.
    presence_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # The threshold that was applied (max of minutes / ratio policies).
    threshold_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    machine_status: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    period: Mapped[Period] = relationship()
    student: Mapped[Student] = relationship()


class AttendanceOverride(Base):
    """Auditable manual attendance correction (append-only).

    Each row records one change of the *effective* status with the actor's
    authenticated identity, a mandatory non-empty reason and the prior value.
    The machine-derived status and the original presence intervals are never
    modified by an override.
    """

    __tablename__ = "attendance_overrides"
    __table_args__ = (
        CheckConstraint(
            "new_effective IN ('PRESENT', 'ABSENT')", name="ck_overrides_new"
        ),
        CheckConstraint(
            "previous_effective IS NULL OR previous_effective IN ('PRESENT', 'ABSENT')",
            name="ck_overrides_previous",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 3", name="ck_overrides_reason_nonempty"
        ),
        Index("ix_overrides_record", "record_id"),
        Index("ix_overrides_period_id", "period_id"),
        Index("ix_overrides_student_id", "student_id"),
        Index("ix_overrides_student_date", "student_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("attendance_records.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("periods.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_effective: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_effective: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    record: Mapped[AttendanceRecord] = relationship()

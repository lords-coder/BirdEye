"""camera management + presence tracking + per-period attendance engine

Revision ID: 0006_camera_attendance
Revises: 0005_embeddings_text
Create Date: 2026-10-15

Camera phase additions:

* ``cameras``: structured connection fields (host/port/username/
  ``password_encrypted``/stream_path), physical ``camera_role`` (classroom
  interior vs doorway exterior), room, notes, ``enabled`` flag and
  health/telemetry columns.  Passwords are stored Fernet-encrypted and never
  leave the server; legacy ``rtsp_url``/``file_path`` rows stay valid.
* ``courses`` + ``periods.course_id``: teacher-portal scoping (a course is
  taught by one user; a period belongs to at most one course).
* ``periods.required_minutes``/``required_ratio``: per-period attendance
  threshold override (NULL = global defaults).
* ``presence_states`` / ``presence_events``: persistent per-student
  OUTSIDE/ENTRY_PENDING/INSIDE/EXIT_PENDING/NEEDS_REVIEW state machine and
  its append-only audit trail.
* ``attendance_records`` / ``attendance_overrides``: derived per-period
  results (machine vs effective status) plus auditable teacher overrides
  with mandatory reasons.
* ``attendance_sessions``: ``source`` (auto|manual) and ``review_status``
  (confirmed|unresolved|needs_review).
* ``users.role`` check constraint extended with ``teacher``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_camera_attendance"
down_revision: Union[str, None] = "0005_embeddings_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRESENCE_STATES = (
    "'OUTSIDE', 'ENTRY_PENDING', 'INSIDE', 'EXIT_PENDING', 'NEEDS_REVIEW'"
)


def upgrade() -> None:
    # ------------------------------------------------------------- cameras
    op.add_column("cameras", sa.Column("host", sa.String(length=255), nullable=True))
    op.add_column("cameras", sa.Column("port", sa.Integer(), nullable=True))
    op.add_column("cameras", sa.Column("username", sa.String(length=128), nullable=True))
    op.add_column(
        "cameras", sa.Column("password_encrypted", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("stream_path", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("camera_role", sa.String(length=32), nullable=True)
    )
    op.add_column("cameras", sa.Column("room", sa.String(length=120), nullable=True))
    op.add_column("cameras", sa.Column("notes", sa.String(length=500), nullable=True))
    op.add_column(
        "cameras",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "cameras", sa.Column("last_frame_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("last_test_status", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "cameras", sa.Column("last_error", sa.String(length=300), nullable=True)
    )
    with op.batch_alter_table("cameras") as batch_op:
        batch_op.create_check_constraint(
            "ck_cameras_role",
            "camera_role IN ('classroom_interior', 'doorway_exterior')",
        )
        batch_op.create_check_constraint(
            "ck_cameras_port_range",
            "port IS NULL OR (port >= 1 AND port <= 65535)",
        )

    # ---------------------------------------------------------------- users
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_role", type_="check")
        batch_op.create_check_constraint(
            "ck_users_role",
            "role IN ('super_admin', 'admin', 'teacher')",
        )

    # --------------------------------------------- courses + period policy
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=True),
        sa.Column("teacher_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_courses_code_nonempty"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_courses_name_nonempty"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_courses_code", "courses", ["code"], unique=True)
    op.create_index("ix_courses_teacher_user_id", "courses", ["teacher_user_id"])

    op.add_column("periods", sa.Column("course_id", sa.Integer(), nullable=True))
    op.add_column(
        "periods", sa.Column("required_minutes", sa.Integer(), nullable=True)
    )
    op.add_column(
        "periods", sa.Column("required_ratio", sa.Float(), nullable=True)
    )
    with op.batch_alter_table("periods") as batch_op:
        batch_op.create_check_constraint(
            "ck_periods_required_minutes",
            "required_minutes IS NULL OR required_minutes >= 1",
        )
        batch_op.create_check_constraint(
            "ck_periods_required_ratio",
            "required_ratio IS NULL OR (required_ratio > 0 AND required_ratio <= 1)",
        )
        batch_op.create_foreign_key(
            "fk_periods_course_id", "courses", ["course_id"], ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_periods_course_id", "periods", ["course_id"])

    # ------------------------------------------- attendance session audit
    op.add_column(
        "attendance_sessions",
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="auto"
        ),
    )
    op.add_column(
        "attendance_sessions",
        sa.Column(
            "review_status",
            sa.String(length=16),
            nullable=False,
            server_default="confirmed",
        ),
    )
    with op.batch_alter_table("attendance_sessions") as batch_op:
        batch_op.create_check_constraint(
            "ck_sessions_source", "source IN ('auto', 'manual')"
        )
        batch_op.create_check_constraint(
            "ck_sessions_review_status",
            "review_status IN ('confirmed', 'unresolved', 'needs_review')",
        )

    # ------------------------------------------------------- presence state
    op.create_table(
        "presence_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="OUTSIDE",
        ),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_camera_id", sa.Integer(), nullable=True),
        sa.Column(
            "pending_observations",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("pending_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_session_id", sa.Integer(), nullable=True),
        sa.Column("review_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"state IN ({_PRESENCE_STATES})", name="ck_presence_state"
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["open_session_id"], ["attendance_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_presence_states_student_id", "presence_states", ["student_id"], unique=True)
    op.create_index("ix_presence_states_state", "presence_states", ["state"])

    op.create_table(
        "presence_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=16), nullable=True),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=16),
            nullable=False,
            server_default="confirmed",
        ),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"to_state IN ({_PRESENCE_STATES})", name="ck_presence_events_to_state"
        ),
        sa.CheckConstraint(
            "review_status IN ('open', 'confirmed', 'dismissed')",
            name="ck_presence_events_review",
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["attendance_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_presence_events_student_id", "presence_events", ["student_id"])
    op.create_index("ix_presence_events_event_time", "presence_events", ["event_time"])
    op.create_index(
        "ix_presence_events_student_ts", "presence_events", ["student_id", "event_time"]
    )
    op.create_index("ix_presence_events_review", "presence_events", ["review_status"])

    # ------------------------------------------------ attendance records
    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "presence_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "threshold_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("machine_status", sa.String(length=16), nullable=False),
        sa.Column("effective_status", sa.String(length=16), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "machine_status IN ('PRESENT', 'ABSENT', 'IN_PROGRESS', "
            "'SCHEDULED', 'NEEDS_REVIEW')",
            name="ck_records_machine_status",
        ),
        sa.CheckConstraint(
            "effective_status IS NULL OR effective_status IN ('PRESENT', 'ABSENT')",
            name="ck_records_effective_status",
        ),
        sa.CheckConstraint("presence_seconds >= 0", name="ck_records_presence_nonneg"),
        sa.ForeignKeyConstraint(["period_id"], ["periods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "period_id", "student_id", "date", name="uq_records_period_student_date"
        ),
    )
    op.create_index("ix_records_period_date", "attendance_records", ["period_id", "date"])
    op.create_index("ix_records_student_id", "attendance_records", ["student_id"])
    op.create_index("ix_records_date", "attendance_records", ["date"])

    op.create_table(
        "attendance_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("period_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("previous_effective", sa.String(length=16), nullable=True),
        sa.Column("new_effective", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=100), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "new_effective IN ('PRESENT', 'ABSENT')", name="ck_overrides_new"
        ),
        sa.CheckConstraint(
            "previous_effective IS NULL OR previous_effective IN ('PRESENT', 'ABSENT')",
            name="ck_overrides_previous",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) >= 3", name="ck_overrides_reason_nonempty"
        ),
        sa.ForeignKeyConstraint(
            ["record_id"], ["attendance_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["period_id"], ["periods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_overrides_record", "attendance_overrides", ["record_id"])
    op.create_index("ix_overrides_period_id", "attendance_overrides", ["period_id"])
    op.create_index("ix_overrides_student_id", "attendance_overrides", ["student_id"])
    op.create_index(
        "ix_overrides_student_date", "attendance_overrides", ["student_id", "date"]
    )


def downgrade() -> None:
    op.drop_table("attendance_overrides")
    op.drop_table("attendance_records")
    op.drop_table("presence_events")
    op.drop_table("presence_states")

    with op.batch_alter_table("attendance_sessions") as batch_op:
        batch_op.drop_constraint("ck_sessions_source", type_="check")
        batch_op.drop_constraint("ck_sessions_review_status", type_="check")
    op.drop_column("attendance_sessions", "review_status")
    op.drop_column("attendance_sessions", "source")

    op.drop_index("ix_periods_course_id", table_name="periods")
    with op.batch_alter_table("periods") as batch_op:
        batch_op.drop_constraint("fk_periods_course_id", type_="foreignkey")
        batch_op.drop_constraint("ck_periods_required_ratio", type_="check")
        batch_op.drop_constraint("ck_periods_required_minutes", type_="check")
    op.drop_column("periods", "required_ratio")
    op.drop_column("periods", "required_minutes")
    op.drop_column("periods", "course_id")

    op.drop_index("ix_courses_teacher_user_id", table_name="courses")
    op.drop_index("ix_courses_code", table_name="courses")
    op.drop_table("courses")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_role", type_="check")
        batch_op.create_check_constraint(
            "ck_users_role", "role IN ('super_admin', 'admin')"
        )

    with op.batch_alter_table("cameras") as batch_op:
        batch_op.drop_constraint("ck_cameras_port_range", type_="check")
        batch_op.drop_constraint("ck_cameras_role", type_="check")
    op.drop_column("cameras", "last_error")
    op.drop_column("cameras", "last_tested_at")
    op.drop_column("cameras", "last_test_status")
    op.drop_column("cameras", "last_frame_at")
    op.drop_column("cameras", "enabled")
    op.drop_column("cameras", "notes")
    op.drop_column("cameras", "room")
    op.drop_column("cameras", "camera_role")
    op.drop_column("cameras", "stream_path")
    op.drop_column("cameras", "password_encrypted")
    op.drop_column("cameras", "username")
    op.drop_column("cameras", "port")
    op.drop_column("cameras", "host")

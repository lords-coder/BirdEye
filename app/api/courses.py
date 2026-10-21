"""Courses (class sections) + teacher assignment.

The teacher-portal authorization unit: a course is taught by at most one
user; periods link to a course via ``Period.course_id``.  Teachers may read
their own courses; admins/super admins manage every course and assignment.
Teacher accounts themselves are created by the super admin (role change on
an approved account) - this module introduces no new registration policy.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Course, Period, User
from ..schemas import CourseCreate, CourseOut, CourseUpdate, PeriodOut
from ..security import Principal, get_current_staff, get_current_user
from ..users import ROLE_TEACHER, STATUS_ACTIVE
from .deps import get_db

logger = logging.getLogger("app.api.courses")

router = APIRouter(prefix="/courses", tags=["courses"])


# ------------------------------------------------------- authorization core
def _teacher_or_403(db: Session, user_id: int | None) -> User:
    """A teacher assignment target must be an ACTIVE teacher account."""
    if user_id is None:
        raise HTTPException(status_code=422, detail="teacher_user_id is required")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    if user.role != ROLE_TEACHER:
        raise HTTPException(
            status_code=422,
            detail=f"user {user_id} is not a teacher (role={user.role})",
        )
    if user.status != STATUS_ACTIVE:
        raise HTTPException(
            status_code=422,
            detail=f"teacher account {user.username!r} is not active",
        )
    return user


def course_out(db: Session, course: Course) -> CourseOut:
    teacher = db.get(User, course.teacher_user_id) if course.teacher_user_id else None
    return CourseOut(
        id=course.id,
        code=course.code,
        name=course.name,
        section=course.section,
        teacher_user_id=course.teacher_user_id,
        teacher_username=teacher.username if teacher else None,
        teacher_display_name=teacher.display_name if teacher else None,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


def period_out(db: Session, period: Period) -> PeriodOut:
    course = db.get(Course, period.course_id) if period.course_id else None
    teacher = (
        db.get(User, course.teacher_user_id)
        if course is not None and course.teacher_user_id
        else None
    )
    return PeriodOut(
        id=period.id,
        name=period.name,
        start_time=period.start_time,
        end_time=period.end_time,
        days_bitmask=period.days_bitmask,
        section=period.section,
        course_id=period.course_id,
        course_code=course.code if course else None,
        course_name=course.name if course else None,
        teacher_user_id=course.teacher_user_id if course else None,
        teacher_username=teacher.username if teacher else None,
        required_minutes=period.required_minutes,
        required_ratio=period.required_ratio,
        created_at=period.created_at,
        updated_at=period.updated_at,
    )


def can_access_period(db: Session, principal: Principal, period: Period) -> bool:
    """Admins: everything.  Teachers: only periods of their own courses."""
    if principal.is_admin:
        return True
    if not principal.is_teacher or principal.user_id is None or period.course_id is None:
        return False
    course = db.get(Course, period.course_id)
    return course is not None and course.teacher_user_id == principal.user_id


def require_period_access(db: Session, principal: Principal, period: Period) -> None:
    if not can_access_period(db, principal, period):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this period belongs to another teacher's class",
        )


def teacher_course_ids(db: Session, principal: Principal) -> set[int]:
    if principal.is_admin:
        return {int(c.id) for c in db.scalars(select(Course)).all()}
    if principal.is_teacher and principal.user_id is not None:
        return {
            int(c.id)
            for c in db.scalars(
                select(Course).where(Course.teacher_user_id == principal.user_id)
            ).all()
        }
    return set()


# ------------------------------------------------------------------- routes
@router.post("", response_model=CourseOut, status_code=status.HTTP_201_CREATED,
             summary="Create a course (admin)")
def create_course(
    body: CourseCreate,
    db: Session = Depends(get_db),
    _: Principal = Depends(get_current_staff),
) -> CourseOut:
    existing = db.scalars(select(Course).where(Course.code == body.code)).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"course code {body.code!r} already exists"
        )
    if body.teacher_user_id is not None:
        _teacher_or_403(db, body.teacher_user_id)
    course = Course(
        code=body.code.strip(),
        name=body.name.strip(),
        section=body.section,
        teacher_user_id=body.teacher_user_id,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course_out(db, course)


@router.get("", response_model=list[CourseOut],
            summary="List courses (teachers see their own)")
def list_courses(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_user),
) -> list[CourseOut]:
    if principal.is_admin:
        rows = db.scalars(select(Course).order_by(Course.code.asc())).all()
    elif principal.is_teacher and principal.user_id is not None:
        rows = db.scalars(
            select(Course)
            .where(Course.teacher_user_id == principal.user_id)
            .order_by(Course.code.asc())
        ).all()
    else:
        raise HTTPException(status_code=403, detail="admin or teacher access required")
    return [course_out(db, c) for c in rows]


@router.get("/{course_id}", response_model=CourseOut, summary="Get one course")
def get_course(
    course_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_user),
) -> CourseOut:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"course {course_id} not found")
    if not principal.is_admin and (
        principal.user_id is None or course.teacher_user_id != principal.user_id
    ):
        raise HTTPException(status_code=403, detail="not your assigned course")
    return course_out(db, course)


@router.get("/{course_id}/periods", response_model=list[PeriodOut],
            summary="Timetable periods of a course")
def course_periods(
    course_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_user),
) -> list[PeriodOut]:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"course {course_id} not found")
    if not principal.is_admin and (
        principal.user_id is None or course.teacher_user_id != principal.user_id
    ):
        raise HTTPException(status_code=403, detail="not your assigned course")
    rows = db.scalars(
        select(Period)
        .where(Period.course_id == course_id)
        .order_by(Period.start_time.asc())
    ).all()
    return [period_out(db, p) for p in rows]


@router.put("/{course_id}", response_model=CourseOut, summary="Update a course (admin)")
def update_course(
    course_id: int,
    body: CourseUpdate,
    db: Session = Depends(get_db),
    _: Principal = Depends(get_current_staff),
) -> CourseOut:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"course {course_id} not found")
    fields = body.model_dump(exclude_unset=True, exclude={"clear_teacher"})
    if "code" in fields and fields["code"] != course.code:
        clash = db.scalars(select(Course).where(Course.code == fields["code"])).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="course code already exists")
    if "teacher_user_id" in fields and fields["teacher_user_id"] is not None:
        _teacher_or_403(db, fields["teacher_user_id"])
    for key, value in fields.items():
        setattr(course, key, value)
    if body.clear_teacher:
        course.teacher_user_id = None
    db.commit()
    db.refresh(course)
    return course_out(db, course)


@router.delete("/{course_id}", summary="Delete a course (admin)")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    _: Principal = Depends(get_current_staff),
) -> dict:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail=f"course {course_id} not found")
    db.delete(course)  # periods keep existing rows, course_id set to NULL
    db.commit()
    return {"ok": True, "detail": f"course {course_id} deleted"}

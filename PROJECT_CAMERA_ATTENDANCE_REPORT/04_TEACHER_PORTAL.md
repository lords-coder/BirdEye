# Teacher Portal & Roles (04)

## Role model

`super_admin` (everything incl. credentials/roles) · `admin` (cameras,
students, all attendance, overrides) · **`teacher`** (own courses only).
`get_current_admin` now fails closed for teachers (it previously accepted
any principal); the live socket and preview endpoints enforce admin-only;
every legacy endpoint keeps admin-only semantics. No new registration
policy: teachers are approved accounts whose role the super admin sets.

## Courses & scoping

`Course` (code unique, name, section, `teacher_user_id`); assignment
targets must be active teacher accounts (admins/unknown/inactive rejected).
`Period.course_id` links lectures to courses. Teachers read: own courses,
their periods, their period records, section-scoped history. Everything
else is 403 — verified live:

| caller | own class | other's class | cameras/students/users/periods-list/presence |
|---|---|---|---|
| teacher.one (assigned) | records + override (audited as teacher) | 403 | 403 |
| teacher.two (unassigned) | 403 everywhere incl. history-out-of-scope | 403 | 403 |
| admin / super_admin | full access | full access | full access |

## UI

Teachers land on **My classes** (admin sections hidden); per-course cards
show today's lectures with machine/effective badges and the override
dialog (explicit status + mandatory reason, client- and server-validated).
Periods section (admin) adds course/teacher/required-minutes columns,
period create/edit with course assignment, and the records table with
interval detail and per-row override. URL manipulation grants nothing —
all rules are backend-enforced.

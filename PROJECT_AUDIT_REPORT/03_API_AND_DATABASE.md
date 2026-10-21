# 03 — API and Database

**48 registered routes total** = 46 router handlers (`app/api/*`: 45 HTTP + 1 WebSocket) + 2 in `app/main.py` (`/`, `/api`).

Auth model: JWT Bearer (`Authorization: Bearer <token>`). When `AUTH_REQUIRED=true` (default), everything except the rows marked *Public* requires a valid token; `AUTH_REQUIRED=false` makes every caller a super admin (`app/security.py:95-98`). Role guards: **any admin** for most routes; **super_admin only** for `/auth/admin/*`.

## 1. Endpoint inventory

### 1.1 System & metadata (public)

| Method | Path | Purpose | Auth | Status | Frontend consumer | Notes / issues |
|---|---|---|---|---|---|---|
| GET | `/` | Serve SPA `index.html` (fallback: metadata JSON) | Public | COMPLETE | boot | `test_api.py:331-339` asserts HTML |
| GET | `/api` | Service metadata JSON | Public | COMPLETE | — | lists docs/health/ws/ui paths |
| GET | `/system/health` | Liveness/readiness; 503 if DB down | Public | COMPLETE | dashboard, system | reports db/redis/inference checks; `inference: "unknown"` when no GPU status ever published |
| GET | `/system/gpu-status` | GPU util/VRAM, queue depth, throughput | Bearer | COMPLETE | dashboard, system | degrades gracefully when Redis down |

README §7 claims a `GET /health` route — **it does not exist** (actual: `/system/health`).

### 1.2 Auth (`app/api/auth.py`)

| Method | Path | Purpose | Auth | Status | Frontend | Notes |
|---|---|---|---|---|---|---|
| POST | `/auth/token` | username/password → JWT (HS256, default 720 min) | Public | COMPLETE | login | 401 invalid creds / 403 non-active status; seeds super-admin on first call |
| POST | `/auth/register` | self sign-up → **pending** account | Public | COMPLETE | register | username regex + password ≥8; cannot self-assign super_admin |
| POST | `/auth/forgot-password` | record reset request | Public | COMPLETE | forgot | always 202, non-enumerating |
| GET | `/auth/me` | current principal | Bearer | COMPLETE | shell | falls back to synthetic principal for legacy tokens |
| GET | `/auth/admin/users` | list accounts | super_admin | COMPLETE | users | — |
| PATCH | `/auth/admin/users/{id}` | set username/password/role/level/status | super_admin | COMPLETE | users | blocks demoting the last active super admin |
| POST | `/auth/admin/users/{id}/approve` | approve + assign security_level 1-5 | super_admin | COMPLETE | users | — |
| POST | `/auth/admin/users/{id}/reject` | reject pending | super_admin | COMPLETE | users | frontend sends unused `{}` body (harmless) |
| POST | `/auth/admin/users/{id}/suspend` | suspend active | super_admin | COMPLETE | users | same `{}` body note |
| GET | `/auth/admin/password-requests` | list reset queue (`?status=`) | super_admin | COMPLETE | users | — |
| POST | `/auth/admin/password-requests/{id}` | `{action: reset\|reject, new_password?}` | super_admin | COMPLETE | users | schema enforces `new_password` for reset |

### 1.3 Students (`app/api/students.py`)

| Method | Path | Purpose | Auth | Status | Frontend | Notes |
|---|---|---|---|---|---|---|
| POST | `/students` | enroll (multipart: name, registration_no, section?, photos 3-5) | Bearer | COMPLETE | enroll form | quality gates + duplicate-identity rejection (409); leftover debug `print` at `:68` |
| GET | `/students` | paginated list (`q`, `section`, `limit`, `offset`, `include_embeddings`) | Bearer | COMPLETE | list/search | returns bare `dict`; **decrypts every listed student's embeddings** to compute `embedding_count` (perf, see 07) |
| GET | `/students/{id}` | one student | Bearer | — | — | unused by frontend |
| PUT | `/students/{id}` | update name/reg/section (409 on reg clash) | Bearer | — | — | unused by frontend |
| POST | `/students/{id}/photos` | add reference photos | Bearer | **BROKEN** | "+ photos" | **Bug #1**: `enrollment.add_photos` counts the encrypted ciphertext string as a list → the photo-count guard always rejects (see 05) |
| PUT | `/students/{id}/face` | replace all photos/embeddings | Bearer | PARTIAL | — | skips the cross-student duplicate-face check that enroll enforces; unused by frontend |
| DELETE | `/students/{id}/face` | clear face data, keep record | Bearer | COMPLETE | clear face | — |
| POST | `/students/bulk-import` | CSV upload (+`dry_run`, `skip_quality_checks`) | Bearer | COMPLETE (static) | — | per-row report; photo paths resolved relative to `DATA_DIR` — **server-side path base is a traversal consideration** (paths joined under `photos_root`; relative `..` components are not explicitly normalized in the CSV path — see 06, MEDIUM/suspected) |
| DELETE | `/students/{id}` | delete student (cascades sessions, nulls logs) | Bearer | COMPLETE | delete | — |

### 1.4 Cameras (`app/api/cameras.py`)

| Method | Path | Purpose | Auth | Status | Frontend | Notes |
|---|---|---|---|---|---|---|
| POST | `/cameras` | register (needs rtsp_url or file_path) | Bearer | COMPLETE | camera form | 409 name clash |
| GET | `/cameras` | list + runtime state | Bearer | COMPLETE | cameras/dashboard | **latent 500**: `CameraOut` inherits `CameraCreate._needs_source` validator (`schemas.py:200-204,224`) — a DB row with both sources NULL fails response validation. Cannot be created via API, but reachable via direct DB edit or a future bulk loader |
| GET | `/cameras/{id}` | one camera | Bearer | — | — | same latent issue |
| PUT | `/cameras/{id}` | update config | Bearer | — | — | unused by frontend; could null both sources → same latent 500 |
| DELETE | `/cameras/{id}` | delete (must be stopped) | Bearer | COMPLETE | delete | clears leftover Redis keys |
| POST | `/cameras/{id}/start` | claim slot, `send_task("camera.run", queue=camera_slot_N)` | Bearer | COMPLETE (static) / UNVERIFIED (runtime) | start | 409 if running or slots busy; 503 if broker down (rolls back slot) |
| POST | `/cameras/{id}/stop` | set stop flag; `?force=true` revokes task | Bearer | COMPLETE (static) / UNVERIFIED | stop | graceful vs immediate paths |

### 1.5 Attendance (`app/api/attendance.py`)

| Method | Path | Purpose | Auth | Status | Frontend | Notes |
|---|---|---|---|---|---|---|
| GET | `/attendance/summary?date=&include_absent=` | daily per-student totals | Bearer | COMPLETE | attendance | date defaults to *local* today |
| GET | `/attendance/live` | ongoing sessions | Bearer | COMPLETE | live (5 s poll) | — |
| GET | `/attendance/logs` | recognition audit log (filters, pagination) | Bearer | COMPLETE | attendance | **contract drift**: frontend reads `total` (`app.js:519`); response has only `items/limit/offset` (`attendance.py:137`) → "N of ?" always |
| GET | `/attendance/period/{period_id}/summary` | per-period summary | Bearer | COMPLETE | — | unused by frontend |
| GET | `/attendance/period/{period_id}/{student_id}` | one student in one period | Bearer | COMPLETE | — | unused by frontend |
| GET | `/attendance/{student_id}?date=` | one student's day | Bearer | COMPLETE | — | unused by frontend |
| POST | `/attendance/sessions/{id}/close` | force-close session | Bearer | COMPLETE | — | uses private service helpers (`# type: ignore`) |

README §7 lists `GET /attendance/period/{period}/{student}` — matches; but the summary variant `/attendance/period/{period_id}/summary` is undocumented in README.

### 1.6 Periods (`app/api/periods.py`) — no frontend consumer

| Method | Path | Auth | Status | Notes |
|---|---|---|---|---|
| POST `/periods` | Bearer | COMPLETE | 409 name clash; schema enforces start<end, days_bitmask 0-127 |
| GET `/periods` | Bearer | COMPLETE | ordered by start_time |
| GET `/periods/{id}` | Bearer | COMPLETE | — |
| PUT `/periods/{id}` | Bearer | COMPLETE | cross-checks start<end on partial update |
| DELETE `/periods/{id}` | Bearer | COMPLETE | sessions keep (FK `SET NULL`) |

### 1.7 Alerts (`app/api/alerts.py`) — no frontend consumer

| Method | Path | Auth | Status | Notes |
|---|---|---|---|---|
| GET `/alerts?type=&severity=&acknowledged=&limit=&offset=` | Bearer | COMPLETE | filters |
| GET `/alerts/{id}` | Bearer | COMPLETE | — |
| POST `/alerts/{id}/ack` | Bearer | COMPLETE | idempotent |
| POST `/alerts/{id}/resolve` | Bearer | COMPLETE | auto-acks |

### 1.8 WebSocket

| Path | Purpose | Auth | Status | Notes |
|---|---|---|---|---|
| `WS /ws/live-feed` | replay ≤100 backlog events then stream `events:live` pub/sub; answers `{"type":"pong"}` | `?token=<jwt>` (signature check only — **status/role not verified**; suspended users retain access) | PARTIAL | README says `/ws` — actual is `/ws/live-feed` |

## 2. Database schema (8 tables)

### `students`
| Column | Type | Constraints |
|---|---|---|
| id | Integer | PK |
| name | String(200) | NOT NULL; CK non-empty trimmed |
| registration_no | String(64) | NOT NULL, UNIQUE, indexed |
| section | String(64) | nullable |
| embeddings | **String** (model) / **JSON** (migration 0001) ⚠ | NOT NULL, default `""`/`"[]"` — encrypted Fernet blob of `list[list[float]]` |
| photo_paths | JSON | NOT NULL, default `[]` — paths relative to `DATA_DIR` |
| created_at / updated_at | DateTime(tz) | server defaults; `onupdate` on updated_at |

Relation: `sessions` (cascade delete-orphan). Property `embedding_list` decrypts on read and **silently returns `[]` on any failure** (`models.py:72-73`).

### `cameras`
id PK; name String(100) NOT NULL UNIQUE; location(200); rtsp_url(500); file_path(500); type ∈ {entry,exit,both} (CK); sampling_rate 1-600 (CK, default 5); timestamps. Relations: `sessions_in`/`sessions_out`.

### `periods`
id PK; name(100) UNIQUE NOT NULL (CK non-empty); start_time/end_time Time (CK start<end); days_bitmask 0-127 (Mon=1<<0); section(64) nullable = all sections; timestamps. Relation: `sessions`.

### `attendance_sessions`
id PK; student_id FK→students **CASCADE** (indexed); camera_in_id / camera_out_id FK→cameras SET NULL; entry_time NOT NULL; exit_time; **date** Date NOT NULL indexed (local calendar day of entry); status ∈ {ongoing,completed} (CK, default ongoing); total_duration seconds (NULL while ongoing); period_id FK→periods SET NULL indexed; created_at. Indexes: `(student_id,date)`, `(date,status)`, `(period_id)` — note `student_id` and `period_id` are indexed both individually and in composites (redundant).

### `alerts`
id PK; type(64); severity ∈ {low,medium,high} (CK, default "medium", no server_default); payload JSON; camera_id/student_id/period_id FKs SET NULL (not indexed); created_at (indexed); acknowledged_at (indexed); resolved_at. No ORM relationships.

### `users`
id PK; username(100) UNIQUE indexed (CK ≥3); password_hash(200) PBKDF2 string; password_source ∈ {env,manual}; display_name; role ∈ {super_admin,admin} (CK); status ∈ {pending,active,rejected,suspended} (CK, default pending); security_level 0-5 (CK); approved_at/by; timestamps.

### `password_reset_requests`
id PK; username(100) (**plain string, no FK**); reason(500); status ∈ {pending,resolved,rejected}; created/resolved timestamps; resolved_by. Indexes on username, status.

### `recognition_logs`
id PK; student_id FK SET NULL (NULL = unknown face, indexed); camera_id FK SET NULL (indexed); timestamp NOT NULL indexed; confidence_score (cosine −1..1); gpu_inference_time_ms (actually the *shared batch* latency — see 05). Indexes: `(student_id,timestamp)`, `(camera_id,timestamp)` + single-column indexes (triple redundancy).

### Migration chain (intact)
`0001_initial` (students/cameras/attendance_sessions/recognition_logs) → `0002_add_periods` (periods + period_id) → `0003_add_alerts` → `0004_users` (users + password_reset_requests). `alembic/env.py` injects `settings.database_url`, `compare_type=True`, `render_as_batch=True`; `alembic.ini` URL is an empty placeholder.

**Schema/code mismatch (CONFIRMED):** `students.embeddings` is `sa.JSON(), server_default="[]"` in `0001_initial.py:29-34` but `String, server_default=""` in `models.py:42-44`. Tests bypass migrations (`Base.metadata.create_all` in `tests/conftest.py:107-108`), so this is invisible to the suite. On PostgreSQL, a JSON column manipulated through a String-mapped attribute is a runtime hazard for any `alembic`-created database. No later migration reconciles it.

**Index hygiene:** `AttendanceSession.period_id`/`student_id` and `RecognitionLog.student_id`/`camera_id`/`timestamp` are each indexed twice (single-column `index=True` plus composite `__table_args__` indexes). Redundant, not harmful.

**Missing relationships:** `Alert`, `RecognitionLog`, `PasswordResetRequest` define no ORM relationships — all joins are hand-built via FK ids (works, more verbose).

## 3. Frontend ↔ backend contract mismatches (complete list)

All 35 HTTP call sites + the WS URL resolve to real routes with correct methods and payload fields. Mismatches found:

| # | Mismatch | Evidence | Impact |
|---|---|---|---|
| 1 | `GET /attendance/logs` response has no `total`; frontend renders "N of ?" | `app.js:519` vs `attendance.py:137` | cosmetic but permanent |
| 2 | `#c-file` `maxlength=550` vs `CameraCreate.file_path` `max_length=500` | `index.html:174` vs `schemas.py:191` | 501-550-char paths pass browser validation → 422 |
| 3 | JSON `{}` bodies sent to body-less routes (start/stop camera, reject/suspend user) | `app.js:450,453,675,679` | works (FastAPI ignores); OpenAPI/Swagger drift |
| 4 | Frontend reads only `runtime.state=="running"` on dashboard vs `starting` counted in table | `app.js:294` vs `:423` | cosmetic inconsistency |
| 5 | `AUTH_REQUIRED=false` mode: Users console hidden (no token ⇒ nav hidden) though backend grants super admin | `app.js:203-207` vs `security.py:95-98` | feature unreachable in supported mode |
| 6 | Backend routes with zero UI: all `/periods` (5), all `/alerts` (4), `PUT /students/{id}`, `PUT /students/{id}/face`, `GET /students/{id}`, `POST /students/bulk-import`, `PUT /cameras/{id}`, `POST /attendance/sessions/{id}/close`, both `/attendance/period/*` routes, `GET /attendance/{student_id}`, `GET /api` | grep of `app/static/` (zero `period|alert` occurrences) | UI is a subset of the API |
| 7 | README API table vs reality: `GET /health` (no), `WS /ws` (actual `/ws/live-feed`) | `README.md:252-253` vs routes | doc-only |

## 4. Seed data & dev accounts

- No seed data is shipped (correctly — `data/` is gitignored by policy and absent).
- Bootstrap account: `ensure_seed_superuser` creates `ADMIN_USERNAME`/`ADMIN_PASSWORD` (default **admin/admin**) as active super_admin on first `/auth/token` or `/auth/register` call (`users.py:166-197`). While `password_source=="env"`, rotating `ADMIN_PASSWORD` in `.env` keeps unlocking (with hash re-sync, `users.py:213-226`).
- Tests pin `admin/admin` via `tests/conftest.py:40-41` against a per-run temp SQLite file.

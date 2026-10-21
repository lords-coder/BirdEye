# 04 — Architecture and Data Flow

All flows below were verified against actual code (file:line references), not documentation.

## 1. High-level system architecture

```mermaid
flowchart TB
    subgraph Clients
        B[Browser SPA<br/>app/static/]
        C[curl / scripts]
    end

    subgraph "API process (uvicorn app.main:app)"
        R[FastAPI routers<br/>auth students cameras<br/>attendance periods alerts system]
        W[WS /ws/live-feed]
    end

    subgraph "Worker process (Celery)"
        T[app.tasks<br/>camera.run + jobs.*]
        CT[camera_task pipeline<br/>frame_reader thread]
    end

    subgraph "Inference process"
        S[app.inference.server<br/>BLPOP + cross-camera batching]
        E[FaceEngine<br/>SCRFD + ArcFace]
    end

    PG[(PostgreSQL 16<br/>or SQLite)]
    RD[(Redis 7<br/>broker · RPC bus<br/>pub/sub · state)]

    B -->|REST Bearer JWT| R
    B -->|WS ?token=| W
    C --> R
    R --> PG
    R -->|send_task camera.run<br/>queue camera_slot_N| RD
    RD --> T
    T --> CT
    CT -->|LPUSH infer:requests<br/>BLPOP infer:response:id| RD
    RD --> S
    S --> E
    CT -->|read sessions/logs<br/>refresh EmbeddingIndex| PG
    CT -->|publish events:live| RD
    RD --> W
    R -->|gpu:status, runtime state| RD
```

Key property (verified): the **API process never loads cv2/onnxruntime/insightface** — heavy imports are function-local in `engine.py`, `frame_reader.py`, `server.py`; the API talks to inference only through Redis RPC (`inference/client.py` imports only `redis`).

## 2. Application startup

```mermaid
sequenceDiagram
    participant U as uvicorn
    participant M as app/main.py
    participant C as config.py
    participant DB as db.py
    participant AU as auth endpoints

    U->>M: import app.main
    M->>C: settings = Settings() (lru_cache)
    Note over C: reads .env (MISSING in archive)<br/>prod hard-fail: EMBEDDING_ENCRYPTION_KEY, JWT_SECRET
    M->>M: configure_logging(settings)
    M->>DB: engine = create_engine(DATABASE_URL)
    Note over DB: import-time engine build<br/>default DATABASE_URL = postgres://localhost:5432
    M->>M: mount /media (StaticFiles, NO AUTH), /static
    M->>M: lifespan: mkdir DATA_DIR/students
    Note over AU: first /auth/token call →<br/>ensure_seed_superuser (admin/admin default)
```

Migrations are **not** run by the API process itself; compose runs `alembic upgrade head` in the api container command (`docker-compose.yml:64`). The worker and inference processes never migrate.

## 3. Frontend navigation and rendering

Vanilla SPA, no framework. `boot()` (`app.js:187-199`): token in `localStorage` → `enterApp()`; else probe `GET /students?limit=1` to detect `AUTH_REQUIRED=false`. `enterApp()` loads `/auth/me` (role gates the Users nav), fetches all dashboard data, opens the WebSocket. Sections toggle via `hidden` attribute; tables re-fetch on section entry. Live section polls `/attendance/live` every 5 s (`liveTimer`) and prepends WS events (cap 60).

## 4. Registration and authentication

```mermaid
flowchart LR
    A[POST /auth/register] --> P[status=pending<br/>cannot log in]
    P --> SA[super admin:<br/>POST /auth/admin/users/id/approve]
    SA --> AC[status=active<br/>security_level 1-5]
    AC --> L[POST /auth/token]
    L -->|PBKDF2 verify +<br/>status check| J[JWT HS256<br/>sub role uid exp]
    J --> G[Bearer on every request]
    G --> DP[resolve_principal:<br/>DB lookup by uid→username<br/>status re-checked per request]
    DP -->|pending/rejected/suspended| F403[403]
```

Password reset is **manual, in-app**: `POST /auth/forgot-password` writes a `PasswordResetRequest`; only the super admin sets a new password (`POST /auth/admin/password-requests/{id}`). No email/SMS integration exists. While the seeded super admin's `password_source=="env"`, the `.env` `ADMIN_PASSWORD` acts as a perpetual alternate credential (re-syncing the hash) — deliberate, documented (`users.py:213-226`).

## 5. Camera pipeline (the core real-time flow)

```mermaid
sequenceDiagram
    participant API as POST /cameras/id/start
    participant R as Redis
    participant CT as camera_task (Celery child)
    participant FR as FrameReader thread
    participant IS as inference server
    participant DB as Database

    API->>R: SET NX camera_slot_N (claim)
    API->>R: send_task("camera.run",[cam,slot]) via Celery
    R->>CT: consume camera_slot_N
    CT->>FR: start thread (cv2.VideoCapture RTSP/file)
    loop every ~250 ms
        CT->>R: heartbeat HSET cam:id:hb
        CT->>CT: collect ≤batch_size frames within batch_flush_ms
        CT->>CT: JPEG encode (q=85)
        CT->>R: LPUSH infer:requests {frames,corr_id}
        R->>IS: BLPOP (coalesce ≤25 ms across cameras)
        IS->>IS: SCRFD batched detect → quality gate → ArcFace embed
        IS->>R: RPUSH infer:response:corr_id
        R->>CT: BLPOP response
        CT->>CT: EmbeddingIndex.match (threshold 0.40 → margin 0.10 → dup-identity skip)
        CT->>CT: K-of-N confirmation (_CONFIRM_BUFFER)
        CT->>R: dedup_pass (SET NX EX 120)
        CT->>DB: INSERT RecognitionLog + open/close AttendanceSession
        CT->>R: PUBLISH events:live → WS clients
    end
    API->>R: SET cam:id:stop → CT finally-cleanup releases slot
```

Maintenance (Celery beat): `jobs.close_stale_sessions` (daily), `jobs.check_period_deviations` (5 min → alerts), `jobs.reap_dead_cameras` (1 min, reclaims expired heartbeats/slots).

## 6. Enrollment flow

`POST /students` (multipart) → `enrollment.enroll_student`:
1. photo-count gate (3-5) + registration_no regex;
2. per photo: `quality.check_photo_quality` → RPC `infer_frames` (exactly one face, det score ≥0.6, width ≥80 px, pose ≤30°); sharpness/brightness enforced only inside the engine's `_quality_ok`;
3. RPC `embed_images` → 512-d vectors;
4. `quality.check_duplicate_face` vs every existing student (cosine ≥ `recognition_threshold` **0.40** — see inconsistency note in 05);
5. encrypt embeddings (`crypto.encrypt_embeddings`, Fernet) → `students.embeddings`; save photos under `DATA_DIR/students/<reg>/`;
6. `bump_students_version()` → all live `EmbeddingIndex` instances refresh lazily.

## 7. Integration boundaries and disconnected parts

```mermaid
flowchart LR
    subgraph Connected
        API --> SVC[services/enrollment+attendance+alerts]
        SVC --> MATCH[matching.EmbeddingIndex]
        PIPE[camera_task] --> MATCH
        PIPE --> SVC
        PIPE --> CL[inference/client]
    end
    subgraph "Disconnected (scripts/tests only)"
        FC[app/formcsv.py]
        CN[app/confirm.py]
        EM[app/evalmetrics.py]
        LI[app/inference/local.py]
    end
    CL --> RS[Redis RPC] --> IS[inference/server]
    SD[scripts/setup_student_database.py] --> FC
    SD --> LI
    JD[scripts/judge_demo.py] --> CN
    JD --> EM
```

- `app/confirm.py` reimplements the live pipeline's confirmation with **different constants** (3 frames/10 s vs `config.confirmation_frames=2`/5 s) and per-track semantics vs the live per-(camera,student) semantics.
- `INFERENCE_MODE=inprocess` is **documented** as a worker debug mode but nothing in `app/tasks.py`, `camera_task.py`, or worker startup ever installs `LocalInference` — only scripts call `.install()`. The flag currently affects only what `/system/*` reports.
- `app/formcsv.py` (Google-Forms pipeline) is not reachable from any HTTP route; the live bulk import uses a *different* CSV schema via `services/enrollment.bulk_enroll_from_csv`.

## 8. Error handling and response delivery

- Global catch-all: `app/main.py:125-133` logs with traceback and returns `500 {"detail":"internal server error"}`.
- Domain errors: `EnrollmentError` → mapped status (`students.py:50-51`); `InferenceUnavailable`/`InferenceError` → 503; Redis down → 503 on camera control, degraded health, silent dedup/version fallbacks.
- Camera pipeline: **no per-iteration try/except** — one DB failure terminates the whole camera task (`camera_task.py:380-384`); cleanup in `finally` keeps Redis state consistent.
- Inference service: CPU fallback with retry on CUDA faults (`server.py:263-279`); stale requests dropped after `inference_request_timeout_s`.
- Frontend: 401 → auto-logout toast; 403 → error toast; per-card dashboard soft-fail to "?"; live-poll errors silenced to console (deliberate).

## 9. Module dependency notes

- No circular imports found by inspection (the "late import to avoid circular" comments in `services/attendance.py:110,134,203` are unnecessary — models never import services).
- `app/tasks.py` is loaded **only** by Celery `include=["app.tasks"]` (`celery_app.py:24`); the API dispatches `camera.run` by name string, so API and worker are import-decoupled (verified).
- `app/crypto.py` computes its Fernet instance **at import time** (100k-iteration PBKDF2), so every process importing `app.models` pays it once.

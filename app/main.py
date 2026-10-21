"""FastAPI application entrypoint.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Deliberately imports nothing GPU/CV2-related at module scope: the API process
never loads InsightFace (enrollment frames go through the shared inference
service over Redis), so it boots in milliseconds without CUDA.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import __version__
from .api import alerts, attendance, auth, cameras, courses, periods, students, system
from .api.ws import router as ws_router
from .config import settings
from .logging_config import configure as configure_logging
from .security import get_current_admin

configure_logging(settings)
logger = logging.getLogger("app.main")

DESCRIPTION = """
AI-based video attendance backend (REST + WebSocket).

**Pipeline**: RTSP/CCTV or video file -> CPU frame reader (sampling) ->
Redis RPC -> shared GPU inference service (InsightFace `buffalo_l`, batched
SCRFD detection + batched ArcFace embeddings) -> cosine matching -> attendance
sessions + audit logs -> WebSocket live feed.

Obtain a JWT via `POST /auth/token` and pass it as
`Authorization: Bearer <token>` on every request (unless `AUTH_REQUIRED=false`).
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    photos_root = settings.photos_root
    (photos_root / "students").mkdir(parents=True, exist_ok=True)
    logger.info(
        "api starting",
        extra={
            "version": __version__,
            "auth_required": settings.auth_required,
            "inference_mode": settings.inference_mode,
            "data_dir": str(photos_root),
        },
    )
    yield
    logger.info("api shutting down")


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``settings.max_upload_bytes`` (SEC-10).

    Pure ASGI so it sits in front of every route (including multipart
    enrollment and CSV import).  Enforces the declared ``Content-Length``;
    uvicorn rejects malformed/oversized chunked framing on its own.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = settings.max_upload_bytes
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                if value.isdigit() and int(value) > limit:
                    body = json.dumps(
                        {"detail": f"request body exceeds {limit} bytes"}
                    ).encode()
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return None
                break
        return await self.app(scope, receive, send)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "auth", "description": "JWT token issuance"},
        {"name": "students", "description": "Enrollment & face data management"},
        {"name": "cameras", "description": "Camera registry + pipeline control"},
        {"name": "attendance", "description": "Sessions, summaries, history, per-period records, overrides, live view, audit log"},
        {"name": "courses", "description": "Courses, teacher assignment and timetable scoping"},
        {"name": "periods", "description": "Timetable periods and attendance policy"},
        {"name": "system", "description": "Health & GPU telemetry"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # we use Bearer headers, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BodySizeLimitMiddleware)

app.include_router(auth.router)
app.include_router(students.router)
app.include_router(cameras.router)
app.include_router(courses.router)
app.include_router(attendance.router)
app.include_router(periods.router)
app.include_router(alerts.router)
app.include_router(system.router)
app.include_router(ws_router)


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def _service_metadata() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "health": "/system/health",
        "gpu_status": "/system/gpu-status",
        "websocket": "/ws/live-feed",
        "ui": "/",
    }


@app.get("/", include_in_schema=False)
def root():
    """Serve the web UI when bundled; metadata JSON otherwise."""
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return JSONResponse(_service_metadata())


@app.get("/api", tags=["system"], summary="Service metadata")
def api_metadata() -> dict:
    return _service_metadata()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# Serve enrolled reference photos at /media/<relative_path>.
# Previously an unauthenticated StaticFiles mount - anyone who could reach
# the API could download every student's reference photos (and StaticFiles
# would happily serve any file under DATA_DIR).  Now: same auth dependency
# as the rest of the API, and the resolved path is confined to photos_root.
photos_root = settings.photos_root
photos_root.mkdir(parents=True, exist_ok=True)


@app.get("/media/{file_path:path}", include_in_schema=False)
def media_file(
    file_path: str,
    _: str = Depends(get_current_admin),
) -> FileResponse:
    candidate = (photos_root / file_path).resolve()
    try:
        candidate.relative_to(photos_root.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(candidate)

# Serve the web UI assets (registered last so API routes take precedence).
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

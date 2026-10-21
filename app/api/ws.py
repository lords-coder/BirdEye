"""WebSocket /ws/live-feed - real-time recognition events.

On connect: replay the newest 100 backlog events (stored in Redis), then
stream the pub/sub channel ``events:live``.  Clients may send
``{"type":"ping"}`` and receive ``{"type":"pong"}``.  Auth (when
AUTH_REQUIRED=true) is a ``?token=<jwt>`` query parameter (admin accounts
only - the feed spans every camera and class, so teachers are rejected
with 4401 and must use the course-scoped REST views).

Resilience: the socket is decoupled from the Redis event bus.  When Redis
is down or restarts, the server keeps the connection open, sends
``{"type":"status", "ok": false, ...}`` notices and re-subscribes with
backoff - a bus outage costs missed events, never a dropped socket.  Only
an auth rejection (4401) or a genuine client disconnect ends the session.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials

from ..config import settings
from ..db import SessionLocal
from ..security import resolve_principal

logger = logging.getLogger("app.api.ws")

router = APIRouter()

_STATUS_OK = json.dumps({"type": "status", "ok": True, "detail": "live"})
_STATUS_DOWN = json.dumps(
    {"type": "status", "ok": False, "detail": "event bus unavailable - retrying"}
)


@router.websocket("/ws/live-feed")
async def live_feed(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    if settings.auth_required:
        # Resolve the FULL principal (signature + DB user + status), not just
        # the JWT signature: a validly signed token belonging to a deleted or
        # disabled account must not be able to stream live recognition
        # events for the whole institution.  Teachers are excluded (the feed
        # spans every camera and class; course-scoped views are REST).
        authorized = False
        if token:
            db = SessionLocal()
            try:
                creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
                principal = resolve_principal(creds, db)
                authorized = principal.is_admin
            except Exception:  # noqa: BLE001 - HTTPException or otherwise
                authorized = False
            finally:
                db.close()
        if not authorized:
            await websocket.close(code=4401, reason="unauthorized")
            return
    await websocket.accept()

    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=3
    )
    stop = asyncio.Event()

    async def receiver() -> None:
        """Client -> server: answer pings; any disconnect ends the session."""
        try:
            while not stop.is_set():
                data = await websocket.receive_text()
                if "ping" in data[:40]:
                    await websocket.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:  # noqa: BLE001 - treat any socket error as gone
            pass
        finally:
            stop.set()

    recv_task = asyncio.create_task(receiver())
    try:
        # ---- replay backlog once (index 0 = newest, send oldest first)
        try:
            backlog = await client.lrange(settings.events_recent_key, 0, 99)
            for raw in reversed(backlog):
                await websocket.send_text(raw)
        except Exception as exc:  # noqa: BLE001 - bus down at connect time
            logger.debug("backlog replay failed: %s", exc)

        # ---- subscribe loop: survive Redis restarts without dropping clients
        backoff = 1
        degraded = False
        while not stop.is_set():
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(settings.events_channel)
                if degraded:
                    degraded = False
                    try:
                        await websocket.send_text(_STATUS_OK)
                    except Exception:  # noqa: BLE001 - client went away
                        break
                backoff = 1
                async for message in pubsub.listen():
                    if stop.is_set():
                        break
                    if message.get("type") == "message":
                        try:
                            await websocket.send_text(message["data"])
                        except Exception:  # noqa: BLE001 - client went away
                            stop.set()
                            break
            except (WebSocketDisconnect, RuntimeError):
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - Redis down, resubscribe
                logger.warning("event bus lost, retrying subscription: %s", exc)
                if not degraded:
                    degraded = True
                    try:
                        await websocket.send_text(_STATUS_DOWN)
                    except Exception:  # noqa: BLE001 - client went away
                        break
                stopped = await _sleep_or_stop(stop, backoff)
                if stopped:
                    break
                backoff = min(backoff * 2, 30)
            finally:
                try:
                    await pubsub.unsubscribe(settings.events_channel)
                except Exception:  # noqa: BLE001 - best effort
                    pass
                try:
                    await pubsub.aclose()
                except Exception:  # noqa: BLE001 - best effort
                    pass
    finally:
        stop.set()
        recv_task.cancel()
        try:
            await recv_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - best effort
            pass


async def _sleep_or_stop(stop: asyncio.Event, delay: int | float) -> bool:
    """Sleep ``delay`` seconds; return True when ``stop`` fired first."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
        return True
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return stop.is_set()

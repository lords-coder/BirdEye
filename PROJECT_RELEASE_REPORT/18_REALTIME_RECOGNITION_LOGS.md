# 18 — Realtime Recognition Logs: Root Cause, Fix, and Live Verification

**Date:** 2026-10-21
**Severity:** HIGH (Recognition Logs page stale — new events invisible until a manual
page reload or a navigation away and back) → **RESOLVED**
**Scope:** `app/services/events.py`, `app/pipeline/camera_task.py`,
`app/api/attendance.py`, `app/static/app.js`, `app/static/index.html`,
`tests/test_realtime_logs.py` (new), `scripts/publish_synthetic_recognition_event.py`
(new), `scripts/check_launcher_mutex.py` (new).
**Not touched:** WebSocket route (`app/api/ws.py`), Redis client, WS auth /
token-revocation checks, outage recovery, attendance/presence logic, dedup gate,
DB schema or migrations, camera pipeline behavior, desktop launcher, `BirdEye.spec`.

---

## 1. Verified root cause (static trace + live reproduction)

The complaint was: *"a newly generated recognition event does not appear on the
Recognition Logs page until I refresh, open the camera feed, or navigate away and
back."* Three independent defects combined to produce exactly that behavior.

### 1.1 The log table was a one-shot fetch with no live path

`showSection("attendance")` → `loadAttendance()` fetched
`GET /attendance/logs?limit=200` **once** and rendered `#logs-body`
(`app/static/app.js`, old line 1077). Nothing re-ran it: there was no
subscription, no timer, and no mutation path into that `<tbody>`.

Meanwhile the global `/ws/live-feed` socket (already connected after login for
every non-teacher session) dispatched each message to exactly two consumers:
`monPushEvent(ev)` (monitor panel) and the `#events` debug box in the Live
section. The Recognition Logs table was never one of them.

This also explains the reported "workaround": navigating away and back re-runs
`showSection` → `loadAttendance()` → fresh fetch, so the page *looked* like it
"refreshed when you open the camera feed". No camera involvement whatsoever — it
was the section re-entry.

### 1.2 The bus published a strict subset of the table (persisted-but-not-published)

`camera_task._handle_faces()` writes a `RecognitionLog` row on **every
dedup-passing sighting** (and on every unknown face), but it only published a
`recognition` WebSocket event when `outcome.changed and action != "none"` —
i.e. only on attendance *state transitions*. A sighting that changed no state
(e.g. a student already inside, seen again) wrote a row that **no live consumer
could ever learn about**. Even a perfect live subscriber would have shown a
stale subset of the table. Events also carried no row identifier, so no client
could deduplicate WebSocket delivery against polling or against the backlog
replay that `/ws/live-feed` performs on every reconnect.

### 1.3 No cursor protocol on the REST endpoint

Without a `since_id`-style cursor, any reconnect or fallback refresh had to
re-download the whole history page, and could not distinguish "rows I already
have" from "rows I missed".

**Reported symptom mapping:** (1.1) explains the manual-refresh symptom and the
camera-feed misdiagnosis; (1.2) explains why the event feed could never be
trusted to mirror the table; (1.3) explains why recovery had no cheap option.

---

## 2. Fix

The existing single authenticated `/ws/live-feed` protocol was **extended**, not
replaced — no second real-time system was introduced.

### 2.1 Backend: one event per persisted audit row

`app/services/events.py` gained `recognition_log_event(...)` → payload:

```json
{ "type": "recognition_log", "ts": "<frame-capture time, ISO-8601 UTC>",
  "log_id": 42, "kind": "recognized|unknown",
  "camera": {"id":..,"name":..,"type":..,"location":..},
  "student": {"id":..,"name":..,"registration_no":..,"section":..} | null,
  "confidence": 0.9123, "inference_ms": 12.5 }
```

`app/pipeline/camera_task.py` publishes it at **all three** `RecognitionLog`
commit sites (unknown face, `DEDUP_LOG_ALL` suppressed sighting, normal
sighting). Each site now `db.flush()`es before `db.commit()` so `log_id` is the
real row primary key, and the publish happens **after** commit — a cursor poll
can therefore never see a row the event announced, nor announce a row that was
rolled back.

* `ts` is the row timestamp (frame-capture time), never the publish time.
* `log_id` is the dedupe key for clients (event vs. poll vs. backlog replay).
* The pre-existing `recognition` / `unknown_face` / `presence_state` events are
  **untouched** — the monitor panel and alerting keep their exact behavior.
* Unknown-face dedup/cooldown is unchanged: still one row (and one event) per
  dedup window, never a row per frame — pinned by a regression test.

### 2.2 API: cursor window on `GET /attendance/logs`

New optional `since_id` query parameter (admin-only, same as before):

* `since_id=N` → only rows with `id > N`, ordered **ascending by id** (monotonic
  in write order, unlike timestamps which can tie), so the client advances its
  cursor to the last item and can never skip a row.
* `has_more` (cursor mode: true only when rows came back *and* the last one is
  below the newest id — an empty window always ends reconciliation), plus
  `newest_id` (cheap `max()` on the primary key).
* `total` is counted **without** the cursor window, so "N of M" keeps meaning the
  whole filtered table while the console live-follows.
* Without `since_id` the endpoint behaves exactly as before (timestamp desc,
  `limit`/`offset`/`total`) — regression-tested.

### 2.3 Frontend: one socket, many listeners; one poll loop; one cursor

`app/static/app.js`:

* **Subscriber registry** `wsSubs` + `wsSubscribe(fn)` (returns an unsubscribe
  closure). The single `onmessage` now dispatches to subscribers and then to the
  existing consumers. Pages can no longer assign their own `onmessage`, so
  duplicate handlers are structurally impossible, and a reconnect cannot stack
  listeners. Synthetic `__open` / `__close` frames let consumers reconcile
  without any change to the server protocol.
* **`logLive` module** (mounted by `showSection("attendance")`, unmounted on
  leaving or logout):
  * **WS primary:** `recognition_log` events are inserted immediately, in
    id order, into `#logs-body`; rows are pixel-identical to polled ones
    (shared markup builder).
  * **Dedupe:** by `log_id` across *all* delivery paths (event, poll, backlog
    replay) — `seen` set is re-seeded from every full render (see §3, bug A).
  * **Single fallback loop:** one `setTimeout` chain (never overlapping
    requests — a single in-flight guard), 4 s cadence, **suspended while the
    socket is healthy (zero HTTP requests)**, cursor-based (`since_id`),
    exponential backoff on API failure (8 s → 16 s → 30 s cap), stopped on
    unmount. Full-history download only happens on mount / Refresh / when no
    cursor exists yet.
  * **Reconciliation:** on `__open` (reconnect) and on `status ok:true` (Redis
    bus recovered) a bounded cursor catch-up runs (max 10 pages) — anything
    published while the socket was down appears without a reload.
  * **Scroll courtesy:** while the newest row is on screen, rows insert at the
    top (natural follow). Once the user scrolls into history, rows are buffered
    behind a discreet `N new events — show` chip and **their scroll position is
    never moved**; the buffer flushes when they return to the top or click the
    chip. Buffer is capped (300) and rendered rows stay capped at 200, so a slow
    tab cannot build an unbounded queue or DOM.
  * **Timestamps** in `Asia/Kolkata` via `fmtIN()`, from the **backend event
    time**. Because SQLite returns UTC wall-clock without an offset while
    WebSocket events carry `+00:00`, naive values are pinned to UTC before
    parsing — otherwise a browser would read them as local time and render rows
    5.5 h apart depending on which path delivered them.
  * **4401 handling:** a rejected token sets `wsAuthFailed`, which blocks any
    further reconnect attempt with the same credentials (cleared on next login);
    the indicator states it instead of retrying forever.
  * **Indicator only visual change:** `#logs-dot` / `#logs-status` in the
    existing card head (`connected — live`, `reconnecting…`,
    `event bus unavailable — polling…`, `disconnected — retrying`,
    `live feed needs an admin login`). Table design, columns and page layout are
    unchanged.

---

## 3. Two defects found by live verification (and fixed)

Static tests could not catch these — they only surface on the wire. Both were
found by driving the **running** stack and both now have regression tests.

### Bug A — duplicate rows after a reconnect (user-visible)

**Observed live:** after a socket drop and reconnect, the page showed
`"5 of 3"` with row ids `["3","2","2","1","1"]`.

**Cause:** `logLiveFetchFull()` cleared the `seen` dedupe set and rendered rows
via `innerHTML`, but never re-seeded the set with the ids it had just put on
screen. `/ws/live-feed` replays the newest 100 backlog events on **every**
connect, so rows 1 and 2 — already rendered by the full fetch — passed the dedupe
check and were inserted a second time.

**Fix:** the full render re-seeds `seen` from exactly the ids it rendered.
**Verified after redeploy:** same scenario (drop socket → publish while down →
reconnect with full backlog replay) now yields exactly
`["4","3","2","1"]`, `"4 of 4"`, zero duplicates.

### Bug B — every section entry downloaded the log page twice

**Observed on the wire:** pairs of `?limit=200` requests ~4 s apart per mount.

**Cause:** `logLiveMount()` armed an immediate cycle *and* `loadAttendance()`
then called `logLiveLoadFull()` after its own summary fetch — two independent
authoritative fetches per entry.

**Fix:** the mount only subscribes, ensures the socket, sets the indicator and
arms the (suspended) loop; `loadAttendance()`'s explicit load is the single
authoritative fetch. **Verified:** `ff: 1` (exactly one full fetch per mount).

---

## 4. Tests

New file `tests/test_realtime_logs.py` — **22 tests**, all passing.

| Area | What is pinned |
|---|---|
| Event shape | `log_id`, `kind`, `ts` == row frame-capture time, null student for unknown faces |
| Pipeline publication | recognized row → published once with its row id; unknown row → `kind:"unknown"`, `student:null`; `DEDUP_LOG_ALL` suppressed sighting → still published; **dedup still yields 1 row/1 event over 5 frames** |
| API cursor | `since_id` returns only unseen rows, ascending; empty window ⇒ `has_more:false`; truncation ⇒ `has_more:true`; `total` excludes the cursor window; default shape/order unchanged; anonymous **and** teacher rejected |
| WebSocket | backlog replay delivers `recognition_log` to an admin; live pub/sub path forwards it; teacher still refused with **4401** |
| Frontend contract | consumes `recognition_log` keyed by `log_id`; single shared listener registry with unsubscribe; single fallback loop, no `setInterval(loadAttendance…)`; cursor (`since_id=`) used; Asia/Kolkata + naive-UTC pinning; backoff cap + `wsAuthFailed`; indicator markup present; **dedupe re-seed after full render**; **no double fetch on mount**; the log page contains no camera/worker/RTSP calls |

Full suite: **322 passed, 2 failed**. The 2 failures are
`tests/test_process_safety.py::TestSingletonMutex` — they acquire the **real**
Windows mutex `Global\BirdEyeLauncherMutex`, which is currently held by the
packaged `BirdEye.exe` (PID 32308, started 07:40, one window,
"BirdEye - AI Video Attendance") that is running on this machine. Evidence:
`scripts/check_launcher_mutex.py` reports the mutex *exists and is held by a live
launcher*. These two tests passed in the previous stage when no packaged app was
running; they are environment-dependent and unrelated to this change (nothing in
this task touches the launcher or the mutex). The running app was **not**
terminated.

---

## 5. Live end-to-end verification (running stack, deployed app)

Physical cameras are unavailable in this deployment (the two legacy camera rows
are metadata-only with `stream_path=NULL`; the Docker Postgres has no camera
rows at all — it contains 1 user, 0 cameras, 0 students, 0 logs). A genuine
sighting therefore could not be produced, so the event was injected through the
**real publication path** with `scripts/publish_synthetic_recognition_event.py`
(run inside the API container): it commits a `RecognitionLog` row exactly as
`_handle_faces` does, then publishes `recognition_log` through
`app.redis_client.publish_event` on the real Redis bus. No attendance state, no
student record, no camera connection and no alert is created.

Browser: `http://localhost:8000`, admin login, Attendance section. **No camera
preview was opened at any point, and no page reload was used to produce any
result below.**

| Step | Result |
|---|---|
| Baseline | indicator `connected — live`, socket OPEN, `0 of 0`, "No recognition events yet." |
| Publish #1 (`log_id=1`, 05:51:52 UTC) | row appeared **without reload/navigation** (`document.readyState` unchanged): `10:51:52` **IST**, student `—`, camera `synthetic-verification`, `0.424`, `7.5`; count `1 of 1`; cursor 0 → 1 |
| Socket drop, publish #2 while down | reconcile fetch recovered it; rows `["2","1"]`, **no duplicates**, cursor 2, status back to `connected — live`, `fails: 0` |
| Steady state | **zero** `/attendance/logs` requests over 12 s (and >7 min later) while the socket was healthy — the fallback is truly suspended |
| Leave → return to the section | listener count 0 after unmount, 1 after re-mount; one fetch; rows still correct, no duplicates |
| Console | no errors from the new code (only a pre-login `401 /students?limit=1` that predates authentication) |
| After fixes + redeploy | mount = **one** fetch; dedupe set re-seeded; socket drop → publish #4 while down → reconnect + full backlog replay → exactly `["4","3","2","1"]`, `"4 of 4"`, `cursor 4`, `seen 4`, `subs 1` |
| "New events" chip | hidden when idle; shows `1 new event — show` when rows are buffered; buffering leaves the DOM untouched (scroll position preserved); clean restore with no phantom rows |

**Synthetic rows left in the audit table (not deleted — audit rows are never
removed):** 4 rows with `student_id NULL`, `camera_id NULL`,
`confidence_score 0.4242`, `gpu_inference_time_ms 7.5`, timestamps
`2026-10-21T05:21:52Z`, `05:25:18Z`, `05:35:04Z`, `05:47:41Z`. They are
identifiable by those exact values. Everything else in the database is
untouched: no rows were deleted, truncated or reset, and no migration ran
(alembic head unchanged at `0006_camera_attendance`).

---

## 6. Deployment performed

`app/static` is baked into the image (`COPY . .`, no bind mount), so the fix is
only live after a rebuild:

1. `docker compose build api` — dependency layers **cached** (`pip install`
   layer untouched: same pinned `requirements.txt`, no package installed or
   upgraded), only the code layer rebuilt.
2. `docker compose up -d api worker` (compose also recreated `inference`, which
   shares the image). **postgres and redis were left running** — no data, no
   volumes, no configuration touched.
3. `/system/health`: `database: up`, `redis: up`; the GPU inference heartbeat
   re-registers ~30 s after its container starts (same as on any prior restart).

---

## 7. Limitations and follow-ups (not in scope, not done)

* **Real-camera verification is still outstanding.** Both camera rows are
  metadata-only (`stream_path=NULL`), so no genuine recognition can be produced.
  When cameras are configured, the expected observation is: an admin opens
  Attendance, a student is recognized by a running pipeline, and the row appears
  within ~1 s with no refresh — same path verified here with a synthetic event.
* **4 synthetic audit rows remain** in `recognition_logs` (§5). They can be
  removed manually if undesired; they were deliberately not deleted.
* The Live section's raw `#events` debug box skips `recognition_log` frames
  (the same occurrence already appears there as `recognition` /
  `unknown_face`), so it shows no duplicated entries.
* The log table keeps its existing newest-200 window (no pagination UI was
  added); `total` stays accurate as rows arrive.
* Default credentials `admin`/`admin` still need rotation after first login.
* The full regression suite still reports the 2 environment-dependent
  `TestSingletonMutex` failures while a packaged `BirdEye.exe` is running (§4).

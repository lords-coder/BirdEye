# 02 — Environment Validation

What exists on the validation machine, what was run for real, what is only
statically validated, and the exact commands to reproduce each claim on a
capable host.

## Machine profile (2026-10-14)

| Component | Present? | Notes |
|---|---|---|
| NVIDIA GPU | **yes** — RTX 5060 Laptop, 8151 MiB | driver ≥ R580-class; Blackwell sm_120 |
| Docker / compose | **no** (WSL also broken: `Wsl/CallMsi/Install/REGDB_E_CLASSNOTREG`) | compose validated statically only |
| Redis | not installed system-wide → **portable Redis 5.0.14.1** run from `%TEMP%\opencode\redis` on 127.0.0.1:6379 | validation only; compose ships `redis:7-alpine` |
| PostgreSQL | **no** | SQLite used; compose postgres runtime unverified |
| git | **not installed** | no repository/diff exists; `PROJECT_REPAIR_REPORT/08_CHANGE_MANIFEST.csv` (79 rows) remains the change record |
| Model weights | `~/.insightface/models/buffalo_l` (downloaded this phase, 5 ONNX files, 340 MB) | gitignored, never committed |

## 1. Redis (real)

```
# validation host (Windows, portable build)
%TEMP%\opencode\redis\redis-server.exe %TEMP%\opencode\redis\redis-validate.conf
%TEMP%\opencode\redis\redis-cli.exe ping          # -> PONG (5.0.14.1)
```
The codebase only needs Redis-5-era commands (LPUSH/BLPOP/SET NX EX/pipelines
— the RPC client comment documents this); a full LPUSH→BLPOP round-trip was
verified with redis-py 7.4.1. All slot claiming, dedup gates, WS pub/sub and
RPC traffic in the acceptance run flowed through this server.

## 2. Model weights (real; three defects found & fixed)

`scripts/download_models.py` had **never run on a clean machine** in this
project (dev setups used `SKIP_MODELS=1`), and failed twice in ways the audit
could not have seen statically:

1. it created `~/.insightface` but not `~/.insightface/models` → zip write
   died with ENOENT before the network was even tried;
2. the official `buffalo_l.zip` is **flat** (ONNX files at archive root) while
   insightface loads from `models/<pack>/` → extraction "succeeded" but
   verification found nothing.

Both fixed (mkdir-before-write + flat-layout recovery/relocation), regression
tests in `tests/test_readiness_changes.py`, and the weights are now present
and verified by the canary below.

```
python scripts/download_models.py     # idempotent; "OK - buffalo_l already at ..."
```

## 3. GPU inference (real)

```
python -m pip uninstall -y onnxruntime
python -m pip install onnxruntime-gpu==1.26.0        # pin documented in requirements.txt
python -m pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 \
    nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cusparse-cu12
python scripts/validate_inference.py
```
Observed evidence (verbatim from the canary):
```
[canary] embed: dim=512 (0.30s)
[canary] detect single face: 1 face(s), scores=[0.888] (0.52s -> 0.10s warm)
[canary] detect two people: 2 face(s) (0.16s)
[canary] ACTIVE session providers: ['CUDAExecutionProvider', 'CPUExecutionProvider']
[canary] GPU inference: ACTIVE (CUDA session)
```
The engine's built-in safety worked as designed: with mismatched CUDA-13
wheels it logged its "CUDA was requested but the session did not initialise"
warning and **fell back to CPU** (still 100 % detection-correct); with the
correct CUDA-12 wheels the session initialised on CUDA. `onnxruntime-gpu` is
*not* installed into `requirements.txt` for CPU hosts — the Dockerfile swaps
it via `ORT_GPU_VERSION` exactly as the acceptance run did natively.

Known one-time cost: first CUDA inference after boot ≈ 58 s (kernel/cuBLAS
initialisation); steady state is 10–16 ms/frame.

## 4. Shared services stack (real, native — no Docker)

```
python -m app.inference.server                          # GPU model, RPC over Redis
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
set CAMERA_SLOTS=2 && celery -A app.workers.celery_app:celery_app worker \
    --pool=threads --concurrency=4 \
    --queues=control,jobs,camera_slot_0,camera_slot_1 --prefetch-multiplier=1
```
`/system/health` → `{"database":"up","redis":"up","inference":"online"}`;
inference log confirms `"provider": "CUDAExecutionProvider"` with
`warmup_ms: 13406`. On Windows use `--pool=threads` (prefork is POSIX-only);
`scripts/run_worker.sh` keeps the documented Linux defaults.

## 5. Docker/Compose (static validation only — Docker unavailable)

```
python scripts/validate_compose.py     # 14 PASS, 1 WARN, 0 FAIL
```
Checks: core services present, image/build on every service, x-app anchor
fields, env_file existence, depends_on resolution, named-volume declaration,
compose-referenced scripts exist, healthcheck route exists in the code,
`ORT_GPU_VERSION` default consistency (Dockerfile == compose == 1.26.0),
Dockerfile ownership of writable mounts, single published port, GPU
reservations.

Two real defects found and fixed this way (**DEF-35**):
* `/models` (the `insightface_models` named volume) was never created/chowned
  in the image → fresh volumes mount root-owned and `appuser` cannot write the
  weights the engine tries to download;
* nothing in the compose file bootstrapped the weights → the inference
  service would race insightface's implicit auto-download (or fail offline).
  The inference service now runs `python scripts/download_models.py` first.

**WARN (accepted):** `SEED_VIDEO_PATH=/samples/videos/lecture.mp4` is not in
the repo — provide the clip under `./samples` or ignore the seed step.

Runtime status: **`docker compose up --build` has NOT been executed** (no
Docker here). The static validator plus the identical native stack that did
run give high confidence, but the compose runtime remains a required gate on a
Docker host:

```
docker compose config -q && docker compose up -d --build
docker compose ps && python scripts/smoke_test.py && python scripts/validate_e2e.py
```

## 6. Camera sources: file pipeline (real) / RTSP failure (synthetic endpoint)

* File-source cameras use the **same** pipeline, batching, RPC, dedup and
  attendance code as RTSP cameras; `scripts/make_test_video.py` builds a
  looping MP4 from enrolled photos and the acceptance run drove it end-to-end.
* **RTSP failure recovery** was validated against an unreachable endpoint
  (`rtsp://127.0.0.1:5554/nowhere`): the worker logged
  `failed to open source, retrying in 2.0s` → `4.0s` (exponential backoff),
  the pipeline stayed `running` with fresh heartbeats, and stop/delete
  succeeded afterwards.
* **Not validated:** behaviour against a *real* RTSP device (auth handshake,
  codec negotiation, mid-stream flapping, PoE/power loss). Requires camera
  hardware — recorded as a go-live gate in `00_READINESS_SUMMARY.md`.

## 7. Real-camera accuracy

**Not measured** — no real footage of enrolled people exists here and
inventing numbers is not an option. `scripts/check_accuracy.py` (explicit
credentials now mandatory) computes TAR/FAR/rank-1 + a threshold sweep for
signing off O-2 in `01_DECISION_REGISTER.md`:

```
set ADMIN_USERNAME=... && set ADMIN_PASSWORD=... && \
python scripts/check_accuracy.py --help
```

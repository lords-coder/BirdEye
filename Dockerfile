# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# AI Video Attendance backend image (README "Docker" section).
#
#   docker compose up --build
#
# GPU swap follows requirements.txt: insightface pulls the CPU `onnxruntime`
# + GUI `opencv-python`; both are replaced here with the GPU build and the
# headless wheel (ORT_GPU_VERSION is interpolated by docker-compose.yml / .env).
# Uses NVIDIA CUDA base image for GPU inference support.
# ---------------------------------------------------------------------------

FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04

ARG ORT_GPU_VERSION=1.26.0
ARG PYTHON_VERSION=3.11

# libglib2.0-0: OpenCV runtime dependency
# curl: container healthcheck (docker-compose.yml + HEALTHCHECK below)
# python3.11-venv: for virtual environment
# libcudnn9-cuda-12: cuDNN 9 for onnxruntime-gpu CUDA 12.x
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        curl \
        python3.11 \
        python3.11-venv \
        libcudnn9-cuda-12 \
    && rm -rf /var/lib/apt/lists/*

# Create python3 symlink (force overwrite if exists)
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/bin/python

# Install pip
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so code changes keep the package layer cached.
# { pip uninstall ... || true; } tolerates a package already being absent
# (insightface pins move) WITHOUT masking a failed install in the chain.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --disable-pip-version-check -r requirements.txt \
    && { pip uninstall -y onnxruntime opencv-python || true; } \
    && pip install --disable-pip-version-check \
        "onnxruntime-gpu==${ORT_GPU_VERSION}" opencv-python-headless

# Application code.  compose bind-mounts ./data -> /data and ./samples ->
# /samples (set DATA_DIR=/data, SEED_VIDEO_PATH=/samples/... in compose).
COPY . .

# Non-root user; /data must be writable for the ./data bind mount on Linux.
# /models backs the insightface named volume: the engine auto-downloads
# weights there on first boot, and a root-owned volume would fail that write
# under appuser (static finding from validate_compose.py - no Docker here).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data /models \
    && chown -R appuser:appuser /app /data /models
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/system/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

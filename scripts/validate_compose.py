#!/usr/bin/env python
"""Static validation of docker-compose.yml + Dockerfile (no Docker required).

This does NOT prove the stack runs - it proves the configuration is
internally consistent, which is what can be verified on machines without
Docker (the readiness pass machine has no Docker/WSL).  Runtime validation
commands are listed in PROJECT_READINESS_REPORT/02_ENVIRONMENT_VALIDATION.md.

    python scripts/validate_compose.py

Exit codes: 0 = no failures (warnings allowed), 1 = at least one failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

FAILURES: list[str] = []
WARNINGS: list[str] = []
PASSES: list[str] = []


def ok(msg: str) -> None:
    PASSES.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def fail(msg: str) -> None:
    FAILURES.append(msg)


def main() -> int:
    compose_path = ROOT / "docker-compose.yml"
    dockerfile_path = ROOT / "Dockerfile"
    if not compose_path.is_file():
        print("docker-compose.yml missing", file=sys.stderr)
        return 1
    compose = yaml.safe_load(compose_path.read_text())
    dockerfile = (
        dockerfile_path.read_text() if dockerfile_path.is_file() else ""
    )

    services: dict = compose.get("services", {}) or {}
    declared_volumes: set = set((compose.get("volumes", {}) or {}).keys())

    # ------------------------------------------------------------- services
    core = {"postgres", "redis", "api", "inference", "worker"}
    missing = core - set(services)
    if missing:
        fail(f"core services missing: {sorted(missing)}")
    else:
        ok(f"all core services present: {sorted(core)}")

    for name, svc in services.items():
        if not (svc.get("image") or svc.get("build")):
            fail(f"service {name}: neither image nor build defined")
    ok("every service has an image or build context")

    # ------------------------------------------------------- x-app anchor
    xapp = compose.get("x-app", {}) or {}
    for anchor_svc in ("api", "inference", "worker"):
        svc = services.get(anchor_svc, {}) or {}
        for field in ("image", "build", "env_file", "volumes"):
            # YAML merge keeps the anchor values under the child key only when
            # NOT merged; after merge they appear directly on the child.
            if field not in svc and field not in xapp:
                fail(f"{anchor_svc}: '{field}' missing in x-app anchor")
    ok("x-app anchor defines image/build/env_file/volumes for app services")

    # -------------------------------------------------------------- env_file
    env_file = (xapp.get("env_file") or [None])[0]
    if env_file:
        if (ROOT / env_file).is_file():
            ok(f"env_file {env_file} exists")
        else:
            warn(
                f"env_file {env_file} not present - run scripts/setup_dev.* "
                "before `docker compose up`"
            )

    # ------------------------------------------------------------ depends_on
    for name, svc in services.items():
        for dep in (svc.get("depends_on") or {}):
            if dep not in services:
                fail(f"service {name}: depends_on unknown service {dep!r}")
    ok("all depends_on references resolve")

    # --------------------------------------------------------------- volumes
    for name, svc in services.items():
        for vol in svc.get("volumes") or []:
            if not isinstance(vol, str):
                continue
            src = vol.split(":", 1)[0]
            if src.startswith("./"):
                continue  # bind mount
            if src not in declared_volumes:
                fail(f"service {name}: volume {src!r} not declared")
    ok("every named volume is declared in top-level `volumes`")

    # -------------------------------------------------------------- commands
    referenced = set(re.findall(r"scripts/[\w.]+", compose_path.read_text()))
    for ref in sorted(referenced):
        if not (ROOT / ref).is_file():
            fail(f"compose references missing file {ref}")
    if referenced:
        ok(f"compose-referenced scripts all exist: {sorted(referenced)}")

    # ----------------------------------------------------------- healthcheck
    api_cmd = " ".join(str(x) for x in (services.get("api", {}).get("healthcheck", {}) or {}).get("test", []))
    m = re.search(r"http://localhost:\d+(/[^\s\"']*)", api_cmd)
    if m:
        route = m.group(1)
        system_py = (ROOT / "app" / "api" / "system.py").read_text()
        if '"/health"' in system_py and route.startswith("/system/health"):
            ok(f"api healthcheck route {route} exists (app/api/system.py)")
        else:
            fail(f"api healthcheck route {route} not found in app/api/system.py")

    # --------------------------------------------------------------- Dockerfile
    if dockerfile:
        df_ort = re.search(r"ARG ORT_GPU_VERSION=([\d.]+)", dockerfile)
        dc_ort = re.search(r"ORT_GPU_VERSION: \"\$\{ORT_GPU_VERSION:-([\d.]+)\}\"",
                           compose_path.read_text())
        if df_ort and dc_ort and df_ort.group(1) == dc_ort.group(1):
            ok(f"ORT_GPU_VERSION default consistent ({df_ort.group(1)})")
        else:
            fail(
                "ORT_GPU_VERSION defaults differ between Dockerfile "
                f"({df_ort.group(1) if df_ort else '?'}) and compose "
                f"({dc_ort.group(1) if dc_ort else '?'})"
            )

        m = re.search(r"chown -R ([\w:]+) ([^\n\\]+)", dockerfile)
        if m:
            owned = set(m.group(2).split())
            for mount in ("/data", "/models"):
                if mount in owned:
                    ok(f"Dockerfile chowns writable mount {mount}")
                else:
                    fail(
                        f"Dockerfile does not chown {mount} - the named "
                        "volume copy-up would be root-owned and appuser "
                        "cannot write model weights"
                    )
        else:
            fail("Dockerfile: no chown line found for appuser")

    # ----------------------------------------------------------------- ports
    publishers = {
        name: (svc.get("ports") or [])
        for name, svc in services.items()
        if svc.get("ports")
    }
    if set(publishers) == {"api"}:
        ok("only `api` publishes host ports")
    else:
        warn(f"services publishing ports: {sorted(publishers)}")

    # ------------------------------------------------------------------- gpu
    for name in ("inference", "worker"):
        devices = (
            ((services.get(name, {}).get("deploy", {}) or {})
             .get("resources", {}) or {})
            .get("reservations", {}) or {}
        ).get("devices")
        if devices:
            ok(f"{name}: GPU device reservation present")
        else:
            warn(f"{name}: no GPU reservation (fine for CPU-only hosts)")

    # -------------------------------------------------------------- samples
    seed = re.search(r"SEED_VIDEO_PATH: \"([^\"]+)\"", compose_path.read_text())
    if seed and seed.group(1).startswith("/samples/"):
        local = ROOT / "samples" / seed.group(1).split("/samples/", 1)[1]
        if local.is_file():
            ok(f"seed video {seed.group(1)} present in ./samples")
        else:
            warn(
                f"seed video {seed.group(1)} not in the repo - provide it "
                "under ./samples or leave SEED_VIDEO_PATH unset"
            )

    # --------------------------------------------------------------- report
    for msg in PASSES:
        print(f"  PASS  {msg}")
    for msg in WARNINGS:
        print(f"  WARN  {msg}")
    for msg in FAILURES:
        print(f"  FAIL  {msg}", file=sys.stderr)
    print(
        f"\n{len(PASSES)} passed, {len(WARNINGS)} warnings, "
        f"{len(FAILURES)} failed (static checks only - run the stack with "
        "`docker compose up` on a Docker host for runtime proof)"
    )
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())

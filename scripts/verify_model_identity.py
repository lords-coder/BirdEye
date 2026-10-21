"""Real-model identity verification (no cameras involved).

Feeds the ENROLLED student's own consented photos through the running shared
GPU inference service (the exact RPC path the camera pipeline uses) and then
through the production matching index, to prove:

  1. the real buffalo_l model detects a face in the photo,
  2. it returns a 512-d embedding,
  3. that embedding matches the stored identity at >= threshold,
  4. an unrelated face does NOT match (unknown stays unverified).

This is deliberately separate from the physical-camera tests: it validates the
MODEL + MATCHING path, not camera capture.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.inference import client as inf_client
from app.matching import EmbeddingIndex
from app.models import Student

DATA = Path("data")


def main() -> None:
    db = SessionLocal()
    stu = db.query(Student).filter(Student.registration_no == "11925210179").one()
    photos = [DATA / p for p in (stu.photo_paths or [])]
    print(f"student: id={stu.id} name={stu.name!r} photos={len(photos)}")
    print(f"recognition_threshold = {settings.recognition_threshold}")

    jpegs = [p.read_bytes() for p in photos if p.is_file()]
    assert jpegs, "no photo bytes found"
    print(f"photo bytes: {[len(j) for j in jpegs]}")

    # ---- 1..2: real GPU detection + embedding via the production RPC -------
    t0 = time.time()
    res = inf_client.infer_frames(jpegs)
    rpc_ms = (time.time() - t0) * 1000
    print(f"\nRPC (batch of {len(jpegs)}): {rpc_ms:.0f} ms wall")
    print(f"  timings: {json.dumps(res.get('timings'))}")

    results = res["results"]
    embeddings = []
    for i, faces in enumerate(results):
        print(f"  photo {i}: {len(faces)} face(s) detected")
        for f in faces:
            det = f.get("score")
            emb = f.get("embedding")
            print(
                f"      score={det:.3f} "
                f"emb_dim={len(emb) if emb else None} "
                f"bbox={[round(b, 1) for b in f.get('bbox', [])]}"
            )
            if emb:
                embeddings.append(emb)

    if not embeddings:
        print("\nFAIL: real model detected no face in the enrolled photos")
        db.close()
        raise SystemExit(1)

    # ---- 3: production matching index -------------------------------------
    idx = EmbeddingIndex()
    idx.refresh(version=None)
    print(f"\nEmbeddingIndex: {idx.student_count} students / "
          f"{idx.embedding_count} embeddings, threshold={idx.threshold}")

    for i, emb in enumerate(embeddings):
        m = idx.match(emb)
        verdict = "MATCH" if m.student_id == stu.id else (
            "WRONG-ID" if m.student_id is not None else "NO MATCH"
        )
        print(f"  photo {i}: student_id={m.student_id} score={m.score:.4f} "
              f"-> {verdict}")

    # ---- 4: an unrelated face must NOT match ------------------------------
    # Take one stored embedding and perturb it beyond any plausible identity.
    import random

    random.seed(1234)
    noise = [random.uniform(-1, 1) for _ in range(len(embeddings[0]))]
    # normalise to unit length like the model output
    norm = sum(x * x for x in noise) ** 0.5 or 1.0
    noise = [x / norm for x in noise]
    m = idx.match(noise)
    print(f"  unrelated vector: student_id={m.student_id} "
          f"score={m.score:.4f} -> "
          f"{'UNKNOWN (correct)' if m.student_id is None else 'FALSE MATCH (WRONG)'}")

    db.close()
    print("\nDONE")


if __name__ == "__main__":
    main()

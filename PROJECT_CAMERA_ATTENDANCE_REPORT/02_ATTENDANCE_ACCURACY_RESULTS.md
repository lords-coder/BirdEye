# Attendance Accuracy Results (02)

Tool: `scripts/check_accuracy.py` (supported implementation), run
2026-10-15 against the live native stack (CUDA buffalo_l, Redis RPC).

## Measured (curated public-figure gallery — NOT production accuracy)

- Genuine pairs n=23 (1 demo identity ×9 transforms; Obama ×7, Biden ×7
  cross-session): mean cosine 0.795, min 0.588.
- Impostor pairs n=70: mean 0.004, max 0.138 → clean margin +0.450.
- Threshold sweep 0.25–0.55 @ production threshold 0.40:
  **TAR 100%, pair-FAR 0%, open-set correct 31/31, unknowns rejected 8/8,
  rank-1 23/23, false rejects 0, wrong-identity 0.**
- Latency: embed RPC mean 292 ms (p50 249, p95 623, n=29); detection RPC
  (2 faces) 2349 ms; 0 probes with no face found.
- Enrollment consistency ≥0.911 min pairwise cosine.

## Ground truth & conditions

Enrolled 3 photos/identity through the production endpoint + real quality
gate; probes are downloaded public photos (transform + cross-session
variants) plus 8 unenrolled faces for rejection. No RTSP/encoding in the
path; small n; well-lit frontal portraits — **favorable conditions that do
not represent classroom lighting, distance, angles, or occlusion.**

## Proposed acceptance thresholds (FOR INSTITUTIONAL APPROVAL — not approved)

- Production threshold stays 0.40 unless a classroom-sampled ROC says
  otherwise; require impostor max < genuine min (clean margin) on ≥50
  enrolled identities photographed **in situ**; unknown-face acceptance 0
  on ≥20 unenrolled walk-throughs; end-to-end camera→event latency budget
  <5 s at 5 enrolled/frame. None of these are university-approved until
  signed — no silent policy choice is made here.

## Real-camera behavior still unknown

Recognition under classroom lighting/distance/angles/obstruction, TAR/FAR
on enrolled students via RTSP, camera→attendance latency over the stream —
all PENDING the authenticated-stream step in 01.

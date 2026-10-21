# Camera Acceptance Results (01)

## Registered configurations (metadata only — verified in DB)

| id | name | host:port | username | role / type | enabled | password stored |
|---|---|---|---|---|---|---|
| 1 | Classroom Exterior | 10.10.150.178:554 | user | doorway_exterior / exit | true | **no** |
| 2 | Classroom Interior | 10.10.149.188:554 | user1 | classroom_interior / entry | true | **no** |

## Device interrogation (no credentials used, attempted, or stored)

- TCP 554: **OPEN** on both hosts (re-verified this phase).
- RTSP OPTIONS/DESCRIBE without auth: `401 Unauthorized`, `WWW-Authenticate:
  Digest` on both → a live RTSP service demanding authentication.
- 8 standard paths (`/`, `/Streaming/Channels/101`, `/h264`, `/live`,
  Dahua/Axis/ONVIF variants): uniformly 401 → **path existence cannot be
  established pre-auth**. The stream path must come from the vendor docs or
  the cameras' web config interfaces (HTTP 80 OPEN on both).
- App-path classification through `camera_test.test_source()` against both
  real hosts (empty-password build): `ok=false, status=error`,
  "TCP connected but the stream could not be opened - verify the username,
  password and RTSP stream path" — honest, no leak (checked for password
  substrings: absent).

## Credential workflow (verified working, no secret handled by implementer)

Create/update accepts password → Fernet-encrypted at rest; `has_password`
only in responses; redacted `display_url`; diagnostics redacted
(regression-tested, 28 + 1 tests). Inline-credential legacy URLs migrate
on write. **No password was pasted, logged, or stored during this phase.**

## Authenticated stream: BLOCKED (user step, ~5 min)

On the deployment host with the stack up, in BirdEye as admin → Cameras →
edit each camera → enter password → Test connection → expect `connected`
+ latency → preview → Save. Then confirm back: run
`POST /cameras/{id}/test` (stored encrypted password is used server-side;
no credential in any command). Until then: **no stream claimed good merely
because port 554 is open.**

## Placement & direction (verified as far as possible without video)

Roles configured per the only documented device info (interior observes
classroom; exterior observes doorway). With appearance-only observations
the engine infers direction from placement (interior = entered,
doorway-after-inside = left); exterior-only sightings raise review, never
auto-exit. No state-machine change was warranted: no reproducible defect
was found. True motion-vector direction remains out of scope (limitation
carried in 06). Placement acceptance (walk-through scenarios) is a
runbook item pending the authenticated stream.

# handoff — System Specification

Specification for the **handoff** (repository name: Cyborg---SmartGlove) simulation-first control studio, the roadmap for the physical glove integration, and the persistence migration to the existing Resume Fit Supabase project.

Companion documents:

- [README.md](./README.md) — how to run the current Phase 1 software.
- [design.md](./design.md) — the accepted frontend design contract.

This document is the result of a documentation pass over the repository at commit `3b595bc` ("Initial handoff app") plus product decisions confirmed with the project owner. It does not claim any test run, hardware measurement, or database state that was not directly observed in the sources listed below. No application code, Supabase object, or hardware behavior was changed to produce it.

Sources reviewed:

| Source | Used for |
|---|---|
| `README.md` | Project intent, safety boundary, integration next steps |
| `backend/app/main.py` | API behavior, Pydantic models, SQLite schema, simulation adapter |
| `backend/app/hardware_boundary.py` | `ServoTransport` protocol, `gated_counts()` gate |
| `backend/tests/test_api.py` | Existing test coverage (nine tests) |
| `backend/requirements.txt` | Backend dependency contract |
| `frontend/src/main.tsx` | UI behavior and wording |
| `frontend/src/index.css` | Frontend visual foundations |
| `frontend/tailwind.config.js`, `frontend/package.json`, `frontend/vite.config.ts` | Build, fonts, proxy |
| `C:\Users\paulr\Downloads\IMG_7158.mp4` | Observational evidence only (see §1.3) |

Status labels used throughout:

- **[Implemented]** — exists in this repository today and is described from source.
- **[Proposed]** — agreed direction; not built yet.
- **[Required before physical testing]** — a condition that must be met before any servo is driven.

---

## 1. System Overview & Goals

### 1.1 What exists today

The project has two distinct halves, and they must not be conflated:

1. **A physical glove prototype (team-reported, separately built).** The team reports that finger movement works and that wrist hardware is still in development. Two controller test scripts are saved in `hardware/reference`; the Feetech-tuna library is included under `backend/feetech_tuna`. Finger assignments, wiring, and physical limits still need confirmation.
2. **This repository — a simulation-first control studio [Implemented].** It models six finger channels, stores macros and calibration, broadcasts simulated telemetry, and defines a safety gate for a future movement driver. It now has a **read-only** serial status route for IDs 1–6. There is **no route that can switch movement to hardware mode**.

The glove builder supplied the ID-to-finger mapping below. The next physical milestone is **checking it during supervised off-body calibration and measuring safe limits**, then integrating guarded movement through the safety boundary (§5).

Stack:

| Layer | Technology | Status |
|---|---|---|
| Frontend | React 19 + TypeScript + Tailwind CSS 3 + Vite 6, single file `frontend/src/main.tsx` | Implemented |
| Backend | FastAPI + Pydantic v2, Python 3.11+, served by Uvicorn | Implemented |
| Persistence | SQLite via the `sqlite3` standard library | Implemented |
| Persistence (target) | One new table in the existing Resume Fit Supabase (Postgres) project | Proposed |
| Telemetry | WebSocket `/ws/telemetry`, full-snapshot broadcasts | Implemented |
| Hardware path | Read-only status for IDs 1–6; no physical movement path | Movement remains gated |

Views already present in the UI: **Dashboard, Manual Control, Macro Studio, AI Vision, Calibration, Devices**.

### 1.2 Audience

- **Developers** running both servers locally and extending the API/UI.
- **Supervised bench-test operators** who will eventually run the glove on a bench, unloaded and off-body first, with a physical E-stop present.

There is no user authentication and no multi-user support. The API is intended for local development only.

### 1.3 Physical prototype — evidence separation

The following categories must never be merged in future documentation or demos:

| Category | Content |
|---|---|
| **Team-reported capability** | Finger movement works on the physical glove; wrist hardware is in development. Not independently verified here. |
| **Visual observation (video, ~51 s)** | A worn glove with finger guides, tendon routing, and a forearm-mounted actuator assembly; early frames show relatively extended fingers beside a can; later frames show fingers curled around the can; manual line adjustments and wearer participation are visible. |
| **Verified measurement** | A read-only serial scan found IDs 1–6 responding at roughly 999–1004 counts, 12.4–12.5 V, and 33–38 °C, with no servo reporting motion at the time. These are status readings, not calibration or proof of controlled grasp. |

No statement in this document or in the UI may present team-reported or observed behavior as measured performance. Calibration values remain unverified placeholders until measured (§5).

### 1.4 Goals

1. Keep the simulation studio honest and useful: spec, simulate, calibrate, review, and test without moving real hardware.
2. Migrate persistence from SQLite to the Resume Fit Supabase project without disturbing Resume Fit's existing tables, auth configuration, or policies.
3. Integrate the prototype's proven finger controller behind the §5 safety gate.
4. Add vision, AI proposals, display surfaces (smart glasses, enclosure screen), and wrist hardware **without creating a second actuation path**.

### 1.5 Explicit non-goals for phase 1

- No real servo writes or hardware movement mode. Read-only serial status is implemented.
- No autonomy: vision detections, AI text commands, or voice input may never bypass human confirmation and the safety gate (§6).
- No claim that placeholder calibration values are safe or measured.
- No wrist channel assignment until wrist hardware has its own specification (§2.4).

---

## 2. Channel & Kinematic Layout

### 2.1 Authoritative software mapping [Implemented]

Defined in `SERVO_NAMES` in `backend/app/main.py:18` and mirrored by the frontend servo list at `frontend/src/main.tsx:11`:

| Channel | Movement |
|---|---|
| 1 | Pinky finger |
| 2 | Ring finger |
| 3 | Middle finger |
| 4 | Index finger |
| 5 | Thumb flexion (up/down) |
| 6 | Thumb opposition (side to side) |

The builder supplied this mapping after the read-only scan found IDs 1–6. It supersedes the older document's ID order. **Prerequisite [Required before physical movement]:** verify the physical tendon assignment off-body and measure safe limits. Serial discovery confirms IDs, not which finger each motor pulls.

### 2.2 Pose contract [Implemented]

A pose is exactly six integer percentages, one per channel, each from 0 to 100. It is never a subset. JSON keys arrive as strings (`"1"`–`"6"`) and are coerced to integer keys by Pydantic (`dict[int, int]`), and are serialized back to strings in JSON responses.

### 2.3 Percentage-to-count mapping [Implemented, simulation only]

```
count = round(open_count + (closed_count - open_count) × percentage / 100)
```

This formula appears in two places that must stay in sync:

- `SimulationAdapter` does **not** apply it; simulation works in percentages directly.
- `gated_counts()` in `backend/app/hardware_boundary.py:43` applies it for the future physical path.

Calibration count bounds:

- The model permits raw counts from **0 to 4095** (`CalibrationInput` field constraints). These are **model bounds, not verified operating limits**.
- `open_count` and `closed_count` must each lie within `[min_count, max_count]`, `min_count < max_count`, and `|closed_count − open_count| ≥ 10`.
- `direction` must match the sign of `closed_count − open_count` (`increasing` or `decreasing`).
- Thumb opposition (channel 6) has its own calibrated motion endpoints, like every other channel. It is not coupled to thumb flexion's endpoints.

### 2.4 Wrist exclusion rule [Required]

Do **not**:

- assign any wrist movement to channels 1–6,
- assume the wrist actuator count, axes, gear ratio, or controller,
- extend the six-channel pose contract to include wrist fields before a separate wrist hardware contract is specified and verified.

Preserving all six finger channels is a confirmed product decision. Wrist execution stays unavailable until separately specified (§7, Phase 7).

---

## 3. Data Models & Schema

### 3.1 Current SQLite persistence [Implemented]

- Database path defaults to `backend/handos.sqlite3` and is overridden by the `HANDOS_DB` environment variable (`backend/app/main.py:19`). The file is gitignored. Tests set `HANDOS_DB` to `backend/tests/test.sqlite3` before importing the app.
- `init_db()` runs in the FastAPI lifespan hook: it creates tables, seeds five sample poses when `macros` is empty, and inserts missing unverified calibration rows (`INSERT OR IGNORE`).

Tables (exact current DDL):

```sql
CREATE TABLE IF NOT EXISTS macros (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL,
  kind        TEXT NOT NULL,
  steps       TEXT NOT NULL,   -- JSON array of Step objects
  created_at  REAL NOT NULL,   -- Unix epoch seconds
  updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration (
  servo_id INTEGER PRIMARY KEY,
  data     TEXT NOT NULL       -- JSON CalibrationInput object
);
```

There are **no** separate `poses`, `device_sessions`, `vision_history`, or `approvals` tables today. Poses are macros with `kind = "pose"` and exactly one step.

Seeded sample poses (when `macros` is empty), each `kind = "pose"` with one 700 ms step, positions ordered channels 1–6:

| Name | Description | Positions |
|---|---|---|
| Open hand | All fingers relaxed | `[0, 0, 0, 0, 0, 0]` |
| Gentle fist | A light, even curl | `[75, 75, 75, 75, 75, 65]` |
| Point | Index extended, other fingers curled | `[80, 80, 80, 0, 75, 55]` |
| Pinch | Thumb meets index | `[0, 0, 0, 45, 55, 75]` |
| Peace sign | Index and middle extended | `[80, 80, 0, 0, 70, 50]` |

Seeded calibration for every channel: `open_count = 2048`, `closed_count = 2448`, `min_count = 1800`, `max_count = 2600`, `direction = "increasing"`, `speed = 30`, `verified = false`. These are **placeholder values for interface testing only**.

### 3.2 Macro validation rules [Implemented]

Pydantic models `Pose`, `Step`, `MacroInput` (`backend/app/main.py:22-52`):

| Field | Rule |
|---|---|
| `name` | String, 1–60 characters, validated **before** trimming; stored after `strip()`. A whitespace-only name passes validation and stores as an empty string — the UI prevents this but the API does not. |
| `description` | String, defaults to `""`, maximum 240 characters. |
| `kind` | `"pose"` or `"sequence"`. |
| `steps` | 1–30 items. |
| pose constraint | `kind == "pose"` requires exactly one step. |
| `duration_ms` | Integer, 100–10,000 per step. |
| positions | Exactly six integer percentages 0–100 (via `Pose`). |

Pydantic behavior worth distinguishing from the file contract in §3.3:

- Extra JSON fields are **ignored**, not rejected.
- Numeric strings are coerced to integers where lossless.
- Integer-keyed dicts are coerced from string keys.
- A 422 response is returned for constraint violations, with FastAPI's standard `detail` array.

Import/export behavior:

| Behavior | Detail |
|---|---|
| Export | `GET /api/macros/export` returns `{"version": 2, "macros": [...]}` with only `name`, `description`, `kind`, `steps` — **no IDs, no timestamps**. Version 2 uses the builder-supplied ID order. |
| Export cap | **None currently.** An export can contain more than 100 records. |
| Import | `POST /api/macros/import` accepts version 1 or 2 and **at most 100** macros; version 1 positions are remapped by finger, and a larger list fails with 422. |
| Empty import | Currently allowed; returns `{"imported": 0}`. |
| Import semantics | Always **creates copies with new UUIDs**; it never overwrites an existing macro by ID. |
| Round-trip asymmetry | Export has no 100-record cap while import does; a database with >100 macros produces an export that cannot be re-imported in one file. The roadmap fixes this asymmetry (§7, Phase 2). |

### 3.3 Version-2 file contract (JSON Schema)

This schema describes the **normalized export/import file**, which is stricter than what Pydantic accepts (see §3.2). JSON object keys must be strings, so channel keys are `"1"`–`"6"`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "handoff-macros.v2.json",
  "title": "handoff macro export, version 2",
  "type": "object",
  "additionalProperties": false,
  "required": ["version", "macros"],
  "properties": {
    "version": { "const": 2 },
    "macros": {
      "type": "array",
      "maxItems": 100,
      "items": { "$ref": "#/$defs/macro" }
    }
  },
  "$defs": {
    "percent": { "type": "integer", "minimum": 0, "maximum": 100 },
    "positions": {
      "type": "object",
      "additionalProperties": false,
      "required": ["1", "2", "3", "4", "5", "6"],
      "properties": {
        "1": { "$ref": "#/$defs/percent" },
        "2": { "$ref": "#/$defs/percent" },
        "3": { "$ref": "#/$defs/percent" },
        "4": { "$ref": "#/$defs/percent" },
        "5": { "$ref": "#/$defs/percent" },
        "6": { "$ref": "#/$defs/percent" }
      }
    },
    "step": {
      "type": "object",
      "additionalProperties": false,
      "required": ["positions", "duration_ms"],
      "properties": {
        "positions": { "$ref": "#/$defs/positions" },
        "duration_ms": { "type": "integer", "minimum": 100, "maximum": 10000 }
      }
    },
    "macro": {
      "type": "object",
      "additionalProperties": false,
      "required": ["name", "description", "kind", "steps"],
      "properties": {
        "name": { "type": "string", "minLength": 1, "maxLength": 60 },
        "description": { "type": "string", "maxLength": 240 },
        "kind": { "enum": ["pose", "sequence"] },
        "steps": {
          "type": "array",
          "minItems": 1,
          "maxItems": 30,
          "items": { "$ref": "#/$defs/step" }
        }
      },
      "allOf": [
        {
          "if": {
            "properties": { "kind": { "const": "pose" } },
            "required": ["kind"]
          },
          "then": {
            "properties": { "steps": { "maxItems": 1 } }
          }
        }
      ]
    }
  }
}
```

Example export file (also a valid import):

```json
{
  "version": 1,
  "macros": [
    {
      "name": "Gentle fist",
      "description": "A light, even curl",
      "kind": "pose",
      "steps": [
        {
          "positions": { "1": 75, "2": 75, "3": 75, "4": 65, "5": 75, "6": 75 },
          "duration_ms": 700
        }
      ]
    },
    {
      "name": "Test wave",
      "description": "Two steps",
      "kind": "sequence",
      "steps": [
        { "positions": { "1": 20, "2": 20, "3": 20, "4": 20, "5": 20, "6": 20 }, "duration_ms": 200 },
        { "positions": { "1": 70, "2": 70, "3": 70, "4": 70, "5": 70, "6": 70 }, "duration_ms": 200 }
      ]
    }
  ]
}
```

Schema caveat: the schema's `maxItems: 100` matches the **import** constraint. Export is not capped, so the schema is the contract for a file that can be imported; an export containing more than 100 macros is valid output but not valid input until the cap asymmetry is resolved.

### 3.4 Proposed Supabase persistence

Use the existing **Resume Fit** Supabase project. Add exactly **one new table** for all saved SmartGlove records rather than a table per record type.

#### Table: `public.cyborg_records`

| Column | Type | Purpose |
|---|---|---|
| `id` | `uuid` | Primary key |
| `record_type` | `text` | `macro`, `calibration`, or `device_session` |
| `record_key` | `text` | Stable key within the type |
| `data` | `jsonb` | Validated JSON object |
| `revision` | `integer` | Positive version counter for concurrent-edit checks |
| `created_at` | `timestamptz` | Creation time |
| `updated_at` | `timestamptz` | Last update time |

Proposed DDL (documentation only — **not applied**; see §3.5 prerequisites):

```sql
create table public.cyborg_records (
  id          uuid primary key default gen_random_uuid(),
  record_type text not null check (record_type in ('macro', 'calibration', 'device_session')),
  record_key text not null,
  data        jsonb not null,
  revision    integer not null default 1 check (revision > 0),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (record_type, record_key)
);

alter table public.cyborg_records enable row level security;
```

Rules:

- **Enforce** the allowed `record_type` values and the `(record_type, record_key)` uniqueness constraint in Postgres, not only in application code.
- **Each macro is its own record.** `record_key` is the macro UUID (the same UUID currently used as the SQLite `macros.id`). `data` holds the `MacroInput` shape (`name`, `description`, `kind`, `steps`).
- **Calibration uses channel keys `"1"`–`"6"`** as `record_key`, one record per channel, `data` holding the `CalibrationInput` object.
- **Device sessions use session UUIDs** as `record_key`, `data` holding the session payload.
- **Typed payloads [Proposed]** for future evidence (validated by Pydantic before write, never free-form):
  - calibration evidence: measurement method, raw measurements, timestamps, operator identity, unloaded/off-body conditions, tooling;
  - resting positions: the measured rest pose per channel;
  - device identity: controller model/firmware, glove serial, wiring revision;
  - E-stop verification: who tested it, when, that it interrupts servo power independently of software;
  - consumed approvals: approval ID, action hash, device session ID, calibration revision, single-use marker.
- **RLS**: enable row level security and **deny browser-role access** (no permissive policies for `anon`/`authenticated`). All access goes through the FastAPI backend using server-side credentials (service key), never from the browser. Follow Supabase's secure-data guidance (consulted: `https://supabase.com/docs/guides/database/secure-data`, `https://supabase.com/docs/guides/database/postgres/row-level-security`).
- **Preserve Resume Fit**: do not alter its existing tables, authentication configuration, or policies. The new table is additive.
- **Credentials stay server-side** in FastAPI environment configuration. Never ship Supabase keys to the frontend.
- **Keep simulation state and live telemetry in FastAPI.** Do not replace motor telemetry with Supabase Realtime, and do not persist per-tick simulation state.

#### Concurrency model [Proposed]

- Updates read `revision`, then write with `where id = :id and revision = :expected`, incrementing `revision`.
- A zero-row update means a conflicting edit: return 409 to the client; the client re-fetches and re-applies.
- Deletes are plain deletes; no soft-delete column is planned unless required later.

#### SQLite → Supabase migration [Proposed, repeatable]

1. Read every row from SQLite `macros` and `calibration`.
2. Upsert each macro as `('macro', id, data, revision = 1, created_at, updated_at)`; **preserve the existing macro UUIDs** (they are already uuid4 strings) and the epoch timestamps converted to `timestamptz`.
3. Upsert each calibration row as `('calibration', servo_id::text, data, …)`; **preserve the JSON values verbatim**, including `verified: false`.
4. Optionally create one initial `device_session` record documenting the migration (operator, timestamp, source DB path hash).
5. Verify: row counts per type, per-macro JSON equality (normalized key order), and calibration equality.
6. The script is idempotent (safe to re-run) and refuses to run when the target already contains records of the same keys unless an explicit `--overwrite` flag is given.
7. **No silent fallback to SQLite after switching.** Once `SUPABASE_URL`/credentials are configured, a database outage must surface as an error, and:
   - persistence-dependent actions (macro CRUD, calibration writes, session records) are **rejected**, not silently written elsewhere;
   - the **software stop remains locally available** because stop state lives in FastAPI memory;
   - reads may serve cached/last-known data only if the UI clearly labels it as stale.

### 3.5 Supabase prerequisites (open items)

These do not block drafting but block implementation:

- The Supabase CLI exists at `C:\Users\paulr\scoop\apps\supabase\current\supabase.exe` but was not on the PATH during investigation; direct execution worked, and project listing failed because no access token was configured.
- The exact Resume Fit project reference and its existing schema have **not** been verified.
- Before applying anything: authenticate the CLI, inspect the Resume Fit schema and policies, capture a migration plan that adds only `public.cyborg_records`, and confirm RLS denies browser roles.

---

## 4. API & Telemetry Contracts

Backend: FastAPI app `"handoff"`, version `0.1.0`. CORS allows `http://127.0.0.1:5173` and `http://localhost:5173` only. There is **no authentication**; keep it bound to localhost.

Error shapes:

- `HTTPException` (e.g. 404, 409): `{"detail": "<message>"}`.
- Validation (422): FastAPI's standard `{"detail": [{"type", "loc", "msg", "input", ...}]}`.

### 4.1 Route summary

| Method | Path | Success | Common failures |
|---|---|---|---|
| GET | `/api/status` | 200 snapshot | — |
| GET | `/api/servos` | 200 list | — |
| GET | `/api/macros` | 200 list | — |
| POST | `/api/macros` | **201** macro | 422 |
| PUT | `/api/macros/{macro_id}` | 200 macro | 404, 422 |
| DELETE | `/api/macros/{macro_id}` | **204** (no body) | 404 |
| GET | `/api/macros/export` | 200 file | — |
| POST | `/api/macros/import` | 200 `{"imported": n}` | 422 |
| POST | `/api/macros/{macro_id}/play` | 200 accepted | 404, 409 |
| POST | `/api/control/pose` | 200 accepted | 409, 422 |
| POST | `/api/control/stop` | 200 snapshot | — |
| POST | `/api/control/reset` | 200 snapshot | — |
| GET | `/api/calibration` | 200 list | — |
| PUT | `/api/calibration/{servo_id}` | 200 calibration | 404, 422 |
| POST | `/api/vision/detection` | 200 ack | 422 |
| WS | `/ws/telemetry` | snapshot stream | — |

### 4.2 Route detail

#### `GET /api/status` → 200

Returns the live simulation snapshot (identical shape to the WebSocket message, §4.3).

#### `GET /api/servos` → 200

```json
[
  { "id": 1, "name": "Pinky finger" },
  { "id": 2, "name": "Ring finger" },
  { "id": 3, "name": "Middle finger" },
  { "id": 4, "name": "Index finger" },
  { "id": 5, "name": "Thumb flexion (up/down)" },
  { "id": 6, "name": "Thumb side-to-side" }
]
```

#### `GET /api/macros` → 200

Array of stored macros, ordered by `updated_at` descending:

```json
[
  {
    "id": "0b6f6c1e-8b1a-4c5e-9f2d-9a1c2b3d4e5f",
    "name": "Gentle fist",
    "description": "A light, even curl",
    "kind": "pose",
    "steps": [
      { "positions": { "1": 75, "2": 75, "3": 75, "4": 65, "5": 75, "6": 75 }, "duration_ms": 700 }
    ],
    "created_at": 1760000000.0,
    "updated_at": 1760000000.0
  }
]
```

#### `POST /api/macros` → **201**

Request: `MacroInput` (name, description, kind, steps). Response: the stored macro including `id`, `created_at`, `updated_at`. Validation failures → 422.

#### `PUT /api/macros/{macro_id}` → 200

Same request shape as create. Replaces all fields, keeps original `created_at`, updates `updated_at`. Unknown ID → 404.

#### `DELETE /api/macros/{macro_id}` → **204**

No response body. Unknown ID → 404.

#### `GET /api/macros/export` → 200

```json
{ "version": 1, "macros": [ { "name": "…", "description": "…", "kind": "pose", "steps": [ { "positions": { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0 }, "duration_ms": 700 } ] } ] }
```

IDs and timestamps are omitted. No record cap (§3.2).

#### `POST /api/macros/import` → 200

Request: `{"version": 1, "macros": [ … at most 100 … ]}`. Response: `{"imported": 3}`. Always creates copies with new UUIDs; an empty list is accepted and returns `{"imported": 0}`.

#### `POST /api/macros/{macro_id}/play` → 200

- Unknown macro → 404.
- Latched software stop → 409 `{"detail": "Software stop is latched"}`.
- Otherwise: any currently running simulation task is **cancelled**, a new playback task starts, and the response returns immediately:

```json
{ "accepted": true, "mode": "simulation" }
```

The response means *accepted*, not *finished*. Progress arrives over the WebSocket.

#### `POST /api/control/pose` → 200

Request:

```json
{ "positions": { "1": 50, "2": 0, "3": 25, "4": 0, "5": 0, "6": 0 } }
```

Must include all six channels, 0–100, else 422. Latched stop → 409. Runs a **350 ms** simulated move (cancelling any running task) and returns `{"accepted": true, "mode": "simulation"}`.

#### `POST /api/control/stop` → 200

Latches the software stop: `stopped: true`, `playing: false`, `target` snapped to current positions, `last_event` set to `"Software stop latched"`, running task cancelled, new snapshot broadcast. Response is the snapshot. While latched, every movement route returns 409.

#### `POST /api/control/reset` → 200

Clears the software stop (`stopped: false`, `last_event` = `"Software stop reset"`) and broadcasts. Reset does **not** start motion. For the future hardware path, reset must not rearm hardware or resume any interrupted action (§5).

#### `GET /api/calibration` → 200

Array merging stored JSON with the channel name:

```json
{ "servo_id": 1, "name": "Pinky finger", "open_count": 2048, "closed_count": 2448, "min_count": 1800, "max_count": 2600, "direction": "increasing", "speed": 30, "verified": false }
```

#### `PUT /api/calibration/{servo_id}` → 200

`servo_id` must be 1–6 else 404. Request is `CalibrationInput` (§3.2 constraints, raw counts 0–4095) → 200 with the saved object, or 422.

#### `POST /api/vision/detection` → 200

Request:

```json
{ "label": "bottle", "confidence": 0.9, "source": "external" }
```

`label` 1–80 characters; `confidence` 0–1; `source` is `"demo"` (default) or `"external"`. Response is always:

```json
{ "received": true, "action": "none" }
```

The event updates `sim.vision` (latest only, with `received_at` epoch), sets `last_event` to `"Detection event received; no macro was triggered"`, and broadcasts. **It never triggers motion** (§6); a test enforces this.

### 4.3 WebSocket `/ws/telemetry`

- On connect: the server accepts, registers the listener, and sends one initial snapshot.
- Then it pushes a full snapshot on every state change: each simulation tick (~50 ms cadence while moving), play start/end, stop/reset, vision event.
- Inbound messages are read and ignored.
- Snapshot shape (field values shown for a fresh server):

```json
{
  "mode": "simulation",
  "positions": { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0 },
  "target":    { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0 },
  "stopped": false,
  "playing": false,
  "last_event": "Simulation ready",
  "vision": null,
  "devices": {
    "controller": "Not connected",
    "glasses": "Not connected",
    "camera": "Browser preview only"
  }
}
```

Field notes:

| Field | Meaning |
|---|---|
| `mode` | Always `"simulation"` today. |
| `positions` | Current simulated percentage per channel. These are **commanded-model positions**, not measured servo positions. |
| `target` | Last commanded target; snapped to current positions on stop. |
| `stopped` | Software stop latched. |
| `playing` | A playback task is active. |
| `last_event` | Human-readable status line. |
| `vision` | Latest detection `{label, confidence, source, received_at}` or `null`. |
| `devices` | Static strings in the simulation snapshot: controller `"Not connected"`, glasses `"Not connected"`, camera `"Browser preview only"`. Live controller readings come separately from `/api/hardware/status`. |

JSON channel keys are **strings** on the wire; the frontend relies on JavaScript object-key access (`positions[i+1]`).

Rate and reliability facts to design against:

- Motion broadcasts are approximately **50 ms** apart, one per interpolation tick (`duration_ms // 50` ticks). This is **not a guaranteed realtime rate**.
- There is **no periodic idle heartbeat**, **no server timestamp**, and **no sequence number** on snapshots. An idle connection receives nothing after the initial snapshot.
- The frontend reconnects every **2.5 seconds** after close and replaces its entire `Status` state with each message; it also performs one REST refresh on mount.
- Future telemetry **[Proposed]** must add: a monotonically increasing sequence number, a server timestamp, explicit separation of **commanded targets** from **measured positions** (once hardware exists), and device link status. Frontend logic must be able to detect stale snapshots after these fields exist.

---

## 5. Safety Gate & Hardware Boundary

`backend/app/hardware_boundary.py` defines the future contract. It is intentionally **not wired to any route**.

### 5.1 Transport protocol [Implemented as a contract only]

```python
class ServoTransport(Protocol):
    def read_position(self, servo_id: int) -> int: ...
    def write_position(self, servo_id: int, count: int, speed: int) -> None: ...
    def disable_torque(self) -> None: ...
```

### 5.2 Current gate behavior [Implemented, not connected]

`gated_counts(percentages, calibration, approval, stopped)` returns `{servo_id: (count, speed)}` and refuses otherwise. Checks, in order:

1. Software stop is not latched.
2. `approval.approved` and `approval.physical_estop_tested_this_session` are both `true`.
3. Percentages and calibration each cover all six channels.
4. Every calibration has `verified == true`.
5. Each percentage is within 0–100.
6. Each mapped count lies within `[min_count, max_count]`.

Failures raise `PermissionError` (safety conditions) or `ValueError` (percentage range).

### 5.3 Known gaps (must be closed before physical use) [Required before physical testing]

- **Approval booleans are reusable.** `ActuationApproval` is a frozen dataclass with two booleans; nothing consumes or invalidates it. One-use behavior is not enforced.
- **Session verification/evidence is not persisted.** "E-stop tested this session" is an in-memory assertion with no record of who, when, or how.
- **Calibration verification is operator-asserted.** `verified: true` is a checkbox, not a measurement record.
- **The gate is not connected to physical execution.** No movement route calls it. A separate read-only status adapter exists.
- **Driver behavior is unverified.** Speed units, torque behavior on stop, and what `disable_torque()` actually does are unknown until the prototype controller is inspected.

### 5.4 Required future behavior [Required before physical testing]

1. **Inspect and reuse the prototype's known-good controller functions** (read/write/torque-off) behind `ServoTransport`; do not rewrite protocol code from scratch.
2. **Measure calibration unloaded and off-body.** Each channel's open, closed, and hard limits; verify direction and speed per channel; record evidence in the calibration payload (§3.4).
3. **Verify a physical E-stop that interrupts servo power independently of software.** Test with the glove off the body. A software stop cannot substitute for a power cut.
4. **Require explicit single-action human approval** for each physical pose or sequence step — one confirmation per action, from the UI.
5. **Bind approvals** to the immutable action, device session, and calibration revision; consume each exactly once.
6. **Invalidate approvals** on stop, disconnect, restart, or any calibration change.
7. **Recheck safety immediately before execution** (call the gate again at execution time, not only at plan time).
8. **Reset cannot automatically resume movement or rearm hardware.** After a stop, the operator starts a new, separately approved action if and only if that is intended.
9. **Treat transport faults and partial writes as stopping conditions**, not warnings: latch the software stop, attempt torque-off, surface a fault state.
10. **Wrist execution remains unavailable** until separately specified and verified (§2.4).
11. Keep the gate callable only on the server; the browser can never compute counts or approve an action directly.

### 5.5 Individual-channel verification procedure [Required before wearable trials]

Run per channel, on the bench, **unloaded and off-body**, before any supervised wearable test:

1. Command open → halfway → closed → open.
2. Repeat the cycle **three times**.
3. Observe and record:
   - **return consistency** — does the channel return to the same open position each cycle (counts, once measured positions are available);
   - **slack** — unspooled tendon or lost motion on reversal;
   - **neighboring fingers** — unintended coupled motion;
   - **available measured positions** — `read_position` values if the controller supports reading.
4. Any inconsistent return, unexpected neighbor motion, or transport fault latches the stop and blocks the next action.

Ordering rule: off-body calibration measurements and the power-interruption (E-stop) verification come **before** any supervised wearable testing.

---

## 6. Vision Integration Contract

### 6.1 Current behavior [Implemented]

- `POST /api/vision/detection` records `{label, confidence, source, received_at}` in memory (`sim.vision`), updates `last_event`, broadcasts, and responds `{"received": true, "action": "none"}`.
- Only the **latest** event is retained; there is no history and no event-inspector UI yet.
- The AI Vision page's browser camera is **preview-only**: it does not analyze frames, does not upload them, and displays a "NO OBJECT DETECTOR RUNNING" badge.
- A backend test asserts that a detection leaves `positions` unchanged.

### 6.2 Proposed flow [Proposed]

```
Detector → event review → explicit object-to-macro mapping
         → human confirmation → execution safety boundary (§5)
```

Rules:

- A **configurable review threshold** (proposed default **0.80**) controls which events are *surfaced for review*. It is a review filter, never an authorization.
- **Confidence never authorizes motion.** No confidence value, including 1.0, may trigger a macro automatically.
- **Stale or unmapped events are rejected as executable proposals.** An event without a saved, explicit object→macro mapping cannot be confirmed into motion.
- Every proposal passes the same validation, confirmation, and safety gate as manual actions; a proposal is a *draft command*, not a command.
- **AI text and voice commands use the same path** (proposal → validation → human confirmation → safety boundary). They are not a parallel execution channel.
- Detections remain logged as events even when ignored.

### 6.3 Event retention [Proposed]

- Persist detection events (bounded, with timestamps and source) so review is possible after the fact; today's single in-memory slot is insufficient.
- Keep the "latest event" field in telemetry for the dashboard.
- Retain the current guarantee: receiving an event never changes `positions`, `target`, or `playing`.

---

## 7. Implementation Roadmap

Ordered phases. Status labels: **[Implemented]**, **[Proposed]**, **[Required before physical testing]**.

### Phase 1 — Simulation studio [Implemented]

Six-channel simulation with interpolation; macro CRUD with export/import; SQLite persistence; software stop latch; WebSocket telemetry; six views (Dashboard, Manual Control, Macro Studio, AI Vision, Calibration, Devices); read-only servo status; `ServoTransport` contract and `gated_counts()` defined but unwired for movement.

### Phase 2 — Persistence migration and simulation hardening [Proposed]

1. Verify Supabase prerequisites (§3.5): CLI auth, Resume Fit project reference, existing schema and policies.
2. Apply `public.cyborg_records` (§3.4) with RLS denying browser roles; keep Resume Fit untouched.
3. Write and test the repeatable SQLite→Supabase migration preserving macro UUIDs, timestamps, and calibration values.
4. Add revision-based optimistic concurrency; 409 on conflicting edits.
5. No silent fallback: database outages reject persistence actions; software stop stays local.
6. Fix export/import cap asymmetry (>100 macros); decide whether export should paginate or the import cap should rise.
7. Telemetry hardening: sequence numbers, server timestamps, idle heartbeat; keep full-snapshot shape but make staleness detectable.
8. Frontend/backend development continues (feature work, not redesign — the current design is accepted; see [design.md](./design.md)).
9. Upgrade the frontend build toolchain; README notes seven npm audit advisories (five high, two moderate) in build tooling.
10. Behavior tests for persistence, migration idempotency, concurrency conflicts, database outages, stale telemetry, and cancellation semantics (§8).

### Phase 3 — Hardware baseline verification [Required before physical testing]

1. Obtain and inspect the existing Feetech/ST3215 controller code; identify known-good read, write, and torque-off functions.
2. Confirm physical servo IDs and tendon assignments against the software channel map (§2.1).
3. Measure each channel unloaded and off-body; populate Calibration with measured values and evidence; set `verified` only on measurement.
4. Verify the physical E-stop interrupts servo power independently of software, off-body.
5. Run the individual-channel procedure (§5.5) and record results.
6. Document driver speed units, torque behavior, and stop behavior from the actual controller.

### Phase 4 — Guarded finger-controller integration [Proposed]

1. Implement `ServoTransport` using the proven controller functions.
2. Wire a deliberate physical-arm flow that calls `gated_counts()` immediately before **every** pose or sequence step, with fresh single-action approval.
3. Bind and consume approvals; invalidate on stop, disconnect, restart, or calibration change.
4. Treat transport faults and partial writes as stopping conditions (latch stop, torque-off attempt, fault state).
5. Extend telemetry to distinguish commanded targets from measured positions.
6. Frontend: guarded physical-action UI on existing pages, matching the accepted design.

### Phase 5 — Vision, AI proposals, and automation [Proposed]

1. Object detection: detector → `/api/vision/detection` with `source: "external"`; event review UI; configurable review threshold (default 0.80).
2. Explicit object-to-macro mappings with human confirmation before any motion.
3. Realtime macros and a **visual timeline**: playback progress, step boundaries, scrub/preview states in the UI, backed by telemetry (not a new execution path).
4. Chatbot for hand control: structured command proposals, never direct execution.
5. Optional voice input: transcribed commands enter the same proposal pipeline.
6. Provider adapters: **Gemini, OpenRouter, and Hugging Face** behind one internal interface. Requirements:
   - credentials and all provider calls stay **server-side**;
   - structured proposals only (typed JSON validated by Pydantic), with capability reporting per provider;
   - explicit timeouts and failure handling (unavailable provider degrades to text-only assistance, never to motion);
   - no provider SDK specifics are assumed in this spec; adapter details are chosen at implementation time;
   - provider output can never call movement routes directly.

### Phase 6 — Displays and smart glasses [Proposed]

1. Smart glasses connected to the glove system: glasses camera feeds the detector; glasses display shows device status only.
2. Screen mounted on the back of the servo enclosure: shows the same read-only status surface.
3. Device-neutral display contract (no model-specific assumptions until hardware models are supplied): a display is described by its capabilities (size, input, camera), not by brand.
4. Device-neutral display states:

| State | Meaning |
|---|---|
| Simulation | No physical actuation path is active |
| Disconnected | Link to the glove/controller is down |
| Awaiting approval | An action is proposed and blocked on human confirmation |
| Moving | An approved action is executing |
| Stopped | Software stop latched (and/or physical E-stop engaged) |
| Fault | Transport fault, partial write, or device error |

5. **Display/glasses status must not create another actuation path.** Displays are read-only status surfaces plus, at most, inputs that produce *proposals* through §6's pipeline.

### Phase 7 — Wrist hardware [Proposed]

1. Receive the wrist hardware specification (actuator count, axes, sensors, limits).
2. Define a **separate** wrist contract — new channels, new gate inputs, wrist-specific calibration and E-stop conditions. Do not map wrist motion onto channels 1–6.
3. Verify off-body, then integrate under the same approval/stop rules as fingers.
4. Wrist execution stays unavailable until steps 1–3 are complete.

---

## 8. Testing

### 8.1 Existing coverage (nine tests)

`backend/tests/test_api.py` contains nine tests; coverage as inspected:

| Test | What it asserts |
|---|---|
| `test_macro_round_trip_and_import_export` | Create → 201 with ID; PUT renames; export has `version: 2`; import returns `{"imported": 1}`; delete → 204 |
| `test_user_servo_ids_and_legacy_import_keep_finger_meaning` | Confirms the builder-supplied labels and remaps version 1 imports by finger |
| `test_existing_macros_are_remapped_once_on_database_upgrade` | Confirms stored gestures migrate once using SQLite `user_version` |
| `test_fresh_sample_gestures_use_correct_finger_ids` | Confirms Point and Pinch seed with the corrected finger IDs |
| `test_stop_latches_and_reset_allows_simulation` | Stop latches; pose while latched → 409; reset unlatches; pose accepted with `mode: "simulation"` |
| `test_calibration_gate_and_vision_never_moves` | Invalid calibration → 422; valid → 200; a vision detection returns `action: "none"` and leaves positions unchanged |
| `test_telemetry_connects` | WebSocket initial snapshot has `mode: "simulation"` and six channels |
| `test_future_hardware_gate_requires_every_condition` | `gated_counts` raises without E-stop confirmation, while stopped, and with an unverified channel; returns `(500, 20)` when all conditions hold |
| `test_read_only_hardware_status_uses_six_ids_and_never_writes` | Six IDs are pinged, status is read, the port closes, and no write API is used |

The nine backend tests and frontend build passed after the mapping update. There is no frontend test suite and no linter.

### 8.2 Future tests [Proposed]

Persistence and migration:

- SQLite→Supabase migration preserves macro UUIDs, timestamps, and calibration values exactly; re-running is idempotent.
- Revision-conflict write returns 409 and does not corrupt the record.
- Database outage: macro/calibration writes fail loudly; software stop still latches and resets; no silent fallback writes.
- RLS verification: browser roles cannot read or write `cyborg_records`.

Telemetry and simulation:

- Snapshot cadence matches `duration_ms // 50` ticks; no idle heartbeat exists today (test documents the current contract until Phase 2 changes it).
- Staleness detection once sequence/timestamp fields exist.
- Cancellation: a new play/pose cancels the previous task; no stale task overwrites the new target; `playing` returns to false.
- Disconnect mid-stream: server drops the listener; reconnect (2.5 s) delivers a fresh full snapshot.
- Partial write / transport fault on the future driver latches stop and calls torque-off (fault-injection with a fake transport).

Safety gate:

- Approvals are single-use; replay of a consumed approval is rejected.
- Approval bound to action hash, session, and calibration revision; changing any invalidates it.
- Stop, disconnect, and restart invalidate outstanding approvals.
- Calibration change invalidates approval and blocks execution until re-verified.
- AI/vision bypass attempts: text commands, voice transcripts, detection events, and provider responses that try to reach movement routes or the driver are rejected; motion can only occur through explicit human-approved execution.

Hardware baseline (once the prototype controller is available):

- Individual-channel test (§5.5): open → halfway → closed → open, three repetitions, asserting return consistency, acceptable slack, and no unintended neighboring movement.
- E-stop power-interruption test: servo power is cut independently of software while motion is commanded.
- Off-body calibration and E-stop verification are prerequisites for, and are re-checked before, any supervised wearable test.

---

## Appendix A — Naming

The UI and API present the product as **handoff**. Persistence artifacts keep the historical name **handos** (`HANDOS_DB`, `handos.sqlite3`, `handos-macros.json` on export). This is intentional; do not rename storage keys without a migration.

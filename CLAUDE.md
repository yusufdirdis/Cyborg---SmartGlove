# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"handoff" (DB/env names still use the older "handos") is a simulation-first control studio for a six-servo smart glove. A FastAPI backend simulates six servo channels and persists macros and calibration in SQLite. A single-file React 19 + TypeScript + Tailwind 3 UI drives it. The user connected the controller and authorized a status check. The Devices page now has a guarded one-servo, 10-count hardware test that is locked by default. Manual controls, macros, and vision remain simulation-only.

## Commands

Backend (Python 3.11+). Run from `backend/`:

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt -r requirements-vision.txt   # vision file is optional (local camera only)
uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m pytest -q                                   # all tests
python -m pytest -q tests/test_api.py::test_telemetry_connects   # single test
```

Frontend (Node 20+). Run from `frontend/`:

```bash
npm install
npm run dev -- --port 5173     # Vite proxies /api and /ws to 127.0.0.1:8000
npm run build                  # tsc -b && vite build; this doubles as the type check
```

There is no linter or frontend test suite. Run both servers at once. The UI is served at http://127.0.0.1:5173 and the API docs at http://127.0.0.1:8000/docs.

## Architecture

- **`backend/app/main.py`** holds the whole API: the Pydantic models (`Pose`, `Step`, `MacroInput`, `CalibrationInput`), the SQLite helpers, the `SimulationAdapter`, and all routes.
  - The SQLite DB path comes from the `HANDOS_DB` env var. The default is `backend/handos.sqlite3`, which is gitignored. `init_db()` runs in the lifespan hook and seeds five sample poses plus six unverified calibration rows.
  - Persistence goes through `backend/app/store.py`: `SQLiteStore` by default, or `PostgresStore` when `SUPABASE_DB_URL` is set. The Postgres store uses the `handoff` schema in the shared Resume Fit Supabase project (`backend/supabase/handoff_schema.sql`, applied by hand, never `supabase db push`) and connects as `handoff_app`.
  - There is no silent fallback: a DB outage raises `StoreUnavailable`, which returns 503. Stop and reset never depend on the store.
  - Macro and calibration `PUT` take an optional `revision`; a mismatch returns 409.
  - Audit logs (`record()`, `record_move()`, `record_proposal()` in `main.py`) are best-effort, written after the action, and never read back to drive motion.
  - `tests/test_store.py` covers these rules. Its real-Postgres test runs only with `HANDOFF_TEST_PG_URL`.
  - Macros store `steps` as a JSON text column. Calibration stores each servo's `CalibrationInput` as a JSON blob.
- **`SimulationAdapter`** is a module-level singleton named `sim`.
  - It interpolates positions in 50 ms ticks and broadcasts a full `snapshot()` to every WebSocket listener on each tick.
  - Only one playback task runs at a time (`sim.task`). A new play or pose cancels the current one.
  - The software stop latches. While it is latched, movement routes return 409 until `/api/control/reset` is called.
- **Telemetry**: `/ws/telemetry` pushes the snapshot dict. The frontend replaces its whole `Status` state on each message and reconnects every 2.5 s.
- **Cloud demo deployment**: Railway uses `HANDOFF_CLOUD=1`, an optional `HANDOFF_PASSCODE` (unset means a public demo), and comma-separated `HANDOFF_CORS_ORIGINS`. Cloud mode always blocks calibration writes and hardware routes; provider keys come from each visitor's browser per request (`assistant.set_visitor_settings()` from `main.visitor_provider_keys`, a ContextVar). They are never stored, logged or replaced by a server key, and `tests/test_visitor_keys.py` enforces this. Physical hardware writes must remain disabled.
- **`backend/app/hardware_boundary.py`** defines the hardware contract and separate gates for full poses and a single 10-count jog.
  - It contains the `ServoTransport` protocol and `gated_counts()`.
  - `gated_counts()` maps 0–100 % poses to servo counts. It refuses unless every servo is verified-calibrated, a physical E-stop has been tested, explicit approval was given, no software stop is latched, and every target is within the measured limits.
- **`backend/app/servo_status.py`** uses the supplied Feetech-tuna library to ping and read IDs 1–6. `/api/hardware/status` opens the port for one read-only scan, then closes it. The Devices page displays the readings; this route has no motor writes.
- **`backend/app/hardware_control.py`** reuses the terminal bench preflight, then uses `gated_jog_count()` and the Feetech transport for a one-use, one-servo command. It is locked unless `HANDOFF_ENABLE_HARDWARE_WRITES=1`, and it rejects unverified or placeholder calibration. Never set this flag for automated tests against real hardware.
  - Manual Control also stages multi-servo targets. `/api/hardware/group/preview` and `/execute` send one synchronized packet, `/api/hardware/live` handles streamed target edits, and `/api/hardware/torque/enable` aligns stored targets before holding torque. All of them sit behind the same writes flag and health checks.
- **`backend/app/live_positions.py`** reads present positions for the Dashboard's 3D glove (Simulated / Physical toggle). It is read-only and never enables torque or sends a target. Set `HANDOFF_LIVE_POSITIONS=0` to turn it off; `tests/conftest.py` does this so tests never poll USB.
- The assistant and Live tools must never call any `/api/hardware/*` write route.
- **`backend/app/vision/`** is the local YOLO and MediaPipe service (`/api/vision/*`). Its camera routes are local-only and it never sends motion commands; imitation targets are simulation-only. Its heavy libraries live in `backend/requirements-vision.txt`, are imported lazily, and are not installed on the hosted API.
- **Motor writes:** `HANDOFF_ENABLE_HARDWARE_WRITES` defaults to on for the local API, but `writes_enabled()` always returns False when `HANDOFF_CLOUD=1`. Otherwise Stop and chat would become local-only for public visitors. `tests/test_deploy.py` covers this.
- **`backend/app/assistant.py`** holds the chat/voice assistant: Gemini, OpenRouter, Hugging Face and Offline providers, the JSON reply contract, and single-use proposals (60 s TTL). `/api/assistant/chat` only drafts proposals; the sole direct action is a stop through `latch_stop()`. `/api/assistant/proposals/{id}/confirm` calls the simulation `control_pose`/`play_macro`. The assistant must never gain a hardware command type; `tests/test_assistant.py` enforces this. `/api/assistant/transcribe` takes a raw audio body (≤ 4 MB) and returns Gemini's transcript text only; it must never trigger motion. The live routes: `/api/assistant/live/session` is local-only unless cloud mode and the demo passcode are enabled; it mints a single-use `v1alpha` ephemeral token with the instruction and tools locked into its constraints. `/api/assistant/live/proposal` validates one Live tool call into the same proposal shape and never moves anything; the Live tools must never include a hardware tool, and `tests/test_live.py` enforces this. Keys load from `backend/.env` (`HANDOS_ENV_FILE` overrides the path; tests use a scratch file). The Settings page uses local `/api/settings/providers` routes to write that file through `set_key` and return masked key hints only; cloud mode blocks those routes so provider keys are managed in Railway variables. Never return a full key.
- **`frontend/src/main.tsx`** contains the entire UI: types, the `api()` fetch helper, `App`, and every page component (Dashboard, Manual Control, Macro Studio, AI Vision, AI Assistant, Calibration, Devices, Settings).
  - The components are dense one-liners. Match that style rather than splitting files unless asked.
  - The TS types in this file mirror the Pydantic models by hand. Keep the two in sync when either one changes.

## Domain invariants

- There are exactly six servos, with IDs 1–6: pinky, ring, middle, index, thumb flexion, thumb opposition. The glove builder supplied this physical mapping. Every pose must include all six as integer percentages from 0 to 100. JSON keys arrive as strings and Pydantic coerces them.
- The first release used another ID order. `init_db()` remaps stored macro steps once using SQLite `user_version`; version 1 macro imports are remapped, while version 2 exports use the current ID order. Calibration records stay on their physical IDs.
- A sequence has 1–30 steps, each lasting 100–10,000 ms. A `pose` macro has exactly one step.
- User-selected app calibration counts run from 0 to 4000. This cap is separate from each glove cord's measured safe travel. The constraints are:
  - min < max
  - open and closed both fall within [min, max]
  - |closed − open| ≥ 10
  - `direction` matches the sign of closed − open
  - `jog_limits_verified` records direct-test bounds separately from full gesture `verified`; one does not imply the other.
  
  The seeded values are placeholders and are marked unverified.
- Vision detections (`POST /api/vision/detection`) only record the event. They must **never** trigger motion, and a test enforces this.
- The read-only serial status route and one-servo preview are authorized. The narrow physical jog remains locked by default even after user-reported direct-test limits are recorded; a fresh off-body setup and physical power-cut check are required for a one-use move. Do not enable it merely to demo the UI. Any future full pose or sequence driver must use `ServoTransport` and `gated_counts()` before every physical step. See the README's "Safety boundary" and "Integration next steps".

## Tests

`backend/tests/test_api.py` sets `HANDOS_DB` to `tests/test.sqlite3` before it imports the app. That file persists between runs and is gitignored. The tests use `TestClient(app)` as a context manager so the lifespan hook (`init_db`) runs.

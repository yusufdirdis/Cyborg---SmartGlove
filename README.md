# handoff — Phase 1

A local control studio for a six-servo smart-glasses hand glove. The React/TypeScript/Tailwind interface has Dashboard, Manual Control, Macro Studio, AI Vision, AI Assistant, Devices, and Settings sections. FastAPI provides persistent SQLite macros and calibration data, six-channel simulation, stop handling, WebSocket telemetry, live servo status, and guarded direct position control for each servo.

## Servo map

| ID | Movement | Mount location in the user's photo |
|---|---|---|
| 1 | Pinky finger | Bottom right |
| 2 | Ring finger | Middle right |
| 3 | Middle finger | Top right |
| 4 | Index finger | Top left |
| 5 | Thumb flexion (up/down) | Middle left |
| 6 | Thumb opposition (side to side) | Bottom left |

This ID mapping was supplied by the glove builder after all six servos responded to a read-only status check. Earlier documents and app releases used a different order. Saved gestures are remapped once during database upgrade so their finger meaning stays the same; calibration counts remain attached to their physical servo IDs. The app accepts calibration numbers only from **0 to 4000**, as requested by the builder. This is a numeric cap; the safe travel of an attached cord is recorded separately for each servo.

The mount locations were supplied later by the user, viewed with the glove above the two-column servo block. The white cords pass through holes in the round black servo horns and run toward the glove; this is an attached-tendon setup, so the unloaded bench jog is not appropriate as photographed.

## Run locally

Use Python 3.11+ and Node.js 20+ in two terminals.

**Terminal 1 — API**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — UI**

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

Open <http://127.0.0.1:5173/>. The API docs are at <http://127.0.0.1:8000/docs>. The Vite server proxies `/api` and `/ws` to the local API. Keep both terminals running.


On first launch, `backend/handos.sqlite3` is created with five editable sample poses and six unverified calibration records. To use another database path, set `HANDOS_DB` before starting the API. Macro export downloads a version 3 JSON file with raw 0–4000 counts; older version 1 and 2 exports are converted when imported. Existing saved percentages are migrated to counts once. Sequences have up to 30 steps with 100–10,000 ms simulation duration per step.

### Supabase (optional)

Macros, calibration and audit logs can live in Supabase instead of SQLite. They use a separate `handoff` schema inside the shared Resume Fit project, so that project's own tables are not touched.

1. Apply [`backend/supabase/handoff_schema.sql`](backend/supabase/handoff_schema.sql) once in the Supabase SQL editor, then run `alter role handoff_app with password '…'`.
2. Set `SUPABASE_DB_URL` in `backend/.env` to the **session pooler** URL for user `handoff_app.<project-ref>`. The template is in `backend/.env.example`.
3. From `backend/`, run `python -m app.migrate_to_supabase` to copy your SQLite macros and calibration, then start the API.

The audit logs record device sessions, E-stop confirmations, physical moves, AI proposals and vision detections. They are written after each action, never trigger motion, and a logging failure never blocks a response.

With `SUPABASE_DB_URL` set there is no SQLite fallback. If the database is unreachable or the project is paused, macro and calibration routes return 503, while Stop and Reset still work. Remove the variable to go back to SQLite.

## Try the first milestone

1. Open **Macro Studio** and choose **New macro**.
2. Set a name and six raw servo target counts from 0–4000, matching Manual Control, then **Save macro**.
3. Choose **Play**. The Dashboard channel meters and WebSocket telemetry update as the six simulated channels move.
4. Use **Stop** to latch the software stop; **Reset simulation** allows movement again.

Manual Control lets you stage physical servo targets and review them before sending. Target edits stay pending until **Confirm movement** is clicked. Saved macros play in simulation. **Test on glove** opens a physical preview and sends each step's six raw targets in one synchronized packet, using the same 0–4000 count range and default speed as Manual Control. It requires an off-body glove, a tested power cutoff, a clear cord path, and typed confirmation. The AI Vision page can show a browser camera preview. It does not analyze frames. Detection events never trigger a macro.

## AI Assistant (chat and voice)

The AI Assistant page turns typed or spoken requests ("make a peace sign", "open your hand") into a **proposal card**. Nothing moves until you click **Confirm**, and a confirmed proposal runs only in simulation through the normal pose and macro routes. The assistant has no hardware command and cannot drive Manual Control's physical servo moves. Saying or typing "stop" latches the same full stop as the Stop button. Typed or spoken words never confirm a move.

Providers run server-side in `backend/app/assistant.py`: Gemini (`gemini-3.8-flash`, Interactions API), OpenRouter, Hugging Face Inference Providers, and an **Offline** keyword matcher that needs no key or network. Add keys on the **Settings** page (paste key, optional model, **Save**, then **Test**), or copy `backend/.env.example` to `backend/.env` (gitignored) by hand. Settings writes the same `backend/.env`, applies changes without a restart, and only ever shows the last four characters of a key; its routes accept local requests only. Providers without a key show as "no key". A provider error or timeout returns a text-only reply. Voice uses **Gemini transcription** when a Gemini key is set: click the mic, speak, and click again (clips stop after 15 s). The browser records a short `audio/webm` clip, `POST /api/assistant/transcribe` sends it to Gemini with the servo and macro names as vocabulary, and the transcript goes into the chat like typed text. Without a Gemini key, voice falls back to the browser Web Speech API (Chrome or Edge).

### Gemini Live

A floating **Live** button on every page opens a real-time Gemini Live session with `gemini-3.8-live`: the browser streams the microphone and, when the camera is on, JPEG frames at 1 fps. Show a hand gesture and the assistant says what it sees and mirrors it as a **proposal card that still needs Confirm**; "stop" uses the normal full stop. Camera sessions last 2 minutes and audio-only sessions 15 minutes, so the panel reconnects automatically with session resumption. Live needs a Gemini key from Settings, and the browser receives only a single-use ephemeral token (`v1alpha`) with the instruction and tools locked server-side.

## Read-only controller status

Connect the servo controller by USB and power its servo bus. The Dashboard hardware circle turns green when IDs 1–6 all respond with position, voltage, temperature, and moving status. The Devices page also reads position mode and torque state for each servo, has a refresh button, and refreshes automatically every 20 seconds. This status check never sends a position command. Saved macros can be previewed in simulation or sent to the glove through Macro Studio after the explicit physical confirmation is completed.

### Live 3D hand

The Dashboard's **YOUR GLOVE** panel shows a 3D model of the glove, built from photos of the real hardware: back and palm cord guides on every finger segment, both curved anchor blocks, cords and tubes. Drag to rotate; switching the **Simulated / Physical** toggle swings the view back to straight on. **Simulated** follows macro playback. **Physical** follows the real servos: while the website is open, the API reads the six present positions about 10 times a second (read-only, never a command, sharing the controller lock with moves and status checks) and maps each raw count to 0–100 % curl with that servo's saved open and closed counts. A **Calibration unverified** badge shows until those counts are measured. The thumb uses collision avoidance so it rests on top of curled fingers instead of passing through them. Set `HANDOFF_LIVE_POSITIONS=0` to turn the position reads off.

Calibration editing is not exposed in the website. The backend stores calibration data for the separate calibration-aware test routes and the Dashboard's physical hand view, which uses saved open and closed counts to map servo positions to finger curl. Manual Control and physical macros use raw counts directly, capped at 0–4000, with live device-health and Stop checks.

### Website hardware controls

**Manual Control → Live servo positions** has a rotatable 3D hand preview that updates from staged targets, a raw-count target and speed for each servo, and an **All servos** slider that stages the same target count from **0–4000** for IDs 1–6, including both thumb motors. The preview normalizes raw counts across 0–4000; actual glove travel can differ. Target edits are staged locally; **Confirm movement** sends selected pending targets together in one request. A multi-servo move uses one synchronized packet, matching `multi_servo.py`. **Discard changes** resets staged targets to the latest read positions. If torque is off, **Align and enable torque** first copies each selected servo's present count to its stored target while torque is off, then enables torque to hold that position; it sends no movement target. The API checks each selected servo's model, position mode, torque, voltage, temperature, and current position, and rejects commands while software Stop is latched. The physical power cutoff remains the reliable emergency stop. No move returns automatically.

On 2026-10-10, a supervised terminal test sent one Servo 1 command from 1004 to 1250 at requested speed 2400 and acceleration 50. It settled at 1248 in about 0.5 seconds. This does not establish the speed or safe travel of other channels or simultaneous moves. Start at a lower setting for channels whose response has not been observed. The manufacturer's [STS3215 specifications](https://www.feetechrc.com/525603.html) describe the servo's 12 V position control and higher no-load speed; 2400 is a requested controller speed, not a guarantee of actual glove speed.

Physical writes are **off by default**. The local API must be started with `HANDOFF_ENABLE_HARDWARE_WRITES=1` for live target edits, the explicit torque-enable button, or confirmed macro playback to write to motors. Manual Control and macros share the same device-health and Stop checks, raw 0–4000 count range, synchronized packet writer, and default speed of 2400. Macro step duration controls simulation timing only. Macro playback never enables torque automatically or returns home.

When those conditions have been met and the assembled glove's movement has been reviewed, restart the API from `backend/` with `HANDOFF_ENABLE_HARDWARE_WRITES=1 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`. Omit the flag to lock physical writes again. Do not enable this mode for an unmeasured setup.

The **Stop** control latches software control. When physical writes are enabled, it also attempts to switch off torque on all six servos. This is a best-effort software action; use the independent physical power cutoff if the glove moves unexpectedly. Resetting the software stop does not re-enable torque. The API and frontend must remain bound to localhost because this prototype has no user login.

### Bench preflight

The separate `backend/app/bench_jog.py` terminal tool provides a read-only preflight for one servo at a time. Stop the API first so it releases the USB controller, then run from `backend`:

```bash
.venv/bin/python -m app.bench_jog --port /dev/cu.YOUR_PORT --servo 1 --delta 10
```

Without `--execute`, it only reads the servo and prints the proposed target. The optional execution path is capped at a single 10-count jog at 50 steps/s, checks position mode, torque, voltage, temperature, movement, and numeric margins, and requires an exact typed confirmation. It does not enable torque or return automatically. Do not use the execution path until the tested power cutoff, fully detached tendons, and safe torque engagement have been confirmed for that servo. During the 2026-10-10 bench preflight, all six servos reported torque off; Servo 1's stored target was 0 while its present position was about 1003. Enabling torque directly in that state could cause a large unexpected move, so the software does not do it.

The API recognizes the tested USB serial adapter (VID:PID `1A86:55D3`) and common Feetech URT-1 adapters (`1A86:7523`). If there are multiple compatible adapters, set `HANDOFF_SERVO_PORT=/dev/cu.YOUR_PORT` before starting the API. The supplied [Feetech-tuna library](https://github.com/iotdesignshop/Feetech-tuna) is included under `backend/feetech_tuna` at commit `50eb1308f9ed490646f39dc58d7ff04c9f4d8eed`, with its [MIT license](backend/third_party_licenses/Feetech-tuna.LICENSE). `pyserial` is installed from `backend/requirements.txt`.

## Safety boundary

Saved gestures and vision events remain **simulation only**. Manual Control's raw-count targets can move the hardware when writes are enabled and the user confirms them. The live route checks position mode, torque, voltage, temperature, and Stop before each command. Keep the glove off-body and the independent power cutoff ready when testing physical movement.

The physical power cutoff must interrupt servo power independently of handoff. Test it with the glove off the body before any wearable trial. A software stop cannot substitute for a power cut. Two supplied Feetech/ST3215 scripts are saved in [`hardware/reference`](hardware/reference/README.md) for review. Their broad movement commands are not exposed by the website.

## Checks

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npm run build
```

The backend tests use a fake serial driver and send no USB writes. The frontend's current Tailwind 3 build dependencies have seven npm audit advisories (five high, two moderate), all in build tooling. Keep this development server bound to localhost and upgrade the build toolchain before any public deployment.

## Integration next steps

1. Verify the builder-supplied finger assignments during supervised off-body calibration. The status check confirms IDs 1–6 respond, but cannot independently identify which finger each servo moves.
2. With the glove off-body, establish each attached cord's direction and safe travel, then record measured limits through the local API. Keep the independent servo power cutoff ready.
3. Test each channel's direction and response at the chosen manual speeds with the glove off-body. Test synchronized moves with two servos first while watching the power supply and cord paths. Macro playback uses the same raw-count path as Manual Control and requires its physical review confirmations.
4. Connect the glasses camera or object detector. Send real events to `POST /api/vision/detection` with `source: "external"`; add a reviewable object-to-macro mapping and user confirmation before allowing any resulting motion.

The API is intended for local development and has no user authentication. Do not expose it to an untrusted network.

# handoff — Phase 1

A local control studio for a six-servo smart-glasses hand glove. The React/TypeScript/Tailwind interface has Dashboard, Manual Control, Macro Studio, AI Vision, AI Assistant, Calibration, Devices, and Settings sections. FastAPI provides persistent SQLite macros and calibration, six-channel simulation, stop handling, WebSocket telemetry, live servo status, and guarded direct position control for each servo.

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

On first launch, `backend/handos.sqlite3` is created with five editable sample poses and six unverified calibration records. To use another database path, set `HANDOS_DB` before starting the API. Macro export downloads a version 2 JSON file; importing adds copies of its macros. Older version 1 exports are accepted and remapped to the corrected finger IDs. Sequences have up to 30 steps with 100–10,000 ms duration per step.

## Try the first milestone

1. Open **Macro Studio** and choose **New macro**.
2. Set a name and six finger percentages, then **Save macro**.
3. Choose **Play**. The Dashboard channel meters and WebSocket telemetry update as the six simulated channels move.
4. Use **Stop** to latch the software stop; **Reset simulation** allows movement again.

Manual Control has separate physical count controls and simulated percentage controls. Moving a physical slider only changes its draft target; it never sends a command until a move is checked and confirmed. **Apply to simulation** still affects only the simulated pose. The AI Vision page can show a browser camera preview. It does not analyze frames. Detection events never trigger a macro.

## AI Assistant (chat and voice)

The AI Assistant page turns typed or spoken requests ("make a peace sign", "open your hand") into a **proposal card**. Nothing moves until you click **Confirm**, and a confirmed proposal runs only in simulation through the normal pose and macro routes. The assistant has no hardware command and cannot drive the Devices hardware test. Saying or typing "stop" latches the same full stop as the Stop button. Typed or spoken words never confirm a move.

Providers run server-side in `backend/app/assistant.py`: Gemini (`gemini-3.8-flash`, Interactions API), OpenRouter, Hugging Face Inference Providers, and an **Offline** keyword matcher that needs no key or network. Add keys on the **Settings** page (paste key, optional model, **Save**, then **Test**), or copy `backend/.env.example` to `backend/.env` (gitignored) by hand. Settings writes the same `backend/.env`, applies changes without a restart, and only ever shows the last four characters of a key; its routes accept local requests only. Providers without a key show as "no key". A provider error or timeout returns a text-only reply. Voice uses **Gemini transcription** when a Gemini key is set: click the mic, speak, and click again (clips stop after 15 s). The browser records a short `audio/webm` clip, `POST /api/assistant/transcribe` sends it to Gemini with the servo and macro names as vocabulary, and the transcript goes into the chat like typed text. Without a Gemini key, voice falls back to the browser Web Speech API (Chrome or Edge).

## Read-only controller status

Connect the servo controller by USB and power its servo bus. The Dashboard hardware circle turns green when IDs 1–6 all respond with position, voltage, temperature, and moving status. The Devices page also reads position mode and torque state for each servo, has a refresh button, and refreshes automatically every 20 seconds. This status check never sends a position command. Saved macros and percentage sliders remain simulation only.

Calibration shows each servo's latest raw position. **Capture current position** makes a fresh read-only status check and copies that count into the chosen field in the unsaved form. It refuses a missing, invalid, or moving servo reading. Capture does not save the field or mark calibration verified. **Direct-test limits checked** records measured minimum/maximum counts for one small physical test; **Gestures calibrated** separately requires open/closed positions and direction. A direct-test check never makes macros eligible for hardware playback.

### Website hardware controls

**Manual Control → Servo positions** has a separate raw-count target and speed for each of the six servos. The target slider is constrained by that servo's saved measured direct-test limits. Each channel has a speed setting from **50 to 500 counts per second**; the default is **250** (five times the 50-count/s terminal test), and choices are saved in this browser. Only one servo can be commanded at a time. Choose a target and speed, select **Check move**, review the current and target counts, confirm that the glove is off-body, the cord path is clear, and the independent power cutoff was tested and is within reach, then select **Confirm move**. The preview expires after 30 seconds and can be executed once. The backend rereads model, mode, voltage, temperature, torque, stored goal, position, and movement state immediately before its single position write. It checks every servo is stopped and revalidates the selected servo's saved limits. Manual Control polls the selected servo's position during motion; **Stop** attempts torque-off, while the physical power cutoff is the reliable emergency stop. No move returns automatically.

The six motors have not each been tested at the new speeds on the attached glove. Start at a lower setting for any channel whose response has not been observed. The manufacturer's [STS3215 specifications](https://www.feetechrc.com/525603.html) describe the servo's 12 V position control and higher no-load speed; the website's 500-count/s cap is a project setting, not a verified safe speed for the assembled glove.

**Devices → Small movement test** connects the terminal bench test's read-only preflight to the website. Select one servo and either +10 or −10 counts, then choose **Check move**. The website reads the actual servo and shows the current count and proposed target. This preview never sends a motor command. It checks the ST3215 model, position mode, torque state, stored target, voltage, temperature, moving state, and count margins through the supplied Feetech library.

Physical writes are **off by default**. After the attached cord's safe direct-test limits have been measured off-body for the selected servo, those bounds are saved and checked in Calibration, and the physical power cutoff is tested for that session, the local API can be started with `HANDOFF_ENABLE_HARDWARE_WRITES=1`. Only then can the preview offer a 30-second, one-use confirmation for one direct position move or one 10-count test. Confirmation checks that the glove is off-body, the cord path is clear, and the physical power cutoff was tested; the backend re-reads the servo and rejects changed position, calibration, or status before writing. It never enables torque automatically, returns home, runs a multi-servo command, or connects saved macros to real motors.

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

The percentage sliders, saved gestures, and vision events remain **simulation only**. Manual Control's raw-count position controls and Devices' 10-count test are the two physical movement routes, both locked by default. They use `gated_target_count()` and `gated_jog_count()` respectively before one motor write. Future six-servo pose and sequence playback must use `gated_counts()` before every step. These gates require:

- Valid, measured and verified calibration for the selected servo (or every servo in a future full pose).
- A tested physical E-stop recorded for that session.
- Explicit approval for that physical actuation.
- No latched software stop; every mapped target inside measured limits.

The physical power cutoff must interrupt servo power independently of handoff. Test it with the glove off the body before any wearable trial. A software stop cannot substitute for a power cut. Two supplied Feetech/ST3215 scripts are saved in [`hardware/reference`](hardware/reference/README.md) for review. Their broad movement commands are not exposed by the website.

## Checks

```bash
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npm run build
```

The backend tests use a fake serial driver and send no USB writes. The frontend's current Tailwind 3 build dependencies have seven npm audit advisories (five high, two moderate), all in build tooling. Keep this development server bound to localhost and upgrade the build toolchain before any public deployment.

## Integration next steps

1. Verify the builder-supplied finger assignments during supervised off-body calibration. The status check confirms IDs 1–6 respond, but cannot independently identify which finger each servo moves.
2. With the glove off-body, establish each attached cord's direction and safe travel, then record measured limits in Calibration. Keep the independent servo power cutoff ready.
3. Test each channel's direction and response at the chosen manual speeds with the glove off-body. A separate six-servo pose flow would need per-step gates, stop monitoring, and fresh confirmation. The current website does not physically play macros.
4. Connect the glasses camera or object detector. Send real events to `POST /api/vision/detection` with `source: "external"`; add a reviewable object-to-macro mapping and user confirmation before allowing any resulting motion.

The API is intended for local development and has no user authentication. Do not expose it to an untrusted network.

# AI Assistant design (chat + voice hand control)

Status: approved 2026-10-10. Branch: feat/AIfeature (based on origin/main 5c4a283). Implements specs.md Phase 5 items 4-6 within design.md 7.1.

## Context
`specs.md` Phase 5 items 4–6 and `design.md` §7.1 already define a chatbot and voice control for the glove. The model **proposes** a structured action, and a human **clicks Confirm** before the simulated hand moves. Provider calls and API keys stay on the server, and a provider failure falls back to text only. The goal is a reliable **hackathon demo**: someone says or types "make a peace sign", a proposal card appears, they click Confirm, and the simulated hand moves.

Decisions confirmed with the user:
- Voice uses the browser Web Speech API.
- The backend uses the official provider SDKs.
- A "stop" command latches the stop immediately, with no Confirm.
- Proposals are confirmed by **click only**.

**Rebased onto origin/main `5c4a283`.** Local `feat/AIfeature` already matches it. The new upstream code changes several things this plan depends on:
- `backend/app/hardware_control.py` adds a guarded **one-servo jog/position path** on the Devices page.
  - Routes: `/api/hardware/control`, `/api/hardware/{jog,position}/{preview,execute}`.
  - It uses a preview token, a typed confirmation phrase, off-body/power-cut/cord checkboxes, `_local_hardware_request()` (local-origin only), and `HANDOFF_ENABLE_HARDWARE_WRITES=1`.
- `hardware_boundary.py` adds `gated_jog_count()` and `gated_target_count()`. `CalibrationInput` gains `jog_limits_verified`, and its count cap is `MAX_SERVO_COUNT=4000`.
- `/api/control/stop` now takes `request: Request`. It calls `_local_hardware_request` when writes are enabled, runs `await sim.stop()`, then `asyncio.to_thread(disable_torque)`, and returns `{...snapshot, hardware_stop}`. The frontend `stop()` reads `hardware_stop.message`.

**The assistant stays simulation-only.** It can never reach `/api/hardware/*`, `execute_jog` or `execute_position`. The schema has no hardware command type, and a test enforces this. Confirmed actions reuse the simulation `control_pose` and `play_macro`, so the 409 stop latch still applies. The assistant's **stop** goes through the same full stop as the button, including torque-off when writes are enabled, so a spoken "stop" is never weaker than a click.

## Design

### Backend: new `backend/app/assistant.py`, which the routes import
- **Command schema (Pydantic, tagged on `type`):**
  - `PoseCommand{type:"pose", positions:dict[int,int], label:str}` is validated with the existing `Pose` from `main.py`. Values run 0 (open) to 100 (flexed).
  - `MacroCommand{type:"macro", macro_id:str}` must match a saved macro.
  - `StopCommand{type:"stop"}`.
  - `NoCommand{type:"none"}`.
  - The model returns `{reply:str, command:...}`. Anything that fails validation becomes a text-only reply that says the action was rejected.
- **`ChatProvider` protocol:** `name`, `available()` (true when its key is set) and `complete(system, messages) -> str`, with a 15 s timeout.
  - `GeminiProvider`: uses `google-genai` (`from google import genai`) and the current **Interactions API**; the docs now call `generateContent` legacy. Default model is `gemini-3.8-flash`, overridable with `GEMINI_MODEL`; key from `GEMINI_API_KEY`. The call is `client.interactions.create(model=..., input=..., response_format={"type":"text","mime_type":"application/json","schema":AssistantReply.model_json_schema()})` and returns `interaction.output_text`. The docs pages I read don't say how to pass system instructions, history or timeouts, so check those against https://ai.google.dev/api while implementing. Until then, fold the system prompt and recent history into `input`, and enforce the timeout with `asyncio.wait_for` on a thread.
  - `OpenRouterProvider`: uses the `openai` SDK with `base_url="https://openrouter.ai/api/v1"`. Key from `OPENROUTER_API_KEY`, model from `OPENROUTER_MODEL`.
  - `HuggingFaceProvider`: uses `huggingface_hub.InferenceClient.chat_completion`. Key from `HF_TOKEN`, model from `HF_MODEL`.
  - For OpenRouter and HF, the default model IDs are taken from their current docs at implementation time and are never hardcoded from memory; the env vars override them. The UI shows the model each provider is using.
  - `KeywordProvider` ("Offline"): a deterministic matcher on macro names, "open", "fist" and "stop". It needs no network, so the demo survives bad venue wifi, and it lets tests run without keys.
- **System prompt:** the servo map (IDs 1–6 from `SERVO_NAMES`), the meaning of 0 and 100, the list of saved macros (id and name), the JSON contract, and a rule never to claim the hand moved.
- **Proposal store:** follows the `hardware_control.py` pending-token pattern: a `uuid4` token, `time.monotonic()` expiry and a `threading.Lock`.
  - Holds `{id, command, provider, created_at, expires_at, status}`, where status is pending, confirmed, cancelled or expired. Proposals expire after 60 s.
  - Single-use: confirm pops the proposal.
  - `clear_proposals()` invalidates every pending proposal when the stop latches or reset runs.
- **Keys:** `python-dotenv` loads `backend/.env`, and `backend/.env` is added to `.gitignore`. Add `backend/.env.example`.

### Small refactor in `backend/app/main.py`
- Extract `async def latch_stop() -> dict` out of the `stop()` route body. It runs `sim.stop()`, then `to_thread(disable_torque)`, then `clear_proposals()`, and returns `{...snapshot, hardware_stop}`.
- The `stop(request)` route keeps its `_local_hardware_request` check and then calls `latch_stop()`. Behavior is unchanged, and the existing `test_software_stop_sends_torque_off_when_hardware_writes_enabled` must still pass.
- `reset()` also calls `clear_proposals()`.

### Routes added to `backend/app/main.py`
- `GET /api/assistant/providers` returns `[{name, available, model}]`.
- `POST /api/assistant/chat {provider, message, history[≤10]}` returns `{reply, proposal|null, stopped:bool}`.
  - **This route never starts motion.**
  - A `stop` command calls `await latch_stop()`. That's the same path as the Stop button, including torque-off when writes are enabled, and it skips approval because stopping only reduces risk. The response includes `hardware_stop`.
  - The route calls `_local_hardware_request(request)` when `writes_enabled()`, matching `/api/control/stop`, so a remote origin can't drive the stop and torque-off.
  - `pose` and `macro` commands only create a pending proposal.
  - Provider errors and timeouts return a text-only reply, never a 500 that the UI could mistake for an action.
- `POST /api/assistant/proposals/{id}/confirm`:
  - Returns 404 if the id is unknown and 410 if the proposal expired or is no longer pending.
  - Otherwise it calls the existing `control_pose(Pose(...))` or `play_macro(macro_id)` functions, so the 409 stop latch, task cancellation and `mode:"simulation"` are inherited.
- `POST /api/assistant/proposals/{id}/cancel`.

### Frontend: `frontend/src/main.tsx`, a new `AI Assistant` page (dense one-liner style)
- Add `'AI Assistant'` to `Page`, `nav` (lucide `MessageSquare`) and `subtitles`. Mirror the new types: `Provider`, `Proposal`, `ChatTurn`.
- **Layout:** a provider `.field` select (unavailable providers are disabled and labelled "no key"), and message rows as 12–13 px bordered rows with no bubbles.
- **Proposal card:**
  - Uses the `.card` style and shows the six mini bars or the macro name, plus the source provider and an amber `Awaiting approval` pill.
  - `btn-primary` Confirm and `btn-secondary` Cancel buttons.
  - The card never auto-executes.
- **Voice:** a mic button uses `SpeechRecognition`/`webkitSpeechRecognition` and puts the final transcript into the same send path. It is hidden, with a note, when the browser doesn't support it. A "Speak replies" toggle uses `speechSynthesis`.
- The Stop button comes from the shared `page-heading` that every non-Dashboard page already renders, so nothing new is needed.
- Copy says "in simulation", and the page states plainly that the assistant can't drive the Devices hardware test.
- When a chat reply has `stopped:true`, the page shows the same notice text the existing `stop()` builds from `hardware_stop.message`. Factor that builder into a small `stopNotice(result)` helper so both paths share it.

### Dependencies (`backend/requirements.txt`)
Add `google-genai`, `openai`, `huggingface_hub` and `python-dotenv`. Pin compatible ranges in the same style as the existing entries.

## Execution and work split
1. Write the brainstorming spec to `docs/superpowers/specs/2026-10-10-ai-assistant-design.md` (condensed from this plan) and commit it on `feat/AIfeature`.
2. Split the work into two packages with no shared files, so the parallel agents don't collide:
   - **Package A, backend (Codex or Claude):** `assistant.py`, the routes in `main.py`, `requirements.txt`, `.env.example`, `.gitignore`, and the tests in `backend/tests/test_assistant.py`.
   - **Package B, frontend (OpenCode or Claude):** `frontend/src/main.tsx`, built against the route contract above. It doesn't need a live backend.
   - **Claude:** integration, review (`/code-review`), and updates to `README.md`, `CLAUDE.md` and `specs.md` (mark Phase 5 items 4–6 as implemented in simulation).
   - The `codex` MCP is disconnected. If it's still down, I'll do both packages myself, or give you briefs to paste into the other agents.

## Verification
- **New pytest tests, with providers monkeypatched to fake JSON:**
  - `chat` with a pose or macro reply returns a pending proposal, and `sim.positions`, `target` and `playing` stay unchanged.
  - Confirm moves the simulation and returns `mode:"simulation"`.
  - Confirm while the stop is latched returns 409.
  - Expired or double confirm returns 410.
  - Cancel works.
  - A `stop` reply latches the stop and invalidates pending proposals. With `HANDOFF_ENABLE_HARDWARE_WRITES=1` and the `fake_controller` fixture from `test_hardware_control.py`, it also sends torque-off.
  - The assistant never reaches hardware moves: monkeypatch `hardware_control.execute_jog`, `execute_position` and `FeetechServoTransport.write_position` to raise, then run chat and confirm for pose, macro and stop. None of them may be called. A model reply that asks for a "jog" or a raw count is rejected as text only.
  - Invalid model JSON or out-of-range positions give a text-only reply with no proposal.
  - A provider exception or timeout gives a text-only reply.
  - A provider without a key reports `available:false`.
  - The Offline provider maps "peace sign" to the seeded macro.
- Run `python -m pytest -q` from `backend/`. All existing tests must still pass, including the test that vision triggers no motion and all of `test_hardware_control.py`.
- Never set `HANDOFF_ENABLE_HARDWARE_WRITES` when testing against real hardware. Use only the fake controller.
- Run `npm run build` from `frontend/` as the type check.
- **Manual end-to-end test:** run both servers and open the AI Assistant page in Chrome. With the Offline provider, say "peace sign", check the card appears, click Confirm, and watch the simulated hand move. Repeat with Gemini using a real key, then say "stop" and confirm the latch shows up on the Dashboard.

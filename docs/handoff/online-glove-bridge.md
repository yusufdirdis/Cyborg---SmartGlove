# Agent handoff: deploy online glove control (bridge)

**Audience:** the agent working for the teammate who owns the Vercel and Railway accounts.
**Branch:** `feat/onlineHardware`. This branch includes `main` (vision service, one-click "Test on glove") plus the bridge. Deploy it only when the owner says to.
**Design:** `docs/superpowers/specs/2026-10-11-online-hardware-bridge-design.md`.

## What it does
- The laptop with the glove runs `python -m app.bridge`, which connects **out** to `wss://api.givemeahand.fit/ws/bridge`.
- Railway relays only allow-listed hardware requests to it: status, control info, position and group preview/execute, live moves, torque enable, macro run, and Stop.
- The **bridge**, on the laptop, accepts a request only while armed on its loopback page **and** with the operator code shown there.
- Railway's own process still has motor writes forced off (`HANDOFF_CLOUD=1`).

## Tasks
1. **Railway** (API service):
   - Add `HANDOFF_BRIDGE_TOKEN=<random secret from the owner>`. The owner puts the same value in the laptop's `backend/.env`. Never commit it or paste it in a PR.
   - Leave `HANDOFF_CLOUD=1`, `SUPABASE_DB_URL`, `HANDOFF_INSTANCE=railway`, `HANDOFF_LIVE_POSITIONS=0` and `HANDOFF_CORS_ORIGINS` as they are. `HANDOFF_PASSCODE` stays unset.
   - **Never set `HANDOFF_ENABLE_HARDWARE_WRITES`.** It's ignored in cloud mode anyway.
   - Python requirements: only `backend/requirements.txt`. **Don't** install `requirements-vision.txt`, which pulls in PyTorch, on Railway.
   - Redeploy from `feat/onlineHardware` with **1 replica**. The bridge connection lives in that single process.
2. **Vercel:** redeploy from `feat/onlineHardware`. No environment changes are needed (`VITE_API_URL=https://api.givemeahand.fit`).

## Verify (no glove needed)
- [ ] `curl https://api.givemeahand.fit/api/health` returns `{"ok":true}`.
- [ ] `curl https://api.givemeahand.fit/api/status` includes `"bridge":{"online":false,...}` while no laptop is connected.
- [ ] `curl -X POST https://api.givemeahand.fit/api/hardware/live -H 'Content-Type: application/json' -d '{"moves":[{"servo_id":1,"target":2000,"speed":100}]}'` returns **403** "The glove is offline…".
- [ ] `curl -X POST https://api.givemeahand.fit/api/control/stop` returns **200**.
- [ ] A WebSocket to `wss://api.givemeahand.fit/ws/bridge` with a wrong token in the first message is closed.
- [ ] https://givemeahand.fit shows the banner "Real glove offline. The bridge on the laptop is not connected."
- [ ] Report the deployed commit hash and these results to the owner. The owner then runs the bridge on the laptop and does the supervised off-body test.

## Rules
- Don't relax the bridge allow-list, the arm or operator-code checks, `_local_hardware_request()`, or any safety test.
- Don't touch the Supabase schema.
- If a check fails, stop and report the exact output.

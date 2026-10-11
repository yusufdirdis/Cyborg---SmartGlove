# Agent handoff: make the demo public (remove the passcode)

**Audience:** the agent working for the teammate who owns the Vercel and Railway accounts.
**Written:** 2026-10-11.
**Branch:** `feat/deploy` (PR #3). Deploy the latest commit, the one titled "Make the cloud demo public; keep calibration local".

## Why
- Visitors now bring their own AI keys (commit `3a3dc54`, already deployed), so the shared passcode no longer protects anyone's AI credits.
- The owner decided to open the demo at https://givemeahand.fit to everyone.

## What the code change does (already written and tested)
- **`HANDOFF_PASSCODE` is now optional.** When it is unset, every `/api/*` route and `/ws/telemetry` are open, and the frontend never shows the passcode screen, because it only appears after a 401. When it is set, behaviour is the same as before.
- **Cloud mode no longer fails closed without a passcode.** Before this change, no passcode meant 503 on every request.
- **Gemini Live works in cloud mode without the passcode.** It uses the visitor's own key from Settings.
- **New: in cloud mode, `PUT /api/calibration/{id}` always returns 403** ("Calibration can only be changed from the local site next to the glove."). The calibration rows in Supabase are shared with the owner's local API, where they gate the real glove's limits. A public visitor must never be able to change them.
- **Unchanged:**
  - Hardware routes remain local-only (403 from the cloud).
  - Server-side key saves return 403.
  - CORS is limited to the configured origins.
  - Stop and reset stay in memory and always work.
- **Tests:** `test_cloud_without_passcode_is_public_but_calibration_and_hardware_stay_local` in `backend/tests/test_deploy.py`. The full suite is 138 passed, 1 skipped.

## Tasks
1. **Railway** (API service): **delete the `HANDOFF_PASSCODE` variable**. An empty value works too, but deleting it is clearer.
   - Leave everything else as it is: `SUPABASE_DB_URL`, `HANDOFF_CLOUD=1`, `HANDOFF_INSTANCE=railway`, `HANDOFF_LIVE_POSITIONS=0`, and `HANDOFF_CORS_ORIGINS=https://givemeahand.fit,https://www.givemeahand.fit,https://handoff-gules-gamma.vercel.app`.
   - **Never set `HANDOFF_ENABLE_HARDWARE_WRITES`.**
   - Redeploy from the latest `feat/deploy`, with **1 replica**.
2. **Vercel:** redeploy from the latest `feat/deploy` so both sides run the same commit. No environment changes are needed; keep `VITE_API_URL=https://api.givemeahand.fit`.

## Verify (report each result)
Set `API=https://api.givemeahand.fit` first.
- [ ] `curl $API/api/health` returns `{"ok":true}`.
- [ ] `curl $API/api/macros` returns **200** with no passcode header, and lists the owner's macros from Supabase.
- [ ] The telemetry WebSocket `wss://api.givemeahand.fit/ws/telemetry` (no query string) connects and sends a snapshot.
- [ ] `curl -X PUT -H 'Content-Type: application/json' $API/api/calibration/1 -d '{"open_count":2048,"closed_count":2448,"min_count":1800,"max_count":2600,"direction":"increasing","speed":30,"verified":true,"jog_limits_verified":true}'` returns **403**, and `curl $API/api/calibration` is unchanged afterwards.
- [ ] `curl -X POST -H 'Content-Type: application/json' $API/api/hardware/live -d '{"moves":[{"servo_id":1,"target":2000,"speed":100}]}'` returns **403**.
- [ ] `curl -X PUT -H 'Content-Type: application/json' $API/api/settings/providers/gemini -d '{"model":"x"}'` returns **403**, and `curl $API/api/settings/providers` returns 200 with `"storage":"browser"`.
- [ ] A CORS preflight from `Origin: https://givemeahand.fit` is allowed, and one from `https://evil.example` is rejected.
- [ ] Opening https://givemeahand.fit in a fresh browser profile shows the app directly, with **no passcode screen**, and the sidebar says "Demo API connected".

## Rules
- Don't commit, log or paste secrets (`SUPABASE_DB_URL`, anyone's AI key).
- Don't open calibration or hardware to the cloud, and don't loosen CORS to `*`. If a check fails, stop and report the exact output.
- Don't touch the Supabase schema or run `supabase db push`. The project is shared with ResumeFit.

When you finish, reply with the deployed commit hash and the checklist results.

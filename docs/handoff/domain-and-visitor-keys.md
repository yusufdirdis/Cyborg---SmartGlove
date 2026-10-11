# Agent handoff: move the demo to givemeahand.fit and ship per-visitor AI keys

**Audience:** the agent working for the teammate who owns the Vercel and Railway accounts.
**Written:** 2026-10-11, after checking the live deploy.
**Branch:** `feat/deploy` (PR #3). Deploy commit `3a3dc54` or later.

## Why
1. The owner bought **`givemeahand.fit`**. DNS is already in place and verified:
   - `givemeahand.fit` → Vercel. It serves the site, and `www` returns a 308 to the apex.
   - `api.givemeahand.fit` → Railway, with HTTPS working. `GET https://api.givemeahand.fit/api/health` returns `{"ok":true}`.
2. **The site at `givemeahand.fit` fails with "TypeError: Failed to fetch".** The database is fine; the API still returns the Supabase macros. The causes are:
   - **CORS:** the API only allows `https://handoff-gules-gamma.vercel.app`. A preflight from `Origin: https://givemeahand.fit` returns **400**.
   - **Stale frontend build:** the live bundle still calls `https://handoff-api-production-4ad8.up.railway.app` directly and doesn't include the new Settings code.
3. **Commit `3a3dc54` ("Let each demo visitor use their own AI keys") is pushed to `feat/deploy` but isn't deployed anywhere.** Railway still answers `GET /api/settings/providers` with the old 403 "Provider keys are managed in Railway variables".

## Tasks

### 1. Railway (API service)
- Set the variable:
  ```
  HANDOFF_CORS_ORIGINS=https://givemeahand.fit,https://www.givemeahand.fit,https://handoff-gules-gamma.vercel.app
  ```
- Leave the other variables as they are (`SUPABASE_DB_URL`, `HANDOFF_CLOUD=1`, `HANDOFF_PASSCODE`, `HANDOFF_INSTANCE=railway`, `HANDOFF_LIVE_POSITIONS=0`).
- **Don't add AI provider keys.** Visitors now bring their own, and in cloud mode the server ignores `GEMINI_API_KEY` and the others.
- **Never set `HANDOFF_ENABLE_HARDWARE_WRITES`.**
- Redeploy from the latest `feat/deploy`, and keep **1 replica**.

### 2. Vercel (frontend project)
- Set `VITE_API_URL=https://api.givemeahand.fit` for **Production and Preview**, with no trailing slash.
- **Redeploy** from the latest `feat/deploy`. `VITE_API_URL` is fixed at build time, so changing it without a rebuild has no effect.
- Keep `givemeahand.fit` as the primary domain, with `www` redirecting to it. Leave `handoff-gules-gamma.vercel.app` working as a fallback.

### 3. What `3a3dc54` changes (for context; already written and tested)
- In cloud mode, **Settings** stores each visitor's Gemini, OpenRouter and Hugging Face key and model **in their browser only** (localStorage key `handoff-visitor-keys`).
- AI calls (`/api/assistant/*`, `/api/settings/*`) send them as the `X-Gemini-Key`, `X-OpenRouter-Key` and `X-HF-Token` headers, plus the `-Model` variants.
- The backend (`main.visitor_provider_keys` middleware) puts them in a per-request ContextVar (`assistant.set_visitor_settings`). They are never written to `.env`, the database or logs, error messages hide them, and the server never falls back to its own key.
- Settings listing and **Test** work in the cloud. Saving a key on the server (`PUT`/`DELETE`) still returns 403.
- Live sessions create their ephemeral token with the visitor's key.
- Tests: `backend/tests/test_visitor_keys.py`, plus the updated `test_deploy.py`. The full suite is 138 passed, 1 skipped. Run `cd backend && python -m pytest -q` before deploying if you change anything.

## Verify (report each result)
Set `API=https://api.givemeahand.fit` and `PC=<demo passcode>` first.
- [ ] `curl -s -o /dev/null -D - -X OPTIONS -H 'Origin: https://givemeahand.fit' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: x-handoff-passcode' $API/api/macros` returns **200** with `access-control-allow-origin: https://givemeahand.fit`.
- [ ] The same preflight with `Origin: https://evil.example` is still rejected.
- [ ] `curl -H "X-Handoff-Passcode: $PC" $API/api/settings/providers` returns **200**, with `"storage":"browser"` and `key_set:false` on every row.
- [ ] `curl -H "X-Handoff-Passcode: $PC" -H "X-Gemini-Key: test-not-real" $API/api/settings/providers` shows Gemini with `key_set:true`, and the response body does **not** contain `test-not-real`.
- [ ] `curl -X PUT -H "X-Handoff-Passcode: $PC" -H 'Content-Type: application/json' $API/api/settings/providers/gemini -d '{"model":"x"}'` returns **403**.
- [ ] `curl -X POST -H "X-Handoff-Passcode: $PC" -H 'Content-Type: application/json' $API/api/hardware/live -d '{"moves":[{"servo_id":1,"target":2000,"speed":100}]}'` returns **403**. Hardware must stay unreachable from the cloud.
- [ ] The JS bundle at `https://givemeahand.fit` contains `https://api.givemeahand.fit` and `handoff-visitor-keys`, and does **not** contain the old Railway URL.
- [ ] In a browser, `https://givemeahand.fit` asks for the passcode. Then:
  - the sidebar shows "Demo API connected" and Macro Studio lists the macros (5 right now);
  - **Settings** says keys are kept in this browser;
  - pasting a Gemini key and clicking **Save & test** reports "Connected";
  - the AI Assistant, given "make a peace sign" and Confirm, moves the **simulated** hand.

## Rules
- Don't commit, log or paste secrets: `SUPABASE_DB_URL`, the passcode, or anyone's AI key.
- Don't touch the Supabase schema or run `supabase db push`. The project also hosts another app (ResumeFit).
- Don't relax `_local_hardware_request()`, the passcode middleware, or any safety test.
- If something fails, stop and report the exact output instead of changing CORS to `*` or disabling checks.

When you finish, reply with the deployed commit hash and the checklist results.

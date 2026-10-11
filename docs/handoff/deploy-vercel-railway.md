# Agent handoff: deploy handoff with the frontend on Vercel and the backend on Railway

**Audience:** the coding agent working for the teammate who owns the Vercel and Railway accounts.
**Written:** 2026-10-10, against branch `feat/Database` (based on `main` @ `6fb7255`).
**Goal:** run the React UI on Vercel and the FastAPI API on Railway, both using the existing Supabase database, behind a shared demo passcode, with **no path to the physical glove from the cloud**.

Read this whole file before changing anything. Then read `CLAUDE.md` and `README.md` at the repo root. They define the safety rules this deploy must keep.

---

## 1. What you are deploying

| Part | Today | Target |
|---|---|---|
| Frontend | `frontend/`: React 19 + TS + Tailwind 3 + Vite 6. The whole UI is in `frontend/src/main.tsx`, written as dense one-liners (keep that style). | **Vercel** static build (`npm run build` → `dist/`). |
| Backend | `backend/`: FastAPI. A single long-lived process holds the simulation, the proposals and the WebSocket telemetry **in memory**. | **Railway**, one always-on service, **exactly 1 replica**. |
| Database | Supabase Postgres, `handoff` schema inside the owner's existing "ResumeFit" project (ref `bxjdarssqxsgequahzzg`, region us-west-1). The backend connects as role `handoff_app` through the session pooler. | Unchanged. Railway uses the same `SUPABASE_DB_URL`. |
| Glove hardware | USB servos on the owner's laptop only. | **Not reachable from the cloud.** The laptop keeps running its own local API for hardware tests. |

Why this split:
- Vercel Functions can't hold WebSockets or in-memory state.
- Railway can, so the API runs there as a normal process.
- The frontend is static, so it can live anywhere; Vercel is used for it.

### Repo facts you will need
- The backend entry point is `backend/app/main.py`, run as `uvicorn app.main:app` from inside `backend/`.
- Dependencies are in `backend/requirements.txt` (Python 3.11+; 3.12 recommended). `backend/feetech_tuna/` is a vendored package that must ship with the backend. Don't drop it, even though the cloud never opens a serial port.
- Storage goes through `backend/app/store.py`: SQLite when `SUPABASE_DB_URL` is empty, Postgres when it is set. There is **no fallback**. If the DB is unreachable, the API answers 503, and the API refuses to start without the `handoff` schema.
- The frontend calls the API with **relative paths**: `api()` uses `fetch('/api'+path)`, a few raw `fetch('/api/...')` calls, and `new WebSocket(`${scheme}://${location.host}/ws/telemetry`)`. Locally, Vite proxies these to port 8000 (`frontend/vite.config.ts`). **On Vercel they would hit Vercel itself**, so you must add an API base URL (§3.1).
- CORS is hard-coded to `http://127.0.0.1:5173` and `http://localhost:5173` (`app.add_middleware(CORSMiddleware, ...)` in `main.py`).
- The local-only guards block Railway traffic by design:
  - `_local_hardware_request()` protects all `/api/hardware/*` writes and macro hardware playback. **Keep it as is.** Hardware must stay unreachable from the cloud.
  - `_local_settings_request()` protects the Settings routes **and** the Gemini Live routes (`/api/assistant/live/session` and `/api/assistant/live/proposal`). In the cloud, Live needs a passcode-based check instead (§3.3).
- The tests are in `backend/tests/`. Run them with `cd backend && python -m pytest -q`; there are currently 121 passed and 1 skipped. `tests/conftest.py` forces SQLite and disables USB polling. Run the frontend type check and build with `cd frontend && npm run build`.

---

## 2. Prerequisites (the repo owner does these, not you)

1. **The code must be on GitHub.** The Supabase work is on `feat/Database`. If `git ls-remote --heads origin` doesn't list `feat/Database`, stop and ask the owner to push it. Branch from it: `git checkout -b feat/deploy origin/feat/Database`.
2. **Secrets come from the owner through a private channel** (password manager or DM), never through the repo, an issue, or a PR:
   - `SUPABASE_DB_URL`: the `handoff_app` pooler URL from the owner's `backend/.env`.
   - The AI keys the demo should use: `GEMINI_API_KEY`, plus optionally `OPENROUTER_API_KEY` and `HF_TOKEN`. Agree who pays: whoever's key is used is billed for the demo traffic.
   - `HANDOFF_PASSCODE`: a demo passcode the owner chooses, 12+ characters.
3. The owner can rotate the `handoff_app` password afterwards: `alter role handoff_app with password '…'` in the Supabase SQL editor, then update the `SUPABASE_DB_URL` variable in Railway.

---

## 3. Code changes to make (on branch `feat/deploy`)

Keep the changes small, in the existing style, and add tests. Do **not** weaken any existing safety test.

### 3.1 Frontend: configurable API origin
- Add `frontend/src/vite-env.d.ts` containing `/// <reference types="vite/client" />`. Otherwise `import.meta.env` doesn't type-check.
- In `main.tsx`, add `const API_ORIGIN=(import.meta.env.VITE_API_URL??'').replace(/\/$/,'')`. It is empty locally, so the Vite proxy keeps working.
- Change `api()` to `fetch(API_ORIGIN+'/api'+path, …)`.
- Change every raw `fetch('/api/…')` and ``fetch(`/api/…`)`` to `API_ORIGIN+…`. That covers proposal confirm, transcribe and live session. Grep for `'/api` and `` `/api `` to be sure none are missed.
- WebSocket: when `API_ORIGIN` is set, build `wss://<host of API_ORIGIN>/ws/telemetry`; otherwise keep today's `location.host` logic.
- Add `frontend/.env.example` containing `VITE_API_URL=` and a comment ("Railway API origin, for example https://handoff-api.up.railway.app; empty locally").

### 3.2 Shared demo passcode (backend and frontend)
- **Backend:** if `HANDOFF_PASSCODE` is set, an HTTP middleware requires header `X-Handoff-Passcode` on every `/api/*` request, compared with `hmac.compare_digest`. It answers 401 `{"detail":"Passcode required"}` otherwise.
  - Let CORS preflight `OPTIONS` requests through.
  - Add an unauthenticated `GET /api/health` returning `{"ok": true}` for the Railway health check.
- **WebSocket:** `/ws/telemetry` accepts `?passcode=…` and closes with code 4401 when it's wrong.
- **Unset passcode:** if `HANDOFF_PASSCODE` is unset, nothing changes. This is the local default, and all existing tests must still pass.
- **Frontend:**
  - A small passcode screen, in the same visual style as the app, is shown when any call returns 401.
  - The passcode is stored in `localStorage` (wrapped in try/catch), sent as the header on `api()` and the raw fetches, and appended to the WS URL.
- **Tests:** add `backend/tests/test_deploy.py` covering:
  - 401 without the header and 200 with it;
  - `/api/health` open without a passcode;
  - WS rejected without the passcode;
  - no behaviour change when the env var is unset.

### 3.3 Cloud mode (`HANDOFF_CLOUD=1`)
- **Settings routes** (`/api/settings/providers*`): return 403 "Provider keys are managed in Railway variables". Railway's disk is ephemeral, and keys must never be writable over the internet. The Settings page should show that message instead of the form.
- **Gemini Live routes:** replace `_local_settings_request(request)` with a check that passes when **either** the request is local **or** (cloud mode and the passcode middleware already accepted the request).
  - Keep `/api/assistant/live/proposal` validate-only. It must never move anything, and `tests/test_live.py` enforces that.
- **Hardware:** leave `_local_hardware_request()` unchanged. In cloud mode, `/api/hardware/status` should return `{"connected": false, "error": "Hardware is only available on the local API."}` without touching `serial.tools.list_ports`.
- **CORS:** read extra allowed origins from `HANDOFF_CORS_ORIGINS` (comma-separated) and add them to the existing two localhost origins. Don't use `*`.
- **Tests:**
  - Settings return 403 in cloud mode.
  - Live session is reachable with the passcode in cloud mode, with token creation mocked like `tests/test_live.py` does.
  - Hardware routes still return 403 from a non-local client.
  - Allowed origins come from the env var.

### 3.4 Railway service config
Add `backend/railway.json`:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "deploy": {
    "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'",
    "healthcheckPath": "/api/health",
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE"
  }
}
```

Also add `backend/.python-version` containing `3.12` so the builder picks a known Python. Check the current Railway docs for the config-file schema before relying on field names.

### 3.5 Docs
- Add a "Deploy (Vercel + Railway)" section to `README.md`: the env-var tables below, plus the warning that the cloud has no hardware.
- Add one line to `CLAUDE.md` about `HANDOFF_CLOUD`, `HANDOFF_PASSCODE` and `HANDOFF_CORS_ORIGINS`.

---

## 4. Railway (CLI)

```bash
npm i -g @railway/cli          # or the official installer
railway login
railway init                   # new project, for example "handoff"
# Create one service from the GitHub repo, branch feat/deploy (or main after merge), root directory: backend
```

Set the service variables in the dashboard, or with `railway variables --set "KEY=value"`:

| Variable | Value |
|---|---|
| `SUPABASE_DB_URL` | from the owner, secret |
| `HANDOFF_INSTANCE` | `railway` |
| `HANDOFF_CLOUD` | `1` |
| `HANDOFF_PASSCODE` | from the owner, secret |
| `HANDOFF_CORS_ORIGINS` | the Vercel production URL, for example `https://handoff.vercel.app` (add preview URLs if needed) |
| `HANDOFF_LIVE_POSITIONS` | `0` |
| `GEMINI_API_KEY` | secret. Optional: `GEMINI_MODEL`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `HF_TOKEN`, `HF_MODEL` |
| **`HANDOFF_ENABLE_HARDWARE_WRITES`** | **never set this on Railway** |

Then:
- Generate a public domain: `railway domain`, or Networking → Generate Domain.
- Deploy, and watch `railway logs` until `/api/health` is green.
- Keep **1 replica**. A second replica would split the simulation, proposals and telemetry.
- **Cost note:** Railway's trial is a one-time $5 credit for 30 days. After that, an always-on service needs the Hobby plan, about $5 per month.

## 5. Vercel (CLI)

```bash
npm i -g vercel
cd frontend
vercel link                    # new project, for example "handoff"; framework Vite; root = frontend
vercel env add VITE_API_URL production   # https://<railway-domain>, no trailing slash
vercel env add VITE_API_URL preview      # same value
vercel --prod                  # build: npm run build, output: dist
```

- `VITE_API_URL` is baked in at **build** time. Redeploy after changing it.
- No database, AI key or passcode goes into Vercel. The browser only ever talks to Railway.
- After the first production deploy, put the final Vercel URL into Railway's `HANDOFF_CORS_ORIGINS` and redeploy Railway.

---

## 6. Verification checklist (report each result to the owner)

**Local, before deploying**
- [ ] `cd backend && python -m pytest -q` passes, including the new `test_deploy.py`.
- [ ] `cd frontend && npm run build` passes.

**Railway API** (replace `$API` and `$PC`)
- [ ] `curl $API/api/health` returns `{"ok":true}`.
- [ ] `curl $API/api/macros` returns 401, and `curl -H "X-Handoff-Passcode: $PC" $API/api/macros` returns 200 with the owner's 6+ macros. That proves Supabase is connected.
- [ ] `curl -X PUT -H "X-Handoff-Passcode: $PC" -H 'Content-Type: application/json' $API/api/settings/providers/gemini -d '{"model":"x"}'` returns 403.
- [ ] `curl -X POST -H "X-Handoff-Passcode: $PC" -H 'Content-Type: application/json' $API/api/hardware/live -d '{"moves":[{"servo_id":1,"target":2000,"speed":100}]}'` returns **403**. Hardware must be unreachable.
- [ ] `curl -X POST -H "X-Handoff-Passcode: $PC" $API/api/control/stop` returns 200, then `/api/control/reset` returns 200.

**Vercel UI**
- [ ] The UI opens, asks for the passcode, then the Dashboard shows the telemetry stream as connected.
- [ ] Macro Studio lists the macros, and Play moves the **simulated** hand.
- [ ] AI Assistant with a Gemini key: type "make a peace sign", click Confirm, and the simulated hand moves.
- [ ] The Live button opens a session.
- [ ] A row with `instance = 'railway'` appears in `handoff.ai_proposals`. The owner can check this in the Supabase table editor.

Report the URLs, the checklist results, and anything you could not verify.

---

## 7. Safety rules you must not break

These come from `CLAUDE.md`. Its tests enforce them, so don't edit those tests to make a change pass.

- **No hardware from the cloud.** Never set `HANDOFF_ENABLE_HARDWARE_WRITES` on Railway, and never relax `_local_hardware_request()`.
- **The AI never moves anything without a human Confirm click.** Confirm runs **simulation only**. Don't add a hardware tool or command type to the assistant or the Live tools.
- **Vision detections only record.** They never trigger motion.
- **Stop must keep working when the DB is down.** Never make `/api/control/stop` depend on Supabase, auth middleware failures, or AI providers. Exempting it from the passcode is acceptable.
- **Secrets stay server-side.** Nothing secret goes into `VITE_*` variables, the repo, logs or PR text. Settings must not write keys in cloud mode.
- **Leave the Supabase schema alone.** Use only the existing `handoff` tables. The schema file is `backend/supabase/handoff_schema.sql`; **never run `supabase db push`** against the ResumeFit project, because it belongs to another app.

## 8. Out of scope
- Physical glove control from the deployed site.
- Real user accounts (the passcode is a demo gate, not auth).
- Custom domains.
- Scaling past one replica.

## 9. Escalate to the owner instead of guessing
- `feat/Database` (or `main` with the DB work) is missing from GitHub.
- The API exits at start with "The handoff schema is missing or outdated" or with 503s. The Supabase project may be paused (free plan), or the URL is wrong.
- Anything that seems to need a Supabase schema change, a hardware change, or a change to a safety test.

When you finish, open a PR from `feat/deploy` to `main` with a summary of the changes, the env-var tables (names only, no values) and the verification results.

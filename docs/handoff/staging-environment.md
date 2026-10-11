# Agent handoff: set up a staging (testing) environment with the Railway and Vercel CLIs

**Audience:** the agent working for the teammate who owns the Vercel and Railway accounts.
**Goal:** a separate, short-lived test copy of the site that runs **branch `feat/onlineHardware`** (online glove bridge, vision, one-click macros) **without touching production** (`givemeahand.fit`, `api.givemeahand.fit`, branch `feat/deploy`).
**Cost:** Railway bills by the minute (a few minutes is under $0.01; a forgotten day is about $0.15–0.50). Stop it when testing is done (§5). Vercel previews are free.

## 0. What already exists (done by the owner)
- **Database:** the Supabase schema **`handoff_staging`** is created in the same project. It is separate from production's `handoff` tables and already accessible to the same `handoff_app` login. It starts with 5 sample macros on first API start.
- **Same database URL:** staging uses the same `SUPABASE_DB_URL` as production, plus `HANDOFF_DB_SCHEMA=handoff_staging`. Copy the URL from production's Railway variables; never print it.
- **Code:** `feat/onlineHardware` on GitHub. Tests: `cd backend && python -m pytest -q` gives 176 passed, 2 skipped.

## 1. Production must not change. Read this first
- `.railway/railway.ts` manages the project as code and pins `handoff-api` to **`branch: "feat/deploy"`**. Railway's docs warn that **whole-project `railway config apply` can delete resources missing from the file**.
- **Don't run `railway config apply` while linked to the production environment** from this branch, and don't change `branch: "feat/deploy"` for production.
- Make the staging changes with environment-scoped CLI commands, or the dashboard, **in the `staging` environment only**.
- If you do use IaC for staging, run `railway config plan` **linked to `staging`** first. Show the owner the plan, and apply only if it touches nothing but `staging`.
- After each step, re-check production (§4, last checklist group).
- CLI flags change between versions. Check `railway <command> --help` and `vercel <command> --help` before running anything, and stop to report if a command differs from what's described here.

## 2. Railway: create `staging`
```bash
railway login
railway link                                  # project "handoff"
railway environment new staging               # see `railway environment --help`; duplicate production if offered
railway environment staging                   # link the CLI to staging (or `railway link --environment staging`)
```
In the **staging** environment, configure service `handoff-api`:
- **Source:** the GitHub repo, branch **`feat/onlineHardware`**, root directory `backend`. If the CLI can't set the source branch, use the dashboard: service → Settings → Source, with the staging environment selected.
- **Start command, health check, replicas:** same as production (`uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers`, `/api/health`, **1 replica**).
- **Python dependencies:** only `backend/requirements.txt`. **Don't** install `requirements-vision.txt`, which pulls in PyTorch.
- **Variables:** `railway variables --environment staging --service handoff-api --set "KEY=value"` for each:

| Variable | Staging value |
|---|---|
| `SUPABASE_DB_URL` | same as production (secret, don't print) |
| `HANDOFF_DB_SCHEMA` | `handoff_staging` |
| `HANDOFF_CLOUD` | `1` |
| `HANDOFF_INSTANCE` | `staging` |
| `HANDOFF_LIVE_POSITIONS` | `0` |
| `HANDOFF_BRIDGE_TOKEN` | a **new** secret, e.g. `openssl rand -hex 24`. **Not** production's value. Send it to the owner privately. |
| `HANDOFF_CORS_ORIGINS` | the staging Vercel URL from §3 (set after §3) |
| `HANDOFF_PASSCODE` | **unset** |
| `HANDOFF_ENABLE_HARDWARE_WRITES` | **never set** |

- **Domain:** `railway domain --environment staging --service handoff-api`. This generates a `*.up.railway.app` URL; call it `$STAGING_API`. A custom `staging-api.givemeahand.fit` is optional and needs DNS records from the owner.
- **Deploy and confirm:** deploy, then `curl $STAGING_API/api/health` should return `{"ok":true}`.

## 3. Vercel: a preview deployment for the branch
```bash
cd frontend
vercel link                                                        # existing project
vercel env add VITE_API_URL preview feat/onlineHardware             # value: $STAGING_API (no trailing slash)
```
Deploy a **preview** (never `--prod`) of `feat/onlineHardware`:
- **Either** push the branch, and Vercel's Git integration builds it with a stable branch URL like `https://<project>-git-feat-onlinehardware-<team>.vercel.app`;
- **or**, from a checkout of the branch, run `vercel deploy` (preview target).

Use the **stable branch URL**, not a one-off deployment URL, as `$STAGING_WEB`. Then:
- set Railway staging `HANDOFF_CORS_ORIGINS=$STAGING_WEB` and redeploy staging;
- confirm the production project's Production environment variables are unchanged (`VITE_API_URL=https://api.givemeahand.fit`).

## 4. Verify (report every result)
**Staging**
- [ ] `curl $STAGING_API/api/health` returns `{"ok":true}`.
- [ ] `curl $STAGING_API/api/macros` lists the staging sample macros (Open hand, Gentle fist, Point, Pinch, Peace sign), **not** production's list.
- [ ] `curl $STAGING_API/api/status` includes `"bridge":{"online":false,...}`.
- [ ] `curl -X POST -H 'Content-Type: application/json' $STAGING_API/api/hardware/live -d '{"moves":[{"servo_id":1,"target":2000,"speed":100}]}'` returns **403** ("The glove is offline…").
- [ ] `curl -X POST $STAGING_API/api/control/stop` returns 200, then `/api/control/reset` returns 200.
- [ ] A CORS preflight from `Origin: $STAGING_WEB` is allowed, and one from `https://evil.example` is rejected.
- [ ] `$STAGING_WEB` loads, shows "Real glove offline. The bridge on the laptop is not connected.", and Macro Studio shows the staging macros.
- [ ] The staging JS bundle contains `$STAGING_API`, not `api.givemeahand.fit`.

**Production unchanged**
- [ ] `curl https://api.givemeahand.fit/api/health` returns `{"ok":true}`, and `/api/macros` still returns the owner's production macros.
- [ ] Railway production's `handoff-api` source is still branch `feat/deploy`, and its variables are unchanged (names only).
- [ ] `https://givemeahand.fit` still loads its current production build.

## 5. Owner's glove test against staging (for reference; the owner runs this)
On the laptop with the glove:
1. Local API: `HANDOFF_DB_SCHEMA=handoff_staging uvicorn app.main:app --host 127.0.0.1 --port 8000`, with `SUPABASE_DB_URL` from `backend/.env`, so macro IDs match staging.
2. Bridge: `HANDOFF_BRIDGE_TOKEN=<staging token> python -m app.bridge --url wss://<STAGING_API host>/ws/bridge`.
3. Open http://127.0.0.1:8765, arm, and give the code to whoever drives from `$STAGING_WEB`. Do a supervised, off-body test.

## 6. Tear down after testing (stops the cost)
- Remove the staging deployment (`railway down --environment staging --service handoff-api`; check `--help`), or delete the `staging` environment. Production is unaffected either way.
- The `handoff_staging` schema can stay; it costs nothing. Vercel previews can stay.

## Rules
- Never print, commit or paste secrets: `SUPABASE_DB_URL`, bridge tokens, AI keys.
- Don't touch the Supabase schema (the owner manages it), and never run `supabase db push`.
- Don't relax the bridge allow-list, the arm or operator-code checks, `_local_hardware_request()`, or any safety test.
- If anything would change production, or a command doesn't match this document, **stop and report** with the exact output.

When you finish, reply with `$STAGING_API`, `$STAGING_WEB`, the deployed commit hash, and the checklist results.

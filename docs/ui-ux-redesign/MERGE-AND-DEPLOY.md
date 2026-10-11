# Merge and deploy

## Where things stand (checked 2026-10-11)
- `main` = `cd3bebf`. The redesign PR targets `main`.
- The **live site** (https://givemeahand.fit) is deployed from **`feat/deploy` (PR #3)**, which is not merged into `main`. It adds the cloud demo: optional passcode, per-visitor AI keys stored in the browser (`handoff-visitor-keys`), `API_ORIGIN` from `VITE_API_URL`, Supabase storage (`backend/app/store.py`, `backend/supabase/handoff_schema.sql`), and 403 for hardware routes from the cloud. It does **not** include the vision service.
- So the redesign reaches the live site only after **both** this PR and `feat/deploy` are in the branch Vercel/Railway deploy from.

## Recommended order
1. Merge this redesign PR into `main` (after review and the test plan).
2. Merge `main` into `feat/deploy` (or rebase PR #3 on the new `main`) and resolve conflicts as below.
3. Deploy from the updated `feat/deploy` (or from `main` once PR #3 is merged), following `docs/handoff/*.md` on that branch.

## Expected conflicts with feat/deploy, and how to resolve them
| Area | feat/deploy changes | Redesign changes | Resolution |
|---|---|---|---|
| `main.tsx` top (api, telemetry) | `API_ORIGIN`, passcode, `authenticatedFetch`, `telemetryUrl` | adds `isDemo` near `json` | Keep both. `isDemo` can become `Boolean(API_ORIGIN)`. |
| `App` sidebar status | “Demo API connected” | small line “Simulation only on this site” in demo | Keep both edits on the same line. |
| `App` render / header | passcode gate, routes | sticky header with Live + Stop; Settings/Studio routing | Keep the gate; take the redesign header and routing. |
| `Dashboard` | re-adds Simulated/Physical with `cal` (older shape) | full Round 3 Dashboard | Take the redesign version; it already uses `cal` and the same `handoff-hand-source` key. |
| `SettingsPage` | browser key storage (`browserStore`, `saveBrowser`, cloud copy) | Gemini row only, new labels and icons | Keep feat/deploy’s storage logic and copy; apply the redesign’s Gemini-only filter, card header, icons and Clear-key step. In browser mode the pill should read “Saved in this browser · ••••xxxx”. |
| `MacroStudio` | still has the old Test-on-glove review panel | one-click run (from main) + condensed rows, no Import/Export | Take main + redesign. |
| `vite-env.d.ts` | adds it | adds the identical file | Identical; no conflict. |
| Supabase store | `macros` table without `source` | SQLite gains `source` | **Required:** add `source text not null default 'you'` to `macros` in `handoff_schema.sql`, read/write it in `store.py`, and mark the five samples and “Saved from AI assistant” rows as `assistant` once. Without this the cloud macros all read “From you”. |
| Vision | absent | AI Vision restyle | In the demo the redesign shows the local-only card, so the missing service is never called. |

## Deploy checks after the merge
- Local site: run `TEST-PLAN.md` sections 1–4.
- Demo build: `VITE_API_URL=https://api.givemeahand.fit npm run build`, then on the live site confirm the local-only cards, “Simulation only on this site”, disabled glove buttons, and that `curl -X POST https://api.givemeahand.fit/api/hardware/live …` still returns 403.
- Never set `HANDOFF_ENABLE_HARDWARE_WRITES` on Railway.

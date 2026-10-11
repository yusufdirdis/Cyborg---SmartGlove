# Test plan

Run everything with motor writes **off**. Never set `HANDOFF_ENABLE_HARDWARE_WRITES=1` while testing, and never click a control that would move the real glove.

## 1. Automated (must all pass before pushing)

### 1.1 Backend
```bash
cd backend
.venv/bin/python -m pytest -q
```
Expect all tests to pass: 130 non-vision tests plus the vision tests. New tests: `test_macro_source_defaults_to_you_and_survives_edits`, `test_seeded_samples_are_from_the_assistant`, `test_existing_database_gains_source_column`. `test_existing_macros_are_remapped_once_on_database_upgrade` now expects `user_version == 3`. If `test_vision` fails with `ModuleNotFoundError: cv2`, install `requirements.txt` and rerun.

### 1.2 Frontend
```bash
cd frontend
npm install
npm run build
```
`tsc -b` must report no errors (`noUnusedLocals` catches leftover imports such as `Settings2`, `ArrowDownToLine`, `MessageSquare`).

### 1.3 Grep checks (run from the repo root; each must print nothing)
```bash
grep -n "macros/export\|macros/import\|ImportPayload" backend/app/main.py frontend/src/main.tsx
grep -n "Settings2\|ArrowDownToLine\|ArrowUpFromLine\|MessageSquare" frontend/src/main.tsx
grep -n "fixed bottom-5 right-5" frontend/src/main.tsx
```
And these must print the numbers shown:
```bash
grep -c "handoff | Your Hand. AI’s Help" frontend/index.html   # 1
grep -o "hardware/run" frontend/src/main.tsx | wc -l           # 2 (Test on glove + Run on glove)
```

## 2. Run the app safely
```bash
# backend, from backend/
HANDOFF_ENABLE_HARDWARE_WRITES=0 HANDOFF_LIVE_POSITIONS=0 HANDOS_DB=/tmp/handoff-qa.sqlite3 HANDOS_ENV_FILE=/tmp/handoff-qa.env .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# frontend, from frontend/
npm run dev -- --port 5173
```
(Use other ports and a temporary proxy config if 8000/5173 are busy.) Confirm `curl -s 127.0.0.1:8000/api/hardware/control` shows `"writes_enabled":false`.

## 3. Manual QA at 1440 × 900 and 375 × 812
For each page: no horizontal scrollbar; Stop visible in the top bar while scrolled to the bottom; Tab moves through controls with a visible focus ring.

| Page | Check |
|---|---|
| Browser tab | Hand favicon; title `handoff | Your Hand. AI’s Help`. |
| Sidebar | Order Overview, Macro Studio, Manual Control, AI Vision, Settings. Clicking each plays its icon animation once. On a short window the sidebar scrolls and Settings is reachable. Phone: menu opens a 280 px drawer. |
| Top bar | API status, Live (icon only on phone), Stop. When stopped: Reset software stop appears. Live panel opens from the top right and covers nothing permanently. |
| Overview · Simulated | Pill “Simulation” on Finger movement and Saved movements. Reset to open returns all fingers to 0 %. Tile click previews the gesture. All macros listed with origin labels; list scrolls after ~4 rows. |
| Overview · Physical (no glove) | “OFFLINE”, overlay with the real error, pills “Physical · offline”, Open hand on glove and every Run on glove disabled with the reason as tooltip. The choice survives a reload. |
| Overview · manual panel | Open → 45/55 split, glove column as tall as the panel, servo cards two per row, no second 3D glove, close button works. Phone: panel stacks below. |
| Macro Studio | Left: New macro only, Glove preview, one-line rows (Delete · Edit · Test on glove · Play, Play far right), origin chips. Right: “Ask Gemini” with the correct key pill. Under 1200 px the columns stack; on phone the row actions wrap to their own line. |
| Manual Control | Page unchanged apart from icons and chips; with writes off the pill reads “Motor control locked”. |
| AI Vision | Two-row settings, pill checkboxes, confidence value on its label, icon Start/Stop, YOLO/Hands chips, stats “—” while stopped. |
| Settings | Devices first (cards + table with No response rows when nothing answers), then the Gemini key card only. |

## 4. Safety checks (all must hold)
- No `/api/hardware/*` write route is reachable from the assistant or Gemini Live (`tests/test_assistant.py`, `tests/test_live.py` pass).
- A tile click in Physical mode never sends a request (Network tab shows nothing until the labelled button is clicked).
- With `HANDOFF_ENABLE_HARDWARE_WRITES=0`: Run on glove / Open hand on glove are disabled (Physical · locked) and the backend would answer 409 anyway.
- Stop is reachable on every page at every scroll position.
- Torque readiness: to see it without hardware, override `window.fetch` in DevTools so `/api/hardware/status` returns six servos with `torque_enabled:false` (two with `moving:true`); within 20 s Manual Control shows “Waiting for servos to settle · 4 of 6”, the glove marker on the line, Align disabled while motor control is locked.

## 5. Demo mode (live-site states)
```bash
cd frontend && VITE_API_URL=https://demo.invalid npm run dev -- --port 5174
```
Sidebar shows “Simulation only on this site”; Manual Control, Settings › Devices and AI Vision show local-only cards; Overview Physical reads “Physical · local only” with glove buttons disabled; Test on glove disabled with “Only available on the local site next to the glove”.

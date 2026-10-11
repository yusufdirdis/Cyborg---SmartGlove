# Implementation plan — 15 commits on top of `cd3bebf`

Each section is one patch in `patches/`, in order. Every commit builds, type-checks and passes the backend tests on its own, so work can stop after any of them. Style rule from `CLAUDE.md`: keep `main.tsx` in its dense one-liner style; new CSS goes at the end of `frontend/src/index.css` in a block headed `/* — handoff UI/UX redesign: … — */`.

Legend: **Cosmetic** = markup, classes, copy, CSS only. **Behaviour** = new state, handlers, calls or data. Behaviour changes were approved by the owner; the reason is given for each.

---

## 01 · Tab title and brand-mark favicon — Cosmetic
- `frontend/index.html`: `<title>handoff | Your Hand. AI’s Help</title>` and `<link rel="icon" type="image/svg+xml" href="/favicon.svg"/>`.
- New `frontend/public/favicon.svg`: the sidebar’s lucide Hand in a 32 px `#111816` circle, white stroke; `@media (prefers-color-scheme: dark)` flips to a `#f7f9f8` circle with a dark hand.
- Canvas: Round 2 · Task 2 (option A chosen).

## 02 · Button and contrast foundations — Cosmetic
- `.btn` becomes `inline-flex`, centred, `gap:7px`, `min-height:40px`, `padding:0 16px`, `white-space:nowrap` (icons sit beside labels everywhere).
- New classes: `.btn-sm` (32 px rows), `.btn-icon` (40 px circle; with `.btn-sm` 32 px), `.btn-line`, `.btn-danger` (grey at rest, Stop-coral on hover/focus), `.btn-danger-solid` (the coral confirm step), `.btn-expand` + `.btn-label` (icon-only that slides its label out on hover/focus in 0.2 s; always open on `hover:none` devices).
- Global `:focus-visible` ring `2px #18201d`, offset 2. Global reduced-motion rule.
- Contrast: muted text `#839187`, `#8d9d90`, `#94a39a` → `#63796a` in `main.tsx`; `Eyebrow` → 10 px, `.13em`, `#63796a`; `.section-kicker`, `.page-heading p`, `.panel-head-copy p`, `.header-connection`, `.sidebar-section-label`, `.stage-head`, `.stage-foot`, `.panel-note`, `.overview-gesture small` → `#63796a` (4.7:1 on white).

## 03 · Shell: 272 px sidebar, nav order, icon animations, sticky Stop — Cosmetic + one move
- Nav array becomes `[Page,LucideIcon,slug][]` in the order **Overview, Macro Studio, Manual Control, AI Vision**, then AI Assistant, Devices, Settings (the two middle ones are removed in 08/09). Each button gets `className="nav-<slug>"` (+ ` active`) and `aria-current="page"`.
- Sidebar: 272 px (drawer 280 px on phones), items 52 px tall, 10 px apart, 21 px icons, 14 px labels; badge 24 px; status text 12/11 px. The sidebar scrolls itself (`overflow-y:auto`) so every item stays reachable on short windows.
- Icon animations (CSS only, play when `.active` is applied): Overview boxes swap rows (0.8 s), Manual Control ticks slide and settle (0.8 s), Macro Studio hand waves (0.9 s), AI Vision flash (0.55 s), Settings gear spins twice (0.9 s). Disabled by reduced motion.
- **Moved:** Stop and “Reset software stop” leave the page heading and the Overview panel and go into the **sticky** `.app-header` (64 px). Reason: brief rule 1 — Stop was scrolling out of view. Page titles now appear once (header title hidden ≥801 px; phones show the page name only). `Dashboard` loses its `stop` prop.

## 04 · Remove macro import and export — Behaviour (owner decision)
- `main.tsx`: delete the Export and Import buttons, the hidden `<input type="file">`, `exportFile`, `importFile`, the `file` ref, and the `ArrowDownToLine`/`ArrowUpFromLine` imports (unused imports fail `noUnusedLocals`).
- `backend/app/main.py`: delete `ImportPayload`, `GET /api/macros/export`, `POST /api/macros/import`. Keep `remap_legacy_positions` (used by `init_db`).
- `backend/tests/test_api.py`: round-trip test now asserts the two routes are gone (404/405); the legacy-import and percent-import tests are removed.
- Docs: `README.md`, `CLAUDE.md`, `design.md`, `specs.md` updated.

## 05 · Condensed macro rows — Cosmetic
- Macro table becomes `.macro-list` / `.macro-row`: number, name, then one meta line (`Pose · description`); no channel bars, no Type column.
- Actions are 32 px pills in DOM order Play, Test on glove, Edit, Delete; `.macro-actions{flex-direction:row-reverse}` puts **Play at the far right**. Test on glove gets the Hand icon at full size; Edit becomes a Pencil (`Settings2` import removed); Delete turns coral on hover; Edit/Delete have `title` tooltips.
- `.macro-list` is a CSS container; under 520 px wide the actions drop to their own row at 40 px (phones and narrow columns).
- All handlers unchanged (Play still sets the glove preview, Test on glove still uses main’s one-click `testOnGlove`).

## 06 · Torque readiness strip — Cosmetic (derived display)
- Replaces the red “Torque is off” banner in `HardwareManual` with `<TorqueReadiness/>` (new component above `HardwareManual`) and `<GloveMarker/>` (inline SVG glove: back of hand, index on top, pointing right).
- Progress is stage-based, never a timer: Read servos (done) → Settle (count of servos with `moving===false` out of 6) → Hold. Aligning while `torqueBusy`. Fill `#d9876d`, glove rides the end, 0.2 s transitions, gentle nudge while aligning.
- Button “Align and hold torque” (Anchor icon) keeps **the same handler and enable rule** (`available && !torqueBusy`); it is secondary while settling and primary when all six are still.
- Servo cards show a chip: Holding / Torque off / Torque unknown, and “No reading” instead of “Stopped” when a servo did not answer.
- Confirm bar: “Torque is off. Turn it on above before confirming movement.” when torque is off; Discard (Undo2) and Confirm (Check) get icons.

## 07 · Icons that say what each button does — Cosmetic
- Proposal Confirm (Check) / Cancel (X), Send (SendHorizontal), Save-as-macro Save (Save), Live End (PhoneOff), Enable audio playback (Volume2), Open Macro Studio (ArrowUpRight instead of “↗”).

## 08 · Settings: Devices first, then the Gemini key only — Behaviour (routing)
- Devices leaves the nav; the Settings page renders `<Devices/>` under a “Devices” heading, then `<SettingsPage/>` under “Gemini”.
- `SettingsPage` renders only the `gemini` row (`rows.filter(p=>p.name==='gemini')`) — the assistant is Gemini-only on main. Card title “Gemini API key”, subtitle “Powers the assistant in Macro Studio and Gemini Live.”, pill “Key saved · ••••xxxx” / “No key”, buttons Save key (Save), Test (PlugZap), Clear key as an icon-only Eraser with the existing two-step confirm (coral “Yes, clear key”).
- Live status becomes an aligned table in servo-ID order: id badge, finger + mount, position with a 0–4000 bar, voltage, temperature, torque chip + mode, “No response” when a servo did not answer, and “Last read hh:mm:ss”. Refresh status is an expanding icon button.

## 09 · Macro Studio and the Gemini assistant on one page — Behaviour (routing)
- AI Assistant leaves the nav (`MessageSquare` import removed). The Macro Studio page renders `.studio-split`: `<MacroStudio/>` left (1.15fr), `<Assistant/>` right (1fr); stacked under 1200 px.
- Subtitle: “Build gestures by hand, or ask Gemini to draft them. Nothing moves until you click Confirm.” Assistant note: “… It cannot run Test on glove. …”.
- Saves from the assistant already refresh the list via `macros-changed`.

## 10 · Overview opens manual controls in place — Behaviour
- `HardwareManual` gets class hooks only: `manual-grid`, `manual-col` (x2), `manual-preview`.
- `Dashboard`: `manualOpen` state; the button becomes a toggle (“Open manual controls” / “Close manual controls”, sliders icon) instead of navigating; when open, `.control-workspace.manual-open` is a 45/55 grid: glove + Finger movement stacked left (glove grows to fill), the panel right with a close button. In the panel, CSS hides the duplicate 3D preview and lays the six servo cards two per row (`.manual-col{display:contents}`).
- Same component, so every gate is unchanged. App passes `onReading={setHardware}`. Manual Control stays as the full page.

## 11 · Overview synced modes, reset, Run on glove — Behaviour (owner request, “Round 3”)
- `Dashboard` regains `cal` (main had removed it) and gets `action`. `source` state persisted in `handoff-hand-source` (same key as `feat/deploy`). One Simulated / Physical switch drives Your glove, Finger movement and Saved movements (same `.mode-pill`: Simulation / Physical · live / Physical · offline / Physical · locked).
- Physical shows `physicalCurls(status.physical, cal)`, dims when offline, overlay with the real error, “Calibration unverified” badge.
- **Reset** in the stage’s bottom-right:
  - Simulated: “Reset to open” → `POST /api/control/pose` with all six at 0.
  - Physical: “Open hand on glove” → `POST /api/hardware/live` with each servo’s calibration `open_count` at `position_speed_default`, refreshing `/hardware/status` after. Enabled only if live, writes enabled, not stopped, all six `torque_enabled && mode===0`, and all calibration `verified`; otherwise disabled with the reason as `title`.
- **Saved movements** lists **every** macro (was `slice(0,4)`), scrolling after ~4 rows. Simulated: tile click previews in simulation (`play`). Physical: tile is inert; a labelled “Run on glove” button calls `POST /api/macros/{id}/hardware/run` (main’s one-click route, same as Test on glove), shows “Running…”, others wait.
- App refetches `/api/macros` on window focus and every 15 s (in addition to `macros-changed`).
- Hardware label tells the truth: “Hardware connected · N of 6 servos” / “Controller found · servos not responding” / “Not connected”.

## 12 · Origin labels — Behaviour (data)
- Backend: `MacroInput.source: Literal["you","assistant"] = "you"`; `ALTER TABLE macros ADD COLUMN source TEXT NOT NULL DEFAULT 'you'` when missing; migration `user_version 3` marks the five built-in samples (exact name + description) and rows described “Saved from AI assistant” as `assistant`; seeding and `save_macro_from_command` store `assistant`; `save_macro` uses named columns and **keeps the existing source on edit**; `macro_row` returns `source`. Sample list hoisted to `SAMPLES`.
- Tests: defaults/edits/422 for bad values, seeded samples, migration of an old v2 database; the existing upgrade test now expects `user_version == 3`.
- Frontend: `Macro.source`; Save as macro posts `source:'assistant'`; `<Origin/>` chip (“From the assistant” with Sparkles, “From you” with User) in Macro Studio rows and Overview tiles.

## 13 · AI Vision restyle — Cosmetic + one truth fix
- Settings in two rows (source + URL / camera picker; analysis pill checkboxes + confidence with its value on the label line); Start/Restart (Play) and Stop (Square) icons; YOLO / Hands as chips; refresh camera list as an icon button (spins while scanning); CircleAlert on runtime errors; segmented view switch without shadow; solid meters (`#75ab75` hand, `#d9876d` imitation); tabular digits.
- Truth fix: when inference is not running, stats read “—” and old results dim with a “From the last run” pill. Subtitle adds “Nothing here moves the glove.”

## 14 · Truthful states for the online demo — Behaviour (display only)
- `isDemo = Boolean(import.meta.env.VITE_API_URL)` (adds `src/vite-env.d.ts`, identical to `feat/deploy`’s). In the demo: Manual Control and the Overview panel, Settings › Devices and AI Vision render `<LocalOnly/>` cards; Physical reads “Physical · local only” with glove buttons disabled; Test on glove disabled with the reason; sidebar says “Simulation only on this site”. Locally nothing changes. The backend 403s remain the real guard.

## 15 · Gemini Live in the sticky top bar — Cosmetic + one move
- The 68 px floating Live button covered controls; it becomes a 44 px `.live-button` beside Stop in the header (icon only under 600 px), and its panel opens from the top right (`top-[72px]`). The bottom padding added in 02 is removed. Phones show “Overview” as the first page’s name.

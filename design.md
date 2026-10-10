# handoff — Frontend Design Contract

The current frontend design is **accepted**. This document freezes what it is, in actual source values, so future work (chatbot, detection review, glasses/screen status, macro timelines, hardware UI) extends it instead of replacing it.

Companion documents:

- [specs.md](./specs.md) — system specification and roadmap.
- [README.md](./README.md) — run instructions.

**Maintenance rule: update this document alongside any intentional frontend design change.** It describes the code as it exists in `frontend/src/main.tsx` and `frontend/src/index.css`. If a change is not reflected here, this document is stale.

Everything below is drawn from source. Where behavior is missing (for example focus management), it is listed as a future requirement, not as an existing feature.

---

## 1. Visual Foundations

### 1.1 Typography

| Element | Actual value |
|---|---|
| Base font | `font-family: Arial, Helvetica, sans-serif` on `:root` (`index.css:4`) |
| Headings | Same Arial stack, `letter-spacing: -.045em` on `h1, h2, h3` |
| Tailwind config | Declares `fontFamily.sans: ['Inter','ui-sans-serif','system-ui']` but Inter is **not loaded** (no font link in `index.html`) and `:root` overrides the rendered base with Arial. Inter is not the actual rendered base font. |
| Brand wordmark | 25 px, weight 800, `letter-spacing: -.065em` |
| Page heading | `h1` 29 px; 27 px at ≤600 px |
| Overview panel headings | 22 px, weight 750 |
| Gestures panel heading | 27 px |
| Card headings | 18–22 px |
| Body / helper text | 13 px, muted tones |
| Buttons and row labels | 12 px, weight 800 |
| Section kickers / micro labels | 10 px, weight 800, `letter-spacing: .13–.14em`, uppercase |

Font smoothing characters: `font-synthesis: none` everywhere.

### 1.2 Color tokens (roles as used)

| Role | Value | Where |
|---|---|---|
| Primary text | `#18201d` | `:root` color |
| App background | `#f8f9f8` / shell `#fff` | `:root`, `.app-layout` |
| Sidebar & header surface | `#f7f9f8` | `.app-sidebar`, `.app-header` |
| Overview panel surface | `#f5f7f6` | `.overview-panel` |
| Card surface | `#fff` | `.card`, `.hand-stage`, `.control-details`, `.overview-gesture` |
| Drawer surface | `#f8faf7` | macro editor panel and sticky footer |
| Primary button / dark action | `#171d19` (hover `#3b4a3c`, dark-action hover `#36483a`) | `.btn-primary`, `.dark-action` |
| Coral accent | `#e36b4c` | brand dot, range input `accent-color` |
| Meter fill | `#d9876d` | `.channel-meter i` |
| Teal utility | `#137b73` | Tailwind `text-teal` (Manual Control values/links, Devices icon tile) |
| Macro "Play" button | `#1a4034` (hover `#2d684b`) | `.macro-play` |
| Macro mini bars | `#75ab75` | `.macro-channel-bars i` |
| Online dot | `#83a88a` / `#88a88d` | sidebar/header/detail status dots |
| Hardware-on indicator | `#65a374` with `#e3f1e5` ring, border `#5d9a6c` | `.hardware-indicator.on` |
| Warning banner | bg `#fff6df`, border `#f0e3ba`, text `#7b652e` | Calibration page |
| Error message | bg `#fff1ec`, border `#ebc1b1`, text `#904733` | `.message-error` |
| Success message | bg `#edf5ec`, border `#d2e2d0`, text `#446947` | `.message-success` |
| Stop surfaces | button bg `#fff7f4` border `#e2b6a9` text `#a74731`; banner bg `#fff2ed` border `#ebc4b6` text `#8b402d` | `.stop-button`, `.stop-message` |

Pill component tones (`main.tsx:54`):

| Tone | Background | Text | Used for |
|---|---|---|---|
| `green` | `#e3f7e8` | `#247658` | Verified, Browser camera, connected states |
| `amber` | `#fff0d8` | `#976a20` | Unverified |
| `gray` | `#edf0ed` | `#708079` | Camera off, disconnected |

Borders: shell `#eef0ee`, cards `#e6ebe5`, stage/details `#ebefea`, fields `#e1e7e0`, inner dividers `#f0f2ef`, macro table outer `#d8dfd4` / rows `#e2e8de`.

### 1.3 Shape, elevation, motion

- Radii: cards **16 px**; overview panels **27 px**, **19 px** at ≤600 px; stage/details **19 px**; gesture cards **17 px**; drawer fields **9 px**; pill buttons **24 px**; sidebar nav **30 px**; brand icon **50 %** (44 px circle).
- Cards have **no shadow**: `.card { box-shadow: none; border: 1px solid #e6ebe5 }`.
- The only glow is the hardware-on indicator ring; the only drop shadow is the glove photo (`drop-shadow(0 12px 12px rgba(37,57,40,.15))`).
- Transitions: buttons only (`background .15s, border-color .15s, color .15s`); sidebar slides in at ≤800 px (`transform .2s`). No other animation.
- Range slider thumbs use the coral accent (`accent-color: #e36b4c`).

### 1.4 Layout constants

| Token | Value |
|---|---|
| Sidebar width | 246 px desktop; 250 px in the ≤800 px off-canvas state |
| Header height | 88 px desktop; 72 px at ≤800 px |
| Content | `padding: 28px 40px 70px`, `max-width: 1740px`, centered |
| Overview stack gap | 20 px |
| Overview panel padding | 30 px 34 px (20 px 17 px at ≤600 px) |
| Card padding | via Tailwind utilities (`p-5`, `md:p-7`) |
| Manual/Calibration grids | Tailwind `xl:` two-column (`1.1fr .9fr` and `2` cols); `xl` is the Tailwind default 1280 px |
| Vision/Devices width | `max-width: 1040px` |

---

## 2. Shell

### 2.1 Sidebar (desktop `≥801 px`)

Structure top to bottom:

1. **Brand row** — circular 44 px `#111816` icon tile with the lucide `Hand` icon (25 px, stroke 2), wordmark `handoff` + coral `.`; a close button exists but is hidden on desktop.
2. **Section label** — `YOUR WORKSPACE` (10 px, `#9aa39d`, uppercase, letter-spaced).
3. **Nav** — pill buttons, 13 px weight 700; inactive `#727d75`, hover `#edf1ed`/`#25352b`, active `#101714` on white. Icons are lucide at 18 px, stroke 1.9. The Dashboard item is labeled **Overview** (while the page title remains `handoff`). Macro Studio shows a circular count badge (22 px; active variant `#47554a`).
4. **Bottom system block** — label `SYSTEM`, status dot (7 px; gray `#abb4aa`, online `#83a88a`), bold 11 px `Local API connected` / `API offline`, and a permanent 10 px line `Simulation mode · no hardware`.

The sidebar is `position: sticky; top: 0; height: 100vh` with padding `37px 22px 24px`; a 74 px gap sits under the brand row.

### 2.2 Header

- Adds a 72 px (mobile) / 88 px (desktop) bar on `#f7f9f8` with a `#eef0ee` bottom border.
- Left: optional mobile menu button (≤800 px only), page title (`handoff` on Dashboard, otherwise the page name) and a subtitle line (`#8a958d`, 14 px).
- Right: connection state (`API connected` / `API offline`, 6 px dot, hidden ≤800 px) and a `SIMULATION` pill (`#eef4e9` bg, `#dfe9d8` border, `#445b4b` text, 10 px, weight 800, letter-spacing .08em).

### 2.3 Content frame and page heading

- Non-Dashboard pages render a `.page-heading`: kicker `WORKSPACE / <PAGE>` (registers as `.section-kicker`), `h1`, subtitle, and actions on the right.
- Actions: a coral **Stop** button always, plus a secondary **Reset simulation** button when `status.stopped` is true.
- When the software stop is latched, a `.stop-message` banner (pause icon + copy) appears on **every** page above the page content. The banner carries the Reset button only on Dashboard; on other pages Reset lives in the page heading. This split is intentional in the current code (`main.tsx:40-41`) — preserve it or update this document if changed.
- Feedback messages render at the top of the content: error `role="alert"`, notice `role="status"`, each with a dismiss `X`. Errors come from the `action()` helper (API `detail` preferred); notices are per-action strings like `Playing <name> in simulation`.

### 2.4 Responsive navigation (`≤800 px`)

- Sidebar becomes fixed off-canvas, 250 px, `translateX(-100%)`, slides in with the `.open` class; a dark overlay (`#15201666`, `z-index: 19`) closes it on click.
- The header shows a hamburger (lucide `Menu`, 21 px); header height drops to 72 px; the connection text is hidden.
- Navigating always closes the menu and smooth-scrolls to top.

### 2.5 Breakpoint reference

| Width | What changes |
|---|---|
| `≤1199 px` | Manual/Control workspaces collapse to one column; content padding `25px 27px 60px`; header padding `0 27px` |
| `≤850 px` | Macro table: header row hidden; rows become `25px 1fr auto`; kind label and channel bars hidden; actions move under the title, left-aligned |
| `≤800 px` | Off-canvas sidebar; header 72 px; connection text hidden; mobile menu appears |
| `≤700 px` | Device cards stack to one column |
| `≤600 px` | Content `17px 15px 45px`; header `0 16px`; header third meta hidden; overview panels 19 px radius, `20px 17px`; panel headings shrink; Dashboard stop button becomes icon-only; gesture grid single column; channel rows tighten; page heading stacks. Glove stage shrinks (300 px stage / 245 px diagram, photo stage 260 px) |

Tailwind utility breakpoints used inside JSX (`xl:`) follow Tailwind defaults (1280 px) and are independent of the custom px breakpoints above.

---

## 3. Component Conventions

### 3.1 Buttons

| Class / pattern | Values | Usage |
|---|---|---|
| `.btn.btn-primary` | bg `#171d19`, white, radius 20 px, 12 px weight 800, padding 10/16; disabled opacity .5 | Save, Apply, Start camera, Save calibration |
| `.btn.btn-secondary` | bg `#f2f5f0`, text `#475b4b` | Cancel, Reset, Set all open, Export/Import, Add step |
| `.stop-button` | coral scheme, radius 24 px, weight 800, square icon + "Stop" | Page heading, Dashboard panel head |
| `.dark-action` | bg `#171d19`, radius 22 px, 11 px weight 800, wide gap, trailing `↗` | "Open manual controls" |
| `.plain-link` | unstyled text button, 11 px weight 800, underline on hover, trailing `↗` | "Open Macro Studio" |
| `.macro-actions button` | 34 px bordered icon buttons; Play variant bg `#1a4034` | Macro table rows |
| Icon-only buttons | lucide icons 14–20 px, `aria-label` present for edit/delete/close/dismiss/menu | Throughout |

### 3.2 Fields

- `.field`: white, 1 px `#e1e7e0`, radius 9 px, padding 9/11; focus border `#789d7d` with a 3 px `#e7f0e4` ring.
- Labels are bold small text above the field; numeric fields clamp via `min`/`max` attributes (calibration counts 0–4095, speed 1–100, duration 100–10000).
- Range inputs are plain `<input type="range">` with the coral `accent-color`; Manual Control labels each slider with the channel name in `aria-label` (`"Index finger curl"`) and shows value percentages.
- Selects use the same `.field` class.

### 3.3 Cards and panels

- `.card` — white, `#e6ebe5` border, 16 px radius, **no shadow**; used for Manual Control clusters, Live preview, help text, Vision card, Calibration servo cards, Device cards, drawer step cards.
- `.overview-panel` — 27 px radius soft gray panels used only on the Dashboard; each has a circular white 47 px icon tile, a heading block, and optional right-side action.
- Section headers follow the pattern: `.section-kicker` (10 px uppercase) over an 18–22 px heading, with muted 13 px description.

### 3.4 Meters, bars, and pills

- **Channel meter** (Dashboard): 5 px track `#edf2ec`, radius 4 px, fill `#d9876d`; row grid is `22px | name | meter | 35px value`, 11 px text, `#f0f2ef` hairline between rows.
- **Macro mini bars**: six 15 px × 34 px `#ecf0e8` columns with bottom-anchored `#75ab75` fills (min 2 px) showing the first step per channel.
- **Pills**: the single `Pill` component with green/amber/gray tones; the header `SIMULATION` pill is separate styling. Pills state a fact — they are never used as buttons.
- **Status dots**: 6–7 px circles; gray when offline/unknown, green when online. The Dashboard hardware pill uses a 10 px ringed indicator instead (see §8).

### 3.5 Feedback and stop controls

- Global messages top of content (error/success, dismissible).
- Stop is always reachable: Dashboard panel head on the Dashboard; page heading elsewhere. Additions (chatbot, review UI, timelines) must keep stop visible or within one step of the surface.
- Latched stop changes copy and offers reset but never auto-resumes.

### 3.6 Icons

`lucide-react`, primarily stroke 1.9–2, sizes: nav 18, panel icons 21–22, buttons 13–17, micro 12. Icon-only buttons always carry an `aria-label`. Decorative marks (e.g. `↗`) are `aria-hidden`.

---

## 4. Page Workflows

### 4.1 Dashboard

Two stacked overview panels:

1. **Control your hand** — panel stop button; hardware connection pill (see §8); a two-column workspace: left "YOUR GLOVE" stage (MOVING/READY state derived from `status.playing`, glove image, footnote "Channel values shown in the simulation panel"), right "MOVEMENT / Finger movement" with the six live channel meters from telemetry and a dark "Open manual controls" action.
2. **Gestures, ready to test.** — `SAVED MOVEMENTS` kicker, up to four macros as cards with index numbers, play buttons disabled while stopped, and a `N saved macros` footnote.

Both panels end with a `.panel-note` honesty strip; the first one states that no motor commands are sent.

### 4.2 Manual Control

- Left card: `Six channels` kicker, `Finger controls` heading, `0–100% curl` pill, six rounded slider rows (open/closed end labels), then **Apply to simulation** (disabled while stopped) and **Set all open**.
- Right column: `Live preview / Glove` card with the same glove graphic, plus a help card explaining that sliders are percentages and actual counts depend on measured endpoints, linking to Calibration.
- Apply sends the six-channel pose to the backend; motion takes 350 ms and progress streams back over telemetry. The draft sliders are re-seeded from live positions on each successful `refresh()`.

### 4.3 Macro Studio

- Toolbar: **New macro**, **Export** (downloads `handoff-macros.json`), **Import** (hidden file input, `application/json,.json`).
- Table: columns `GESTURE / TYPE / CHANNELS / ACTIONS`; rows show index, name + description, `Pose` or `Sequence · N steps`, six mini bars from the first step, and Play / Edit / Delete buttons. Delete uses a native `window.confirm`. Play is disabled while stopped.
- The table collapses at ≤850 px (see §2.5).

### 4.4 AI Vision

- Single card, max 1040 px: `Camera input`, `Live preview` heading, status pill (`Browser camera` / `Camera off`), a 16:9 dark stage (`#172d29`, radius 16 px) containing the `<video>` element or a placeholder, a persistent badge **NO OBJECT DETECTOR RUNNING** (`#152d29`/80, 10 px), camera error text, a Start/Stop button, and the caption that video stays in the browser and is not analyzed.
- The badge and caption are truthful today. Any future detector must replace them with equally truthful, state-driven text — never leave a "running" claim visible when the state is unknown (§7).

### 4.5 Calibration

- Amber warning banner: measured values only, unverified placeholders, calibrate unloaded and test the E-stop before hardware.
- Two-column grid of servo cards (one per channel): `Servo 0N` kicker, channel name, Verified/Unverified pill; numeric fields Open/Closed/Min/Max (0–4095); Curl direction select; Speed cap (1–100); a measurement-attestation checkbox that sets `verified`; Save button.
- Editing any field resets that row's `verified` to false in the draft; saving sends the full `CalibrationInput`.

### 4.6 Devices

- List of cards (two columns, ≤700 px one column): icon tile (`#edf6ef` bg, teal icon), device name, one-line detail, and a pill showing the raw `status.devices` string. A pill is green only when the state string is exactly `connected` (trimmed, lowercased).
- Today the backend reports `Not connected` for controller and glasses; the browser camera is intentionally not listed here (it is a preview, not a glove-system device).

---

## 5. Macro Editor Drawer

- Trigger: New macro / Edit. Closes on overlay click, `X`, or **Cancel**.
- Structure: full-screen overlay `#0d2520` at 60 %; right-aligned panel `max-width: 590px`, full height, `#f8faf7`, `p-6 md:p-8`, custom thin scrollbar (`.mini-scroll`, 7 px, `#cbd4ca`).
- Content order: `Macro editor` kicker + `Create macro`/`Edit macro` heading; Name (max 60); Description textarea (max 240, 2 rows); Type select (`Pose · one position` / `Sequence · timed steps`); step cards; Add step (sequence only, disabled at 30); sticky footer with **Save macro** (primary, flex-1) and **Cancel**.
- Pose type slices steps to the first; switching to sequence keeps existing steps. Each step card holds six labeled ranges (with value readouts in teal) and a duration number field (100–10000). Remove appears when a sequence has 2+ steps.
- Save is blocked client-side when the name is blank after trim; server errors surface through the global message bar.

**Future requirement (not implemented):** the drawer has no focus trap, no `Escape` handling, and does not restore focus on close. Add these when the drawer is next touched; do not claim they exist today.

---

## 6. Media: the Glove Graphic

- Asset: `frontend/public/images/handoff-glove.png` (served as `/images/handoff-glove.png`). It shows a white glove with black tendon guides and cords; the alt text is "Five-finger white glove with black tendon guides and cords".
- Component: `HandGraphic` renders the image inside `.hand-photo-stage` (radial white → `#f8faf7` → `#eef3ef` gradient, 300 px tall; 260 px ≤600 px) with a small `.diagram-label` reading `GLOVE` and a drop shadow on the image.
- Used in Dashboard (stage with MOVING/READY head and foot note) and Manual Control (Live preview card).
- Rules: keep `object-fit: contain`, keep the gradient stage, keep the label; if the asset is ever replaced, update the alt text in the same change. Do not introduce 3D renders, AI-generated hand art, or busy backgrounds.

---

## 7. Rules for Future Additions

All additions must reuse the tokens and components above. No new accent colors; no shadows on cards; keep 10 px uppercase kickers, 11–13 px labels, and 16/19/27 px radii.

### 7.1 Chatbot (hand control)

- Place as a new page or a card cluster following `.card` + `.control-details` conventions.
- Chat messages: 12–13 px text in bordered rows; do not use chat-bubble styling that breaks the flat, border-first aesthetic.
- Model output renders as a **structured proposal card** (serif-free, same card style) showing the intended macro/pose, its source, and two actions: primary **Confirm** (`btn-primary`) and secondary **Cancel** (`btn-secondary`).
- The proposal card must show `Awaiting approval` state and must never auto-execute. When a proposal is confirmed, it enters the same pipeline described in [specs.md §6](./specs.md#6-vision-integration-contract).
- Copy must stay truthful about the current mode (`Simulation`, or future physical states).

### 7.2 Detection review

- Extend the AI Vision page; reuse the dark stage and badge for the feed.
- Event list: table-like rows in the existing border style (`.macro-table` patterns) with label, confidence, source, and time; confidence shown as a pill or muted value, never as a green "ready" signal.
- Mapping: a `.field` select pairing object → macro; a threshold control (default 0.80) presented like the Manual sliders.
- Confirmation: an explicit `btn-primary` confirm on the selected event; the confirm action must be the only path toward motion.
- Replace the static `NO OBJECT DETECTOR RUNNING` badge with a state-driven variant of the same badge styling.

### 7.3 Smart glasses and enclosure screen

- These are **status surfaces**. Add them to Devices as additional cards (icon tile + name + detail + pill) and represent connection state with the existing dot/pill vocabulary.
- Display states to render: `Simulation`, `Disconnected`, `Awaiting approval`, `Moving`, `Stopped`, `Fault`. Use gray for disconnected/idle, green only for connected/healthy, amber for awaiting approval, coral tones for stopped/fault. Text must be literal state, not decoration.
- The glasses/screen UI never adds Play/Apply/Execute controls; at most it produces proposals per §7.1. Status displays must not create another actuation path.
- Keep model names device-neutral until the hardware models are known; the backend's `devices` map keys (`controller`, `glasses`, `camera`) are the vocabulary.

### 7.4 Real-time macros and visual timeline

- Build on the macro table row: reuse the six mini bars and index styling.
- A timeline scrubber can reuse the 5 px meter geometry (`#edf2ec` track, `#d9876d` progress); step boundaries should be hairline dividers (`#f0f2ef`) with 10 px uppercase step labels.
- Live playback progress must come from telemetry; do not fake progress with client timers.
- Keep the current coral/green/teal palette; a new timeline must not introduce a new accent hue.

---

## 8. Truthfulness and Known Issues

### 8.1 Known correctness issue (documented, not fixed here)

Dashboard hardware pill (`main.tsx:65`):

```jsx
<div className="hardware-connection" role="status"
     aria-label={hardwareConnected ? 'Hardware connected' : 'Hardware disconnected'}>
  <span className={`hardware-indicator ${hardwareConnected ? 'on' : ''}`} aria-hidden="true"/>
  Hardware connected
</div>
```

The indicator and the accessible label derive from `hardwareConnected` (which is `status.devices.controller.trim().toLowerCase() === 'connected'`), but the **visible text is unconditionally "Hardware connected"**. Today the backend always reports `Not connected`, so the visible text is wrong.

**Future fix (specified, not applied):** render the label text conditionally — `Hardware connected` / `Hardware disconnected` — keeping the same class names and layout so the styling is unchanged. Until then, do not describe the pill as accurate, and do not "fix" it as part of unrelated work.

Related coupling rule: device state strings must be exactly `connected` (case-insensitive, trimmed) to render as connected. New device states (e.g. `Fault`, `Awaiting approval`) require a deliberate mapping update, not ad-hoc string checks scattered through components.

### 8.2 Truthful copy that must be preserved

- Sidebar: `Simulation mode · no hardware`.
- Dashboard panel note: no motor commands are sent; hardware remains disconnected.
- Manual Control: percentages only; actual counts depend on measured open/closed values.
- AI Vision: video stays in the browser; it is not analyzed; no detector is running.
- Calibration: banner states values are unverified placeholders and that the E-stop must be tested before any hardware adapter is enabled.
- New surfaces must follow the same rule: the UI states the current mode and what is actually happening, including `Simulation`, `Stopped`, and `Fault`.

### 8.3 Status text rules

- `MOVING` / `READY` on the glove stage derives from `status.playing`.
- `API connected` / `API offline` derives from WebSocket state (2.5 s reconnect loop).
- `Simulation only` chip in the Movement detail block is static today because only simulation exists; when a hardware mode is ever added, it becomes state-driven in the same styling.
- Never let a static label assert a live capability; if state can be unknown, show unknown explicitly (gray dot, gray pill, "Not connected").

---

## 9. Accessibility

### 9.1 Implemented today

- Icon-only buttons have `aria-label` (brand home, menu open/close, dismiss error/notice, edit/delete macro, close editor).
- Sliders and step sliders have descriptive `aria-label`s; calibration inputs use visible `<label>` elements; the attestation is a native checkbox with a text label.
- The glove image has meaningful alt text; the diagram pin label is ornamental.
- Error and notice messages use `role="alert"` / `role="status"`; the Dashboard hardware pill is `role="status"` with a state-derived `aria-label`.
- Native `<button>`, `<input>`, `<select>`, `<textarea>` elements provide baseline keyboard operability; fields have a visible focus ring.
- `lang="en"` on the document; viewport meta present.

### 9.2 Future requirements (not implemented — do not claim otherwise)

- Macro drawer: focus trap, `Escape` to close, and focus return to the invoking button.
- Mobile menu: `aria-expanded` / `aria-controls`, `Escape` to close, focus management.
- Custom focus-visible styling for buttons and links to match the `.field` focus treatment.
- Reduced-motion support for the smooth scroll and sidebar transition (`prefers-reduced-motion`).
- Contrast verification for the muted 10–11 px gray text (e.g. `#9aa39d`, `#909b91`) against `#f7f9f8`/`#f5f7f6`.
- A skip link and landmark labeling audit for the shell.
- Any new review/approval UI must be operable and confirmable by keyboard alone.

---

## 10. Change Process

1. Any intentional design change updates this file in the same change, including palette/radius/breakpoint changes.
2. New components must reuse existing tokens; a new color or radius requires a documented reason here.
3. Honesty rules in §8 apply to every new surface.
4. Hardware-adjacent UI (approval, execution, timeline, status) must keep the stop control reachable and must never imply capability the backend does not have.

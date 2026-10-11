# Design spec — what the canvas shows, in numbers and words

Visual source: https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s (final walkthrough at the bottom). Where the canvas and this file disagree with the patches, the **patches win**: they encode three fixes found during live testing (sidebar scrolls on short windows, Live moved into the top bar, “No reading” instead of “Stopped”).

## 1. Foundations
| Token | Value |
|---|---|
| Font | Arial, Helvetica, sans-serif (unchanged) |
| Text | primary `#18201d`; muted `#63796a` (4.7:1); secondary `#4f6658` |
| Surfaces | app `#f8f9f8`, sidebar/header `#f7f9f8`, panels `#f5f7f6`, cards `#fff` |
| Borders | cards `#e6ebe5`, fields `#e1e7e0`, row lines `#f0f2ef`, pills `#dfe5da` |
| Primary button | `#171d19` / white; hover `#3b4a3c` |
| Secondary button | `#f2f5f0` / `#475b4b` |
| Stop / destructive | `#fff7f4` bg, `#e2b6a9` border, `#a74731` text |
| Pills | green `#e3f7e8`/`#247658`, amber `#fff0d8`/`#976a20`, grey `#edf0ed`/`#5f6f65`, live (dark) `#171d19`/white |
| Accent | coral `#e36b4c` only for the brand dot and slider thumbs; meter fill `#d9876d` |
| Focus | `2px solid #18201d`, offset 2 px, on every interactive element |
| Motion | UI transitions ≤ 0.2 s; nav icon flourishes 0.55–0.9 s; all off under `prefers-reduced-motion` |

## 2. Buttons
| Type | Rule | Size |
|---|---|---|
| A · Expand on hover | utility, reversible, no motion: icon at rest, label slides out on hover/focus (toolbars and headers only) | 40 px circle |
| B · Always labelled | anything that moves, saves or sends | 40 px; 32 px inside list rows |
| C · Destructive | grey at rest, coral on hover; the confirm step is coral and labelled | 40 / 32 px |
| D · Safety & switches | Stop is the one 44 px button; segmented switches keep their pressed state | 44 px Stop |

## 3. Shell
- Sidebar 272 px, nav order **Overview · Macro Studio · Manual Control · AI Vision · Settings**, 52 px items, 21 px icons, animated icon on selection, scrolls when the window is short. Phone drawer 280 px.
- Sticky top bar (64 px): connection status, Reset software stop (when latched), Gemini Live, Stop. Page title appears once (in the page heading; on phones in the bar).
- Browser tab: brand-mark favicon + “handoff | Your Hand. AI’s Help”.

## 4. Pages
**Overview.** “Control your hand” panel: truthful hardware pill; Your glove (Simulated / Physical switch that also drives Finger movement and Saved movements; 3D glove; Reset to open / Open hand on glove bottom-right); Finger movement (mode pill; Open/Close manual controls). With the panel open: 45/55 split, glove grows, panel shows the full manual controls with servo cards two per row and the readiness strip when torque is off. Saved movements: every macro with its origin label; Physical tiles have “Run on glove”.

**Macro Studio.** Left: New macro (no Import/Export), Glove preview card (main’s), condensed rows (number · name · origin · type · description | Delete · Edit · Test on glove · Play). Right: Gemini assistant (“Ask Gemini”, Gemini ready / key needed, proposals with Check/X, Send).

**Manual Control.** Full-size page as on main, plus the torque readiness strip, torque chips, torque-aware confirm bar and icon buttons.

**AI Vision.** Camera and models card in two rows, status chips, live stream with the two views, stats, results (dimmed and labelled when from the last run). Gesture imitation stays “Simulation only”.

**Settings.** Devices (cards + live status table) then Gemini API key.

**Online demo (VITE_API_URL set).** Local-only cards for Manual Control, Devices and AI Vision; Physical · local only; glove buttons disabled with reasons; sidebar “Simulation only on this site”.

## 5. Copy deck (new or changed strings, word for word)
- Tab: `handoff | Your Hand. AI’s Help`
- Overview: `Preview gestures in simulation, or run them on the glove.` · `Hardware connected · {n} of 6 servos` · `Controller found · servos not responding` · `Not connected` · `Reset to open` · `Open hand on glove` · `Opening…` · `Simulated hand reset to open` · `Hand opened on glove` · `Run on glove` · `Running…` · `“{name}” completed on glove` · `Click Run on glove to play a macro on the real glove. Stop works at any time.` · `Torque is off: turn it on in manual controls so the glove can hold each pose.` · `{reason}, so macros can’t run on the glove.` · `Turn torque on first: Open manual controls` · `Calibration unverified: the open position is unknown` · `Open manual controls` / `Close manual controls` · `{n} saved macros` `synced with Macro Studio` · modes `Simulation`, `Physical · live`, `Physical · offline`, `Physical · locked`, `Physical · local only`
- Origin: `From the assistant` · `From you`
- Torque strip: `TORQUE OFF · SERVOS {ids}` · `Waiting for servos to settle` · `{n} of 6 stationary. Best to wait until all six have stopped.` · `Ready to hold position` · `Sets each stored target to where the servo is now, then turns torque on. Nothing moves.` · `Aligning targets and turning torque on…` · `Checking each servo is stationary and healthy. No movement command is sent.` · `Read servos` · `Settle · {n}/6` · `Settled` · `Hold` · `Align and hold torque` · `Aligning…` · chips `Holding` / `Torque off` / `Torque unknown` · `No reading` · `Torque is off. Turn it on above before confirming movement.`
- Macro Studio: `Build gestures by hand, or ask Gemini to draft them. Nothing moves until you click Confirm.` · `Gemini only proposes simulated moves. It cannot run Test on glove. Say or type “stop” to stop immediately.`
- Settings: `Connected devices and your Gemini API key. The key stays on this computer.` · `Devices` · `Gemini` · `Gemini API key` · `Powers the assistant in Macro Studio and Gemini Live.` · `Key saved · {hint}` · `Save key` · `Remove the saved Gemini key?` · `The key is saved in backend/.env and never shown again; only the last four characters appear.` · `One row per servo, in ID order. Positions are raw counts out of 4000; saved travel limits remain active.` · `No response` · `Last read {time}`
- AI Vision: `Run local object detection and hand tracking from your camera. Nothing here moves the glove.` · `Analysis` · `From the last run`
- Demo: `Simulation only on this site` · `Manual control works on the local site only` · `This online demo has no glove attached. Open handoff on the computer connected to the glove to move servos. You can still preview gestures on Overview.` · `Devices are shown on the local site only` · `The servo controller and glasses connect to the computer next to the glove.` · `AI Vision runs on the local site` · `Object detection and hand tracking need the camera and models on the computer next to the glove.` · `Only available on the local site next to the glove`

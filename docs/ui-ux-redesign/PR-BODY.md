## UI/UX redesign: shell, Overview, Macro Studio, Settings, AI Vision

Implements the approved design canvas (https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s) in 15 focused commits on top of `cd3bebf`. Details per commit: `docs/ui-ux-redesign/IMPLEMENTATION-PLAN.md`; spec and copy: `DESIGN-SPEC.md`.

### What changes
- **Shell:** 272 px sidebar in the order Overview · Macro Studio · Manual Control · AI Vision · Settings, animated nav icons, sticky top bar with Gemini Live and **Stop always visible**, brand favicon and tab title.
- **Overview:** one Simulated/Physical switch drives all three boxes; Reset to open / Open hand on glove; every saved macro listed with “From you / From the assistant”; one-click Run on glove in Physical mode; manual controls open in place.
- **Macro Studio + Gemini assistant** on one page; condensed rows with even buttons; Import/Export removed.
- **Manual Control:** torque readiness strip instead of the red banner, torque chips, clearer confirm bar.
- **Settings:** Devices first (clean live-status table), then the Gemini key only.
- **AI Vision:** restyle of main’s local-inference page; stopped results labelled.
- **Online demo:** truthful local-only states when built with `VITE_API_URL`.
- Contrast raised to 4.5:1, focus rings everywhere, reduced-motion respected.

### Behaviour changes to review
- Import/Export routes and UI removed (`GET /api/macros/export`, `POST /api/macros/import`).
- New `macros.source` column + `user_version 3` migration (samples and assistant saves = `assistant`; edits keep the source).
- Overview can run macros on the glove (`/api/macros/{id}/hardware/run`, same one-click policy as Test on glove) and open the hand (`/api/hardware/live` to calibration `open_count`, only with torque on and verified calibration).
- Macro list refetches on window focus and every 15 s.

### Safety
Stop always visible; nothing physical moves without a labelled button click; tiles, voice, AI and vision never move motors; assistant and Live still have no hardware tools; all server gates (write flag, health, torque, Stop) unchanged.

### Verification
- [ ] `cd backend && python -m pytest -q` — _paste result_
- [ ] `cd frontend && npm run build` — _paste result_
- [ ] Grep checks in `TEST-PLAN.md` §1.3
- [ ] Manual QA at 1440 and 375 px (`TEST-PLAN.md` §3), writes off
- [ ] Demo-mode check (`TEST-PLAN.md` §5)

### Follow-up for the live site
`feat/deploy` needs the `source` column in Supabase and the conflict resolutions in `MERGE-AND-DEPLOY.md` before this reaches givemeahand.fit.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

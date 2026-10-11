# handoff UI/UX redesign — implementation handoff

Start here. This folder is everything a coding session needs to ship the redesign from the design canvas to `main` and the live site.

| File | What it is |
|---|---|
| `PROMPT.md` | The exact message to paste into the coding Claude session. |
| `patches/0001…0015-*.patch` | The whole redesign as 15 ordered, verified commits (`git am`). |
| `IMPLEMENTATION-PLAN.md` | What each of the 15 commits changes, file by file, and why. Use it to review the patches or to re-implement by hand. |
| `DESIGN-SPEC.md` | Design system, page-by-page spec and copy deck (what the canvas shows, in words and numbers). |
| `TEST-PLAN.md` | Automated checks, manual QA per page and state, safety checks, demo-mode checks. |
| `MERGE-AND-DEPLOY.md` | How this lands next to `feat/deploy` (the live site), expected conflicts, deploy steps. |
| `PR-BODY.md` | Ready-made pull request title and description. |

Design canvas (source of truth for visuals): https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s — the bottom row is the final walkthrough (step 0 = plan, steps 1–6 = pages).

## Base and verification

- **Base commit:** `origin/main` at `cd3bebf` (Merge pull request #4, one-click Test on glove). The patches apply cleanly on it with `git am`.
- **Verified (2026-10-11), on a clean export of `cd3bebf` with all 15 patches applied:**
  - After every single commit: `npx tsc -b` passes, `vite build` succeeds, and `python -m pytest -q` passes (127 → 130 tests as tests are removed/added).
  - The one test that failed in verification, `tests/test_vision.py::test_raw_camera_jpeg_is_encoded_before_inference_can_mutate_the_frame`, fails identically on untouched `main` in that environment because `opencv-python` (`cv2`) was not installed there. With `pip install -r backend/requirements.txt` it should pass; it is not related to this work.
  - The app was run (backend with `HANDOFF_ENABLE_HARDWARE_WRITES=0`, `HANDOFF_LIVE_POSITIONS=0`, a scratch DB and an empty key file) and every page was checked in a browser at 1440 px and 375 px: no horizontal overflow, Stop always in the sticky top bar, layouts as designed. A demo-mode build (`VITE_API_URL` set) was checked for the local-only states.
- **No hardware was moved** during verification; motor writes were forced off.

## Non-negotiable rules (from the design brief — keep them while coding)

1. Stop is visible on every page at every scroll position (it lives in the sticky top bar).
2. Nothing physical happens without an explicit click on a labelled button (Confirm movement, Align and hold torque, Test on glove, Run on glove, Open hand on glove). Tiles, sliders, voice, AI replies and detections never move motors.
3. Labels tell the truth: never show connected / live / ready unless it is true; unknown reads as unknown.
4. Physical controls look different from simulation controls; locked controls say why.
5. AI proposals read as proposals (“Awaiting approval”, Confirm, Cancel) and never auto-run.
6. Keep the visual language: existing tokens, no card shadows, no new accent hue.
7. The 3D glove stays the Overview centrepiece.

Never set `HANDOFF_ENABLE_HARDWARE_WRITES` for automated tests against real hardware. Never call a `/api/hardware/*` write route from the assistant or Gemini Live.

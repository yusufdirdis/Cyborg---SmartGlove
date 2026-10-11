# Paste this into the coding Claude session

Copy everything inside the box below into a new Claude Code session opened on this repository (`/Users/christopher/Cyborg---SmartGlove`).

---

```text
Implement the handoff UI/UX redesign, push it, and open a pull request into main.

Everything you need is in docs/ui-ux-redesign/. Read README.md first, then IMPLEMENTATION-PLAN.md, TEST-PLAN.md and MERGE-AND-DEPLOY.md before changing anything. The design canvas is https://claude.ai/artifact/PK5dQJuLDmaGGFb9KaiP5s (read it with the Artifact tool if you need to see a page; the bottom row is the final walkthrough).

The redesign is already written and verified as 15 ordered patches in docs/ui-ux-redesign/patches/, made against origin/main at cd3bebf. Your job is to land them faithfully, prove they work, and open the PR. Do not redesign anything.

Steps:
1. git fetch origin. Check `git status`: the only untracked files should be docs/ui-ux-redesign/. Stop and ask me if there are other uncommitted changes.
2. Branch: `git switch ux/polish-and-fixes` then `git merge --ff-only origin/main`. If the fast-forward is refused, create a fresh branch instead: `git switch -c ux/redesign origin/main`, and use that name everywhere below.
3. Check the base: `git log -1 --format=%h origin/main`. If it is not cd3bebf, main has moved: note the new commits, then continue and resolve conflicts in step 4 using IMPLEMENTATION-PLAN.md (each section lists the exact code a patch expects and why).
4. Apply: `git am --3way docs/ui-ux-redesign/patches/*.patch`. If a patch stops, resolve it so the result matches that patch's intent in IMPLEMENTATION-PLAN.md, run `git am --continue`, and keep going. Never skip a patch. Keep the commit messages.
5. Verify, exactly as TEST-PLAN.md section 1 says:
   - Backend: in backend/, use the project venv (create it if missing: python -m venv .venv && .venv/bin/pip install -r requirements.txt), then `.venv/bin/python -m pytest -q`. Expect every test to pass (130 plus the vision tests). If test_vision fails only because cv2 is missing, install requirements and rerun; report anything else.
   - Frontend: in frontend/, `npm install` then `npm run build`. It must pass with no TypeScript errors.
   - Also run TEST-PLAN.md section 1.3 (the grep checks).
6. Run the app and do the manual QA in TEST-PLAN.md sections 2–4 with motor writes OFF: start the backend with `HANDOFF_ENABLE_HARDWARE_WRITES=0 HANDOFF_LIVE_POSITIONS=0` on a free port, the frontend with a matching proxy, and check every page at 1440 px and 375 px. Do not click any button that would move the real glove, and never set HANDOFF_ENABLE_HARDWARE_WRITES=1. Fix only genuine bugs, each as a small extra commit explaining why.
7. Commit the handoff notes (not the patches): `git add docs/ui-ux-redesign/*.md && git commit -m "Add UI/UX redesign handoff notes"`.
8. Push: `git push -u origin <branch>`.
9. Open the PR with gh: `gh pr create --base main --head <branch> --title "UI/UX redesign: shell, Overview, Macro Studio, Settings, AI Vision" --body-file docs/ui-ux-redesign/PR-BODY.md`. Fill in the "Verification" section of the body with your real results before creating it.
10. Do not merge the PR, do not enable auto-merge, and do not touch the feat/deploy branch. Report back with: the PR link, the test and build output summary, any conflicts you resolved and how, and any QA issue you found.
```

---

When the session finishes, merge order for the live site is in `MERGE-AND-DEPLOY.md`.

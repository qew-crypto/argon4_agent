# QA — whole-goal completion gate / live command revision

## Executed locally

- Published-base code from merged PR #1; Python 3.13 clean venv.
- `RUN_BROWSER_TESTS=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium python -m unittest discover -s tests -v`: **56 tests passed**, including **4 real Chromium local-HTML smoke tests**.
- `python -m compileall -q agent main.py tests` and `git diff --check`: passed.

New v3 regression coverage:
- Whole-test goal + active question 1 + Save/Next rejects premature completion. Single-question requests are not expanded to whole workflows.
- Actor claims question 1 done -> completion veto -> forced next concrete action -> answer selection and save/next -> final submitted observation -> independent approval -> one real final reply, with no additional user message.
- Forced API step excludes finish_task/report_progress and uses required tool choice. Read-only reviewer JSON is validated strictly; string "false" is not boolean approval.
- /status, /autonomy, /unlimited and /commands execute during the task lock; an explicit autonomy change resolves an already pending matching tool approval.
- Registered menu contains new commands; unknown command answers with build ID rather than silently disappearing.

Earlier regression coverage retained:
- Native OpenAI-compatible browser/finish/progress calls; unsupported tools 400 -> JSON fallback, cached for subsequent calls; invalid/truncated JSON not executed; legacy/plain reply becomes progress, not implicit completion.
- 45 useful tool steps after a progress message, beyond the old 40-step cap; explicit completion only then. `/unlimited` resets steps/total deadline. `/stop` cancels an active task.
- Default browser autonomy skips approval buttons for browser/read/list/send tools.
- Standalone natural-language screenshot request during a running task; screenshot auto-delivery during a normal tool action, duplicate suppression, invalid-photo document fallback, failure not claimed as delivered, retry possible.
- One FIO secret -> three fields in **real Chromium**, plus two-/three-word mapping and compound-name structured JSON; parts masked from text observations and one-use enforced.
- Actual focus/type/fill on inputs/contenteditable and label-overlaid checkbox check/uncheck. Input labels/required/editable metadata verified.
- Existing Google cookie validation/UI fixture, API config, `/secret`, owner allowlist, paths/files/ZIP, health/removed OAuth routes remain covered.

## Not executed / not claimed

- No real user's API endpoint/model/token, Google/Argon session, Telegram network delivery, Bothost deployment or Docker image build.
- Telegram delivery is mocked at network boundary; actual Chromium generates PNGs for the screenshot tests.
- Native function-call tests use synthetic API responses, not evidence that every OpenAI-compatible service supports tools. Explicit off/JSON mode is available.
- No guarantee of correct arbitrary model behavior, perfect task completion, unlimited quota/credits/host uptime, iframe/canvas coverage or automation of every site.
- No fixed task step/time cap by default, but per-call timeouts, genuine errors, missing input and stalled/protocol-loop guards still stop work. API spending can grow substantially.
- `/secret` remains non-E2E, best-effort redaction, not a sandbox against host/same-UID code. Screenshots may show unmasked private data to the owner.

CI is configured to repeat tests with Python 3.12 and Playwright Chromium; its final status must be checked on the PR separately.

No live user API, live Telegram menu update/network delivery or Bothost deployment was performed. Completion review is a model-based check with a deterministic active-question veto, not a proof system or a guarantee of task success. It adds API usage. Build ID: whole-goal-gate-v3.

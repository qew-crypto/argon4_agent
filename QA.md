# QA — Gemini Web / browser / secret revision

## Executed locally

- Python 3.13, installed `requirements.txt` in a clean virtual environment.
- `RUN_BROWSER_TESTS=1 CHROMIUM_EXECUTABLE=/usr/bin/chromium python -m unittest discover -s tests -v`: **35 tests passed**, including 2 real Chromium tests using local HTML only.
- `python -m compileall -q agent main.py tests`: passed.
- `git diff --check`: passed.

Coverage:
- Owner/private-chat access; API setup and provider dispatch remain functional under mocks.
- Migration from `agy`/`account` to `web`; old model key discarded.
- Host cookie JSON/storage-state parsing, domain filtering, permissions, symlink/FIFO/workspace and size rejection.
- Gemini model/strength selection against a **fixture DOM**, complete JSON response; unavailable model rejected with no prompt sent.
- API/web provider dispatch, sanitized errors, cancellation closing provider context.
- Actual Chromium task-browser snapshot with usable selectors, input/select operations, screenshot PNG, no password value in snapshot.
- `/screen` while task lock is held; uses existing browser and sends owner-only photo/file.
- Multi-step loop and `/continue` preserving original goal and Tools instance, without closing task browser.
- `/secret` deletion request, no value in reply/history/goal; expiry, single-use, redaction, masked fill errors.
- `/health` works; old HTTP `/login` and `/auth/*` removed.
- Existing path traversal/symlink, file/ZIP, subprocess environment, timeout, output-limit tests.

## NOT executed / not claimed

- No real Telegram bot/token, Google session/cookies, Gemini/Argon generation or live current model/effort menu.
- No real Bothost deployment or Docker image build in this environment.
- No guarantee of Google access from Russia or any particular host/region/account.
- No guarantee that current Gemini UI selectors match every account/language; host-side inspection may require selector overrides or a code update.
- No guarantee of accuracy on arbitrary tasks, self-directed assessment compliance, image understanding in Web mode, or sandboxing hostile model-generated shell commands.
- `/secret` is **not E2E** and does not isolate process memory from the host/same-UID code. It is a best-effort short-lived value injection mechanism, not a security audit.

GitHub Actions is configured to repeat tests with Playwright Chromium on Python 3.12. CI has not been observed until the branch workflow actually runs.

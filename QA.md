# QA — Bothost edition

Выполнено локально:
- 19 unittest: paths/symlinks, shell timeout/output/env, ZIP, settings permissions, actions protocol, owner/private-chat allowlist, no key echo, setup saving, Google URL allowlist, no raw PTY in status, auth input validation, token expiration, HTTPS URL validation — PASS.
- Официальный Antigravity CLI установлен в тестовую папку; реальные --help/модели commands исследованы.
- Login manager запустил реальный agy в PTY, выбрал Google OAuth, получил официальный accounts.google.com URL и достиг waiting_code — PASS. В Google не входили.
- Отмена login/PTy процесса — PASS.
- Локальный HTTP сервис: health, login page/CSP, bearer capability, HTTPS proxy gate, Origin rejection — PASS.
- Python compileall — PASS.
- Chromium read/fill/screenshot — проверены в исходной VPS версии; браузерные tools этой версии не изменялись.

Не выполнено:
- Docker build/run (Docker в тестовой среде отсутствует).
- Live Bothost deployment и реальная роль/права их bind-mount.
- Google OAuth completion и сохранение/восстановление keyring в контейнере.
- Реальные Telegram/API запросы (токены не подключены).
- Gemini 4 Argon доступ и точный provider model ID.

/login — экспериментальная адаптация интерактивного CLI. При изменении UI официальный remote login parser может потребовать обновления. Проверка успеха делает реальный короткий модельный запрос только после ввода кода пользователем на своём deployment.

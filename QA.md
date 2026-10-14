# QA — Bothost edition

Выполнено локально:
- 52 unittest: paths/symlinks, shell timeout/output/env, ZIP, settings permissions, actions protocol, owner/private-chat allowlist, no key echo, setup saving, Google URL allowlist, no raw PTY in status, auth input validation, token expiration, HTTPS URL validation — PASS.
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

## Исправление D-Bus startup

Добавлены 4 теста: отсутствие executable не фатально; ошибка D-Bus сохраняет диагностику и не завершает startup; успешный keyring создает password file с правами 600; URL/параметры секретов не попадают в диагностику. PASS. Реальный D-Bus после этого изменения на Bothost пока не проверен; требуется повторный deploy.

## Form rejection diagnostics

9 новых тестов: canonical HTTPS origin, стандартный Forwarded header, отсутствие TLS без обхода, storage-проверка, различимые ошибки missing/invalid token, JSON capability fallback, отказ неверному Origin без передачи OAuth-кода, malformed JSON. PASS. Локальный Chromium открыл форму с mock responses, проверил JS и восстановление токена после reload: PASS. Реальный HTTPS reverse proxy Bothost пока не проверен.

## Encrypted OAuth code / headerless reverse proxy

52 unit tests PASS: дополнительно RSA/AES envelope, tampering, cross-session rejection, replay, long code, TLS proof failures/peer binding, encrypted HTTP code round-trip, отказ plaintext input, подтверждённый и неподтверждённый headerless proxy.

Локальный integration smoke с Chromium и TLS reverse proxy, намеренно удаляющим ВСЕ forwarded scheme headers: PASS. Проверены публичный proof-круг к тому же backend, private TCP-peer pin, refresh формы, WebCrypto RSA/AES шифрование и дешифрование тестового кода. В теле POST на прокси plaintext кода отсутствовал. Самоподписанный сертификат отвергнут production default CA validation; тестовый успешный контур использовал явно доверенный тестовый CA только в QA harness. Код production не отключает SSL validation.

Живой Bothost round-trip после этих изменений и завершение Google OAuth по-прежнему не проверены — необходимо обновление deployment пользователем.

## Safe login diagnostics

8 новых тестов: отдельные stdout/stderr, allowlisted категории без эха секретов, различие OAuth/keyring/auth, отсутствие ложной квоты по случайным цифрам OAuth-кода, JSON envelope после startup noise, failed/successful verification, отказ модельного запроса при явном invalid_grant. PASS. Всего 52 unit tests.

Реальный установленный CLI без credentials завершился с AUTH_REQUIRED/exit=1, классификатор корректно распознал отказ. Syntax-check JS и повторный локальный TLS-proxy/WebCrypto round-trip PASS. Реальная причина отказа конкретного Bothost аккаунта ещё не определена: теперь её нужно получить через /checklogin или повторный /login.

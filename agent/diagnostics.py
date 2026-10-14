"""Allowlisted diagnostics. NEVER return raw CLI output, URLs, tokens or codes."""
import json

MESSAGES = {
    'OK': 'Проверочный запрос выполнен. Сохранённый вход работает.',
    'OAUTH_CODE_REJECTED': 'Google отклонил код OAuth. Нужен новый /login и новый код для этой ссылки.',
    'KEYRING_ERROR': 'CLI не смог прочитать или сохранить credentials в keyring. Повторный код не исправит хранилище.',
    'AUTH_REQUIRED': 'CLI не видит сохранённой авторизации. OAuth мог не завершиться или профиль не сохранился.',
    'QUOTA_EXHAUSTED': 'Провайдер сообщил о квоте/лимите запросов. Повторный вход обычно не помогает.',
    'REGION_UNSUPPORTED': 'Провайдер сообщил, что доступ недоступен в этой стране/локации.',
    'ACCOUNT_NOT_SUPPORTED': 'Провайдер сообщил об ограничении аккаунта или необходимой подписке.',
    'PROJECT_REQUIRED': 'CLI требует проект Google Cloud или настройку проекта.',
    'ONBOARDING_REQUIRED': 'CLI требует дополнительный шаг настройки/согласия. Автоматический вход его не завершил.',
    'MODEL_UNAVAILABLE': 'CLI сообщил, что модель не найдена или не поддерживается.',
    'ACCESS_DENIED': 'Провайдер отказал в доступе. Проверь разрешения аккаунта/модели.',
    'NETWORK_TLS_ERROR': 'CLI сообщил о сетевой ошибке, DNS, соединении или сертификате.',
    'TIMEOUT': 'Проверочный запрос не завершился за отведённое время.',
    'CLI_NOT_FOUND': 'Команда agy отсутствует. Нужна сборка через собственный Dockerfile.',
    'CLI_ARGUMENTS': 'Установленная версия agy не принимает параметры проверочного запроса.',
    'CLI_OUTPUT_FORMAT': 'Ответ agy не содержит ожидаемый JSON-результат.',
    'UNKNOWN_CLI_ERROR': 'CLI завершил проверку без успешного результата; известная причина не распознана.',
    'INTERNAL_ERROR': 'Не завершён внутренний этап проверки. Нужна диагностика кода бота.',
}


def known_failure(text):
    t = str(text).lower()
    rules = [
        ('OAUTH_CODE_REJECTED', ('invalid_grant','invalid authorization code','invalid authentication code','authorization code expired','code has already been used')),
        ('KEYRING_ERROR', ('keyring error','failed to store','failed to save credentials','failed to save token','secret service','org.freedesktop.secrets','failed to unlock keyring','no keyring','keyring is locked')),
        ('REGION_UNSUPPORTED', ('not available in your country','unsupported country','unsupported region','location is not supported','user location is not supported')),
        ('QUOTA_EXHAUSTED', ('resource_exhausted','quota exceeded','rate limit exceeded','too many requests','http 429','status 429','429 too many','"code":429','"code": 429')),
        ('ACCOUNT_NOT_SUPPORTED', ('account is not supported','account not eligible','subscription required','requires a subscription','upgrade your plan','not available for this account')),
        ('PROJECT_REQUIRED', ('google_cloud_project','project id is required','no project configured','project must be specified','select a google cloud project')),
        ('ONBOARDING_REQUIRED', ('accept the terms','accept terms','terms of service must','complete onboarding','onboarding required','select a project')),
        ('MODEL_UNAVAILABLE', ('model not found','unknown model','unsupported model','invalid model','model does not exist')),
        ('AUTH_REQUIRED', ('authentication required','not authenticated','not signed in','no credentials','login required','please sign in','unauthenticated')),
        ('ACCESS_DENIED', ('permission_denied','permission denied','access denied','403 forbidden','http 403')),
        ('CLI_ARGUMENTS', ('unknown flag','flag provided but not defined','unknown option','unrecognized argument')),
        ('NETWORK_TLS_ERROR', ('certificate verify failed','certificate signed by unknown authority','x509:','tls handshake','connection refused','connection reset','no such host','network is unreachable','name resolution','dial tcp','sslcertverificationerror')),
        ('TIMEOUT', ('deadline exceeded','request timed out','context deadline','timeout exceeded')),
    ]
    for name, needles in rules:
        if any(n in t for n in needles):
            return name
    return None


def decode_envelope(text):
    try:
        data = json.loads(text)
        if isinstance(data, dict) and 'status' in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Some releases emit a non-JSON startup line. Only accept a real envelope,
    # never treat arbitrary terminal text as a successful response.
    decoder = json.JSONDecoder()
    for pos, char in enumerate(str(text)):
        if char != '{':
            continue
        try:
            data, _ = decoder.raw_decode(text[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and 'status' in data and ('response' in data or 'error' in data):
            return data
    return None


def diagnosis(name, exit_code=None, phase=None):
    if name not in MESSAGES:
        name = 'UNKNOWN_CLI_ERROR'
    suffix = ''
    if isinstance(exit_code, int):
        suffix += f'; exit={exit_code}'
    if phase in ('oauth','model','startup'):
        suffix += '; этап=' + phase
    return f'{name}: {MESSAGES[name]}{suffix}'

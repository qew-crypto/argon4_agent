import json
import os
import secrets
from urllib.parse import urlsplit
from aiohttp import web


def https_origin(url):
    try:
        p = urlsplit(url.strip())
        if (p.scheme != 'https' or not p.hostname or p.username is not None or
            p.password is not None or p.query or p.fragment or p.path not in ('', '/')):
            return None
        port = p.port
        host = p.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        return 'https://' + host + (':' + str(port) if port and port != 443 else '')
    except ValueError:
        return None


def public_url():
    url = os.getenv('PUBLIC_URL', '').strip()
    if not url and os.getenv('DOMAIN'):
        url = 'https://' + os.environ['DOMAIN'].strip().removeprefix('https://').rstrip('/')
    return https_origin(url)


def proxy_https(request):
    if request.secure:
        return True
    if request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip().lower() == 'https':
        return True
    # Also accept the standard RFC 7239 proxy header. Never skip TLS verification.
    first = request.headers.get('Forwarded', '').split(',')[0]
    import re
    return bool(re.search(r'(?:^|;)\s*proto=(?:"https"|https)\s*(?:;|$)', first, re.I))


PAGE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение Google</title><style nonce="NONCE">body{max-width:620px;margin:40px auto;padding:20px;font:17px system-ui;background:#111827;color:#f3f4f6}button,input{padding:12px;font:inherit;margin:10px 0;max-width:100%;box-sizing:border-box}a{color:#93c5fd}input{width:100%}#form{display:none}</style>
<h1>Подключение Google</h1><p>Вход выполняется на официальном сайте Google. Здесь вводится только одноразовый код возврата, не пароль.</p>
<p id="status">Проверка…</p><p><a id="google" target="_blank" rel="noreferrer noopener" hidden>Открыть официальный вход Google</a></p>
<form id="form"><label>Одноразовый код из браузера Google<input id="code" type="password" autocomplete="off" required></label><button>Подключить</button></form>
<script nonce="NONCE">const storageKey='agent-google-login';let token=location.hash.slice(1);
try{if(token){sessionStorage.setItem(storageKey,JSON.stringify({token,expires:Date.now()+600000}))}else{let saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(saved&&saved.expires>Date.now())token=saved.token;else sessionStorage.removeItem(storageKey)}}catch(e){}history.replaceState(null,'',location.pathname);
const labels={starting:'Готовлю вход…',waiting_code:'Открой Google по ссылке, войди, затем вставь полученный код ниже.',checking:'Проверяю подключение (до 2 минут)…',success:'Подключено! Вернись в Telegram. Режим Antigravity включён.',error:'Не удалось запустить вход. Проверь логи запуска и поддержку CLI хостингом. Секреты в поддержку не отправляй.',expired:'Ссылка истекла. Запусти /login снова.',check_failed:'Код отправлен, но пробный запрос не прошёл. Причина может быть в коде, keyring, доступе, квоте или сети. Вход не подтверждён.',already_signed_in:'CLI сообщает о сохранённой сессии. Вернись в бот и выбери /mode agy, затем проверь обычным сообщением.'};
const errors={session_missing:'Ссылка открылась без токена входа. Открой новую кнопку из /login; не вводи адрес формы вручную.',session_invalid:'Ссылка истекла, заменена новым /login или бот был перезапущен. Запусти /login и открой новую кнопку.',https_not_confirmed:'HTTPS не подтверждён прокси Bothost. Код Google пока не вводи. Прокси должен передавать X-Forwarded-Proto: https или Forwarded: proto=https.',origin_mismatch:'Домен формы не совпадает с PUBLIC_URL. Укажи в Bothost точный HTTPS-домен открытой страницы, затем обнови deployment и запусти /login снова.',invalid_json:'Не удалось прочитать запрос формы.',code_rejected:'Нет ожидающего входа или код отклонён. Проверь /status и начни /login заново.'};
async function api(path,data){if(!token)throw Error(errors.session_missing);let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({...data,session_token:token})});let d;try{d=await r.json()}catch(e){throw Error('Сервер вернул неожиданный ответ (HTTP '+r.status+'). Проверь deployment Bothost.')}if(!r.ok){if(d.error==='session_invalid'){try{sessionStorage.removeItem(storageKey)}catch(e){}}throw Error(errors[d.error]||'Запрос отклонён (HTTP '+r.status+').')}return d}
async function poll(){try{let d=await api('/auth/status');document.querySelector('#status').textContent=labels[d.stage]||d.stage;
let a=document.querySelector('#google');a.hidden=!d.google_url;if(d.google_url)a.href=d.google_url;document.querySelector('#form').style.display=d.stage==='waiting_code'?'block':'none';if(['starting','waiting_code','checking'].includes(d.stage))setTimeout(poll,1500)}catch(e){document.querySelector('#status').textContent=e.message}}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();let el=document.querySelector('#code'),code=el.value;el.value='';try{await api('/auth/code',{code});poll()}catch(e){document.querySelector('#status').textContent=e.message}};poll();</script></html>'''


async def start_server(login):
    async def health(request):
        return web.json_response({'status': 'ok'})

    async def page(request):
        if not public_url():
            raise web.HTTPServiceUnavailable(text='Configure PUBLIC_URL with HTTPS')
        nonce = secrets.token_urlsafe(18)
        return web.Response(text=PAGE.replace('NONCE', nonce), content_type='text/html', headers={
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})

    def denied(reason, status=403):
        return web.json_response({'error': reason}, status=status,
                                 headers={'Cache-Control': 'no-store'})

    def check(request, data):
        # Reverse proxy terminates TLS. Missing HTTPS indication is not silently bypassed.
        if not proxy_https(request):
            return 'https_not_confirmed'
        header = request.headers.get('Authorization', '')
        # Some hosting proxies strip Authorization. A POST JSON capability is an
        # equivalent authenticated fallback, never a query parameter or logged value.
        token = header[7:] if header.startswith('Bearer ') else data.get('session_token', '')
        if not isinstance(token, str) or not token:
            return 'session_missing'
        if not login.valid(token):
            return 'session_invalid'
        origin = request.headers.get('Origin')
        if origin and https_origin(origin) != public_url():
            return 'origin_mismatch'
        return None

    async def auth_data(request):
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def status(request):
        data = await auth_data(request)
        if data is None:
            return denied('invalid_json', 400)
        reason = check(request, data)
        if reason:
            return denied(reason)
        return web.json_response(login.status(), headers={'Cache-Control': 'no-store'})

    async def code(request):
        data = await auth_data(request)
        if data is None:
            return denied('invalid_json', 400)
        reason = check(request, data)
        if reason:
            return denied(reason)
        try:
            await login.submit(str(data.get('code', '')).strip())
        except ValueError:
            return denied('code_rejected', 400)
        return web.json_response({'ok': True}, headers={'Cache-Control': 'no-store'})

    app = web.Application(client_max_size=8192)
    app.add_routes([web.get('/health', health), web.get('/login', page),
                    web.post('/auth/status', status), web.post('/auth/code', code)])
    # No access logs: links and auth values must never be logged by this application.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
    return runner

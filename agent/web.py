import asyncio
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
<script nonce="NONCE">let encryption=null;const storageKey='agent-google-login';let token=location.hash.slice(1);
try{if(token){sessionStorage.setItem(storageKey,JSON.stringify({token,expires:Date.now()+600000}))}else{let saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');if(saved&&saved.expires>Date.now())token=saved.token;else sessionStorage.removeItem(storageKey)}}catch(e){}history.replaceState(null,'',location.pathname);
const labels={starting:'Готовлю вход…',waiting_code:'Открой Google по ссылке, войди, затем вставь полученный код ниже.',checking:'Проверяю подключение (до 2 минут)…',success:'Подключено! Вернись в Telegram. Режим Antigravity включён.',error:'Не удалось запустить вход. Проверь логи запуска и поддержку CLI хостингом. Секреты в поддержку не отправляй.',expired:'Ссылка истекла. Запусти /login снова.',check_failed:'Код отправлен, но пробный запрос не прошёл. Вход не подтверждён. Конкретная диагностика приведена ниже; её можно отправить в поддержку без секретов.',already_signed_in:'CLI сообщает о сохранённой сессии. Вернись в бот и выбери /mode agy, затем проверь обычным сообщением.'};
const errors={session_missing:'Ссылка открылась без токена входа. Открой новую кнопку из /login; не вводи адрес формы вручную.',session_invalid:'Ссылка истекла, заменена новым /login или бот был перезапущен. Запусти /login и открой новую кнопку.',https_not_confirmed:'Сервер не смог подтвердить публичный HTTPS-маршрут до своего контейнера. Проверь PUBLIC_URL, домен и исходящий HTTPS на Bothost; код Google пока не вводи.',origin_mismatch:'Домен формы не совпадает с PUBLIC_URL. Укажи в Bothost точный HTTPS-домен открытой страницы, затем обнови deployment и запусти /login снова.',invalid_json:'Не удалось прочитать запрос формы.',code_rejected:'Нет ожидающего входа или код отклонён. Проверь /status и начни /login заново.',encrypted_input_required:'Форма устарела или шифрование не прошло. Открой новую ссылку из /login.',encrypted_input_invalid:'Зашифрованный код отклонён или уже использован. Начни новый /login.'};
async function api(path,data){if(location.protocol!=='https:'||!window.isSecureContext||!crypto.subtle)throw Error('Вход доступен только в защищённом HTTPS-браузере. Открой кнопку /login во внешнем браузере.');if(!token)throw Error(errors.session_missing);let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({...data,session_token:token})});let d;try{d=await r.json()}catch(e){throw Error('Сервер вернул неожиданный ответ (HTTP '+r.status+'). Проверь deployment Bothost.')}if(!r.ok){if(d.error==='session_invalid'){try{sessionStorage.removeItem(storageKey)}catch(e){}}throw Error(errors[d.error]||'Запрос отклонён (HTTP '+r.status+').')}return d}
async function poll(){try{let d=await api('/auth/status');encryption=d.encryption||null;document.querySelector('#status').textContent=(labels[d.stage]||d.stage)+(d.diagnostic?' — '+d.diagnostic:'');
let a=document.querySelector('#google');a.hidden=!d.google_url;if(d.google_url)a.href=d.google_url;document.querySelector('#form').style.display=d.stage==='waiting_code'?'block':'none';if(['starting','waiting_code','checking'].includes(d.stage))setTimeout(poll,1500)}catch(e){document.querySelector('#status').textContent=e.message}}
function b64(bytes){return btoa(String.fromCharCode(...new Uint8Array(bytes)))}
function from64(text){return Uint8Array.from(atob(text),c=>c.charCodeAt(0))}
async function encryptCode(code){if(!encryption||encryption.protocol!=='agent-auth-v1')throw Error('Ключ формы ещё не готов. Дождись загрузки или начни /login заново.');
const pub=await crypto.subtle.importKey('spki',from64(encryption.public_key),{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
const aes=await crypto.subtle.generateKey({name:'AES-GCM',length:256},true,['encrypt']);const iv=crypto.getRandomValues(new Uint8Array(12));const encoder=new TextEncoder();
const data=encoder.encode(JSON.stringify({code,challenge:encryption.challenge}));const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode('agent-auth-v1:'+encryption.challenge)},aes,data);
const key=await crypto.subtle.encrypt({name:'RSA-OAEP'},pub,await crypto.subtle.exportKey('raw',aes));return {key:b64(key),iv:b64(iv),ciphertext:b64(ciphertext)}}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();let el=document.querySelector('#code'),code=el.value;el.value='';try{const encrypted=await encryptCode(code);code='';await api('/auth/code',{encrypted});poll()}catch(e){document.querySelector('#status').textContent=e.message}};poll();</script></html>'''


async def start_server(login):
    from .authcrypto import EncryptedLogin
    from .tlsverify import PublicTLS
    tls = PublicTLS(public_url())
    crypto_lock = asyncio.Lock()
    crypto_session = None
    crypto_token = None

    async def health(request):
        return web.json_response({'status': 'ok', 'auth_version': 'encrypted-code-v1', 'login_diagnostics': True})

    async def page(request):
        if not public_url():
            raise web.HTTPServiceUnavailable(text='Configure PUBLIC_URL with HTTPS')
        nonce = secrets.token_urlsafe(18)
        return web.Response(text=PAGE.replace('NONCE', nonce), content_type='text/html', headers={
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'Strict-Transport-Security': 'max-age=31536000', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})

    def denied(reason, status=403):
        return web.json_response({'error': reason}, status=status,
                                 headers={'Cache-Control': 'no-store'})

    async def check(request, data):
        header = request.headers.get('Authorization', '')
        # Some hosting proxies strip Authorization. A POST JSON capability is an
        # equivalent authenticated fallback, never a query parameter or logged value.
        token = header[7:] if header.startswith('Bearer ') else data.get('session_token', '')
        if not isinstance(token, str) or not token:
            return 'session_missing', None
        if not token.isascii() or len(token) > 256 or not login.valid(token):
            return 'session_invalid', None
        origin = request.headers.get('Origin')
        if origin and https_origin(origin) != public_url():
            return 'origin_mismatch', None
        if not proxy_https(request):
            # Headerless proxy: verify CA-valid public HTTPS route to THIS instance,
            # pin its immediate TCP peer, and require exact browser HTTPS Origin.
            if not origin or not public_url() or not await tls.accepts(request):
                return 'https_not_confirmed', None
        return None, token

    async def auth_data(request):
        try:
            data = await request.json()
        except (ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def status(request):
        nonlocal crypto_session, crypto_token
        data = await auth_data(request)
        if data is None:
            return denied('invalid_json', 400)
        reason, token = await check(request, data)
        if reason:
            return denied(reason)
        result = login.status()
        if result.get('stage') == 'waiting_code':
            async with crypto_lock:
                if crypto_token != token or crypto_session is None:
                    crypto_session = await asyncio.to_thread(EncryptedLogin)
                    crypto_token = token
                result['encryption'] = crypto_session.public()
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    async def code(request):
        data = await auth_data(request)
        if data is None:
            return denied('invalid_json', 400)
        reason, token = await check(request, data)
        if reason:
            return denied(reason)
        if 'code' in data or not isinstance(data.get('encrypted'), dict):
            return denied('encrypted_input_required', 400)
        if crypto_session is None or crypto_token != token:
            return denied('encrypted_input_invalid', 400)
        try:
            async with crypto_lock:
                plain = crypto_session.decrypt(data['encrypted'])
        except ValueError:
            return denied('encrypted_input_invalid', 400)
        try:
            await login.submit(plain)
        except ValueError:
            return denied('code_rejected', 400)
        return web.json_response({'ok': True}, headers={'Cache-Control': 'no-store'})

    app = web.Application(client_max_size=16384)
    app.add_routes([web.get('/_tls-probe/{nonce}', tls.handler), web.get('/health', health), web.get('/login', page),
                    web.post('/auth/status', status), web.post('/auth/code', code)])
    # No access logs: links and auth values must never be logged by this application.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
    return runner

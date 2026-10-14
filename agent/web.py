import json
import os
import secrets
from urllib.parse import urlsplit
from aiohttp import web


def public_url():
    url = os.getenv('PUBLIC_URL', '').rstrip('/')
    if not url and os.getenv('DOMAIN'):
        url = 'https://' + os.environ['DOMAIN'].strip().removeprefix('https://').rstrip('/')
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.query or p.fragment or p.path not in ('', '/'):
        return None
    return url


PAGE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение Google</title><style nonce="NONCE">body{max-width:620px;margin:40px auto;padding:20px;font:17px system-ui;background:#111827;color:#f3f4f6}button,input{padding:12px;font:inherit;margin:10px 0;max-width:100%;box-sizing:border-box}a{color:#93c5fd}input{width:100%}#form{display:none}</style>
<h1>Подключение Google</h1><p>Вход выполняется на официальном сайте Google. Здесь вводится только одноразовый код возврата, не пароль.</p>
<p id="status">Проверка…</p><p><a id="google" target="_blank" rel="noreferrer noopener" hidden>Открыть официальный вход Google</a></p>
<form id="form"><label>Одноразовый код из браузера Google<input id="code" type="password" autocomplete="off" required></label><button>Подключить</button></form>
<script nonce="NONCE">const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
const labels={starting:'Готовлю вход…',waiting_code:'Открой Google по ссылке, войди, затем вставь полученный код ниже.',checking:'Проверяю подключение (до 2 минут)…',success:'Подключено! Вернись в Telegram. Режим Antigravity включён.',error:'Не удалось запустить вход. Проверь логи запуска и поддержку CLI хостингом. Секреты в поддержку не отправляй.',expired:'Ссылка истекла. Запусти /login снова.',check_failed:'Код отправлен, но пробный запрос не прошёл. Причина может быть в коде, keyring, доступе, квоте или сети. Вход не подтверждён.',already_signed_in:'CLI сообщает о сохранённой сессии. Вернись в бот и выбери /mode agy, затем проверь обычным сообщением.'};
async function api(path,data){let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(data||{})});if(!r.ok)throw Error('Запрос отклонён или ссылка истекла. Используй /login снова.');return r.json()}
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

    def check(request):
        # TLS is terminated by Bothost's reverse proxy. Never allow direct HTTP code entry.
        if not request.secure and request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip() != 'https':
            raise web.HTTPForbidden()
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer ') or not login.valid(header[7:]):
            raise web.HTTPForbidden()
        origin = request.headers.get('Origin')
        if origin and origin != public_url():
            raise web.HTTPForbidden()

    async def status(request):
        check(request)
        return web.json_response(login.status(), headers={'Cache-Control': 'no-store'})

    async def code(request):
        check(request)
        data = await request.json()
        try:
            await login.submit(str(data.get('code', '')).strip())
        except ValueError:
            raise web.HTTPBadRequest(text='Code rejected')
        return web.json_response({'ok': True}, headers={'Cache-Control': 'no-store'})

    app = web.Application(client_max_size=8192)
    app.add_routes([web.get('/health', health), web.get('/login', page),
                    web.post('/auth/status', status), web.post('/auth/code', code)])
    # No access logs: links and auth values must never be logged by this application.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
    return runner

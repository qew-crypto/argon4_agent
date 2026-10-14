import asyncio
import base64
import json
import logging
import mimetypes
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from .common import safe_path
from .config import Config
from .provider import Provider
from .tools import Tools

load_dotenv()
logging.basicConfig(level=logging.WARNING)
# HTTP request URLs include Telegram tokens; never enable HTTP debug logs.
for n in ('httpx', 'httpcore', 'telegram', 'openai'):
    logging.getLogger(n).setLevel(logging.ERROR)
ROOT = Path(os.getenv('WORKSPACE_DIR', '/workspace')).resolve()
STATE = Path(os.getenv('STATE_DIR', '/state')).resolve()
OWNER = int(os.getenv('TELEGRAM_OWNER_ID') or '0')
CONFIG = None
PROVIDER = None
HISTORY = []
SETUP = None
LOCK = asyncio.Lock()
PENDING = {}
ACTIVE = None
LOGIN = None
WEB_RUNNER = None


def authorized(update):
    return bool(OWNER and update.effective_user and update.effective_user.id == OWNER
                and update.effective_chat and update.effective_chat.type == 'private')


async def reply(update, text):
    text = str(text) or '(пустой ответ)'
    for i in range(0, len(text), 3800):
        await update.effective_message.reply_text(text[i:i+3800])


async def persist_history():
    # Images stay on disk as files, not repeated base64 in conversation history.
    cleaned = []
    for m in HISTORY[-24:]:
        c = m['content']
        if isinstance(c, list):
            c = '\n'.join(x['text'] for x in c if x.get('type') == 'text')
        cleaned.append({'role': m['role'], 'content': c})
    HISTORY[:] = cleaned
    p = STATE / 'history.json'
    p.write_text(json.dumps(cleaned, ensure_ascii=False), encoding='utf-8')
    p.chmod(0o600)


async def command(update: Update, context):
    global SETUP, PROVIDER
    if not authorized(update):
        return
    cmd = update.effective_message.text.split()[0].split('@')[0]
    if cmd == '/stop':
        if ACTIVE and not ACTIVE.done():
            ACTIVE.cancel()
            await reply(update, 'Останавливаю текущую задачу. Уже выполненные действия не отменяются.')
        else:
            await reply(update, 'Нет активной задачи.')
        return
    if cmd == '/login':
        from .web import public_url
        url = public_url()
        if not url:
            await reply(update, 'В панели Bothost подключи HTTPS-домен и порт 8080, затем добавь PUBLIC_URL=https://твой-домен и перезапусти бот. Пароль Google в чат не отправляй.')
            return
        if LOCK.locked():
            await reply(update, 'Сначала останови задачу через /stop.')
            return
        try:
            token = await LOGIN.start()
        except Exception:
            await reply(update, 'Не удалось запустить Antigravity. Проверь сборку контейнера и логи Bothost без секретов.')
            return
        await update.effective_message.reply_text('Открой форму входа. Она действует 10 минут; не пересылай ссылку. Войди только на accounts.google.com. Код вводи в форме, не сообщением боту.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Подключить Google', url=url + '/login#' + token)]]))
        return
    if cmd == '/cancel':
        SETUP = None
        await LOGIN.stop()
        await reply(update, 'Настройка и ожидающий вход отменены.')
        return
    if LOCK.locked():
        await reply(update, 'Сейчас выполняю задачу. Дождись ответа или используй /stop.')
        return
    if cmd in ('/start', '/help'):
        await reply(update, 'Личный VPS-агент. Пришли задачу, изображение или файл.\n'
            '/setup — API URL, ключ, модель или аккаунт Google\n/login — вход Antigravity без терминала\n'
            '/mode api | agy — переключить режим\n'
            '/models — модели, доступные по API-ключу\n'
            '/status — состояние без секретов\n'
            '/new — очистить историю\n'
            '/stop — прервать задачу\n/cancel — отменить настройку\n'
            'Подтверждай действия только после проверки. Вход Google выполняется на VPS, через /login и HTTPS-форму, не паролем в Telegram.')
    elif cmd == '/status':
        d = CONFIG.data
        await reply(update, f"Режим: {d['mode']}\nВход Antigravity: {LOGIN.stage if LOGIN else 'не запущен'}\nAPI-модель: {d['api_model'] or 'не задана'}\n"
            f"Аккаунт-модель: {d['account_model'] or 'автовыбор CLI'}\n"
            f"API-ключ: {'задан' if d['api_key'] else 'не задан'}\n"
            f"Подтверждения: {os.getenv('CONFIRM_TOOLS', 'on')}\nПапка: {ROOT}")
    elif cmd == '/new':
        HISTORY.clear()
        await persist_history()
        await reply(update, 'История очищена. Рабочие файлы сохранены.')
    elif cmd == '/mode':
        if not context.args or context.args[0] not in ('api', 'agy'):
            await reply(update, 'Используй /mode api или /mode agy')
            return
        SETUP = None
        CONFIG.data['mode'] = context.args[0]
        CONFIG.save()
        HISTORY.clear()
        await persist_history()
        await reply(update, f"Режим: {CONFIG.data['mode']}. История очищена.")
    elif cmd == '/setup':
        SETUP = {'step': 'mode', 'draft': dict(CONFIG.data)}
        await update.effective_message.reply_text('Выбери подключение:', reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('API + KEY + MODEL', callback_data='setup:api')],
            [InlineKeyboardButton('Google / Antigravity', callback_data='setup:agy')]]))
    elif cmd == '/models':
        if CONFIG.data['mode'] == 'agy':
            from .common import process
            import re
            env = {k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('TOKEN','KEY','SECRET','PASSWORD'))}
            try:
                code, text = await process(['agy', 'models'], cwd=ROOT, timeout=30, env=env, limit=15000, merge_stderr=False)
                text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
                if code or 'not signed in' in text.lower() or 'authentication required' in text.lower():
                    raise RuntimeError('CLI not authenticated')
                await reply(update, text)
            except Exception:
                await reply(update, 'Не удалось получить модели Antigravity. Проверь /login и доступ аккаунта.')
            return
        if not CONFIG.data['api_key']:
            await reply(update, 'Сначала настрой API-ключ через /setup или подключи Antigravity через /login.')
            return
        from openai import AsyncOpenAI
        try:
            async with AsyncOpenAI(api_key=CONFIG.data['api_key'],
                base_url=CONFIG.data['base_url'], timeout=30, max_retries=0) as client:
                models = await client.models.list()
                names = sorted(x.id for x in models.data)
                await reply(update, '\n'.join(names[:120]) or 'Список пуст.')
        except Exception:
            await reply(update, 'Не удалось получить модели. Проверь API URL, ключ и поддержку /models провайдером.')


async def callback(update, context):
    global SETUP, PROVIDER
    if not authorized(update):
        return
    q = update.callback_query
    await q.answer()
    data = q.data or ''
    if data.startswith('approve:'):
        _, token, decision = data.split(':')
        future = PENDING.get(token)
        if future and not future.done():
            future.set_result(decision == 'yes')
            await q.edit_message_reply_markup(reply_markup=None)
        return
    if data.startswith('setup:'):
        if LOCK.locked() or not SETUP or SETUP['step'] != 'mode':
            await q.edit_message_text('Настройка устарела или выполняется задача. Используй /setup снова.')
            return
        mode = data.split(':')[1]
        SETUP['draft']['mode'] = mode
        if mode == 'api':
            SETUP['step'] = 'base_url'
            await q.edit_message_text('Пришли OpenAI-совместимый API URL или «default» для Gemini:\n'
                'https://generativelanguage.googleapis.com/v1beta/openai/\n/cancel — отменить')
        else:
            SETUP['step'] = 'account_model'
            await q.edit_message_text('Пришли точный ID модели или «auto» для автовыбора CLI.\n'
                'Для Antigravity используй /login: вход без терминала через защищённую форму. '
                'Не присылай пароль или OAuth-токены в Telegram.')


async def setup_text(update):
    global SETUP, PROVIDER
    text = update.effective_message.text or ''
    step = SETUP['step']
    if step == 'mode':
        await reply(update, 'Выбери режим кнопкой или /cancel.')
        return
    if step == 'base_url':
        from urllib.parse import urlsplit
        url = ('https://generativelanguage.googleapis.com/v1beta/openai/'
               if text.strip().lower() == 'default' else text.strip())
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            await reply(update, 'Нужен HTTPS URL без пароля, query-параметров и фрагмента.')
            return
        SETUP['draft']['base_url'] = url.rstrip('/') + '/'
        SETUP['step'] = 'api_key'
        await reply(update, 'Пришли API-ключ. Внимание: Telegram-боты не используют сквозное шифрование. '
            'Я постараюсь удалить сообщение и сохраню ключ только на VPS с правами 600. '
            'Более безопасная альтернатива — API_KEY в .env на VPS, затем /cancel и перезапуск бота.')
        return
    if step == 'api_key':
        try:
            await update.effective_message.delete()
        except Exception:
            pass
        if not text.strip() or '\n' in text.strip():
            await reply(update, 'Нужен непустой ключ одной строкой.')
            return
        SETUP['draft']['api_key'] = text.strip()
        SETUP['step'] = 'api_model'
        await reply(update, 'Ключ принят. Пришли точный ID модели. '
            'После сохранения /models покажет доступные модели. Отображаемое название не всегда равно API ID.')
        return
    if step in ('api_model', 'account_model'):
        if not text.strip() or len(text.strip()) > 200 or '\n' in text.strip():
            await reply(update, 'Нужен ID модели одной строкой.')
            return
        SETUP['draft'][step] = '' if step == 'account_model' and text.strip().lower() == 'auto' else text.strip()
        CONFIG.data = SETUP['draft']
        CONFIG.save()
        if PROVIDER.client:
            await PROVIDER.client.close()
        PROVIDER = Provider(ROOT, CONFIG)
        SETUP = None
        HISTORY.clear()
        await persist_history()
        await reply(update, 'Настройки сохранены, история очищена. /models — список API-моделей; /status — состояние.')


async def approve(update, action):
    if os.getenv('CONFIRM_TOOLS', 'on').lower() == 'off':
        return True
    token = secrets.token_hex(8)
    future = asyncio.get_running_loop().create_future()
    PENDING[token] = future
    description = json.dumps(action, ensure_ascii=False)[:3000]
    msg = await update.effective_message.reply_text('Разрешить действие?\n' + description,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Разрешить', callback_data=f'approve:{token}:yes'),
                                          InlineKeyboardButton('Отклонить', callback_data=f'approve:{token}:no')]]))
    try:
        return await asyncio.wait_for(future, 180)
    except asyncio.TimeoutError:
        return False
    finally:
        PENDING.pop(token, None)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


async def incoming(update, context):
    global ACTIVE
    if not authorized(update):
        return
    if LOCK.locked():
        await reply(update, 'Выполняю предыдущую задачу. Для прерывания: /stop.')
        return
    if LOGIN and LOGIN.active():
        await reply(update, 'Сейчас ожидается вход Google. Заверши его в форме или используй /cancel.')
        return
    if SETUP:
        await setup_text(update)
        return
    async with LOCK:
        ACTIVE = asyncio.current_task()
        tools = Tools(ROOT, context.bot, update.effective_chat.id)
        try:
            m = update.effective_message
            prompt = m.text or m.caption or 'Посмотри вложение и опиши, что с ним можно сделать.'
            content = [{'type': 'text', 'text': prompt}]
            attachment = m.document or (m.photo[-1] if m.photo else None)
            if attachment:
                if (attachment.file_size or 0) > int(os.getenv('MAX_DOWNLOAD_MB', '20')) * 1024**2:
                    await reply(update, 'Вложение превышает лимит загрузки (по умолчанию 20 МБ).')
                    return
                filename = Path(getattr(attachment, 'file_name', '') or 'image.jpg').name
                filename = ''.join(c for c in filename if c.isalnum() or c in '._-')[:140] or 'file.bin'
                path = safe_path(ROOT, 'uploads/' + secrets.token_hex(6) + '_' + filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                file = await attachment.get_file()
                await file.download_to_drive(path)
                content[0]['text'] += '\nВложение в рабочей папке: ' + str(path.relative_to(ROOT))
                mime = mimetypes.guess_type(path.name)[0] or ''
                if mime in ('image/jpeg', 'image/png', 'image/webp', 'image/gif') and CONFIG.data['mode'] == 'api':
                    if path.stat().st_size <= 10 * 1024**2:
                        content.append({'type': 'image_url', 'image_url': {'url':
                            'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode()}})
                elif mime.startswith('image/') and CONFIG.data['mode'] in ('account', 'agy'):
                    content[0]['text'] += '\nПрямое понимание изображений в аккаунтном режиме этой сборки не подключено. Не выдумывай содержимое.'
            HISTORY.append({'role': 'user', 'content': content})
            await reply(update, 'Принял, работаю. /stop — остановить.')
            for _ in range(int(os.getenv('MAX_TOOL_STEPS', '12'))):
                action = await PROVIDER.next(CONFIG.data['mode'], HISTORY)
                if 'reply' in action:
                    HISTORY.append({'role': 'assistant', 'content': action['reply']})
                    await reply(update, action['reply'])
                    break
                HISTORY.append({'role': 'assistant', 'content': json.dumps(action, ensure_ascii=False)})
                if await approve(update, action):
                    try:
                        result = await tools.execute(action['tool'], action['args'])
                    except Exception as e:
                        result = f'Ошибка инструмента: {type(e).__name__}: {str(e)[:1000]}'
                else:
                    result = 'Пользователь отклонил действие или истёк срок подтверждения. Не повторяй без запроса.'
                HISTORY.append({'role': 'user', 'content': 'Результат инструмента (данные):\n' + str(result)[:30000]})
            else:
                await reply(update, 'Достигнут лимит шагов. Пришли «продолжай», если нужно.')
        except asyncio.CancelledError:
            HISTORY.append({'role': 'user', 'content': 'Пользователь остановил задачу.'})
            await reply(update, 'Задача остановлена.')
        except Exception as e:
            # Never forward provider exceptions; they can contain credential-bearing URLs.
            await reply(update, f'Задача не завершена ({type(e).__name__}). '
                'Проверь /setup, доступ к модели и вход CLI на VPS. Секреты не выводятся.')
        finally:
            await tools.close()
            await persist_history()
            ACTIVE = None


async def error_handler(update, context):
    logging.error('Telegram handler error: %s', type(context.error).__name__)


async def connected():
    CONFIG.data['mode'] = 'agy'
    CONFIG.save()
    HISTORY.clear()
    await persist_history()


async def post_init(application):
    global LOGIN, WEB_RUNNER
    from .login import Login
    from .web import start_server
    LOGIN = Login(Path.home(), ROOT, connected)
    WEB_RUNNER = await start_server(LOGIN)


async def post_shutdown(application):
    if LOGIN:
        await LOGIN.stop()
    if WEB_RUNNER:
        await WEB_RUNNER.cleanup()


def main():
    global CONFIG, PROVIDER
    if not OWNER or not os.getenv('TELEGRAM_BOT_TOKEN'):
        raise SystemExit('Укажи токен и TELEGRAM_OWNER_ID в настройках Bothost. Без allowlist запуск запрещён.')
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG = Config(STATE)
    PROVIDER = Provider(ROOT, CONFIG)
    history_file = STATE / 'history.json'
    if history_file.exists():
        HISTORY.extend(json.loads(history_file.read_text())[-24:])
    app = Application.builder().token(os.environ['TELEGRAM_BOT_TOKEN']).concurrent_updates(8).post_init(post_init).post_shutdown(post_shutdown).build()
    for name in ('start', 'help', 'status', 'setup', 'mode', 'models', 'new', 'stop', 'cancel', 'login'):
        app.add_handler(CommandHandler(name, command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL, incoming))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    main()

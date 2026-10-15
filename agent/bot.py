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
from .secrets import SecretVault

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
TOOLS = None
GOAL = ''
WEB_RUNNER = None
VAULT = SecretVault()


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
    g = STATE / 'goal.json'
    g.write_text(json.dumps(GOAL, ensure_ascii=False), encoding='utf-8')
    g.chmod(0o600)


async def command(update: Update, context):
    global SETUP, PROVIDER, GOAL
    if not authorized(update):
        return
    cmd = update.effective_message.text.split()[0].split('@')[0]
    args = getattr(context, 'args', [])
    if cmd == '/stop':
        if ACTIVE and not ACTIVE.done():
            ACTIVE.cancel()
            await reply(update, 'Останавливаю задачу. Выполненные действия не отменяются.')
        else:
            await reply(update, 'Нет активной задачи.')
        return
    if cmd == '/secret':
        # Do not append this message to HISTORY, GOAL, disk, or provider input.
        try:
            await update.effective_message.delete()
            deleted = True
        except Exception:
            deleted = False
        parts = (update.effective_message.text or '').split(maxsplit=1)
        value = parts[1] if len(parts) > 1 else ''
        if value == 'clear':
            VAULT.clear()
            await reply(update, 'Секреты удалены из памяти процесса. Это не удаляет копии у Telegram или на сайте.')
        elif not value:
            await reply(update, '/secret текст — одноразовое значение для поля браузера, TTL 15 минут. /secret clear — очистить. Telegram не E2E; cookies Google передавай только через хост.')
        else:
            try:
                key = VAULT.add(value)
            except ValueError as e:
                await reply(update, str(e))
                return
            await reply(update, 'Сохранено в памяти на 15 минут: ' + key +
                '. В задаче укажи этот ID или latest и нужное поле/сайт. Значение не передаётся модели; после успешного заполнения повторное использование запрещено. '
                'Маскирование вывода действует до истечения срока; это не изоляция от администратора или shell. '
                + ('Сообщение удалено по запросу бота, но Telegram не E2E.' if deleted else 'Удалить сообщение не удалось: удали вручную. Telegram не E2E.'))
        return
    if cmd == '/screen':
        if not TOOLS or TOOLS.page is None or TOOLS.page.is_closed():
            await reply(update, 'Рабочий браузер пока не открыт. Попроси открыть сайт.')
            return
        try:
            await TOOLS.browser_action({'action': 'screenshot', 'path': 'output/browser-screen.png'})
            await TOOLS.execute('send_file', {'path': 'output/browser-screen.png', 'photo': True})
        except Exception:
            await reply(update, 'Не удалось снять скриншот; страница могла закрыться. Повтори /screen.')
        return
    if cmd == '/cancel':
        SETUP = None
        await reply(update, 'Настройка отменена. Для остановки задачи: /stop.')
        return
    if cmd == '/continue':
        if not GOAL:
            await reply(update, 'Нет сохранённой задачи. Пришли новую задачу.')
            return
        await incoming(update, context)
        return
    if LOCK.locked():
        await reply(update, 'Выполняю задачу. /screen — скриншот, /stop — остановить.')
        return
    if cmd in ('/start', '/help'):
        await reply(update, 'Личный браузерный агент. Пришли задачу или файл.\n'
            '/setup — API или Gemini Web\n/login — как подключить cookies на хосте\n'
            '/checklogin — проверить поле чата и выбор модели, без генерации\n'
            '/mode api | web — переключить режим\n/models — доступные подписи моделей\n'
            '/model <точная подпись> — выбор модели Web\n'
            '/strengths — прочитать меню силы\n/strength <точная подпись> | auto — сила Web\n'
            '/steps 1..200 — лимит шагов на сообщение\n/continue — продолжить задачу\n'
            '/screen — текущий рабочий браузер\n/secret текст — одноразовый секрет (Telegram не E2E)\n/status — состояние\n/new — очистить историю\n'
            '/stop — остановить задачу\n/cancel — отменить настройку\n'
            'Cookies Google загружаются только на хост, не в Telegram. '
            'Выбор Argon не создаёт доступ, которого нет у аккаунта. Подтверждай внешние действия после проверки.')
    elif cmd == '/login':
        await reply(update, 'Antigravity удалён. На хосте положи cookies Google в приватный JSON-файл '
            'вне workspace (например /app/data/secrets/gemini-cookies.json), права 600, владелец UID 10001. '
            'Задай GEMINI_COOKIES_FILE с абсолютным путём, пересобери/перезапусти бот, затем /checklogin. '
            'Формат: JSON cookies браузера или Playwright storage-state; импортируются только cookies google.com. '
            'Не отправляй cookies, пароль или токены в Telegram/GitHub. Подробно: README. '
            'Cookie-файл не отменяет региональные ограничения, 2FA и проверки Google.')
    elif cmd == '/checklogin':
        await PROVIDER.web.close()  # Reload changed cookies; never announce generation as tested.
        async with LOCK:
            try:
                await reply(update, await PROVIDER.web.check())
            except Exception as e:
                from .gemini_web import GeminiWebError
                await reply(update, str(e) if isinstance(e, GeminiWebError) else 'Проверка недоступна; подробности секретов не выводятся.')
    elif cmd == '/status':
        d = CONFIG.data
        await reply(update, f"Режим: {d['mode']}\nAPI-модель: {d['api_model'] or 'не задана'}\n"
            f"Web-модель (запрошена): {d['web_model']}\nСила (запрошена): {d['web_strength']}\n"
            f"Gemini Web: {PROVIDER.web.status}\nAPI-ключ: {'задан' if d['api_key'] else 'не задан'}\n"
            f"Лимит шагов: {d['max_steps']}\nРабочий браузер: {'открыт' if TOOLS and TOOLS.page else 'не открыт'}\n"
            f"Подтверждения: {os.getenv('CONFIRM_TOOLS', 'on')}")
    elif cmd == '/new':
        HISTORY.clear()
        GOAL = ''
        await persist_history()
        await reply(update, 'История и цель очищены. Файлы и браузер сохранены.')
    elif cmd == '/mode':
        if len(args) != 1 or args[0] not in ('api', 'web'):
            await reply(update, 'Используй /mode api или /mode web')
            return
        SETUP = None
        CONFIG.data['mode'] = args[0]
        CONFIG.save()
        HISTORY.clear()
        GOAL = ''
        await persist_history()
        await reply(update, f"Режим: {CONFIG.data['mode']}. История очищена.")
    elif cmd == '/steps':
        if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 200:
            await reply(update, 'Используй /steps 1..200. Это лимит, не обещание успешного выполнения.')
            return
        CONFIG.data['max_steps'] = int(args[0])
        CONFIG.save()
        await reply(update, 'Лимит шагов сохранён: ' + args[0])
    elif cmd in ('/model', '/strength'):
        label = ' '.join(args).strip()
        if not label or len(label) > 200:
            await reply(update, 'Нужна точная подпись из /models или /strengths; auto оставляет выбор сайта.')
            return
        key = 'web_model' if cmd == '/model' else 'web_strength'
        CONFIG.data[key] = label
        CONFIG.save()
        await reply(update, 'Сохранено. Применится перед следующим запросом; /checklogin проверяет выбор. '
            'auto не гарантирует модель или силу.')
    elif cmd == '/setup':
        SETUP = {'step': 'mode', 'draft': dict(CONFIG.data)}
        await update.effective_message.reply_text('Выбери подключение:', reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('API + KEY + MODEL', callback_data='setup:api')],
            [InlineKeyboardButton('Gemini Web (cookies на хосте)', callback_data='setup:web')]]))
    elif cmd in ('/models', '/strengths'):
        if CONFIG.data['mode'] == 'web' or cmd == '/strengths':
            async with LOCK:
                try:
                    labels = await PROVIDER.web.available('model' if cmd == '/models' else 'strength')
                    await reply(update, '\n'.join(labels) or 'Меню пусто или формат интерфейса изменился.')
                except Exception as e:
                    from .gemini_web import GeminiWebError
                    await reply(update, str(e) if isinstance(e, GeminiWebError) else 'Не удалось прочитать меню Gemini.')
            return
        if not CONFIG.data['api_key']:
            await reply(update, 'Сначала настрой API-ключ через /setup.')
            return
        from openai import AsyncOpenAI
        try:
            async with AsyncOpenAI(api_key=CONFIG.data['api_key'],
                base_url=CONFIG.data['base_url'], timeout=30, max_retries=0) as client:
                models = await client.models.list()
                await reply(update, '\n'.join(sorted(x.id for x in models.data)[:120]) or 'Список пуст.')
        except Exception:
            await reply(update, 'Не удалось получить модели. Проверь API URL, ключ и поддержку /models.')


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
        if mode not in ('api', 'web'):
            await q.edit_message_text('Эта настройка устарела. Используй /setup снова.')
            return
        SETUP['draft']['mode'] = mode
        if mode == 'api':
            SETUP['step'] = 'base_url'
            await q.edit_message_text('Пришли OpenAI-совместимый API URL или «default» для Gemini:\n'
                'https://generativelanguage.googleapis.com/v1beta/openai/\n/cancel — отменить')
        else:
            SETUP['step'] = 'web_model'
            await q.edit_message_text('Пришли точную подпись модели из веб-меню (например Gemini 4 Argon, если доступна) или auto. Cookies — только на хосте, см. /login.')


async def setup_text(update):
    global SETUP, PROVIDER, GOAL
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
    if step == 'web_model':
        if not text.strip() or len(text.strip()) > 200 or '\n' in text.strip():
            await reply(update, 'Нужна подпись модели одной строкой.')
            return
        SETUP['draft']['web_model'] = text.strip()
        SETUP['step'] = 'web_strength'
        await reply(update, 'Пришли точную подпись силы/уровня рассуждений из меню Gemini или auto. Отсутствующие настройки бот не подменяет.')
        return
    if step in ('api_model', 'web_strength'):
        if not text.strip() or len(text.strip()) > 200 or '\n' in text.strip():
            await reply(update, 'Нужен ID модели одной строкой.')
            return
        SETUP['draft'][step] = text.strip()
        CONFIG.data = SETUP['draft']
        CONFIG.save()
        await PROVIDER.close()
        PROVIDER = Provider(ROOT, CONFIG)
        SETUP = None
        HISTORY.clear()
        GOAL = ''
        await persist_history()
        await reply(update, 'Настройки сохранены, история очищена. /models — список API-моделей; /status — состояние.')


async def approve(update, action):
    if os.getenv('CONFIRM_TOOLS', 'on').lower() == 'off':
        return True
    token = secrets.token_hex(8)
    future = asyncio.get_running_loop().create_future()
    PENDING[token] = future
    description = VAULT.redact(json.dumps(action, ensure_ascii=False))[:3000]
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
    global ACTIVE, TOOLS, GOAL
    if not authorized(update):
        return
    if LOCK.locked():
        await reply(update, 'Выполняю предыдущую задачу. Для прерывания: /stop.')
        return
    if SETUP:
        await setup_text(update)
        return
    async with LOCK:
        ACTIVE = asyncio.current_task()
        if TOOLS is None:
            TOOLS = Tools(ROOT, context.bot, update.effective_chat.id, vault=VAULT)
        tools = TOOLS
        try:
            m = update.effective_message
            prompt = m.text or m.caption or 'Посмотри вложение и опиши, что с ним можно сделать.'
            continuing = prompt.split()[0] == '/continue' or prompt.strip().lower() in ('продолжай', 'продолжи')
            if continuing:
                prompt = 'Продолжай сохранённую задачу по истории и результатам инструментов. Не повторяй уже выполненные внешние действия.'
            else:
                GOAL = prompt[:20000]
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
                elif mime.startswith('image/') and CONFIG.data['mode'] == 'web':
                    content[0]['text'] += '\nПрямое понимание изображений в аккаунтном режиме этой сборки не подключено. Не выдумывай содержимое.'
            HISTORY.append({'role': 'user', 'content': content})
            await reply(update, 'Принял, работаю. /stop — остановить.')
            deadline = asyncio.get_running_loop().time() + int(os.getenv('MAX_TASK_SECONDS', '1800'))
            for _ in range(CONFIG.data['max_steps']):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await reply(update, 'Лимит времени задачи. /continue — продолжить, /screen — посмотреть браузер.')
                    break
                goal = {'role': 'user', 'content': 'Текущая цель пользователя (данные): ' + GOAL}
                async with asyncio.timeout(remaining):
                    action = await PROVIDER.next(CONFIG.data['mode'], [goal, *HISTORY[-30:]])
                if 'reply' in action:
                    text = VAULT.redact(action['reply'])
                    HISTORY.append({'role': 'assistant', 'content': text})
                    await reply(update, text)
                    break
                HISTORY.append({'role': 'assistant', 'content': VAULT.redact(json.dumps(action, ensure_ascii=False))})
                if await approve(update, action):
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError('Лимит времени задачи; используй /continue.')
                        async with asyncio.timeout(remaining):
                            result = await tools.execute(action['tool'], action['args'])
                    except Exception as e:
                        result = f'Ошибка инструмента: {type(e).__name__}: {str(e)[:1000]}'
                else:
                    result = 'Пользователь отклонил действие или истёк срок подтверждения. Не повторяй без запроса.'
                HISTORY.append({'role': 'user', 'content': 'Результат инструмента (данные):\n' + VAULT.redact(str(result))[:30000]})
            else:
                await reply(update, 'Достигнут лимит шагов. /continue — продолжить с сохранённой историей, /screen — браузер.')
        except asyncio.CancelledError:
            HISTORY.append({'role': 'user', 'content': 'Пользователь остановил задачу.'})
            await reply(update, 'Задача остановлена.')
        except Exception as e:
            # Never forward provider exceptions; they can contain credential-bearing URLs.
            from .gemini_web import GeminiWebError
            await reply(update, str(e) if isinstance(e, GeminiWebError) else
                f'Задача не завершена ({type(e).__name__}). Проверь /setup, /checklogin и доступ к модели. Секреты не выводятся.')
        finally:
            await persist_history()
            ACTIVE = None


async def error_handler(update, context):
    logging.error('Telegram handler error: %s', type(context.error).__name__)


async def post_init(application):
    global WEB_RUNNER
    from .web import start_server
    WEB_RUNNER = await start_server()


async def post_shutdown(application):
    VAULT.clear()
    if PROVIDER:
        await PROVIDER.close()
    if TOOLS:
        await TOOLS.close()
    if WEB_RUNNER:
        await WEB_RUNNER.cleanup()


def main():
    global CONFIG, PROVIDER, GOAL
    if not OWNER or not os.getenv('TELEGRAM_BOT_TOKEN'):
        raise SystemExit('Укажи токен и TELEGRAM_OWNER_ID в настройках Bothost. Без allowlist запуск запрещён.')
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG = Config(STATE)
    PROVIDER = Provider(ROOT, CONFIG)
    history_file = STATE / 'history.json'
    if history_file.exists():
        HISTORY.extend(json.loads(history_file.read_text())[-24:])
    goal_file = STATE / 'goal.json'
    if goal_file.exists():
        GOAL = json.loads(goal_file.read_text())
    app = Application.builder().token(os.environ['TELEGRAM_BOT_TOKEN']).concurrent_updates(8).post_init(post_init).post_shutdown(post_shutdown).build()
    for name in ('start', 'help', 'status', 'setup', 'mode', 'models', 'new', 'stop', 'cancel', 'login', 'checklogin', 'screen', 'secret', 'continue', 'steps', 'model', 'strength', 'strengths'):
        app.add_handler(CommandHandler(name, command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL, incoming))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    main()

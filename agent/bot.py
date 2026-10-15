import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
PENDING_TOOLS = {}
ACTIVE = None
TOOLS = None
GOAL = ''
WEB_RUNNER = None
VAULT = SecretVault()
BUILD = 'whole-goal-gate-v3'
RUN_STATE = {'phase': 'idle', 'steps': 0, 'reason': 'ещё не запускалась'}
COMMANDS = (
    ('start', 'Начать / справка'), ('help', 'Все команды'),
    ('status', 'Сборка, задача и настройки'), ('stop', 'Остановить задачу'),
    ('screen', 'Текущий скриншот браузера'), ('continue', 'Продолжить задачу'),
    ('unlimited', 'Убрать лимиты шагов и времени'),
    ('autonomy', 'browser / all / confirm'), ('steps', 'off или 1..200'),
    ('secret', 'Одноразовый секрет; Telegram не E2E'), ('new', 'Очистить историю и цель'),
    ('setup', 'Настроить API / Gemini Web'), ('cancel', 'Отменить мастер настройки'),
    ('mode', 'api или web'), ('models', 'Доступные модели'), ('model', 'Выбрать Web-модель'),
    ('strengths', 'Доступные уровни Web'), ('strength', 'Выбрать уровень Web'),
    ('login', 'Инструкция cookies на хосте'), ('checklogin', 'Проверить Gemini Web'),
    ('commands', 'Обновить меню команд Telegram'),
)
LIVE_COMMANDS = {'/status', '/help', '/start', '/autonomy', '/unlimited', '/steps', '/commands'}


async def sync_commands(telegram_bot):
    await telegram_bot.set_my_commands([BotCommand(name, description) for name, description in COMMANDS])


async def unknown_command(update, context):
    if authorized(update):
        await reply(update, 'Неизвестная команда в сборке ' + BUILD + '. /help — список, /commands — обновить меню. Если новой команды нет в /help, на хосте старая сборка: нужна пересборка из актуального main.')



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


def is_screen_request(text):
    text = (text or '').strip().lower()
    # Only a standalone screenshot request. Composite site tasks still go to the
    # model, where browser(screenshot) auto-sends its result.
    return bool(re.fullmatch(
        r'(?:(?:пожалуйста|бро)[,\s]+)?(?:(?:сделай|покажи|пришли|отправь|скинь)(?:\s+мне)?\s+)?'
        r'(?:скрин(?:шот)?|снимок|screenshot)(?:\s+(?:текущего\s+)?(?:браузера|экрана|страницы))?'
        r'(?:\s+пожалуйста)?[!?.,\s]*', text))


async def send_screen(update):
    if not TOOLS or TOOLS.page is None or TOOLS.page.is_closed():
        await reply(update, 'Рабочий браузер пока не открыт. Попроси открыть сайт.')
        return
    try:
        result = await TOOLS.browser_action({'action': 'screenshot', 'path': 'output/browser-screen.png'})
        if isinstance(result, dict) and not result.get('sent'):
            await reply(update, 'Скриншот создан, но не отправлен. Повтори /screen или проверь логи без секретов.')
    except Exception:
        await reply(update, 'Не удалось снять скриншот; страница могла закрыться. Повтори /screen.')


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
        await send_screen(update)
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
    if LOCK.locked() and cmd not in LIVE_COMMANDS:
        await reply(update, 'Выполняю задачу. Доступны /status, /screen, /stop, /autonomy, /unlimited, /steps и /help.')
        return
    if cmd == '/commands':
        try:
            await sync_commands(context.bot)
            await reply(update, 'Меню Telegram обновлено. Сборка: ' + BUILD)
        except Exception:
            await reply(update, 'Меню обновить не удалось, но ручной ввод команд работает. Сборка: ' + BUILD)
    elif cmd in ('/start', '/help'):
        await reply(update, 'Личный браузерный агент. Пришли задачу или файл.\n'
            '/setup — API или Gemini Web\n/login — как подключить cookies на хосте\n'
            '/checklogin — проверить поле чата и выбор модели, без генерации\n'
            '/mode api | web — переключить режим\n/models — доступные подписи моделей\n'
            '/model <точная подпись> — выбор модели Web\n'
            '/strengths — прочитать меню силы\n/strength <точная подпись> | auto — сила Web\n'
            '/steps off | 1..200 — лимит шагов (off = без лимита)\n/unlimited — убрать лимиты шагов и общего времени\n/autonomy browser | all | confirm — подтверждения\n/continue — продолжить задачу\n'
            '/screen — текущий рабочий браузер\n/secret текст — одноразовый секрет (Telegram не E2E)\n/status — состояние\n/new — очистить историю\n'
            '/stop — остановить задачу\n/cancel — отменить настройку\n/commands — обновить меню Telegram\n'
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
        await reply(update, f"Сборка: {BUILD}\nЗадача: {RUN_STATE['phase']}\nШагов: {RUN_STATE['steps']}\nПричина: {RUN_STATE['reason']}\nРежим: {d['mode']}\nAPI-модель: {d['api_model'] or 'не задана'}\n"
            f"Web-модель (запрошена): {d['web_model']}\nСила (запрошена): {d['web_strength']}\n"
            f"Gemini Web: {PROVIDER.web.status}\nAPI-ключ: {'задан' if d['api_key'] else 'не задан'}\n"
            f"Лимит шагов: {d['max_steps'] or 'без лимита'}\nОбщее время: {d['task_timeout'] or 'без лимита'}\nАвтономность: {d['autonomy']}\nРабочий браузер: {'открыт' if TOOLS and TOOLS.page else 'не открыт'}\n"
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
    elif cmd == '/unlimited':
        CONFIG.data.update(max_steps=0, task_timeout=0)
        CONFIG.save()
        await reply(update, 'Фиксированные лимиты шагов и общего времени отключены. Работаю до результата, необходимого ввода, ошибки/зависания или /stop. API всё равно расходует квоту и деньги.')
    elif cmd == '/autonomy':
        if len(args) != 1 or args[0] not in ('browser', 'all', 'confirm'):
            await reply(update, 'Используй /autonomy browser (браузер сам), all (все tools без кнопок, включая shell), confirm (все с подтверждением).')
            return
        CONFIG.data['autonomy'] = args[0]
        CONFIG.save()
        # Apply an owner's explicit policy change to an already pending prompt.
        for token, future in list(PENDING.items()):
            tool = PENDING_TOOLS.get(token)
            allowed = args[0] == 'all' or args[0] == 'browser' and tool in ('browser', 'send_file', 'read_file', 'list_files')
            if allowed and not future.done():
                future.set_result(True)
        await reply(update, 'Режим сохранён: ' + args[0] + '. all даёт модели выполнение shell/файловых действий без кнопок. Следи за /screen; /stop останавливает задачу.')
    elif cmd == '/steps':
        if len(args) != 1 or not (args[0].lower() == 'off' or args[0].isdigit() and 0 <= int(args[0]) <= 200):
            await reply(update, 'Используй /steps off или /steps 1..200.')
            return
        CONFIG.data['max_steps'] = 0 if args[0].lower() == 'off' else int(args[0])
        CONFIG.save()
        await reply(update, 'Лимит шагов: ' + str(CONFIG.data['max_steps'] or 'без лимита'))
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
    policy = CONFIG.data.get('autonomy', 'browser') if CONFIG else 'browser'
    if policy == 'all' or policy == 'browser' and action['tool'] in ('browser', 'send_file', 'read_file', 'list_files'):
        return True
    token = secrets.token_hex(8)
    future = asyncio.get_running_loop().create_future()
    PENDING[token] = future
    PENDING_TOOLS[token] = action['tool']
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
        PENDING_TOOLS.pop(token, None)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


async def incoming(update, context):
    global ACTIVE, TOOLS, GOAL
    if not authorized(update):
        return
    if not SETUP and is_screen_request(update.effective_message.text):
        await send_screen(update)
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
            from .api_protocol import ProtocolError
            seconds = CONFIG.data['task_timeout']
            started_at = asyncio.get_running_loop().time()
            deadline = started_at + seconds if seconds else None
            steps = idle = protocol_errors = repeated = 0
            previous_observation = None
            require_action = False
            used_browser = False
            rejected_completions = 0
            RUN_STATE.update(phase='running', steps=0, reason='выполняется')
            while True:
                # /unlimited and /steps apply between steps, without restarting.
                seconds = CONFIG.data['task_timeout']
                deadline = started_at + seconds if seconds else None
                if CONFIG.data['max_steps'] and steps >= CONFIG.data['max_steps']:
                    await reply(update, 'Достигнут заданный лимит шагов. /unlimited — убрать лимиты, /continue — продолжить.')
                    break
                remaining = deadline - asyncio.get_running_loop().time() if deadline else None
                if remaining is not None and remaining <= 0:
                    await reply(update, 'Достигнут заданный лимит времени. /unlimited и /continue — продолжить без него.')
                    break
                goal = {'role': 'user', 'content': 'Текущая цель пользователя (данные): ' + GOAL}
                try:
                    async with asyncio.timeout(remaining):
                        if require_action:
                            action = await PROVIDER.next(CONFIG.data['mode'], [goal, *HISTORY[-30:]], require_action=True)
                        else:
                            action = await PROVIDER.next(CONFIG.data['mode'], [goal, *HISTORY[-30:]])
                except ProtocolError:
                    protocol_errors += 1
                    if protocol_errors >= 3:
                        await reply(update, 'API трижды не вернул корректное действие. Задача не объявляется выполненной. Проверь поддержку tools/JSON у модели; /continue — повторить.')
                        break
                    HISTORY.append({'role': 'user', 'content': 'Protocol correction: return one valid tool call, or JSON action. Use explicit completed/needs_input/blocked status only when true. Incomplete JSON is not executed.'})
                    continue
                protocol_errors = 0
                steps += 1
                RUN_STATE['steps'] = steps
                if 'reply' in action:
                    text = VAULT.redact(action['reply'])
                    status = action.get('status', 'completed')
                    if status == 'completed':
                        from .completion import active_workflow_reason
                        snapshot = None
                        # Read-only fresh observation, no click/save/submit replay.
                        page = getattr(tools, 'page', None)
                        if page is not None and not page.is_closed() and (used_browser or continuing):
                            try:
                                async with asyncio.timeout(30):
                                    raw = await tools.browser_action({'action': 'text'})
                                snapshot = json.loads(VAULT.redact(json.dumps(raw, ensure_ascii=False)))
                            except Exception:
                                snapshot = {'error': 'Fresh browser observation unavailable; do not assume completion.'}
                        veto = active_workflow_reason(GOAL, snapshot)
                        RUN_STATE['phase'] = 'checking_completion'
                        try:
                            if veto:
                                decision = {'approved': False, 'reason': veto}
                            else:
                                remaining = deadline - asyncio.get_running_loop().time() if deadline else None
                                async with asyncio.timeout(remaining):
                                    decision = await PROVIDER.review_completion(CONFIG.data['mode'], GOAL,
                                        VAULT.redact(json.dumps(action, ensure_ascii=False)), HISTORY[-24:], snapshot)
                        except ProtocolError:
                            decision = {'approved': False, 'reason': 'Completion checker did not return a valid decision. Inspect actual state and continue; do not assert success.'}
                        RUN_STATE['phase'] = 'running'
                        if not decision['approved']:
                            rejected_completions += 1
                            require_action = True
                            reason = VAULT.redact(decision['reason'])
                            RUN_STATE['reason'] = 'продолжаю: ' + reason[:300]
                            # Rejected "done" never becomes the visible final reply.
                            HISTORY.append({'role': 'user', 'content': 'Whole-task completion REJECTED: ' + reason +
                                '\nThe original goal is NOT done. Perform the next concrete tool action now. For an active question, select/fill the answer, save and proceed; do not merely report its solution.'})
                            if rejected_completions == 1:
                                await reply(update, 'Проверка показала, что остались шаги. Продолжаю автоматически; /stop — остановить.')
                            await persist_history()
                            continue
                        RUN_STATE.update(phase='completed', reason=VAULT.redact(decision['reason'])[:300])
                    HISTORY.append({'role': 'assistant', 'content': text})
                    # A progress message must not end the agent loop.
                    if text and (status != 'progress' or idle == 0):
                        await reply(update, text)
                    if status in ('completed', 'needs_input', 'blocked'):
                        if status != 'completed':
                            RUN_STATE.update(phase=status, reason=text[:300])
                        break
                    require_action = True
                    idle += 1
                    if idle >= 5:
                        await reply(update, 'Модель пять раз подряд прислала текст вместо действий. Останавливаю пустой цикл, чтобы не расходовать API бесконечно. Задача не завершена; /continue — повторить.')
                        break
                    HISTORY.append({'role': 'user', 'content': 'Continue the actual task now. A plan/update is not completion. Call the next tool; finish_task only after verification, genuine blocking error or required missing input. Do not ask permission for routine browser actions.'})
                    continue
                idle = 0
                require_action = False
                if action['tool'] == 'browser':
                    used_browser = True
                HISTORY.append({'role': 'assistant', 'content': VAULT.redact(json.dumps(action, ensure_ascii=False))})
                if await approve(update, action):
                    try:
                        remaining = deadline - asyncio.get_running_loop().time() if deadline else None
                        if remaining is not None and remaining <= 0:
                            raise TimeoutError('Лимит времени задачи; используй /continue.')
                        async with asyncio.timeout(remaining):
                            result = await tools.execute(action['tool'], action['args'])
                    except Exception as e:
                        result = f'Ошибка инструмента: {type(e).__name__}: {str(e)[:1000]}'
                else:
                    result = 'Пользователь отклонил действие или истёк срок подтверждения. Не повторяй без нового запроса.'
                    HISTORY.append({'role': 'user', 'content': result})
                    await reply(update, 'Действие не разрешено; задача приостановлена. /continue — по твоему новому запросу.')
                    break
                observation = VAULT.redact(str(result))[:30000]
                HISTORY.append({'role': 'user', 'content': 'Результат инструмента (данные):\n' + observation})
                # A stuck loop guard is NOT a step budget: unlimited meaningful
                # actions are allowed; eight identical action/results are not.
                fingerprint = json.dumps(action, sort_keys=True, ensure_ascii=False) + observation
                repeated = repeated + 1 if fingerprint == previous_observation else 1
                previous_observation = fingerprint
                if repeated >= 8:
                    await reply(update, 'Восемь одинаковых действий дали один и тот же результат. Задача приостановлена как зависшая, а не выполненная. Проверь /screen; /continue — повторить.')
                    break
                # Persist progress after every step; browser stays open.
                await persist_history()
        except asyncio.CancelledError:
            RUN_STATE.update(phase='stopped', reason='команда /stop или отмена')
            HISTORY.append({'role': 'user', 'content': 'Пользователь остановил задачу.'})
            await reply(update, 'Задача остановлена.')
        except Exception as e:
            RUN_STATE.update(phase='error', reason=type(e).__name__)
            # Never forward provider exceptions; they can contain credential-bearing URLs.
            from .gemini_web import GeminiWebError
            await reply(update, str(e) if isinstance(e, GeminiWebError) else
                f'Задача не завершена ({type(e).__name__}). Проверь /setup, /checklogin и доступ к модели. Секреты не выводятся.')
        finally:
            if RUN_STATE['phase'] in ('running', 'checking_completion'):
                RUN_STATE.update(phase='paused', reason='ошибка протокола, заданный лимит или защита от зависания; задача не завершена')
            await persist_history()
            ACTIVE = None


async def error_handler(update, context):
    logging.error('Telegram handler error: %s', type(context.error).__name__)


async def post_init(application):
    global WEB_RUNNER
    from .web import start_server
    WEB_RUNNER = await start_server()
    try:
        await sync_commands(application.bot)
    except Exception as e:
        logging.warning('Command menu sync failed: %s', type(e).__name__)


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
    CONFIG.save()  # Persist execution-policy migration once at startup.
    PROVIDER = Provider(ROOT, CONFIG)
    history_file = STATE / 'history.json'
    if history_file.exists():
        HISTORY.extend(json.loads(history_file.read_text())[-24:])
    goal_file = STATE / 'goal.json'
    if goal_file.exists():
        GOAL = json.loads(goal_file.read_text())
    app = Application.builder().token(os.environ['TELEGRAM_BOT_TOKEN']).concurrent_updates(8).post_init(post_init).post_shutdown(post_shutdown).build()
    for name, _ in COMMANDS:
        app.add_handler(CommandHandler(name, command))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL, incoming))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    main()

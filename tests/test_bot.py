import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from agent import bot
    from agent.config import Config
    from agent.provider import Provider
except ImportError:
    bot = None


@unittest.skipIf(bot is None, 'install requirements.txt for Telegram handler tests')
class BotTests(unittest.IsolatedAsyncioTestCase):
    def make_update(self, user_id, chat_type='private', text=''):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=7, type=chat_type),
            effective_message=SimpleNamespace(text=text, delete=AsyncMock(), reply_text=AsyncMock()))

    def test_owner_allowed(self):
        old = bot.OWNER
        try:
            bot.OWNER = 7
            self.assertTrue(bot.authorized(self.make_update(7)))
        finally:
            bot.OWNER = old

    def test_non_owner_denied(self):
        old = bot.OWNER
        try:
            bot.OWNER = 7
            self.assertFalse(bot.authorized(self.make_update(8)))
            self.assertFalse(bot.authorized(self.make_update(7, 'group')))
        finally:
            bot.OWNER = old

    async def test_key_is_not_echoed(self):
        old_setup = bot.SETUP
        try:
            bot.SETUP = {'step': 'api_key', 'draft': {}}
            u = self.make_update(7, text='LOCAL_TEST_KEY')
            await bot.setup_text(u)
            u.effective_message.delete.assert_awaited_once()
            self.assertEqual(bot.SETUP['draft']['api_key'], 'LOCAL_TEST_KEY')
            for call in u.effective_message.reply_text.await_args_list:
                self.assertNotIn('LOCAL_TEST_KEY', str(call))
        finally:
            bot.SETUP = old_setup

    async def test_config_wizard_save(self):
        old = (bot.CONFIG, bot.PROVIDER, bot.SETUP, bot.STATE, list(bot.HISTORY))
        with tempfile.TemporaryDirectory() as t:
            try:
                from pathlib import Path
                bot.STATE = Path(t)
                bot.CONFIG = Config(t)
                bot.PROVIDER = Provider(Path(t), bot.CONFIG)
                bot.SETUP = {'step': 'api_model', 'draft': dict(bot.CONFIG.data)}
                await bot.setup_text(self.make_update(7, text='model-id-from-provider'))
                self.assertEqual(Config(t).data['api_model'], 'model-id-from-provider')
                self.assertIsNone(bot.SETUP)
            finally:
                bot.CONFIG, bot.PROVIDER, bot.SETUP, bot.STATE, hist = old
                bot.HISTORY[:] = hist

    async def test_secret_command_deleted_not_in_history_and_not_echoed(self):
        old = (bot.OWNER, list(bot.HISTORY), bot.GOAL)
        try:
            bot.OWNER = 7
            u = self.make_update(7, text='/secret LOCAL_HANDLER_SECRET')
            await bot.command(u, SimpleNamespace(args=['LOCAL_HANDLER_SECRET']))
            u.effective_message.delete.assert_awaited_once()
            self.assertEqual(bot.HISTORY, old[1])
            self.assertEqual(bot.GOAL, old[2])
            for call in u.effective_message.reply_text.await_args_list:
                self.assertNotIn('LOCAL_HANDLER_SECRET', str(call))
            self.assertEqual(bot.VAULT.resolve('latest')[1], 'LOCAL_HANDLER_SECRET')
        finally:
            bot.VAULT.clear()
            bot.OWNER, hist, bot.GOAL = old
            bot.HISTORY[:] = hist

    async def test_screen_works_during_task_and_sends_current_browser(self):
        from unittest.mock import MagicMock
        old = (bot.OWNER, bot.TOOLS)
        try:
            bot.OWNER = 7
            tools = SimpleNamespace(page=MagicMock(), browser_action=AsyncMock(), execute=AsyncMock())
            tools.page.is_closed.return_value = False
            bot.TOOLS = tools
            async with bot.LOCK:
                await bot.command(self.make_update(7, text='/screen'), SimpleNamespace(args=[]))
            tools.browser_action.assert_awaited_once_with({'action': 'screenshot', 'path': 'output/browser-screen.png'})
            tools.execute.assert_not_awaited()  # screenshot auto-delivers inside Tools
        finally:
            bot.OWNER, bot.TOOLS = old

    async def test_steps_validation(self):
        from pathlib import Path
        old = (bot.OWNER, bot.CONFIG)
        with tempfile.TemporaryDirectory() as t:
            try:
                bot.OWNER = 7; bot.CONFIG = Config(t)
                await bot.command(self.make_update(7, text='/steps 80'), SimpleNamespace(args=['80']))
                self.assertEqual(Config(t).data['max_steps'], 80)
                await bot.command(self.make_update(7, text='/steps 9999'), SimpleNamespace(args=['9999']))
                self.assertEqual(Config(t).data['max_steps'], 80)
            finally:
                bot.OWNER, bot.CONFIG = old

    async def test_multistep_continue_preserves_goal_and_browser_tools(self):
        from pathlib import Path
        from unittest.mock import patch
        old = (bot.OWNER, bot.CONFIG, bot.PROVIDER, bot.TOOLS, bot.STATE, bot.ROOT,
               bot.SETUP, bot.GOAL, list(bot.HISTORY), bot.ACTIVE)
        with tempfile.TemporaryDirectory() as t:
            try:
                bot.OWNER = 7; bot.STATE = bot.ROOT = Path(t)
                bot.CONFIG = Config(t); bot.CONFIG.data['max_steps'] = 2
                bot.SETUP = None; bot.HISTORY.clear(); bot.GOAL = ''
                tools = SimpleNamespace(execute=AsyncMock(return_value='saved'), close=AsyncMock())
                bot.TOOLS = tools
                step = {'tool': 'list_files', 'args': {'path': '.'}}
                bot.PROVIDER = SimpleNamespace(next=AsyncMock(side_effect=[step, step, {'reply': 'finished'}]))
                first = self.make_update(7, text='Сделай большую задачу')
                first.effective_message.document = None; first.effective_message.photo = []
                with patch.object(bot, 'approve', AsyncMock(return_value=True)):
                    await bot.incoming(first, SimpleNamespace(bot=None))
                    self.assertEqual(tools.execute.await_count, 2)
                    self.assertEqual(bot.GOAL, 'Сделай большую задачу')
                    following = self.make_update(7, text='/continue')
                    following.effective_message.document = None; following.effective_message.photo = []
                    await bot.command(following, SimpleNamespace(bot=None, args=[]))
                self.assertEqual(bot.GOAL, 'Сделай большую задачу')
                self.assertIs(bot.TOOLS, tools)
                tools.close.assert_not_awaited()
                self.assertEqual(bot.HISTORY[-1]['content'], 'finished')
                self.assertEqual(bot.PROVIDER.next.await_args.args[1][0]['content'],
                    'Текущая цель пользователя (данные): Сделай большую задачу')
            finally:
                (bot.OWNER, bot.CONFIG, bot.PROVIDER, bot.TOOLS, bot.STATE, bot.ROOT,
                 bot.SETUP, bot.GOAL, hist, bot.ACTIVE) = old
                bot.HISTORY[:] = hist

    async def test_unlimited_loop_continues_after_progress_and_more_than_old_budget(self):
        from pathlib import Path
        from unittest.mock import patch
        old = (bot.OWNER, bot.CONFIG, bot.PROVIDER, bot.TOOLS, bot.STATE, bot.ROOT,
               bot.SETUP, bot.GOAL, list(bot.HISTORY), bot.ACTIVE)
        with tempfile.TemporaryDirectory() as t:
            try:
                bot.OWNER = 7; bot.STATE = bot.ROOT = Path(t)
                bot.CONFIG = Config(t); bot.CONFIG.data.update(max_steps=0, task_timeout=0)
                bot.SETUP = None; bot.HISTORY.clear(); bot.GOAL = ''
                bot.TOOLS = SimpleNamespace(execute=AsyncMock(side_effect=[f'step {i}' for i in range(45)]))
                actions = [{'reply': 'Сейчас открою', 'status': 'progress'}]
                actions += [{'tool': 'browser', 'args': {'action': 'text'}} for _ in range(45)]
                actions += [{'reply': 'Проверено, готово', 'status': 'completed', 'evidence': 'UI confirmed'}]
                bot.PROVIDER = SimpleNamespace(next=AsyncMock(side_effect=actions))
                update = self.make_update(7, text='Долгая задача')
                update.effective_message.document = None; update.effective_message.photo = []
                with patch.object(bot, 'approve', AsyncMock(return_value=True)):
                    await bot.incoming(update, SimpleNamespace(bot=None))
                self.assertEqual(bot.TOOLS.execute.await_count, 45)
                self.assertEqual(bot.HISTORY[-1]['content'], 'Проверено, готово')
                self.assertEqual(bot.PROVIDER.next.await_count, 47)
            finally:
                (bot.OWNER, bot.CONFIG, bot.PROVIDER, bot.TOOLS, bot.STATE, bot.ROOT,
                 bot.SETUP, bot.GOAL, hist, bot.ACTIVE) = old
                bot.HISTORY[:] = hist

    async def test_browser_autonomy_skips_approval_and_unlimited_command(self):
        from pathlib import Path
        from unittest.mock import patch
        old = (bot.OWNER, bot.CONFIG)
        with tempfile.TemporaryDirectory() as t:
            try:
                bot.OWNER = 7; bot.CONFIG = Config(t)
                bot.CONFIG.data['autonomy'] = 'browser'
                u = self.make_update(7, text='/unlimited')
                with patch.dict('os.environ', {'CONFIRM_TOOLS': 'on'}):
                    for name in ('browser', 'send_file', 'read_file', 'list_files'):
                        self.assertTrue(await bot.approve(u, {'tool': name, 'args': {}}))
                u.effective_message.reply_text.assert_not_awaited()
                bot.CONFIG.data.update(max_steps=40, task_timeout=1800)
                await bot.command(u, SimpleNamespace(args=[]))
                self.assertEqual(Config(t).data['max_steps'], 0)
                self.assertEqual(Config(t).data['task_timeout'], 0)
            finally:
                bot.OWNER, bot.CONFIG = old

    async def test_stop_cancels_unlimited_task(self):
        import asyncio
        old = (bot.OWNER, bot.ACTIVE)
        try:
            bot.OWNER = 7
            task = asyncio.create_task(asyncio.sleep(60))
            bot.ACTIVE = task
            await bot.command(self.make_update(7, text='/stop'), SimpleNamespace(args=[]))
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            bot.OWNER, bot.ACTIVE = old

    async def test_plain_screenshot_request_during_running_task(self):
        from unittest.mock import MagicMock
        old = (bot.OWNER, bot.TOOLS, bot.SETUP)
        try:
            bot.OWNER = 7; bot.SETUP = None
            tools = SimpleNamespace(page=MagicMock(), browser_action=AsyncMock(return_value={'sent': True}))
            tools.page.is_closed.return_value = False; bot.TOOLS = tools
            async with bot.LOCK:
                await bot.incoming(self.make_update(7, text='Пришли скрин браузера'), SimpleNamespace(bot=None))
            tools.browser_action.assert_awaited_once()
            self.assertFalse(bot.is_screen_request('Открой example.com и пришли скрин'))
        finally:
            bot.OWNER, bot.TOOLS, bot.SETUP = old

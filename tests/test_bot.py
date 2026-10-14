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

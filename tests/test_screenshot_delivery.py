import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from telegram.error import BadRequest
from agent.tools import Tools


class ScreenshotTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, root, bot):
        tools = Tools(root, bot, 1)
        async def capture(path, **kwargs):
            Path(path).write_bytes(b'fixture-image')
        tools.context = MagicMock()
        tools.page = MagicMock(); tools.page.is_closed.return_value = False
        tools.page.screenshot = AsyncMock(side_effect=capture)
        return tools

    async def test_photo_failure_uses_document_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as t:
            bot = MagicMock(); bot.send_photo = AsyncMock(side_effect=BadRequest('PHOTO_INVALID_DIMENSIONS'))
            bot.send_document = AsyncMock()
            tools = await self.prepare(Path(t), bot)
            result = await tools.browser_action({'action': 'screenshot'})
            self.assertTrue(result['sent'])
            bot.send_document.assert_awaited_once()
            await tools.execute('send_file', {'path': result['path'], 'photo': True})
            bot.send_document.assert_awaited_once()

    async def test_send_failure_never_claims_delivery_and_can_retry(self):
        with tempfile.TemporaryDirectory() as t:
            bot = MagicMock(); bot.send_photo = AsyncMock(side_effect=RuntimeError('NETWORK'))
            bot.send_document = AsyncMock()
            tools = await self.prepare(Path(t), bot)
            result = await tools.browser_action({'action': 'screenshot'})
            self.assertFalse(result['sent'])
            bot.send_document.assert_not_awaited()
            self.assertFalse(tools.sent_screens)
            bot.send_photo = AsyncMock()
            await tools.execute('send_file', {'path': result['path'], 'photo': True})
            bot.send_photo.assert_awaited_once()

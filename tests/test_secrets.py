import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from agent.secrets import SecretVault
from agent.tools import Tools


class VaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_use_expiry_redaction_clear(self):
        vault = SecretVault(ttl=0.02)
        key = vault.add('LOCAL_TEST_SECRET')
        self.assertEqual(vault.resolve('latest'), (key, 'LOCAL_TEST_SECRET'))
        vault.consume(key)
        with self.assertRaises(ValueError):
            vault.resolve(key)
        self.assertNotIn('LOCAL_TEST_SECRET', vault.redact('URL=LOCAL_TEST_SECRET'))
        import asyncio
        await asyncio.sleep(0.04)
        self.assertFalse(vault._items)
        vault.add('another'); vault.clear()
        self.assertFalse(vault._items)

    async def test_fill_secret_never_returns_snapshot(self):
        with tempfile.TemporaryDirectory() as t:
            vault = SecretVault(); key = vault.add('LOCAL_TEST_SECRET')
            tools = Tools(Path(t), None, 1, vault=vault)
            field = MagicMock(); field.fill = AsyncMock()
            tools.page = MagicMock(); tools.page.is_closed.return_value = False
            tools.page.locator.return_value = field
            tools.context = MagicMock()
            result = await tools.browser_action({'action': 'fill_secret', 'selector': '#password', 'secret_id': key})
            field.fill.assert_awaited_once_with('LOCAL_TEST_SECRET')
            self.assertNotIn('LOCAL_TEST_SECRET', result)
            with self.assertRaises(ValueError):
                vault.resolve(key)
            vault.clear()

    async def test_fill_failure_sanitized_and_not_consumed(self):
        with tempfile.TemporaryDirectory() as t:
            vault = SecretVault(); key = vault.add('LOCAL_TEST_SECRET')
            tools = Tools(Path(t), None, 1, vault=vault)
            field = MagicMock(); field.fill = AsyncMock(side_effect=RuntimeError('LOCAL_TEST_SECRET'))
            tools.page = MagicMock(); tools.page.is_closed.return_value = False
            tools.page.locator.return_value = field; tools.context = MagicMock()
            with self.assertRaises(ValueError) as e:
                await tools.browser_action({'action': 'fill_secret', 'selector': '#password', 'secret_id': key})
            self.assertNotIn('LOCAL_TEST_SECRET', str(e.exception))
            self.assertEqual(vault.resolve(key)[1], 'LOCAL_TEST_SECRET')
            vault.clear()

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

    async def test_split_fio_without_model_or_repeated_secret(self):
        from agent.secrets import field_values
        fields = [{'selector': '#surname', 'part': 'surname'}, {'selector': '#name', 'part': 'given_name'},
                  {'selector': '#patronymic', 'part': 'patronymic'}]
        self.assertEqual(field_values('Иванов Иван Иванович', fields), ['Иванов', 'Иван', 'Иванович'])
        import json
        compound = json.dumps({'фамилия': 'Иванов Петров', 'имя': 'Иван', 'отчество': 'Иванович'})
        self.assertEqual(field_values(compound, fields)[0], 'Иванов Петров')
        with self.assertRaises(ValueError):
            field_values('Иванов Петров Иван Иванович', fields)
        self.assertEqual(field_values('{"email":"LOCAL_EMAIL","password":"LOCAL_PASSWORD"}',
            [{'selector': '#email', 'key': 'email'}, {'selector': '#password', 'key': 'password'}], 'json'),
            ['LOCAL_EMAIL', 'LOCAL_PASSWORD'])

    async def test_fill_secret_form_consumes_once_and_never_returns_values(self):
        with tempfile.TemporaryDirectory() as t:
            vault = SecretVault(); key = vault.add('Иванов Иван Иванович')
            tools = Tools(Path(t), None, 1, vault=vault)
            targets = []
            for _ in range(3):
                field = MagicMock(); field.fill = AsyncMock(); field.count = AsyncMock(return_value=1)
                field.is_visible = AsyncMock(return_value=True); field.is_editable = AsyncMock(return_value=True)
                targets.append(field)
            tools.page = MagicMock(); tools.page.is_closed.return_value = False
            tools.page.locator.side_effect = targets; tools.context = MagicMock()
            result = await tools.browser_action({'action': 'fill_secret_form', 'secret_id': key,
                'fields': [{'selector': '#surname', 'part': 'surname'}, {'selector': '#name', 'part': 'given_name'},
                           {'selector': '#patronymic', 'part': 'patronymic'}]})
            for field, value in zip(targets, ['Иванов', 'Иван', 'Иванович']):
                field.fill.assert_awaited_once_with(value)
                self.assertNotIn(value, result)
                self.assertNotIn(value, vault.redact('На странице: ' + value))
            with self.assertRaises(ValueError):
                vault.resolve(key)
            vault.clear()

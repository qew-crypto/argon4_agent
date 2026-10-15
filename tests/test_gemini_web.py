import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from agent.config import Config
from agent.gemini_web import GeminiWeb, GeminiWebError, load_cookies, prompt_text
from agent.provider import Provider


class CookieTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.path = self.root / 'cookies.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)

    def test_json_and_storage_state_filter_domains(self):
        cookies = [{'name': 'fixture', 'value': 'LOCAL_TEST_ONLY', 'domain': '.google.com', 'sameSite': 'no_restriction'},
                   {'name': 'excluded', 'value': 'LOCAL_TEST_ONLY', 'domain': '.evilgoogle.com'}]
        for raw in (cookies, {'cookies': cookies, 'origins': [{'origin': 'https://evil.test'}]}):
            self.write(raw)
            result = load_cookies(self.path, self.workspace)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['sameSite'], 'None')
            self.assertNotIn('origins', result[0])

    def test_no_world_readable_secret(self):
        self.write([{'name': 'x', 'value': 'LOCAL_TEST_ONLY', 'domain': '.google.com'}])
        self.path.chmod(0o644)
        with self.assertRaises(GeminiWebError) as e:
            load_cookies(self.path, self.workspace)
        self.assertNotIn('LOCAL_TEST_ONLY', str(e.exception))

    def test_no_symlinks_workspace_relative_or_fifo(self):
        self.write([])
        link = self.root / 'link'; link.symlink_to(self.path)
        fifo = self.root / 'fifo'; os.mkfifo(fifo, 0o600)
        for path in (link, 'relative.json', self.workspace / 'cookie.json', fifo):
            with self.assertRaises(GeminiWebError):
                load_cookies(path, self.workspace)

    def test_malformed_empty_and_oversized(self):
        for raw in ({}, [], [{'name': 'x', 'domain': '.google.com'}], 'bad'):
            self.write(raw)
            with self.assertRaises(GeminiWebError):
                load_cookies(self.path, self.workspace)
        self.path.write_text('x' * (1024 * 1024 + 1))
        with self.assertRaises(GeminiWebError):
            load_cookies(self.path, self.workspace)

    def test_migration_and_default_steps(self):
        (self.root / 'settings.json').write_text(json.dumps({'mode': 'agy', 'account_model': 'old-model'}))
        c = Config(self.root)
        self.assertEqual(c.data['mode'], 'web')
        self.assertNotIn('account_model', c.data)
        self.assertEqual(c.data['max_steps'], 0)

    def test_prompt_unicode_and_image_not_sent_as_base64(self):
        result = prompt_text('system', [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Задача'}, {'type': 'image_url', 'image_url': {'url': 'SECRET_IMAGE'}}]}])
        self.assertIn('Задача', result)
        self.assertNotIn('SECRET_IMAGE', result)
        with self.assertRaises(GeminiWebError):
            prompt_text('system', [{'role': 'user', 'content': 'a' * 100}], max_chars=50)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_dispatch_and_close(self):
        with tempfile.TemporaryDirectory() as t:
            provider = Provider(Path(t), Config(t))
            provider.web.generate = AsyncMock(return_value='{"reply":"ok","status":"completed","evidence":"fixture"}')
            self.assertEqual(await provider.next('web', [{'role': 'user', 'content': 'hi'}]), {'reply': 'ok', 'status': 'completed', 'evidence': 'fixture'})
            provider.web.close = AsyncMock()
            await provider.close()
            provider.web.close.assert_awaited_once()

    async def test_api_dispatch_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            config = Config(t); config.data.update(api_key='LOCAL_TEST_ONLY', api_model='fixture')
            provider = Provider(Path(t), config)
            create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"reply":"API"}'))]))
            provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock())
            self.assertEqual(await provider.next('api', []), {'reply': 'API', 'status': 'progress', 'evidence': ''})
            await provider.close()

    async def test_profile_must_be_outside_workspace(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {'GEMINI_PROFILE_DIR': t}):
            web = GeminiWeb(Path(t), Config(t))
            with self.assertRaises(GeminiWebError):
                web.profile_path()

    async def test_errors_never_expose_playwright_secrets(self):
        with tempfile.TemporaryDirectory() as t:
            web = GeminiWeb(Path(t), Config(t))
            web.open_chat = AsyncMock(side_effect=RuntimeError('LOCAL_TEST_SECRET_IN_URL'))
            with self.assertRaises(GeminiWebError) as e:
                await web.generate('system', [])
            self.assertNotIn('LOCAL_TEST_SECRET', str(e.exception))

    async def test_cancellation_closes_context(self):
        with tempfile.TemporaryDirectory() as t:
            web = GeminiWeb(Path(t), Config(t))
            web.open_chat = AsyncMock(side_effect=asyncio.CancelledError())
            web.close = AsyncMock()
            with self.assertRaises(asyncio.CancelledError):
                await web.generate('system', [])
            web.close.assert_awaited_once()

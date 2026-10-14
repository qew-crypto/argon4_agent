import asyncio
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from agent.common import safe_path, process
from agent.config import Config
from agent.provider import parse_action
from agent.tools import Tools


class CoreTests(unittest.TestCase):
    def test_path_traversal(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for x in ('../escape', '/etc/passwd'):
                with self.assertRaises(ValueError):
                    safe_path(root, x)
            self.assertEqual(safe_path(root, 'nested/a.txt'), root / 'nested/a.txt')

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'escape').symlink_to('/etc')
            with self.assertRaises(ValueError):
                safe_path(root, 'escape/passwd')

    def test_reply(self):
        self.assertEqual(parse_action('hello'), {'reply': 'hello'})
        self.assertEqual(parse_action('```json\n{"reply":"ok"}\n```'), {'reply': 'ok'})
        self.assertEqual(parse_action('{"tool":"shell","args":{"command":"pwd"}}')['tool'], 'shell')
        self.assertEqual(parse_action('[1]'), {'reply': '[1]'})

    def test_config_persistence_permissions(self):
        with tempfile.TemporaryDirectory() as t:
            c = Config(t)
            c.data.update(api_key='TEST_PLACEHOLDER', api_model='test-model')
            c.save()
            self.assertEqual(Config(t).data['api_model'], 'test-model')
            self.assertEqual(c.path.stat().st_mode & 0o777, 0o600)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_and_truncation(self):
        with tempfile.TemporaryDirectory() as t:
            code, text = await process(['/bin/sh', '-c', 'printf abcdef'], cwd=t, timeout=2, limit=3)
            self.assertEqual(code, 0)
            self.assertIn('[вывод обрезан]', text)

    async def test_timeout(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(asyncio.TimeoutError):
                await process(['/bin/sh', '-c', 'sleep 5'], cwd=t, timeout=0.05)

    async def test_write_read_zip(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            tools = Tools(root, None, 0)
            await tools.execute('write_file', {'path': 'out/test.txt', 'text': 'Привет'})
            self.assertEqual(await tools.execute('read_file', {'path': 'out/test.txt'}), 'Привет')
            await tools.execute('zip_files', {'paths': ['out'], 'output': 'out/archive.zip'})
            with zipfile.ZipFile(root / 'out/archive.zip') as z:
                self.assertEqual(z.namelist(), ['out/test.txt'])
            with self.assertRaises(ValueError):
                await tools.execute('send_file', {'path': '/etc/passwd'})

    async def test_shell_env_scrub(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {'API_KEY': 'DO_NOT_LEAK'}):
            tools = Tools(Path(t), None, 0)
            text = await tools.execute('shell', {'command': 'printf "%s" "$API_KEY"'})
            self.assertNotIn('DO_NOT_LEAK', text)

    async def test_unknown_tool(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):
                await Tools(Path(t), None, 0).execute('bad', {})


if __name__ == '__main__':
    unittest.main()

class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_json_not_mixed_with_diagnostics(self):
        with tempfile.TemporaryDirectory() as t:
            code, text = await process(['/bin/sh', '-c', 'printf ok; printf diagnostic >&2'],
                cwd=t, timeout=2, merge_stderr=False)
            self.assertEqual(text, 'ok')

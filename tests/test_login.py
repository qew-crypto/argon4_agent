import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from agent.login import Login, official_link
try:
    from agent.web import public_url
except ImportError:
    public_url = None

class LoginTests(unittest.IsolatedAsyncioTestCase):
    def test_only_official_google_origin(self):
        self.assertIsNone(official_link('\x1b]8;;https://evil.example/login\x07'))
        self.assertEqual(official_link('\x1b]8;;https://accounts.google.com/o/oauth2/auth?client_id=demo\x07'),
            'https://accounts.google.com/o/oauth2/auth?client_id=demo')
        self.assertIsNone(official_link('https://accounts.google.com.evil.test/foo'))

    async def test_no_raw_terminal_in_status(self):
        with tempfile.TemporaryDirectory() as t:
            login = Login(Path(t), Path(t), AsyncMock())
            login.buffer = 'RAW_SECRET_TEST'
            self.assertNotIn('RAW_SECRET_TEST', str(login.status()))
            with self.assertRaises(ValueError):
                await login.submit('test-code')

    async def test_terminal_injection_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            login = Login(Path(t), Path(t), AsyncMock())
            login.stage = 'waiting_code'
            with self.assertRaises(ValueError):
                await login.submit('abc\n/shell')
            with self.assertRaises(ValueError):
                await login.submit('abc\x1b[1;1R')

    def test_https_url(self):
        if not public_url:
            self.skipTest('install dependencies for web tests')
        with patch.dict(os.environ, {'PUBLIC_URL':'https://example.test', 'DOMAIN':''}):
            self.assertEqual(public_url(), 'https://example.test')
        with patch.dict(os.environ, {'PUBLIC_URL':'http://example.test', 'DOMAIN':''}):
            self.assertIsNone(public_url())

    def test_token_expiration(self):
        import time
        with tempfile.TemporaryDirectory() as t:
            login = Login(Path(t), Path(t), AsyncMock())
            login.token = 'demo-token'
            login.deadline = time.monotonic() + 10
            self.assertTrue(login.valid('demo-token'))
            self.assertFalse(login.valid('wrong-token'))
            login.deadline = time.monotonic() - 1
            self.assertFalse(login.valid('demo-token'))

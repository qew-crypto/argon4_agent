import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from agent.login import Login


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_stuck_startup_probes_saved_login(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock());login.stage='starting'
            with patch.object(login,'verify',AsyncMock()) as verify,patch('builtins.print'):
                await login.startup_watchdog(wait=0)
                await login.verify_task
            self.assertEqual(login.stage,'checking')
            self.assertTrue(login.startup_fallback)
            verify.assert_awaited_once_with(delay=0)
            await login.stop()

    async def test_waiting_code_is_not_interrupted(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock());login.stage='waiting_code';login.url='https://accounts.google.com'
            with patch.object(login,'verify',AsyncMock()) as verify:
                await login.startup_watchdog(wait=0)
            verify.assert_not_awaited()
            self.assertEqual(login.stage,'waiting_code')

    async def test_cancel_removes_watchdog(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock());login.stage='starting'
            login.startup_task=asyncio.create_task(login.startup_watchdog(wait=60))
            await login.stop()
            self.assertIsNone(login.startup_task)
            self.assertEqual(login.stage,'idle')

    async def test_fallback_error_has_startup_context(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock());login.startup_fallback=True
            with patch('agent.login.capture_process',AsyncMock(return_value=(1,'','authentication required'))),patch('builtins.print'):
                await login.verify(delay=0)
            self.assertIn('STARTUP_TIMEOUT',login.diagnostic)
            self.assertIn('AUTH_REQUIRED',login.diagnostic)

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from agent.diagnostics import known_failure, decode_envelope, diagnosis
from agent.common import capture_process
from agent.login import Login


class DiagnosticTests(unittest.TestCase):
    def test_allowlisted_message_does_not_echo_secret(self):
        raw='permission_denied token=LOCAL_TEST_SECRET https://example.test/oauth?code=SECRET'
        result=diagnosis(known_failure(raw),exit_code=1,phase='model')
        self.assertIn('ACCESS_DENIED',result)
        self.assertNotIn('LOCAL_TEST_SECRET',result)
        self.assertNotIn('https://',result)

    def test_oauth_and_keyring_differ(self):
        self.assertEqual(known_failure('invalid_grant'),'OAUTH_CODE_REJECTED')
        self.assertEqual(known_failure('failed to save credentials in secret service'),'KEYRING_ERROR')
        self.assertEqual(known_failure('authentication required'),'AUTH_REQUIRED')

    def test_quota_not_random_code_digits(self):
        self.assertIsNone(known_failure('4/0AX429LOCALRANDOMCODE'))
        self.assertEqual(known_failure('HTTP 429 Too Many Requests'),'QUOTA_EXHAUSTED')

    def test_json_envelope_with_startup_noise(self):
        data=decode_envelope('starting service\n{"status":"SUCCESS","response":"OK"}\n')
        self.assertEqual(data['response'],'OK')
        self.assertIsNone(decode_envelope('startup failed'))


class DiagnosticAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdout_stderr_separated(self):
        with tempfile.TemporaryDirectory() as t:
            code,out,err=await capture_process(['/bin/sh','-c','printf response; printf error >&2'],cwd=t,timeout=2)
            self.assertEqual((code,out,err),(0,'response','error'))

    async def test_failed_check_reports_auth_without_raw_output(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock())
            with patch('agent.login.capture_process',AsyncMock(return_value=(1,'','authentication required TOKEN=LOCAL_PRIVATE'))),patch('builtins.print'):
                await login.verify(delay=0)
            self.assertEqual(login.stage,'check_failed')
            self.assertIn('AUTH_REQUIRED',login.diagnostic)
            self.assertNotIn('LOCAL_PRIVATE',str(login.status()))
            login.on_success.assert_not_awaited()

    async def test_success_runs_connection_callback(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock())
            with patch('agent.login.capture_process',AsyncMock(return_value=(0,'{"status":"SUCCESS","response":"OK"}',''))),patch('builtins.print'):
                await login.verify(delay=0)
            self.assertEqual(login.stage,'success')
            login.on_success.assert_awaited_once()

    async def test_explicit_oauth_rejection_prevents_model_probe(self):
        with tempfile.TemporaryDirectory() as t:
            login=Login(Path(t),Path(t),AsyncMock());login.oauth_failure='OAUTH_CODE_REJECTED'
            with patch('agent.login.capture_process',AsyncMock()) as capture,patch('builtins.print'):
                await login.verify(delay=0)
            self.assertIn('OAUTH_CODE_REJECTED',login.diagnostic)
            capture.assert_not_awaited()

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

class ExcerptTests(unittest.TestCase):
    def test_unknown_message_is_actionable(self):
        from agent.diagnostics import error_excerpt
        text=error_excerpt({'error': {'message':'Agent backend returned an unexpected state'}})
        self.assertIn('unexpected state',text)

    def test_credentials_and_urls_redacted(self):
        from agent.diagnostics import redacted_excerpt
        text=redacted_excerpt('Error at https://example.test/?x=PRIVATE\naccess_token=PRIVATE_VALUE\nmail: user@example.test\nBearer PRIVATE_BEARER_VALUE')
        for secret in ('PRIVATE','user@example.test','https://'):
            self.assertNotIn(secret,text)

    def test_long_unlabelled_token_redacted(self):
        from agent.diagnostics import redacted_excerpt
        secret='aZ9_'*12
        self.assertNotIn(secret,redacted_excerpt('Failed request '+secret))

    def test_nested_credentials_not_selected(self):
        from agent.diagnostics import error_excerpt
        result=error_excerpt({'error':{'message':'Failed to start backend','credentials':{'refresh_token':'PRIVATE'}}})
        self.assertIn('Failed to start backend',result)
        self.assertNotIn('PRIVATE',result)

    def test_excerpt_is_bounded(self):
        from agent.diagnostics import redacted_excerpt
        self.assertLessEqual(len(redacted_excerpt('Something failed. '*1000)),500)

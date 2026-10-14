import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
try:
    from aiohttp import ClientSession
    from agent.web import start_server, https_origin, proxy_https, PAGE
except ImportError:
    start_server = None


@unittest.skipIf(start_server is None, 'install dependencies for HTTP tests')
class OriginTests(unittest.TestCase):
    def test_canonical_origin(self):
        self.assertEqual(https_origin('https://EXAMPLE.test:443/'), 'https://example.test')
        self.assertIsNone(https_origin('http://example.test'))
        self.assertIsNone(https_origin('https://:password@example.test'))
        self.assertIsNone(https_origin('https://example.test/path'))

    def test_proxy_header_without_bypass(self):
        for headers in ({'X-Forwarded-Proto':'https'}, {'Forwarded':'for=1.2.3.4;proto="https";host=example.test'}):
            self.assertTrue(proxy_https(SimpleNamespace(secure=False, headers=headers)))
        self.assertFalse(proxy_https(SimpleNamespace(secure=False, headers={})))
        self.assertFalse(proxy_https(SimpleNamespace(secure=False, headers={'Forwarded':'proto=http'})))

    def test_refresh_keeps_tab_scoped_token(self):
        self.assertIn('sessionStorage', PAGE)
        self.assertIn('Date.now()+600000', PAGE)
        self.assertNotIn('localStorage', PAGE)


@unittest.skipIf(start_server is None, 'install dependencies for HTTP tests')
class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ, {'PORT':'0', 'PUBLIC_URL':'https://qa.example.test'})
        self.env.start()
        self.login=SimpleNamespace(valid=lambda token:token=='test-capability',
            status=lambda:{'stage':'waiting_code'}, submit=AsyncMock())
        self.runner=await start_server(self.login)
        port=next(iter(self.runner.sites))._server.sockets[0].getsockname()[1]
        self.url=f'http://127.0.0.1:{port}'
        self.client=ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        self.env.stop()

    async def post(self, headers, data=None, path='/auth/status'):
        async with self.client.post(self.url+path,headers=headers,json=data or {}) as r:
            return r.status,await r.json()

    async def test_missing_https_distinct(self):
        code,data=await self.post({'Authorization':'Bearer test-capability'})
        self.assertEqual((code,data['error']),(403,'https_not_confirmed'))

    async def test_missing_invalid_tokens_distinct(self):
        headers={'X-Forwarded-Proto':'https'}
        self.assertEqual((await self.post(headers))[1]['error'],'session_missing')
        self.assertEqual((await self.post(headers,{'session_token':'wrong'}))[1]['error'],'session_invalid')

    async def test_json_token_survives_stripped_authorization(self):
        headers={'X-Forwarded-Proto':'https','Origin':'https://qa.example.test'}
        code,data=await self.post(headers,{'session_token':'test-capability'})
        self.assertEqual(code,200)
        self.assertEqual(data['stage'],'waiting_code')

    async def test_wrong_origin_does_not_submit_code(self):
        headers={'X-Forwarded-Proto':'https','Origin':'https://evil.test','Authorization':'Bearer test-capability'}
        code,data=await self.post(headers,{'code':'TEST_CODE'},'/auth/code')
        self.assertEqual(data['error'],'origin_mismatch')
        self.login.submit.assert_not_awaited()

    async def test_standard_forwarded_https(self):
        code,data=await self.post({'Forwarded':'for=1.2.3.4;proto=https','Origin':'https://qa.example.test'},
                                  {'session_token':'test-capability'})
        self.assertEqual(code,200)

    async def test_invalid_json(self):
        async with self.client.post(self.url+'/auth/status',data='invalid') as r:
            self.assertEqual(r.status,400)
            self.assertEqual((await r.json())['error'],'invalid_json')

    async def test_plaintext_code_no_longer_accepted(self):
        headers={'X-Forwarded-Proto':'https','Origin':'https://qa.example.test','Authorization':'Bearer test-capability'}
        code,data=await self.post(headers,{'code':'LOCAL_TEST_CODE'},'/auth/code')
        self.assertEqual((code,data['error']),(400,'encrypted_input_required'))
        self.login.submit.assert_not_awaited()

    async def test_encrypted_code_roundtrip_once(self):
        from test_authcrypto import envelope
        headers={'X-Forwarded-Proto':'https','Origin':'https://qa.example.test','Authorization':'Bearer test-capability'}
        _,status=await self.post(headers)
        encrypted=envelope(status['encryption'])
        code,data=await self.post(headers,{'encrypted':encrypted},'/auth/code')
        self.assertEqual(code,200)
        self.login.submit.assert_awaited_once_with('LOCAL_TEST_CODE')
        code,data=await self.post(headers,{'encrypted':encrypted},'/auth/code')
        self.assertEqual((code,data['error']),(400,'encrypted_input_invalid'))
        self.login.submit.assert_awaited_once()

    async def test_verified_headerless_peer_fallback(self):
        with patch('agent.tlsverify.PublicTLS.accepts',AsyncMock(return_value=True)):
            code,data=await self.post({'Origin':'https://qa.example.test'}, {'session_token':'test-capability'})
            self.assertEqual(code,200)
            self.assertIn('encryption',data)

    async def test_unverified_headerless_peer_denied(self):
        with patch('agent.tlsverify.PublicTLS.accepts',AsyncMock(return_value=False)):
            code,data=await self.post({'Origin':'https://qa.example.test'}, {'session_token':'test-capability'})
            self.assertEqual((code,data['error']),(403,'https_not_confirmed'))

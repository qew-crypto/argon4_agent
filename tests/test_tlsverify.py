import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
try:
    from agent.tlsverify import PublicTLS, socket_peer
    from aiohttp import web
except ImportError:
    PublicTLS = None


def req(ip='127.0.0.1', nonce=''):
    return SimpleNamespace(transport=SimpleNamespace(get_extra_info=lambda name:(ip,12345)),
                           match_info={'nonce':nonce})


@unittest.skipIf(PublicTLS is None, 'install aiohttp')
class TLSProofTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_origin_fails_closed(self):
        self.assertFalse(await PublicTLS(None).accepts(req()))

    async def test_socket_peer_not_forwarded_header(self):
        self.assertEqual(socket_peer(req('::ffff:127.0.0.1')),'127.0.0.1')
        tls=PublicTLS('https://example.test');tls.peer='127.0.0.1'
        with patch.object(tls,'probe',AsyncMock(return_value=True)):
            self.assertTrue(await tls.accepts(req('127.0.0.1')))
            self.assertFalse(await tls.accepts(req('10.0.0.99')))

    async def test_unknown_challenge_endpoint_rejected(self):
        tls=PublicTLS('https://example.test')
        with self.assertRaises(web.HTTPNotFound):await tls.handler(req(nonce='wrong'))
        tls.nonce='expected';tls.proof='private-probe-proof'
        response=await tls.handler(req(nonce='expected'))
        self.assertEqual(response.status,200)
        self.assertEqual(tls.candidate,'127.0.0.1')

    async def test_network_failure_fails_closed(self):
        tls=PublicTLS('https://example.test')
        with patch('agent.tlsverify.ClientSession',side_effect=RuntimeError('TLS error')):
            self.assertFalse(await tls.probe())
            self.assertIsNone(tls.peer)
            self.assertIsNone(tls.nonce)

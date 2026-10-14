"""Verify our public TLS route and pin its immediate TCP proxy peer.
No credential/code is sent in the probe; no SSL validation is disabled.
"""
import asyncio
import ipaddress
import secrets
import time
from aiohttp import ClientSession, ClientTimeout, web


def socket_peer(request):
    peer = request.transport.get_extra_info('peername') if request.transport else None
    if not peer:
        return None
    try:
        ip = ipaddress.ip_address(peer[0])
        return str(ip.ipv4_mapped if getattr(ip, 'ipv4_mapped', None) else ip)
    except ValueError:
        return None


class PublicTLS:
    def __init__(self, origin):
        self.origin = origin
        self.lock = asyncio.Lock()
        self.nonce = self.proof = self.candidate = None
        self.peer = None
        self.expires = 0

    async def handler(self, request):
        nonce = request.match_info.get('nonce', '')
        if not self.nonce or not nonce.isascii() or len(nonce) > 100 or not secrets.compare_digest(nonce, self.nonce):
            raise web.HTTPNotFound()
        self.candidate = socket_peer(request)
        return web.json_response({'proof': self.proof}, headers={'Cache-Control': 'no-store'})

    async def probe(self):
        if not self.origin or not self.origin.startswith('https://'):
            return False
        async with self.lock:
            if self.peer and self.expires > time.monotonic():
                return True
            self.peer = None
            self.nonce, self.proof = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.candidate = None
            try:
                # System CA validation remains enabled. Reject redirects to other routes/domains.
                async with ClientSession(timeout=ClientTimeout(total=15)) as client:
                    async with client.get(self.origin + '/_tls-probe/' + self.nonce,
                                          allow_redirects=False) as response:
                        if response.status != 200:
                            return False
                        data = await response.json()
                if data.get('proof') != self.proof or not self.candidate:
                    return False
                self.peer = self.candidate
                self.expires = time.monotonic() + 300
                return True
            except Exception:
                return False
            finally:
                self.nonce = self.proof = self.candidate = None

    async def accepts(self, request):
        if not await self.probe():
            return False
        return socket_peer(request) == self.peer

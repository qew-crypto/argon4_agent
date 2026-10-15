import unittest
from aiohttp.test_utils import AioHTTPTestCase
from agent.web import make_app


class HealthTests(AioHTTPTestCase):
    async def get_application(self):
        return make_app()

    async def test_health_and_removed_login_routes(self):
        response = await self.client.get('/health')
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['auth_version'], 'gemini-web-host-cookies-v1')
        for path in ('/login', '/auth/status', '/auth/code'):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404)

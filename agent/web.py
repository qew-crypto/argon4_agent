"""Health endpoint only. No public login/cookie upload or arbitrary browser API."""
import os
from aiohttp import web


def make_app():
    async def health(request):
        return web.json_response({'ok': True, 'auth_version': 'gemini-web-host-cookies-v1'},
                                 headers={'Cache-Control': 'no-store'})
    app = web.Application(client_max_size=1024)
    app.add_routes([web.get('/health', health)])
    return app


async def start_server():
    runner = web.AppRunner(make_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', '8080'))).start()
    return runner

"""
VOLT MUSIC Engine Entrypoint.
Initializes MusicController, registers aiohttp API routes, and starts the service.
"""
import asyncio
from datetime import datetime
from aiohttp import web
from config import BOT_PORT
from controllers import MusicController, build_routes


def create_app(ctrl: MusicController | None = None) -> web.Application:
    controller = ctrl or MusicController()
    app = web.Application()
    app.add_routes(build_routes(controller))
    return app


async def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting VOLT MUSIC Hi-Fi Audio Engine...")
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', BOT_PORT)
    await site.start()
    print(f"VOLT MUSIC Engine controller running on http://0.0.0.0:{BOT_PORT}...")
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    asyncio.run(main())

"""Receives adderall's webhooks and DMs them to the owner.

adderall POSTs Discord-style messages (`{"content": "..."}`) to URLs set in its
settings; for now that is each ClickUp task newly assigned to you (adderall
#101). Point one of those URLs at this server:

    http://host.docker.internal:<webhook_port>/adderall/<ADDERALL_WEBHOOK_TOKEN>

The token in the path is the only thing standing between the port and anyone
who can reach it, so a request without the right one gets a 404.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from harmony.adderall.link import AdderallLink

log = logging.getLogger(__name__)

MAX_CONTENT = 2000  # what a Discord webhook itself accepts
MAX_BODY = 16 * 1024


class WebhookServer:
    def __init__(self, link: "AdderallLink", port: int, token: str, host: str = "0.0.0.0"):
        self.link, self.port, self.host = link, port, host
        self._token = token.encode()
        self._runner: web.AppRunner | None = None
        self._sending: set[asyncio.Task] = set()

    def app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/adderall/{token}", self.handle)
        return app

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app(), access_log=None)  # the access log would print the token
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()
        log.info("adderall: webhook receiver listening on port %s", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def handle(self, request: web.Request) -> web.Response:
        if not hmac.compare_digest(request.match_info["token"].encode(), self._token):
            raise web.HTTPNotFound()
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="expected JSON") from None
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise web.HTTPBadRequest(text='expected {"content": "..."}')
        # Answer now and DM in the background: rewording takes a model call,
        # and adderall waits on this reply before carrying on with its sync.
        task = asyncio.create_task(self.link.notify(content.strip()[:MAX_CONTENT]))
        self._sending.add(task)
        task.add_done_callback(self._sending.discard)
        return web.Response(status=204)

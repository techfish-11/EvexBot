"""
linkapi.py
チャンネル 1269637837041565769 に投稿された HTTP/HTTPS リンク直近10件を
返す REST API サーバー（aiohttp）を Discord Bot と同時に起動する Cog。

エンドポイント:
  GET http://localhost:8080/links
  -> { "links": ["https://...", ...] }
"""

import re
import logging
from typing import List

import aiohttp
from aiohttp import web
from discord.ext import commands

LINK_CHANNEL_ID: int = 1269637837041565769
API_HOST: str = "0.0.0.0"
API_PORT: int = 8080
LINK_LIMIT: int = 10

URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

logger = logging.getLogger(__name__)


class LinkApiCog(commands.Cog):
    """チャンネルの直近リンクを返す API サーバー Cog"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        app = web.Application()
        app.router.add_get("/links", self._handle_links)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, API_HOST, API_PORT)
        await site.start()
        logger.info("LinkAPI server started on %s:%s", API_HOST, API_PORT)

    async def cog_unload(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            logger.info("LinkAPI server stopped.")

    async def _fetch_links(self) -> List[str]:
        """チャンネル履歴を遡り、HTTPリンクを最大 LINK_LIMIT 件収集する。"""
        channel = self.bot.get_channel(LINK_CHANNEL_ID)
        if channel is None:
            logger.warning("Channel %s not found.", LINK_CHANNEL_ID)
            return []

        links: List[str] = []
        async for message in channel.history(limit=200):
            for url in URL_PATTERN.findall(message.content):
                links.append(url)
                if len(links) >= LINK_LIMIT:
                    return links
        return links

    async def _handle_links(self, request: web.Request) -> web.Response:
        """GET /links ハンドラ"""
        links = await self._fetch_links()
        return web.json_response({"links": links})


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkApiCog(bot))

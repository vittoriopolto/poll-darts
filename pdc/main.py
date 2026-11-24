"""Avvio del bot.

- Con PUBLIC_URL / RENDER_EXTERNAL_URL impostato: server web (webhook) per Render.
- Senza, oppure con `python -m pdc --polling`: long polling, comodo in locale.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from telegram import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, Update
from telegram.ext import AIORateLimiter, Application

from . import handlers
from .config import Config
from .db import DB

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("pdc")

WEBHOOK_PATH = "/telegram"


def make_application(cfg: Config, webhook: bool) -> Application:
    builder = Application.builder().token(cfg.bot_token).rate_limiter(AIORateLimiter(max_retries=3))
    if webhook:
        builder = builder.updater(None)
    app = builder.build()
    app.bot_data["cfg"] = cfg
    handlers.setup(app)
    return app


async def on_startup(app: Application, cfg: Config) -> None:
    app.bot_data["db"] = await DB.connect(cfg.database_url, cfg.close_minutes_before)
    if not cfg.admin_ids:
        log.warning("ADMIN_IDS vuoto: nessuno potrà usare i comandi admin. Usa /mio_id per scoprire il tuo ID.")
    try:
        await app.bot.set_my_commands(handlers.BOT_COMMANDS_PRIVATE, scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_my_commands(handlers.BOT_COMMANDS_GROUP, scope=BotCommandScopeAllGroupChats())
    except Exception:
        log.exception("set_my_commands fallito")


def run_webhook(cfg: Config) -> None:
    app = make_application(cfg, webhook=True)
    url = cfg.public_url.rstrip("/") + WEBHOOK_PATH

    async def telegram(request: Request) -> Response:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.webhook_secret:
            return Response(status_code=403)
        await app.update_queue.put(Update.de_json(await request.json(), app.bot))
        return Response()

    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True})

    async def root(_: Request) -> Response:
        return PlainTextResponse("PDC poll bot attivo 🎯")

    @asynccontextmanager
    async def lifespan(_):
        async with app:  # initialize/shutdown del bot
            await on_startup(app, cfg)
            await app.bot.set_webhook(url, secret_token=cfg.webhook_secret, allowed_updates=Update.ALL_TYPES)
            log.info("Webhook impostato su %s", url)
            await app.start()
            yield
            await app.stop()
            await app.bot_data["db"].close()

    web = Starlette(routes=[
        Route(WEBHOOK_PATH, telegram, methods=["POST"]),
        Route("/health", health, methods=["GET", "HEAD"]),
        Route("/", root, methods=["GET", "HEAD"]),
    ], lifespan=lifespan)
    uvicorn.run(web, host="0.0.0.0", port=cfg.port, log_level="info")


def run_polling(cfg: Config) -> None:
    app = make_application(cfg, webhook=False)

    async def post_init(a: Application) -> None:
        await on_startup(a, cfg)

    async def post_shutdown(a: Application) -> None:
        if "db" in a.bot_data:
            await a.bot_data["db"].close()

    app.post_init = post_init
    app.post_shutdown = post_shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    cfg = Config.from_env()
    if "--polling" in sys.argv or not cfg.public_url:
        log.info("Avvio in modalità polling")
        run_polling(cfg)
    else:
        log.info("Avvio in modalità webhook")
        run_webhook(cfg)


if __name__ == "__main__":
    main()

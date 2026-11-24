"""Configurazione letta dalle variabili d'ambiente."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _int_list(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    admin_ids: frozenset[int]
    tz: ZoneInfo
    # URL pubblico del servizio (su Render è RENDER_EXTERNAL_URL, impostato in automatico).
    public_url: str | None
    port: int
    webhook_secret: str
    # Minuti prima dell'inizio sessione in cui si chiudono i sondaggi.
    close_minutes_before: int
    # Titolo grande sulla copertina.
    cover_title: str
    # "invio": gruppo mutato solo mentre il bot pubblica copertina e sondaggi (default).
    # "chiusura": resta mutato finché i sondaggi non si chiudono.
    mute_mode: str

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ["BOT_TOKEN"]
        return cls(
            bot_token=token,
            database_url=os.environ["DATABASE_URL"],
            admin_ids=_int_list(os.environ.get("ADMIN_IDS", "")),
            tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Rome")),
            public_url=(os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or None),
            port=int(os.environ.get("PORT", "8000")),
            # Il segreto viene confrontato con l'header X-Telegram-Bot-Api-Secret-Token.
            # Telegram accetta solo A-Z a-z 0-9 _ - (max 256).
            webhook_secret=re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("WEBHOOK_SECRET") or "")[:256]
            or token.split(":")[-1][:32],
            close_minutes_before=int(os.environ.get("CLOSE_MINUTES_BEFORE", "5")),
            cover_title=os.environ.get("COVER_TITLE", "PDC180 POLL"),
            mute_mode=os.environ.get("MUTE_MODE", "invio").strip().lower(),
        )

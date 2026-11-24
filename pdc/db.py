"""Accesso al database Postgres (Neon) con asyncpg."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


@dataclass
class Match:
    id: int
    session_id: int
    position: int
    player1: str
    player2: str
    poll_id: str | None
    poll_message_id: int | None
    winner: int | None

    def label(self) -> str:
        return f"{self.player1} - {self.player2}"


@dataclass
class Event:
    id: int
    name: str
    created_by: int
    created_at: datetime
    ended_at: datetime | None


@dataclass
class Session:
    id: int
    name: str
    starts_at: datetime
    created_by: int
    chat_id: int | None
    launched_at: datetime | None
    closed_at: datetime | None
    event_id: int | None = None


@dataclass
class RankRow:
    user_id: int
    display: str
    points: int
    answered: int  # voti su partite che hanno già un risultato


def clean_dsn(dsn: str) -> str:
    """Toglie i parametri che asyncpg non conosce (Neon aggiunge channel_binding=require)."""
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def display_name(username: str | None, first: str | None, last: str | None) -> str:
    if username:
        return f"@{username}"
    full = " ".join(p for p in (first, last) if p)
    return full or "?"


class DB:
    def __init__(self, pool: asyncpg.Pool, close_minutes_before: int = 5):
        self.pool = pool
        self.close_delta = timedelta(minutes=close_minutes_before)

    @classmethod
    async def connect(cls, dsn: str, close_minutes_before: int = 5) -> "DB":
        # statement_cache_size=0: compatibile anche con l'endpoint "pooler" (PgBouncer) di Neon.
        pool = await asyncpg.create_pool(clean_dsn(dsn), min_size=1, max_size=5, statement_cache_size=0)
        db = cls(pool, close_minutes_before)
        async with pool.acquire() as con:
            await con.execute(SCHEMA)
        return db

    async def close(self) -> None:
        await self.pool.close()

    # ---------- utenti ----------

    async def upsert_user(self, user_id: int, username: str | None, first: str | None,
                          last: str | None, chat_id: int | None = None, status: str | None = None) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                """INSERT INTO users (user_id, username, first_name, last_name)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (user_id) DO UPDATE
                   SET username = EXCLUDED.username, first_name = EXCLUDED.first_name,
                       last_name = EXCLUDED.last_name, updated_at = now()""",
                user_id, username, first, last,
            )
            if chat_id is not None:
                await con.execute(
                    """INSERT INTO group_members (chat_id, user_id, status) VALUES ($1, $2, COALESCE($3, 'member'))
                       ON CONFLICT (chat_id, user_id) DO UPDATE
                       SET last_seen = now(), status = COALESCE($3, group_members.status)""",
                    chat_id, user_id, status,
                )

    async def count_members(self, chat_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT count(*) FROM group_members WHERE chat_id = $1 AND status NOT IN ('left', 'kicked')", chat_id)

    # ---------- eventi ----------

    _EV = "SELECT id, name, created_by, created_at, ended_at FROM events"

    async def create_event(self, name: str, created_by: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO events (name, created_by) VALUES ($1, $2) RETURNING id", name, created_by)

    async def active_event(self) -> Event | None:
        row = await self.pool.fetchrow(self._EV + " WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")
        return Event(**dict(row)) if row else None

    async def find_event(self, ref: str) -> Event | None:
        """Cerca per ID (anche '#3') o per nome (senza maiuscole; se ce ne sono più, il più recente)."""
        r = ref.strip()
        if r.lstrip("#").isdigit():
            row = await self.pool.fetchrow(self._EV + " WHERE id = $1", int(r.lstrip("#")))
            if row:
                return Event(**dict(row))
        row = await self.pool.fetchrow(self._EV + " WHERE lower(name) = lower($1) ORDER BY id DESC LIMIT 1", r)
        return Event(**dict(row)) if row else None

    async def latest_event(self) -> Event | None:
        """L'evento in corso, altrimenti l'ultimo chiuso."""
        row = await self.pool.fetchrow(self._EV + " ORDER BY (ended_at IS NULL) DESC, id DESC LIMIT 1")
        return Event(**dict(row)) if row else None

    async def end_event(self, event_id: int) -> None:
        await self.pool.execute("UPDATE events SET ended_at = now() WHERE id = $1 AND ended_at IS NULL", event_id)

    async def list_events(self, limit: int = 10) -> list[tuple[Event, int]]:
        rows = await self.pool.fetch(
            "SELECT e.id, e.name, e.created_by, e.created_at, e.ended_at, count(s.id) AS n "
            "FROM events e LEFT JOIN sessions s ON s.event_id = e.id GROUP BY e.id ORDER BY e.id DESC LIMIT $1",
            limit)
        return [(Event(**{k: r[k] for k in ("id", "name", "created_by", "created_at", "ended_at")}), r["n"])
                for r in rows]

    async def get_event(self, event_id: int) -> Event | None:
        row = await self.pool.fetchrow(self._EV + " WHERE id = $1", event_id)
        return Event(**dict(row)) if row else None

    async def set_session_event(self, session_id: int, event_id: int | None) -> None:
        await self.pool.execute("UPDATE sessions SET event_id = $2 WHERE id = $1", session_id, event_id)

    # ---------- sessioni ----------

    async def create_session(self, name: str, starts_at: datetime, created_by: int,
                             matches: list[tuple[str, str]], event_id: int | None = None) -> int:
        async with self.pool.acquire() as con, con.transaction():
            sid = await con.fetchval(
                "INSERT INTO sessions (name, starts_at, created_by, event_id) VALUES ($1, $2, $3, $4) RETURNING id",
                name, starts_at, created_by, event_id,
            )
            await con.executemany(
                "INSERT INTO matches (session_id, position, player1, player2) VALUES ($1, $2, $3, $4)",
                [(sid, i, p1, p2) for i, (p1, p2) in enumerate(matches, start=1)],
            )
        return sid

    async def get_session(self, session_id: int) -> Session | None:
        row = await self.pool.fetchrow(
            "SELECT id, name, starts_at, created_by, chat_id, launched_at, closed_at, event_id FROM sessions WHERE id = $1",
            session_id)
        return Session(**dict(row)) if row else None

    async def list_sessions(self, limit: int = 10) -> list[Session]:
        rows = await self.pool.fetch(
            "SELECT id, name, starts_at, created_by, chat_id, launched_at, closed_at, event_id "
            "FROM sessions ORDER BY id DESC LIMIT $1", limit)
        return [Session(**dict(r)) for r in rows]

    async def latest_session_for_chat(self, chat_id: int) -> Session | None:
        row = await self.pool.fetchrow(
            "SELECT id, name, starts_at, created_by, chat_id, launched_at, closed_at, event_id FROM sessions "
            "WHERE chat_id = $1 ORDER BY launched_at DESC NULLS LAST, id DESC LIMIT 1", chat_id)
        return Session(**dict(row)) if row else None

    async def delete_session(self, session_id: int) -> bool:
        res = await self.pool.execute("DELETE FROM sessions WHERE id = $1", session_id)
        return res.endswith(" 1")

    async def mark_launched(self, session_id: int, chat_id: int) -> None:
        await self.pool.execute(
            "UPDATE sessions SET chat_id = $2, launched_at = now() WHERE id = $1", session_id, chat_id)

    async def save_permissions(self, session_id: int, perms_json: str | None) -> None:
        await self.pool.execute("UPDATE sessions SET saved_permissions = $2::jsonb WHERE id = $1",
                                session_id, perms_json)

    async def get_saved_permissions(self, session_id: int) -> str | None:
        return await self.pool.fetchval("SELECT saved_permissions::text FROM sessions WHERE id = $1", session_id)

    async def last_saved_permissions(self, chat_id: int) -> str | None:
        return await self.pool.fetchval(
            "SELECT saved_permissions::text FROM sessions WHERE chat_id = $1 AND saved_permissions IS NOT NULL "
            "ORDER BY launched_at DESC LIMIT 1", chat_id)

    async def mark_closed(self, session_id: int) -> None:
        await self.pool.execute("UPDATE sessions SET closed_at = now() WHERE id = $1", session_id)

    async def sessions_to_close(self, now: datetime) -> list[Session]:
        """Sessioni lanciate, con sondaggi ancora aperti e orario di chiusura già passato."""
        rows = await self.pool.fetch(
            "SELECT id, name, starts_at, created_by, chat_id, launched_at, closed_at, event_id FROM sessions "
            "WHERE launched_at IS NOT NULL AND closed_at IS NULL AND starts_at - $2::interval <= $1",
            now, self.close_delta)
        return [Session(**dict(r)) for r in rows]

    # ---------- partite ----------

    async def get_matches(self, session_id: int) -> list[Match]:
        rows = await self.pool.fetch(
            "SELECT id, session_id, position, player1, player2, poll_id, poll_message_id, winner "
            "FROM matches WHERE session_id = $1 ORDER BY position", session_id)
        return [Match(**dict(r)) for r in rows]

    async def get_match(self, match_id: int) -> Match | None:
        row = await self.pool.fetchrow(
            "SELECT id, session_id, position, player1, player2, poll_id, poll_message_id, winner "
            "FROM matches WHERE id = $1", match_id)
        return Match(**dict(row)) if row else None

    async def set_poll(self, match_id: int, poll_id: str, message_id: int) -> None:
        await self.pool.execute(
            "UPDATE matches SET poll_id = $2, poll_message_id = $3 WHERE id = $1", match_id, poll_id, message_id)

    async def total_votes(self, session_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT count(*) FROM votes v JOIN matches m ON m.id = v.match_id WHERE m.session_id = $1", session_id)

    async def set_winner(self, match_id: int, winner: int | None) -> None:
        await self.pool.execute("UPDATE matches SET winner = $2 WHERE id = $1", match_id, winner)

    # ---------- voti ----------

    async def record_vote(self, poll_id: str, user_id: int, choice: int | None,
                          now: datetime) -> tuple[str, int | None]:
        """Registra (o cancella, se choice è None) un voto.

        Ritorna (esito, chat_id del gruppo); esito è 'ok', 'removed', 'unknown_poll' o 'too_late'. I voti arrivati dopo l'orario
        di chiusura vengono ignorati anche se Telegram non ha ancora chiuso il sondaggio
        (es. servizio addormentato su Render).
        """
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT m.id, s.starts_at, s.chat_id FROM matches m JOIN sessions s ON s.id = m.session_id "
                "WHERE m.poll_id = $1", poll_id)
            if row is None:
                return "unknown_poll", None
            if now >= row["starts_at"] - self.close_delta:
                return "too_late", row["chat_id"]
            if choice is None:
                await con.execute("DELETE FROM votes WHERE match_id = $1 AND user_id = $2", row["id"], user_id)
                return "removed", row["chat_id"]
            await con.execute(
                """INSERT INTO votes (match_id, user_id, choice, voted_at) VALUES ($1, $2, $3, $4)
                   ON CONFLICT (match_id, user_id) DO UPDATE SET choice = EXCLUDED.choice, voted_at = EXCLUDED.voted_at""",
                row["id"], user_id, choice, now)
            return "ok", row["chat_id"]

    async def vote_counts(self, session_id: int) -> dict[int, int]:
        rows = await self.pool.fetch(
            "SELECT m.id, count(v.user_id) AS n FROM matches m LEFT JOIN votes v ON v.match_id = m.id "
            "WHERE m.session_id = $1 GROUP BY m.id", session_id)
        return {r["id"]: r["n"] for r in rows}

    # ---------- classifica ----------

    async def ranking(self, *, session_id: int | None = None, chat_id: int | None = None,
                      event_id: int | None = None) -> list[RankRow]:
        """Classifica: +1 per ogni voto uguale al vincitore.

        session_id -> solo quella sessione; event_id -> le sessioni di quell'evento;
        chat_id -> tutte le sessioni lanciate in quel gruppo; nessuno -> tutte le sessioni.
        """
        rows = await self.pool.fetch(
            """SELECT u.user_id, u.username, u.first_name, u.last_name,
                      count(*) FILTER (WHERE m.winner IS NOT NULL AND v.choice = m.winner) AS points,
                      count(*) FILTER (WHERE m.winner IN (1, 2)) AS answered
               FROM votes v
               JOIN matches m  ON m.id = v.match_id
               JOIN sessions s ON s.id = m.session_id
               JOIN users u    ON u.user_id = v.user_id
               WHERE ($1::int IS NULL OR s.id = $1) AND ($2::bigint IS NULL OR s.chat_id = $2)
                 AND ($3::int IS NULL OR s.event_id = $3)
               GROUP BY u.user_id
               ORDER BY points DESC, lower(COALESCE(u.username, u.first_name, ''))""",
            session_id, chat_id, event_id)
        return [RankRow(r["user_id"], display_name(r["username"], r["first_name"], r["last_name"]),
                        r["points"], r["answered"]) for r in rows]

    async def results_progress(self, *, session_id: int | None = None, chat_id: int | None = None,
                               event_id: int | None = None) -> tuple[int, int]:
        """(partite con risultato, partite totali) nel perimetro della classifica."""
        row = await self.pool.fetchrow(
            """SELECT count(*) FILTER (WHERE m.winner IS NOT NULL) AS done, count(*) AS total
               FROM matches m JOIN sessions s ON s.id = m.session_id
               WHERE ($1::int IS NULL OR s.id = $1) AND ($2::bigint IS NULL OR s.chat_id = $2)
                 AND ($3::int IS NULL OR s.event_id = $3)
                 AND s.launched_at IS NOT NULL""",
            session_id, chat_id, event_id)
        return row["done"], row["total"]

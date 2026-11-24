"""Test end-to-end dei comandi con un bot Telegram finto e un Postgres vero.

Richiede TEST_DATABASE_URL (viene svuotato!). Senza, il test viene saltato.
"""
import asyncio
import itertools
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest
from telegram import ChatPermissions

from pdc import handlers as h
from pdc.config import Config
from pdc.db import DB

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL non impostato")

ADMIN, GROUP = 111, -1001
TZ = ZoneInfo("Europe/Rome")


class FakeBot:
    def __init__(self):
        self.calls = []
        self.ids = itertools.count(1000)
        self.perms = ChatPermissions(can_send_messages=True, can_send_polls=True)

    def _log(self, name, *a, **kw):
        self.calls.append((name, a, kw))

    async def get_chat(self, chat_id):
        return NS(permissions=self.perms)

    async def set_chat_permissions(self, chat_id, perms, **kw):
        self._log("perms", chat_id, perms.can_send_messages)

    async def send_photo(self, chat_id, photo, **kw):
        assert photo[:8] == b"\x89PNG\r\n\x1a\n"
        self._log("photo", chat_id, **kw)

    async def send_poll(self, chat_id, question, options, **kw):
        n = next(self.ids)
        self._log("poll", chat_id, question, options, **kw)
        return NS(poll=NS(id=f"poll{n}"), message_id=n)

    async def send_message(self, chat_id, text, **kw):
        self._log("msg", chat_id, text, **kw)

    async def stop_poll(self, chat_id, message_id, **kw):
        self._log("stop", chat_id, message_id)


def make_ctx(bot, cfg, database, args=()):
    return NS(application=NS(bot_data={"cfg": cfg, "db": database}), bot=bot, args=list(args), user_data={})


def make_update(user_id=ADMIN, chat_id=ADMIN, chat_type="private", text=""):
    replies = []

    async def reply_text(t, **kw):
        replies.append(t)

    async def delete():
        pass

    msg = NS(text=text, reply_text=reply_text, delete=delete, sender_chat=None, chat=NS(id=chat_id))
    user = NS(id=user_id, username=f"u{user_id}", first_name="Nome", last_name=None, is_bot=False)
    return NS(effective_user=user, effective_chat=NS(id=chat_id, type=chat_type), effective_message=msg,
              message=msg, callback_query=None, poll_answer=None), replies


def make_cb(data, chat_id=ADMIN):
    edits = []

    async def answer(*a, **kw):
        pass

    async def edit_message_text(t, **kw):
        edits.append(t)

    async def edit_message_reply_markup(*a, **kw):
        pass

    upd, _ = make_update()
    upd.callback_query = NS(data=data, answer=answer, edit_message_text=edit_message_text,
                            edit_message_reply_markup=edit_message_reply_markup, message=NS(chat=NS(id=chat_id)))
    return upd, edits


def vote(poll_id, user_id, option_ids):
    user = NS(id=user_id, username=f"player{user_id}", first_name="P", last_name=None, is_bot=False)
    return NS(poll_answer=NS(poll_id=poll_id, user=user, option_ids=option_ids))


async def scenario():
    import asyncpg
    con = await asyncpg.connect(DSN)
    await con.execute("DROP TABLE IF EXISTS votes, matches, sessions, events, group_members, users CASCADE")
    await con.close()

    cfg = Config(bot_token="1:x", database_url=DSN, admin_ids=frozenset({ADMIN}), tz=TZ, public_url=None,
                 port=0, webhook_secret="s", close_minutes_before=5, cover_title="PDC180 POLL", mute_mode="invio")
    database = await DB.connect(DSN, 5)
    bot = FakeBot()

    # --- step 0: /start_event
    upd, rep = make_update()
    await h.cmd_start_event(upd, make_ctx(bot, cfg, database, ["Open", "di", "Natale"]))
    assert "ID evento: <b>1</b>" in rep[0]
    upd, rep = make_update()
    await h.cmd_start_event(upd, make_ctx(bot, cfg, database, ["Altro"]))
    assert "già un evento in corso" in rep[0]

    # --- step 1: /crea_sessione
    ctx = make_ctx(bot, cfg, database)
    upd, rep = make_update()
    assert await h.new_session_start(upd, ctx) == h.ASK_NAME
    upd, rep = make_update(text="Mercoledì pomeriggio")
    assert await h.new_session_name(upd, ctx) == h.ASK_MATCHES
    upd, rep = make_update(text="Rossi - Bianchi\nVerdi - Neri\nGialli vs Blu")
    assert await h.new_session_matches(upd, ctx) == h.ASK_START
    start = datetime.now(TZ) + timedelta(hours=2)
    upd, rep = make_update(text=start.strftime("%d/%m/%Y %H:%M"))
    assert await h.new_session_time(upd, ctx) == h.ASK_CONFIRM
    upd, edits = make_cb("new:save")
    await h.new_session_confirm(upd, ctx)
    assert "ID sessione: <b>1</b>" in edits[0]
    assert (await database.get_session(1)).event_id == 1

    # non admin non può creare
    upd, rep = make_update(user_id=999, chat_id=999)
    assert await h.new_session_start(upd, make_ctx(bot, cfg, database)) == -1

    # --- step 2: /lancia_sondaggi 1 nel gruppo
    upd, rep = make_update(chat_id=GROUP, chat_type="supergroup")
    await h.cmd_launch(upd, make_ctx(bot, cfg, database, ["1"]))
    kinds = [c[0] for c in bot.calls]
    assert kinds == ["perms", "photo", "poll", "poll", "poll", "perms", "msg"], kinds
    assert bot.calls[0][1] == (GROUP, False) and bot.calls[5][1] == (GROUP, True)  # mute e smute
    polls = [c for c in bot.calls if c[0] == "poll"]
    assert all(p[2]["is_anonymous"] is False for p in polls)
    assert all("close_date" not in p[2] for p in polls)  # chiusura > 10 min: la gestisce il job
    assert polls[0][1][2] == ["Rossi", "Bianchi"]

    # rilancio bloccato
    upd, rep = make_update(chat_id=GROUP, chat_type="supergroup")
    await h.cmd_launch(upd, make_ctx(bot, cfg, database, ["1"]))
    assert "già stata lanciata" in rep[0]

    # --- voti: poll1000 = Rossi-Bianchi, poll1001 = Verdi-Neri, poll1002 = Gialli-Blu
    ctx = make_ctx(bot, cfg, database)
    for uid, choices in {1: [0, 0, 0], 2: [1, 0, 1], 3: [0, 1, 1]}.items():
        for i, c in enumerate(choices):
            await h.on_poll_answer(vote(f"poll{1000 + i}", uid, [c]), ctx)
    await h.on_poll_answer(vote("poll1000", 3, []), ctx)      # utente 3 ritira il voto
    await h.on_poll_answer(vote("poll1000", 3, [1]), ctx)     # ...e rivota Bianchi
    assert await database.total_votes(1) == 9
    assert await database.count_members(GROUP) == 3            # registrati votando

    # --- step 3: risultati, con interruzione "sessione non conclusa"
    bot.calls.clear()
    upd, rep = make_update()
    await h.cmd_results(upd, make_ctx(bot, cfg, database, ["1"]))
    assert "0/3" in rep[0]
    m1, m2, m3 = await database.get_matches(1)
    await h.cb_result(make_cb(f"r:m:{m1.id}:1")[0], ctx)        # vince Rossi
    upd, edits = make_cb(f"r:m:{m2.id}:stop")
    await h.cb_result(upd, ctx)
    assert "non conclusa" in edits[0]

    # classifica a metà: solo partita 1 conta
    rows = await database.ranking(session_id=1)
    assert {r.display: r.points for r in rows} == {"@player1": 1, "@player2": 0, "@player3": 0}

    # ripresa: riparte dalla partita 2
    bot.calls.clear()
    upd, rep = make_update()
    await h.cmd_results(upd, make_ctx(bot, cfg, database, ["1"]))
    assert "1/3" in rep[0]
    assert "Verdi - Neri" in bot.calls[-1][1][1]
    await h.cb_result(make_cb(f"r:m:{m2.id}:1")[0], ctx)        # vince Verdi
    bot.calls.clear()
    await h.cb_result(make_cb(f"r:m:{m3.id}:2")[0], ctx)        # vince Blu
    assert "Tutte le 3 partite" in bot.calls[-1][1][1]

    # --- step 4: classifica
    rows = await database.ranking(session_id=1)
    pts = {r.display: r.points for r in rows}
    # u1: R,V,G -> 2 ; u2: B,V,B -> 2 ; u3: B,N,B -> 1
    assert pts == {"@player1": 2, "@player2": 2, "@player3": 1}
    assert rows[2].display == "@player3"
    bot.calls.clear()
    upd, rep = make_update(chat_id=GROUP, chat_type="supergroup")
    await h.cmd_ranking_session(upd, make_ctx(bot, cfg, database, []))
    await h.cmd_ranking(upd, make_ctx(bot, cfg, database, []))
    photos = [c for c in bot.calls if c[0] == "photo"]
    assert len(photos) == 2 and "1. @player1 – 2" in photos[0][2]["caption"]
    assert "3. @player3 – 1" in photos[0][2]["caption"]

    # --- chiusura: voti oltre l'orario ignorati, job chiude i sondaggi
    await database.pool.execute("UPDATE sessions SET starts_at = now() + interval '4 minutes'")
    st, _ = await database.record_vote("poll1000", 4, 1, datetime.now(timezone.utc))
    assert st == "too_late"
    bot.calls.clear()
    await h.close_due_polls(make_ctx(bot, cfg, database))
    assert [c[0] for c in bot.calls] == ["stop", "stop", "stop", "msg"]
    bot.calls.clear()
    await h.close_due_polls(make_ctx(bot, cfg, database))
    assert bot.calls == []  # non richiude due volte

    # --- eventi: classifica evento, chiusura, sessione fuori evento
    assert {r.display: r.points for r in await database.ranking(event_id=1)} == pts
    bot.calls.clear()
    upd, rep = make_update(chat_id=GROUP, chat_type="supergroup")
    await h.cmd_ranking_event(upd, make_ctx(bot, cfg, database, ["open", "di", "natale"]))
    assert "Classifica evento" in bot.calls[0][2]["caption"] and "in corso" in bot.calls[0][2]["caption"]
    upd, rep = make_update()
    await h.cmd_end_event(upd, make_ctx(bot, cfg, database, []))
    assert "chiuso" in rep[0]
    upd, rep = make_update()
    await h.cmd_end_event(upd, make_ctx(bot, cfg, database, ["1"]))
    assert "già chiuso" in rep[0]
    sid2 = await database.create_session("Extra", start, ADMIN, [("A", "B")], None)
    assert (await database.get_session(sid2)).event_id is None
    assert await database.results_progress(event_id=1) == (3, 3)
    upd, rep = make_update()
    await h.cmd_session_event(upd, make_ctx(bot, cfg, database, [str(sid2), "Open di Natale"]))
    assert (await database.get_session(sid2)).event_id == 1
    upd, rep = make_update()
    await h.cmd_events(upd, make_ctx(bot, cfg, database, []))
    assert "2 sessioni" in rep[0]

    await database.close()


def test_full_flow():
    asyncio.run(scenario())

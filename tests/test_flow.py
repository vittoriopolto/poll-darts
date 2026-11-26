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
    username = "pdc_test_bot"

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
        return NS(message_id=next(self.ids))

    async def stop_poll(self, chat_id, message_id, **kw):
        self._log("stop", chat_id, message_id)


def make_ctx(bot, cfg, database, args=()):
    return NS(application=NS(bot_data={"cfg": cfg, "db": database}), bot=bot, args=list(args), user_data={})


def make_update(user_id=ADMIN, chat_id=ADMIN, chat_type="private", text="", markups=None):
    replies = []

    async def reply_text(t, **kw):
        replies.append(t)
        if markups is not None:
            markups.append(kw.get("reply_markup"))

    async def delete():
        pass

    msg = NS(text=text, reply_text=reply_text, delete=delete, sender_chat=None, chat=NS(id=chat_id))
    user = NS(id=user_id, username=f"u{user_id}", first_name="Nome", last_name=None, is_bot=False)
    return NS(effective_user=user, effective_chat=NS(id=chat_id, type=chat_type), effective_message=msg,
              message=msg, callback_query=None, poll_answer=None), replies


def make_cb(data, chat_id=ADMIN, user_id=ADMIN):
    edits = []

    async def answer(*a, **kw):
        pass

    async def edit_message_text(t, **kw):
        edits.append(t)

    async def edit_message_reply_markup(*a, **kw):
        pass

    upd, _ = make_update(user_id=user_id, chat_id=chat_id)
    upd.callback_query = NS(data=data, answer=answer, edit_message_text=edit_message_text,
                            edit_message_reply_markup=edit_message_reply_markup, message=NS(chat=NS(id=chat_id)))
    return upd, edits


def vote(poll_id, user_id, option_ids):
    user = NS(id=user_id, username=f"player{user_id}", first_name="P", last_name=None, is_bot=False)
    return NS(poll_answer=NS(poll_id=poll_id, user=user, option_ids=option_ids))


async def scenario():
    import asyncpg
    con = await asyncpg.connect(DSN)
    await con.execute("DROP TABLE IF EXISTS feedback_answers, feedback_options, feedback_questions, feedback_pools, votes, matches, sessions, events, group_members, users CASCADE")
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
    assert polls[0][1][1] == "Rossi - Bianchi"                 # domanda: solo i due nomi
    photo = next(c for c in bot.calls if c[0] == "photo")
    assert "/ranking" not in photo[2]["caption"]               # niente comandi cliccabili

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
    assert len(photos) == 2 and "1. @player1 – <b>2</b> (2/3, 67%)" in photos[0][2]["caption"]
    assert "3. @player3 – <b>1</b> (1/3, 33%)" in photos[0][2]["caption"]
    # versione solo testo
    bot.calls.clear()
    await h.cmd_ranking_session(upd, make_ctx(bot, cfg, database, ["1", "txt"]))
    await h.cmd_ranking(upd, make_ctx(bot, cfg, database, ["TXT"]))
    assert [c[0] for c in bot.calls] == ["msg", "msg"]
    assert "Classifica sessione" in bot.calls[0][1][1] and "(2/3, 67%)" in bot.calls[0][1][1]

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
    bot.calls.clear()
    await h.cmd_ranking_event(upd, make_ctx(bot, cfg, database, ["open", "di", "natale", "txt"]))
    assert bot.calls[0][0] == "msg" and "Open di Natale" in bot.calls[0][1][1]
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

    # --- eliminazioni
    sid3 = await database.create_session("Da tagliare", start, ADMIN, [("A", "B"), ("C", "D"), ("E", "F")], 1)
    ctx = make_ctx(bot, cfg, database)
    upd, rep = make_update()
    await h.cmd_delete_match(upd, make_ctx(bot, cfg, database, [str(sid3), "2"]))
    ma, mc, me = await database.get_matches(sid3)
    await h.cb_delete(make_cb(f"delm:{mc.id}:yes")[0], ctx)
    assert [(m.position, m.label()) for m in await database.get_matches(sid3)] == [(1, "A - B"), (2, "E - F")]
    upd, edits = make_cb("delx")
    await h.cb_delete(upd, ctx)
    assert "non elimino" in edits[0]
    # sessione: chiede conferma, poi elimina
    upd, rep = make_update()
    await h.cmd_delete_session(upd, make_ctx(bot, cfg, database, [str(sid3)]))
    assert "2 partite e 0 voti" in rep[0]
    await h.cb_delete(make_cb(f"dels:{sid3}")[0], ctx)
    assert await database.get_session(sid3) is None
    # evento "solo evento": le sessioni restano senza evento
    upd, rep = make_update()
    await h.cmd_delete_event(upd, make_ctx(bot, cfg, database, ["1"]))
    assert "Contiene 2 sessioni" in rep[0]
    await h.cb_delete(make_cb("dele:1:only")[0], ctx)
    assert await database.get_event(1) is None and (await database.get_session(1)).event_id is None
    # evento "+ sessioni": cancella anche voti
    eid = await database.create_event("Tmp", ADMIN)
    await database.set_session_event(1, eid)
    await h.cb_delete(make_cb(f"dele:{eid}:all")[0], ctx)
    assert await database.get_session(1) is None and await database.ranking() == []
    # non admin
    upd, rep = make_update(user_id=999, chat_id=999)
    await h.cmd_delete_session(upd, make_ctx(bot, cfg, database, [str(sid2)]))
    assert rep == [] and await database.get_session(sid2) is not None

    await feedback_scenario(bot, cfg, database)
    await database.close()


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def feedback_scenario(bot, cfg, database):
    U1, U2 = 501, 502
    ctx = make_ctx(bot, cfg, database)

    async def say(text, state_fn):
        upd, rep = make_update(text=text)
        return await state_fn(upd, ctx), rep

    # --- /crea_feedback: domanda 1 con 3 risposte, domanda 2 con 2, poi stop
    upd, rep = make_update()
    assert await h.fb_new_start(upd, ctx) == h.FB_QUESTION and "domanda 1" in rep[0]
    st, rep = await say("Ti piace il gioco?", h.fb_new_question)
    assert st == h.FB_OPTION
    st, rep = await say("Sì", h.fb_new_option)
    assert st == h.FB_MORE
    # fine domanda con una sola risposta: rifiutato
    upd, _ = make_cb("fbw:endq")
    assert await h.fb_new_button(upd, ctx) == h.FB_MORE
    upd, _ = make_cb("fbw:add")
    assert await h.fb_new_button(upd, ctx) == h.FB_OPTION
    await say("No", h.fb_new_option)
    await say("Così così", h.fb_new_option)  # scritta senza premere "aggiungi"
    upd, _ = make_cb("fbw:endq")
    assert await h.fb_new_button(upd, ctx) == h.FB_QUESTION
    await say("Orario sondaggi?", h.fb_new_question)
    await say("Va bene", h.fb_new_option)
    await say("Prima", h.fb_new_option)
    await h.fb_new_button(make_cb("fbw:endq")[0], ctx)
    upd, edits = make_cb("fbw:stop")
    assert await h.fb_new_button(upd, ctx) == h.ConversationHandler.END
    assert "ID feedback: <b>1</b>" in edits[0]
    qs = await database.feedback_questions(1)
    assert [(q.text, [o.text for o in q.options]) for q in qs] == [
        ("Ti piace il gioco?", ["Sì", "No", "Così così"]), ("Orario sondaggi?", ["Va bene", "Prima"])]

    # --- /consigli nel gruppo (senza ID = l'ultimo): immagine + bottone con deep link
    bot.calls.clear()
    upd, rep = make_update(chat_id=GROUP, chat_type="supergroup")
    await h.cmd_consigli(upd, make_ctx(bot, cfg, database, []))
    name, a, kw = bot.calls[0]
    assert name == "photo" and "Rispondi qui sotto" in kw["caption"]
    btn = buttons(kw["reply_markup"])[0]
    assert btn.url == "https://t.me/pdc_test_bot?start=fb_1"
    # non admin: niente
    bot.calls.clear()
    upd, rep = make_update(user_id=U1, chat_id=GROUP, chat_type="supergroup")
    await h.cmd_consigli(upd, make_ctx(bot, cfg, database, ["1"]))
    assert bot.calls == []

    # --- U1 clicca: /start fb_1 in privato, risponde alle 2 domande
    async def answer(user, qtext, opt):
        msg = next(c for c in reversed(bot.calls) if c[0] == "msg" and c[1][0] == user)
        assert qtext in msg[1][1], msg
        b = next(b for b in buttons(msg[2]["reply_markup"]) if b.text == opt)
        await h.cb_feedback_answer(make_cb(b.callback_data, chat_id=user, user_id=user)[0], ctx)

    bot.calls.clear()
    upd, rep = make_update(user_id=U1, chat_id=U1)
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fb_1"]))
    assert "Sono 2 domande" in rep[0]
    await answer(U1, "Ti piace il gioco?", "Sì")
    await answer(U1, "Domanda 2/2", "Prima")
    assert "Grazie" in bot.calls[-1][1][1]
    # U2 risponde solo alla prima, poi riprende dalla seconda
    upd, rep = make_update(user_id=U2, chat_id=U2)
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fb_1"]))
    await answer(U2, "Ti piace il gioco?", "No")
    upd, rep = make_update(user_id=U2, chat_id=U2)
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fb_1"]))
    await answer(U2, "Orario sondaggi?", "Prima")
    # U1 ha già finito: gli offre di cambiare; cambia la prima risposta in "No"
    mk = []
    upd, rep = make_update(user_id=U1, chat_id=U1, markups=mk)
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fb_1"]))
    assert "già risposto" in rep[0]
    await h.cb_feedback_redo(make_cb(buttons(mk[0])[0].callback_data, chat_id=U1, user_id=U1)[0], ctx)
    await answer(U1, "Domanda 1/2", "No")
    await answer(U1, "Domanda 2/2", "Prima")

    # --- /risultati_feedback
    upd, rep = make_update()
    await h.cmd_feedback_results(upd, make_ctx(bot, cfg, database, []))
    assert "#1</b> – 2 domande · 2 persone" in rep[0]
    upd, rep = make_update()
    await h.cmd_feedback_results(upd, make_ctx(bot, cfg, database, ["1"]))
    r = rep[0]
    assert "2 persone hanno risposto" in r
    assert "<code>  0%</code> · 0 voti – Sì" in r and "<code>100%</code> · 2 voti – No" in r
    assert "<code>100%</code> · 2 voti – Prima" in r
    assert "start=fbd_1_2" in r
    upd, rep = make_update()
    await h.cmd_feedback_results(upd, make_ctx(bot, cfg, database, ["1", "2"]))
    assert "@u502, @u501" in rep[0] and "<b>Va bene</b> – 0% · 0 voti\n–" in rep[0]
    # il link "dettaglio" apre /start fbd_1_1 (solo admin)
    upd, rep = make_update()
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fbd_1_1"]))
    assert "domanda 1" in rep[0] and "<b>No</b> – 100% · 2 voti" in rep[0]
    upd, rep = make_update(user_id=U1, chat_id=U1)
    await h.cmd_start(upd, make_ctx(bot, cfg, database, ["fbd_1_1"]))
    assert "Bot sondaggi" in rep[0]


def test_full_flow():
    asyncio.run(scenario())

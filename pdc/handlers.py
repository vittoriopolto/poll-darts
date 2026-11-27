"""Comandi e gestori del bot."""
from __future__ import annotations

import json
import re
import logging
import warnings
from datetime import datetime, timedelta, timezone
from html import escape

from telegram import (ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, PollAnswerHandler, filters)

from telegram.warnings import PTBUserWarning

from . import images
from .config import Config
from .db import DB, Event, Match, Session
from .parsing import ParseError, competition_positions, fmt_dt, parse_matches, parse_start

log = logging.getLogger(__name__)

# Utente fittizio con cui Telegram mostra gli admin anonimi nei gruppi.
GROUP_ANONYMOUS_BOT = 1087968824

ASK_NAME, ASK_MATCHES, ASK_START, ASK_CONFIRM = range(4)


# ---------------------------------------------------------------- utilità

def cfg(ctx: ContextTypes.DEFAULT_TYPE) -> Config:
    return ctx.application.bot_data["cfg"]


def db(ctx: ContextTypes.DEFAULT_TYPE) -> DB:
    return ctx.application.bot_data["db"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if user.id in cfg(ctx).admin_ids:
        return True
    # Admin anonimo del gruppo: Telegram nasconde chi è, ci fidiamo perché solo gli admin possono farlo.
    msg = update.effective_message
    return (user.id == GROUP_ANONYMOUS_BOT and msg is not None and msg.sender_chat is not None
            and msg.sender_chat.id == update.effective_chat.id)


def close_time(s: Session, c: Config) -> datetime:
    return s.starts_at - timedelta(minutes=c.close_minutes_before)


async def reply(update: Update, text: str, **kw):
    return await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                                     disable_web_page_preview=True, **kw)


def parse_session_arg(ctx: ContextTypes.DEFAULT_TYPE) -> int | None:
    if not ctx.args:
        return None
    try:
        return int(ctx.args[0].lstrip("#"))
    except ValueError:
        return None


async def register_user(ctx: ContextTypes.DEFAULT_TYPE, user, chat_id: int | None = None,
                        status: str | None = None) -> None:
    if user is None or user.is_bot:
        return
    try:
        await db(ctx).upsert_user(user.id, user.username, user.first_name, user.last_name, chat_id, status)
    except Exception:  # la registrazione non deve mai bloccare il resto
        log.exception("upsert_user fallito")


# ---------------------------------------------------------------- impostazioni utente

def _settings_text(tag: bool) -> str:
    stato = "✅ attivo" if tag else "🛑 disattivo"
    spiega = ("in classifica compari con la @ e ricevi la notifica del tag" if tag
              else "in classifica compari col nome senza @, nessuna notifica")
    return f"⚙️ <b>Impostazioni</b>\n\nStato tag: <b>{stato}</b>\n<i>({spiega})</i>"


def _settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Start Tag", callback_data="tag:on"),
                                  InlineKeyboardButton("🛑 Stop Tag", callback_data="tag:off")]])


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        await reply(update, f"Scrivimi in privato: <a href=\"{_deep_link(ctx, 'impostazioni')}\">/impostazioni</a>")
        return
    await register_user(ctx, update.effective_user)
    tag = await db(ctx).tag_enabled(update.effective_user.id)
    await reply(update, _settings_text(tag), reply_markup=_settings_kb())


async def cb_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    await register_user(ctx, user)
    tag = q.data == "tag:on"
    await db(ctx).set_tag_enabled(user.id, tag)
    await ctx.bot.send_message(update.effective_chat.id, _settings_text(tag), parse_mode=ParseMode.HTML,
                               reply_markup=_settings_kb())


# ---------------------------------------------------------------- comandi base

HELP_ADMIN = (
    "<b>Eventi (admin)</b>\n"
    "/start_event <i>nome</i> – apre un evento: le sessioni create da ora finiscono lì\n"
    "/end_event [<i>id o nome</i>] – chiude l'evento (default: quello in corso)\n"
    "/eventi – elenco eventi\n"
    "/sessione_evento <i>id_sessione</i> <i>id_evento</i>|nessuno – sposta una sessione\n\n"
    "<b>Comandi admin (in privato)</b>\n"
    "/crea_sessione – crea una sessione (nome, partite, orario)\n"
    "/sessioni – ultime sessioni con le partite e i loro ID\n"
    "/partite <i>id</i> – elenco partite in testo; in privato chiede se mandarlo nel gruppo\n"
    "/risultati_sessione <i>id</i> – inserisci i vincitori (riprende da dove eri rimasto)\n"
    "/risultati_sessione <i>id</i> tutte – rivedi/correggi tutti i risultati\n"
    "/risultato_partita <i>id_partita</i> – dai/correggi il risultato di una sola partita (o tocca ✏️ in /sessioni)\n"
    "Risultato: tocca il vincitore e poi i punti, oppure scrivi i punti <code>2 3</code> (giocatore 1, giocatore 2)\n"
    "/delete_event <i>id o nome</i> – elimina un evento (con o senza le sue sessioni)\n"
    "/delete_session <i>id</i> – elimina una sessione con partite e voti\n"
    "/delete_match <i>id_sessione</i> [<i>n° partita</i>] – elimina una partita e i suoi voti\n\n"
    "<b>Feedback (admin)</b>\n"
    "/crea_feedback – crea un feedback: domande e risposte, una alla volta\n"
    "/risultati_feedback [<i>id</i>] – percentuali per domanda (senza id: elenco)\n"
    "/risultati_feedback <i>id</i> <i>n° domanda</i> – chi ha votato cosa\n"
    "/consigli [<i>id</i>] – nel gruppo: invito con il bottone per rispondere (default: l'ultimo)\n\n"
    "<b>Comandi admin (nel gruppo)</b>\n"
    "/lancia_sondaggi <i>id</i> – muta il gruppo, pubblica copertina e sondaggi\n"
    "/muta, /smuta – muta/smuta il gruppo a mano\n\n"
)
HELP_ALL = (
    "<b>Comandi per tutti</b>\n"
    "/ranking – classifica globale (aggiungi <i>txt</i> per averla solo come testo)\n"
    "/ranking_evento [<i>id o nome</i>] – classifica di un evento (default: quello in corso o l'ultimo)\n"
    "/ranking_sessione [<i>id</i>] – classifica di una sessione (default: l'ultima)\n"
    "/ranking_txt, /ranking_evento_txt, /ranking_sessione_txt – come sopra, solo testo e senza tag @\n"
    "/impostazioni – in privato: scegli se essere taggato con la @ in classifica\n"
    "/mio_id – mostra il tuo ID Telegram"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(ctx, update.effective_user)
    # Deep link dai bottoni: /start fb_ID (rispondi al feedback), /start fbd_ID_N (dettaglio, admin).
    arg = ctx.args[0] if ctx.args else ""
    if update.effective_chat.type == ChatType.PRIVATE and arg == "impostazioni":
        await cmd_settings(update, ctx)
        return
    if (update.effective_chat.type == ChatType.PRIVATE and arg.startswith("rp_") and arg[3:].isdigit()
            and is_admin(update, ctx)):
        ctx.args = [arg[3:]]
        await cmd_match_result(update, ctx)
        return
    if update.effective_chat.type == ChatType.PRIVATE and arg.startswith("fb"):
        kind, _, rest = arg.partition("_")
        nums = rest.split("_")
        if kind == "fb" and rest.isdigit():
            await start_feedback(update, ctx, int(rest))
            return
        if kind == "fbd" and len(nums) == 2 and all(n.isdigit() for n in nums) and is_admin(update, ctx):
            await send_feedback_detail(update, ctx, int(nums[0]), int(nums[1]))
            return
    text = "🎯 <b>Bot sondaggi freccette</b>\n\n" + (HELP_ADMIN if is_admin(update, ctx) else "") + HELP_ALL
    await reply(update, text)


async def cmd_my_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await reply(update, f"Il tuo ID è <code>{u.id}</code>. Chat: <code>{update.effective_chat.id}</code>")


# ---------------------------------------------------------------- step 1: /crea_sessione

async def new_session_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update, ctx):
        await reply(update, "Solo gli admin possono creare sessioni.")
        return ConversationHandler.END
    ctx.user_data["new"] = {}
    await reply(update, "🆕 Nuova sessione.\n\n1/3 – Come si chiama? (es. <i>Mercoledì pomeriggio</i>)\n\n"
                        "/annulla per uscire.")
    return ASK_NAME


async def new_session_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name or len(name) > 120:
        await reply(update, "Nome non valido (max 120 caratteri). Riprova.")
        return ASK_NAME
    ctx.user_data["new"]["name"] = name
    await reply(update, "2/3 – Mandami le partite, <b>una per riga</b>, nel formato\n"
                        "<code>GIOCATORE 1 - GIOCATORE 2</code>\n\nEsempio:\n"
                        "<code>Rossi - Bianchi\nVerdi - Neri</code>")
    return ASK_MATCHES


async def new_session_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        matches = parse_matches(update.message.text)
    except ParseError as e:
        await reply(update, f"⚠️ {escape(str(e))}\n\nRimanda l'elenco completo.")
        return ASK_MATCHES
    ctx.user_data["new"]["matches"] = matches
    tz = cfg(ctx).tz
    await reply(update, f"Ok, {len(matches)} partite.\n\n3/3 – A che ora inizia la sessione?\n"
                        f"Esempi: <code>25/11/2026 15:30</code>, <code>domani 15:30</code>, <code>15:30</code>\n"
                        f"(fuso orario {tz.key}; i sondaggi si chiudono {cfg(ctx).close_minutes_before} "
                        f"minuti prima)")
    return ASK_START


async def new_session_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    c = cfg(ctx)
    try:
        start = parse_start(update.message.text, c.tz)
    except ParseError as e:
        await reply(update, f"⚠️ {escape(str(e))}")
        return ASK_START
    data = ctx.user_data["new"]
    data["start"] = start.isoformat()
    lines = "\n".join(f"{i}. {escape(p1)} - {escape(p2)}" for i, (p1, p2) in enumerate(data["matches"], 1))
    warn = ""
    if start - timedelta(minutes=c.close_minutes_before) <= datetime.now(c.tz):
        warn = "\n\n⚠️ Attenzione: l'orario di chiusura dei sondaggi è già passato."
    ev = await db(ctx).active_event()
    data["event_id"] = ev.id if ev else None
    ev_line = (f"Evento: <b>#{ev.id} {escape(ev.name)}</b>\n" if ev
               else "Evento: nessuno (apri un evento con /start_event)\n")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Salva", callback_data="new:save"),
                                InlineKeyboardButton("❌ Annulla", callback_data="new:cancel")]])
    await reply(update, f"<b>{escape(data['name'])}</b>\n{ev_line}Inizio: {fmt_dt(start, c.tz)}\n"
                        f"Chiusura sondaggi: {fmt_dt(start - timedelta(minutes=c.close_minutes_before), c.tz)}\n\n"
                        f"{lines}{warn}\n\nSalvo?", reply_markup=kb)
    return ASK_CONFIRM


async def new_session_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = ctx.user_data.pop("new", None)
    if q.data != "new:save" or not data:
        await q.edit_message_text("Creazione annullata.")
        return ConversationHandler.END
    start = datetime.fromisoformat(data["start"])
    sid = await db(ctx).create_session(data["name"], start, update.effective_user.id, data["matches"],
                                       data.get("event_id"))
    await q.edit_message_text(
        f"✅ Sessione salvata.\n\nID sessione: <b>{sid}</b>\n\n"
        f"Nel gruppo lancia: <code>/lancia_sondaggi {sid}</code>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def new_session_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("new", None)
    await reply(update, "Creazione annullata.")
    return ConversationHandler.END


def _chunks(blocks: list[str], limit: int = 3800) -> list[str]:
    """Unisce i blocchi in messaggi sotto il limite di Telegram (4096 caratteri)."""
    out, cur = [], ""
    for blk in blocks:
        if cur and len(cur) + len(blk) + 2 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{blk}" if cur else blk
    if cur:
        out.append(cur)
    return out


async def cmd_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, ctx):
        return
    c = cfg(ctx)
    sessions = await db(ctx).list_sessions(15, by_start=True)
    if not sessions:
        await reply(update, "Nessuna sessione. Creane una con /crea_sessione")
        return
    blocks = []
    for s in sessions:
        matches = await db(ctx).get_matches(s.id)
        done = sum(m.winner is not None for m in matches)
        state = ("🔒 chiusa" if s.closed_at else "🟢 sondaggi aperti") if s.launched_at else "📝 da lanciare"
        ev = f" · evento #{s.event_id}" if s.event_id else ""
        lines = [f"<b>#{s.id}</b> {escape(s.name)} – {fmt_dt(s.starts_at, c.tz)}",
                 f"    {state} · risultati {done}/{len(matches)}{ev}"]
        for m in matches:
            lines.append(f'    <a href="{_deep_link(ctx, f"rp_{m.id}")}">✏️{m.id}</a> {match_line(m)}')
        blocks.append("\n".join(lines))
    blocks.append("Tocca ✏️ accanto a una partita per dare o correggere il risultato "
                  "(oppure <code>/risultato_partita ID</code>).")
    for text in _chunks(blocks):
        await reply(update, text)


async def _matches_text(ctx: ContextTypes.DEFAULT_TYPE, s: Session) -> str:
    d = db(ctx)
    ev = await d.get_event(s.event_id) if s.event_id else None
    matches = await d.get_matches(s.id)
    lines = "\n".join(match_line(m) for m in matches) or "Nessuna partita."
    return (f"🏆 {escape(ev.name) if ev else 'Nessun evento'}\n"
            f"📁 {escape(s.name)}\n"
            f"⏰ {fmt_dt(s.starts_at, cfg(ctx).tz)}\n\n{lines}")


async def _matches_target_group(ctx: ContextTypes.DEFAULT_TYPE, s: Session) -> int | None:
    """Gruppo in cui mandare l'elenco: quello della sessione, altrimenti quello dell'ultima sessione lanciata."""
    return s.chat_id or await db(ctx).last_group_chat()


async def cmd_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/partite <id_sessione>: evento, sessione, orario e l'elenco delle partite, in testo.

    In privato mostra l'elenco e chiede con un bottone se mandarlo nel gruppo; nel gruppo cancella
    il comando e manda l'elenco come messaggio a sé (non come risposta)."""
    if not is_admin(update, ctx):
        return
    chat = update.effective_chat
    sid = parse_session_arg(ctx)
    s = await db(ctx).get_session(sid) if sid else None
    if not s:
        await reply(update, "Uso: /partite <i>id_sessione</i> (vedi /sessioni)")
        return
    text = await _matches_text(ctx, s)
    if chat.type != ChatType.PRIVATE:
        try:
            await update.effective_message.delete()
        except TelegramError:
            pass
        await ctx.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    await reply(update, text)
    if await _matches_target_group(ctx, s) is None:
        await reply(update, "Nessun gruppo conosciuto: lancia <code>/partite</code> direttamente nel gruppo.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📣 Sì, manda nel gruppo", callback_data=f"ptg:{s.id}"),
                                InlineKeyboardButton("No", callback_data="ptg:no")]])
    await reply(update, "Le mando nel gruppo?", reply_markup=kb)


async def cb_matches_to_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update, ctx):
        await q.answer("Solo admin", show_alert=True)
        return
    arg = q.data.split(":")[1]
    s = await db(ctx).get_session(int(arg)) if arg.isdigit() else None
    if not s:
        await q.answer()
        await q.edit_message_text("Ok, non le mando.")
        return
    group = await _matches_target_group(ctx, s)
    if group is None:
        await q.answer("Nessun gruppo conosciuto", show_alert=True)
        return
    await q.answer("Mando…")
    try:
        await ctx.bot.send_message(group, await _matches_text(ctx, s), parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
    except TelegramError as e:
        log.warning("elenco partite non inviato al gruppo %s: %s", group, e)
        await q.edit_message_text("⚠️ Non sono riuscito a mandarle nel gruppo.")
        return
    await q.edit_message_text("📣 Elenco partite mandato nel gruppo.")


def _private_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    return is_admin(update, ctx) and update.effective_chat.type == ChatType.PRIVATE


async def _stop_open_polls(ctx: ContextTypes.DEFAULT_TYPE, s: Session, matches: list[Match]) -> int:
    """Chiude su Telegram i sondaggi ancora aperti di una sessione che sta per essere eliminata."""
    if not s.chat_id or s.closed_at:
        return 0
    n = 0
    for m in matches:
        if m.poll_message_id:
            try:
                await ctx.bot.stop_poll(s.chat_id, m.poll_message_id)
                n += 1
            except TelegramError:
                pass
    return n


NO_KB = InlineKeyboardButton("❌ Annulla", callback_data="delx")


async def cmd_delete_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private_admin(update, ctx):
        return
    sid = parse_session_arg(ctx)
    s = await db(ctx).get_session(sid) if sid else None
    if not s:
        await reply(update, "Uso: /delete_session <i>id</i> (vedi /sessioni)")
        return
    n_matches, n_votes = await db(ctx).session_stats(sid)
    extra = ""
    if s.launched_at and not s.closed_at:
        extra = "\nI sondaggi ancora aperti nel gruppo verranno chiusi."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Sì, elimina", callback_data=f"dels:{sid}"), NO_KB]])
    await reply(update, f"Eliminare la sessione <b>#{sid} {escape(s.name)}</b>?\n"
                        f"Verranno cancellate {n_matches} partite e {n_votes} voti, e spariranno dalle classifiche."
                        f"{extra}\nI messaggi già pubblicati nel gruppo restano.", reply_markup=kb)


async def cmd_delete_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _private_admin(update, ctx):
        return
    d = db(ctx)
    ref = " ".join(ctx.args).strip()
    ev = await d.find_event(ref) if ref else None
    if not ev:
        await reply(update, "Uso: /delete_event <i>id o nome</i> (vedi /eventi)")
        return
    n_s, n_m, n_v = await d.event_stats(ev.id)
    rows = [[InlineKeyboardButton(f"🗑 Evento + {n_s} sessioni", callback_data=f"dele:{ev.id}:all")]] if n_s else []
    rows.append([InlineKeyboardButton("🗑 Solo l'evento" + (" (le sessioni restano)" if n_s else ""),
                                      callback_data=f"dele:{ev.id}:only")])
    rows.append([NO_KB])
    await reply(update, f"Eliminare l'evento <b>#{ev.id} {escape(ev.name)}</b>?\n"
                        f"Contiene {n_s} sessioni, {n_m} partite e {n_v} voti.\n\n"
                        f"• <b>Evento + sessioni</b>: cancella tutto, voti compresi.\n"
                        f"• <b>Solo l'evento</b>: le sessioni restano (senza evento) e contano ancora "
                        f"nella classifica globale.", reply_markup=InlineKeyboardMarkup(rows))


async def cmd_delete_match(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete_match id_sessione [numero_partita]: senza numero mostra le partite come bottoni."""
    if not _private_admin(update, ctx):
        return
    d = db(ctx)
    sid = parse_session_arg(ctx)
    s = await d.get_session(sid) if sid else None
    if not s:
        await reply(update, "Uso: /delete_match <i>id_sessione</i> [<i>numero partita</i>]")
        return
    matches = await d.get_matches(sid)
    if len(ctx.args) > 1 and ctx.args[1].isdigit():
        m = next((m for m in matches if m.position == int(ctx.args[1])), None)
        if not m:
            await reply(update, f"La sessione #{sid} non ha una partita {escape(ctx.args[1])}.")
            return
        await _confirm_match_delete(ctx, update.effective_chat.id, m, s)
        return
    if not matches:
        await reply(update, "Questa sessione non ha partite.")
        return
    kb = [[InlineKeyboardButton(f"{m.position}. {m.label()}", callback_data=f"delm:{m.id}:ask")] for m in matches]
    kb.append([NO_KB])
    await reply(update, f"Quale partita della sessione <b>#{sid} {escape(s.name)}</b> vuoi eliminare?",
                reply_markup=InlineKeyboardMarkup(kb))


async def _confirm_match_delete(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, m: Match, s: Session,
                                edit=None) -> None:
    n_votes = await db(ctx).match_votes(m.id)
    poll = "\nIl suo sondaggio nel gruppo verrà chiuso." if (m.poll_message_id and not s.closed_at) else ""
    text = (f"Eliminare la partita <b>{m.position}. {escape(m.label())}</b> (sessione #{s.id})?\n"
            f"Verranno cancellati i suoi {n_votes} voti e i punti relativi. Le partite successive vengono "
            f"rinumerate.{poll}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Sì, elimina", callback_data=f"delm:{m.id}:yes"), NO_KB]])
    if edit:
        await edit(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update, ctx):
        await q.answer("Solo admin", show_alert=True)
        return
    await q.answer()
    d = db(ctx)
    kind, *rest = q.data.split(":")
    if kind == "delx":
        await q.edit_message_text("Ok, non elimino nulla.")
        return

    if kind == "dels":
        s = await d.get_session(int(rest[0]))
        if not s:
            await q.edit_message_text("Sessione non trovata (già eliminata?).")
            return
        stopped = await _stop_open_polls(ctx, s, await d.get_matches(s.id))
        await d.delete_session(s.id)
        await q.edit_message_text(f"🗑 Sessione #{s.id} {s.name} eliminata."
                                  + (f" Chiusi {stopped} sondaggi." if stopped else ""))
        return

    if kind == "dele":
        ev = await d.get_event(int(rest[0]))
        if not ev:
            await q.edit_message_text("Evento non trovato (già eliminato?).")
            return
        with_sessions = rest[1] == "all"
        stopped = 0
        if with_sessions:
            for sid in await d.event_session_ids(ev.id):
                s = await d.get_session(sid)
                stopped += await _stop_open_polls(ctx, s, await d.get_matches(sid))
        await d.delete_event(ev.id, with_sessions)
        await q.edit_message_text(f"🗑 Evento #{ev.id} {ev.name} eliminato"
                                  + (" con tutte le sue sessioni." if with_sessions else "; le sessioni restano.")
                                  + (f" Chiusi {stopped} sondaggi." if stopped else ""))
        return

    if kind == "delm":
        m = await d.get_match(int(rest[0]))
        if not m:
            await q.edit_message_text("Partita non trovata (già eliminata?).")
            return
        s = await d.get_session(m.session_id)
        if rest[1] == "ask":
            await _confirm_match_delete(ctx, q.message.chat.id, m, s, edit=q.edit_message_text)
            return
        await _stop_open_polls(ctx, s, [m])
        await d.delete_match(m.id)
        await q.edit_message_text(f"🗑 Partita {m.label()} eliminata dalla sessione #{s.id}.")


# ---------------------------------------------------------------- mute / smute

async def mute_group(bot, chat_id: int) -> tuple[str | None, str | None]:
    """Muta il gruppo. Ritorna (permessi precedenti in JSON, errore)."""
    try:
        chat = await bot.get_chat(chat_id)
        prev = chat.permissions.to_json() if chat.permissions else None
        await bot.set_chat_permissions(chat_id, ChatPermissions.no_permissions())
        return prev, None
    except TelegramError as e:
        return None, str(e)


async def unmute_group(bot, chat_id: int, saved_json: str | None) -> str | None:
    try:
        if saved_json:
            perms = ChatPermissions.de_json(json.loads(saved_json), bot)
        else:
            perms = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
                                    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                                    can_send_voice_notes=True, can_send_polls=True,
                                    can_send_other_messages=True, can_add_web_page_previews=True)
        await bot.set_chat_permissions(chat_id, perms)
        return None
    except TelegramError as e:
        return str(e)


async def cmd_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == ChatType.PRIVATE or not is_admin(update, ctx):
        return
    _, err = await mute_group(ctx.bot, update.effective_chat.id)
    await reply(update, f"⚠️ Non riesco a mutare il gruppo: {escape(err)}" if err else "🔇 Gruppo mutato.")


async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type == ChatType.PRIVATE or not is_admin(update, ctx):
        return
    saved = await db(ctx).last_saved_permissions(update.effective_chat.id)
    err = await unmute_group(ctx.bot, update.effective_chat.id, saved)
    await reply(update, f"⚠️ Non riesco a smutare il gruppo: {escape(err)}" if err else "🔊 Gruppo riaperto.")


# ---------------------------------------------------------------- step 2: /lancia_sondaggi

async def cmd_launch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await reply(update, "Questo comando va lanciato nel gruppo.")
        return
    if not is_admin(update, ctx):
        return
    c, d = cfg(ctx), db(ctx)
    sid = parse_session_arg(ctx)
    s = await d.get_session(sid) if sid else None
    if not s:
        await reply(update, "Uso: /lancia_sondaggi <i>id_sessione</i>")
        return
    force = len(ctx.args) > 1 and ctx.args[1].lower() == "forza"
    if s.launched_at and not force:
        await reply(update, f"La sessione #{sid} è già stata lanciata. "
                            f"Per rilanciarla comunque: <code>/lancia_sondaggi {sid} forza</code>")
        return
    closes = close_time(s, c)
    seconds_left = (closes - now_utc()).total_seconds()
    if seconds_left < 15:
        await reply(update, f"I sondaggi di questa sessione si sarebbero dovuti chiudere alle "
                            f"{fmt_dt(closes, c.tz)}: troppo tardi per lanciarli.")
        return
    matches = await d.get_matches(sid)

    # Il comando stesso sparisce (serve il permesso di cancellare, altrimenti pazienza).
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass

    prev_perms, mute_err = await mute_group(ctx.bot, chat.id)
    if prev_perms:
        await d.save_permissions(sid, prev_perms)
    await d.mark_launched(sid, chat.id)

    event = await d.get_event(s.event_id) if s.event_id else None
    lines = ([event.name] if event else []) + [f"Inizio: {fmt_dt(s.starts_at, c.tz)}",
             f"Voti aperti fino alle {closes.astimezone(c.tz):%H:%M}",
             f"{len(matches)} partite · chi vince?"]
    # Niente comandi nel messaggio: nel gruppo la gente ci cliccherebbe sopra.
    caption = (f"🎯 <b>{escape(c.cover_title)}</b> – {escape(s.name)}"
               + (f" · {escape(event.name)}" if event else "") + "\n\n"
               f"Vota il vincitore di ogni partita qui sotto: ogni pronostico giusto vale <b>+1 punto</b>.\n"
               f"I sondaggi si chiudono alle <b>{closes.astimezone(c.tz):%H:%M}</b>.")
    try:
        await ctx.bot.send_photo(chat.id, images.cover_image(c.cover_title, s.name, lines),
                                 caption=caption, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning("copertina non inviata: %s", e)
        await ctx.bot.send_message(chat.id, caption, parse_mode=ParseMode.HTML)

    failed = []
    for m in matches:
        kw = {}
        # Telegram accetta close_date solo tra 5 e 600 secondi nel futuro: oltre ci pensa il job.
        secs = (closes - now_utc()).total_seconds()
        if 5 <= secs <= 600:
            kw["close_date"] = closes
        try:
            msg = await ctx.bot.send_poll(
                chat.id,
                question=f"{m.player1} - {m.player2}",
                options=[m.player1, m.player2],
                is_anonymous=False,           # indispensabile per sapere CHI ha votato
                allows_multiple_answers=False,
                **kw,
            )
            await d.set_poll(m.id, msg.poll.id, msg.message_id)
        except TelegramError as e:
            log.exception("send_poll fallito")
            failed.append(f"#{m.position} {m.label()}: {e}")

    if c.mute_mode != "chiusura" and prev_perms is not None:
        err = await unmute_group(ctx.bot, chat.id, prev_perms)
        if err:
            mute_err = f"smute fallito: {err}"

    # Report privato all'admin che ha lanciato
    report = [f"✅ Sessione #{sid} lanciata: {len(matches) - len(failed)}/{len(matches)} sondaggi pubblicati.",
              f"Chiusura automatica alle {fmt_dt(closes, c.tz)}."]
    if mute_err:
        report.append(f"⚠️ Mute: {mute_err}\n(Il bot deve essere admin con il permesso 'Limitare utenti'.)")
    if failed:
        report.append("⚠️ Errori:\n" + "\n".join(failed))
    try:
        await ctx.bot.send_message(update.effective_user.id, "\n".join(report))
    except (Forbidden, BadRequest):  # admin anonimo o chat privata mai aperta col bot
        if failed or mute_err:
            await ctx.bot.send_message(chat.id, "\n".join(report))


# ---------------------------------------------------------------- voti

async def on_poll_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pa = update.poll_answer
    user = pa.user
    if user is None:  # voto "come canale/gruppo" (voter_chat): non associabile a una persona
        return
    choice = pa.option_ids[0] + 1 if pa.option_ids else None
    await register_user(ctx, user)
    status, chat_id = await db(ctx).record_vote(pa.poll_id, user.id, choice, now_utc())
    if chat_id is not None:
        await register_user(ctx, user, chat_id)
    log.info("voto %s user=%s choice=%s -> %s", pa.poll_id, user.id, choice, status)


async def close_due_polls(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Job ricorrente: chiude i sondaggi delle sessioni il cui orario di chiusura è passato."""
    c, d = cfg(ctx), db(ctx)
    for s in await d.sessions_to_close(now_utc()):
        for m in await d.get_matches(s.id):
            if m.poll_message_id:
                try:
                    await ctx.bot.stop_poll(s.chat_id, m.poll_message_id)
                except BadRequest as e:  # già chiuso (es. via close_date) o messaggio cancellato
                    log.info("stop_poll %s: %s", m.id, e)
                except TelegramError:
                    log.exception("stop_poll fallito")
        await d.mark_closed(s.id)
        if c.mute_mode == "chiusura":
            await unmute_group(ctx.bot, s.chat_id, await d.get_saved_permissions(s.id))
        n = await d.total_votes(s.id)
        try:
            await ctx.bot.send_message(
                s.chat_id, f"🔒 Sondaggi di <b>{escape(s.name)}</b> chiusi. Voti registrati: {n}.\n"
                           f"Buon divertimento! 🎯", parse_mode=ParseMode.HTML)
        except TelegramError:
            log.exception("messaggio di chiusura fallito")


# ---------------------------------------------------------------- step 3: /risultati_sessione

def _result_keyboard(m: Match, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🏆 {m.player1}", callback_data=f"r:{mode}:{m.id}:1")],
        [InlineKeyboardButton(f"🏆 {m.player2}", callback_data=f"r:{mode}:{m.id}:2")],
        [InlineKeyboardButton("🚫 Non disputata", callback_data=f"r:{mode}:{m.id}:0"),
         InlineKeyboardButton("❌ Annulla" if mode == "s" else "⏸ Sessione non conclusa",
                              callback_data=f"r:{mode}:{m.id}:stop")],
    ])


def _split_flag(name: str) -> tuple[str, str]:
    """'🏴 Gary Anderson' -> ('🏴', 'Gary Anderson'): la bandiera è la prima parola senza lettere/cifre."""
    tok, _, rest = name.strip().partition(" ")
    if rest and not any(ch.isalnum() for ch in tok):
        return tok, rest.strip()
    return "", name.strip()


def match_line(m: Match) -> str:
    """Riga di una partita per /partite e /sessioni (HTML).

    ✓ 1. <b>Menzies</b> 🏴 <b>3</b> - 2 🇦🇹 Suljovic   (giocata: vincitore e suo punteggio in grassetto)
    ○ 1. Menzies 🏴 - 🇦🇹 Suljovic                     (da giocare)
    """
    f1, n1 = _split_flag(m.player1)
    f2, n2 = _split_flag(m.player2)
    n1, n2 = escape(n1), escape(n2)
    if m.winner == 1:
        n1 = f"<b>{n1}</b>"
    elif m.winner == 2:
        n2 = f"<b>{n2}</b>"
    left = f"{n1} {f1}".strip()
    right = f"{f2} {n2}".strip()
    if m.winner in (1, 2) and m.score1 is not None and m.score2 is not None:
        s1, s2 = str(m.score1), str(m.score2)
        if m.winner == 1:
            s1 = f"<b>{s1}</b>"
        else:
            s2 = f"<b>{s2}</b>"
        mid = f" {s1} - {s2} "
    else:
        mid = " - "
    icon = {None: "○", 0: "🚫"}.get(m.winner, "✓")
    tail = " (non disputata)" if m.winner == 0 else ""
    return f"{icon} {m.position}. {left}{mid}{right}{tail}"


def _winner_text(m: Match) -> str:
    name = {None: "—", 0: "non disputata", 1: m.player1, 2: m.player2}[m.winner]
    if m.winner in (1, 2) and m.score1 is not None and m.score2 is not None:
        name += f" ({m.score1}-{m.score2})"
    return name


async def _ask_next(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, session_id: int, mode: str,
                    after_position: int) -> None:
    d = db(ctx)
    matches = await d.get_matches(session_id)
    if mode == "a":
        nxt = next((m for m in matches if m.position > after_position), None)
    else:
        nxt = next((m for m in matches if m.winner is None), None)
    if nxt is None:
        missing = [m for m in matches if m.winner is None]
        if missing:
            await ctx.bot.send_message(chat_id, f"Mancano ancora {len(missing)} risultati: "
                                                f"/risultati_sessione {session_id}")
            return
        s = await d.get_session(session_id)
        summary = "\n".join(f"{m.position}. {escape(m.label())} → <b>{escape(_winner_text(m))}</b>" for m in matches)
        kb = None
        if s and s.chat_id:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📣 Pubblica classifica nel gruppo",
                                                             callback_data=f"pub:{session_id}")]])
        await ctx.bot.send_message(
            chat_id, f"🎯 Fatto! Tutte le {len(matches)} partite della sessione #{session_id} "
                     f"hanno un risultato.\n\n{summary}", parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    cur = "" if nxt.winner is None else f"\n(attuale: {escape(_winner_text(nxt))})"
    msg = await ctx.bot.send_message(
        chat_id, f"Partita {nxt.position}/{len(matches)}\n<b>{escape(nxt.label())}</b>\n\nChi ha vinto?{cur}"
                 f"{SCORE_HINT}", parse_mode=ParseMode.HTML, reply_markup=_result_keyboard(nxt, mode))
    _await_score(ctx, nxt.id, mode, msg)


async def cmd_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, ctx):
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await reply(update, "Inserisci i risultati in privato con il bot.")
        return
    sid = parse_session_arg(ctx)
    s = await db(ctx).get_session(sid) if sid else None
    if not s:
        await reply(update, "Uso: /risultati_sessione <i>id</i> [tutte]\nVedi /sessioni")
        return
    mode = "a" if len(ctx.args) > 1 and ctx.args[1].lower() in ("tutte", "tutti", "all") else "m"
    matches = await db(ctx).get_matches(sid)
    done = sum(m.winner is not None for m in matches)
    note = ""
    if close_time(s, cfg(ctx)) > now_utc():
        note = "\n⚠️ I sondaggi di questa sessione sono ancora aperti."
    await reply(update, f"📋 <b>{escape(s.name)}</b> – risultati inseriti {done}/{len(matches)}.{note}")
    await _ask_next(ctx, update.effective_chat.id, sid, mode, 0)


async def cmd_match_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/risultato_partita <id_partita>: dà o corregge il risultato di una sola partita."""
    if not is_admin(update, ctx):
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await reply(update, "Inserisci i risultati in privato con il bot.")
        return
    mid = parse_session_arg(ctx)
    m = await db(ctx).get_match(mid) if mid else None
    if not m:
        await reply(update, "Uso: /risultato_partita <i>id_partita</i> (gli ID sono in /sessioni)")
        return
    s = await db(ctx).get_session(m.session_id)
    cur = "" if m.winner is None else f"\n(attuale: {escape(_winner_text(m))})"
    msg = await reply(update, f"📋 {escape(s.name) if s else ''} · partita {m.position}\n<b>{escape(m.label())}</b>\n\n"
                              f"Chi ha vinto?{cur}{SCORE_HINT}", reply_markup=_result_keyboard(m, "s"))
    _await_score(ctx, m.id, "s", msg)


SCORE_HINT = ("\n\nTocca il vincitore (poi ti chiedo i punti), oppure scrivi i punti: "
              "<code>2 3</code> = giocatore 1, spazio, giocatore 2.")
SCORE_RE = re.compile(r"^\s*(\d{1,2})(?:\s*[-–:]\s*|\s+)(\d{1,2})\s*$")
KEYCAPS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]


def _points_kb(prefix: str, values: list[int], skip: str | None) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(KEYCAPS[v], callback_data=f"{prefix}:{v}") for v in values]
    rows = [btns[i:i + 5] for i in range(0, len(btns), 5)]
    if skip:
        rows.append([InlineKeyboardButton("⏭ Senza punteggio", callback_data=skip)])
    return InlineKeyboardMarkup(rows)


async def _result_saved(ctx: ContextTypes.DEFAULT_TYPE, q, m: Match, mode: str) -> None:
    """Risultato completo: aggiorna il messaggio e, se serve, passa alla partita successiva."""
    if (ctx.user_data.get("score_for") or {}).get("match_id") == m.id:
        ctx.user_data.pop("score_for", None)
    await q.edit_message_text(f"✅ {m.position}. {escape(m.label())} → <b>{escape(_winner_text(m))}</b>",
                              parse_mode=ParseMode.HTML)
    if mode != "s":
        await _ask_next(ctx, q.message.chat.id, m.session_id, mode, m.position)


async def cb_points(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tasti dei punti: pw:mode:match:winner:N (punti vincitore), pl:mode:match:winner:wp:N (punti perdente)."""
    q = update.callback_query
    if not is_admin(update, ctx):
        await q.answer("Solo admin", show_alert=True)
        return
    parts = q.data.split(":")
    kind, mode, mid, w = parts[0], parts[1], int(parts[2]), int(parts[3])
    d = db(ctx)
    m = await d.get_match(mid)
    if m is None or w not in (1, 2):
        await q.answer("Partita non trovata")
        return
    await q.answer()
    winner_name, loser_name = (m.player1, m.player2) if w == 1 else (m.player2, m.player1)
    if kind == "pw" and parts[4] == "skip":
        m.winner, m.score1, m.score2 = w, None, None
        await _result_saved(ctx, q, m, mode)
        return
    if kind == "pw":
        wp = int(parts[4])
        await q.edit_message_text(
            f"{m.position}. {escape(m.label())}\n🏆 <b>{escape(winner_name)}</b> {wp}\n\n"
            f"Punti di <b>{escape(loser_name)}</b>?", parse_mode=ParseMode.HTML,
            reply_markup=_points_kb(f"pl:{mode}:{mid}:{w}:{wp}", list(range(wp)), None))
        return
    wp, lp = int(parts[4]), int(parts[5])
    s1, s2 = (wp, lp) if w == 1 else (lp, wp)
    await d.set_winner(m.id, w, s1, s2)
    m.winner, m.score1, m.score2 = w, s1, s2
    await _result_saved(ctx, q, m, mode)


def _await_score(ctx: ContextTypes.DEFAULT_TYPE, match_id: int, mode: str, msg) -> None:
    """Ricorda la partita a cui l'admin può rispondere scrivendo il punteggio."""
    ctx.user_data["score_for"] = {"match_id": match_id, "mode": mode,
                                  "message_id": getattr(msg, "message_id", None)}


async def on_score_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """In privato: '6-4' scritto dopo la domanda 'Chi ha vinto?' salva vincitore e punteggio."""
    pending = ctx.user_data.get("score_for")
    msg = update.effective_message
    if not pending or not msg or not msg.text or not is_admin(update, ctx):
        return
    mt = SCORE_RE.match(msg.text)
    if not mt:
        return
    s1, s2 = int(mt.group(1)), int(mt.group(2))
    if s1 == s2:
        await reply(update, "Pareggio non valido: scrivi i punti con un vincitore, es. <code>2 3</code>.")
        return
    d = db(ctx)
    m = await d.get_match(pending["match_id"])
    if m is None:
        ctx.user_data.pop("score_for", None)
        return
    ctx.user_data.pop("score_for", None)
    winner = 1 if s1 > s2 else 2
    await d.set_winner(m.id, winner, s1, s2)
    m.winner, m.score1, m.score2 = winner, s1, s2
    done = f"✅ {m.position}. {escape(m.label())} → <b>{escape(_winner_text(m))}</b>"
    chat_id = update.effective_chat.id
    if pending.get("message_id"):
        try:
            await ctx.bot.edit_message_text(done, chat_id=chat_id, message_id=pending["message_id"],
                                            parse_mode=ParseMode.HTML)
        except TelegramError:
            await reply(update, done)
    else:
        await reply(update, done)
    if pending["mode"] != "s":
        await _ask_next(ctx, chat_id, m.session_id, pending["mode"], m.position)


async def cb_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update, ctx):
        await q.answer("Solo admin", show_alert=True)
        return
    _, mode, match_id, value = q.data.split(":")
    if value in ("0", "stop") and (ctx.user_data.get("score_for") or {}).get("match_id") == int(match_id):
        ctx.user_data.pop("score_for", None)
    d = db(ctx)
    m = await d.get_match(int(match_id))
    if m is None:
        await q.answer("Partita non trovata")
        return
    if value == "stop" and mode == "s":
        await q.answer()
        await q.edit_message_text(f"Nessuna modifica a {escape(m.label())}.")
        return
    if value == "stop":
        await q.answer("Ok, mi fermo qui")
        await q.edit_message_text(
            f"⏸ Sessione non conclusa: riprendi quando vuoi con /risultati_sessione {m.session_id}")
        return
    await q.answer()
    w = int(value)
    await d.set_winner(m.id, w)  # il vincitore vale subito; i punti si aggiungono con i tasti
    m.winner, m.score1, m.score2 = w, None, None
    if w == 0:
        await _result_saved(ctx, q, m, mode)
        return
    winner_name = m.player1 if w == 1 else m.player2
    await q.edit_message_text(
        f"{m.position}. {escape(m.label())}\n🏆 <b>{escape(winner_name)}</b>\n\n"
        f"Punti di <b>{escape(winner_name)}</b>?", parse_mode=ParseMode.HTML,
        reply_markup=_points_kb(f"pw:{mode}:{m.id}:{w}", list(range(1, 10)), f"pw:{mode}:{m.id}:{w}:skip"))


async def cb_publish(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not is_admin(update, ctx):
        await q.answer("Solo admin", show_alert=True)
        return
    sid = int(q.data.split(":")[1])
    s = await db(ctx).get_session(sid)
    if not s or not s.chat_id:
        await q.answer("Sessione mai lanciata in un gruppo", show_alert=True)
        return
    await q.answer("Pubblico…")
    await send_ranking(ctx, s.chat_id, session=s)
    await q.edit_message_reply_markup(None)
    await ctx.bot.send_message(q.message.chat.id, "📣 Classifica pubblicata nel gruppo.")


# ---------------------------------------------------------------- step 4: classifiche

def pct(points: int, answered: int) -> int | None:
    """% di pronostici giusti sui voti dati a partite con risultato (intero arrotondato)."""
    return (points * 200 + answered) // (2 * answered) if answered else None  # arrotonda 0,5 per eccesso


def _rank_line(p: int, name: str, pts: int, answered: int) -> str:
    pc = pct(pts, answered)
    return f"{p}. {escape(name)} – <b>{pts}</b> ({pts}/{answered}" + (f", {pc}%)" if pc is not None else ")")


def pop_txt_flag(ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Toglie 'txt' dagli argomenti del comando e dice se c'era."""
    args = list(ctx.args or [])
    found = any(a.lower() in ("txt", "testo") for a in args)
    ctx.args = [a for a in args if a.lower() not in ("txt", "testo")]
    return found


async def send_ranking(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, *, session: Session | None = None,
                       event: Event | None = None, scope_chat: int | None = None, text_only: bool = False,
                       tag: bool = True) -> None:
    """tag=False: nomi senza @ (nessuna notifica), per le versioni _txt dei comandi."""
    c, d = cfg(ctx), db(ctx)
    if event:
        rows = await d.ranking(event_id=event.id, tag=tag)
        done, total = await d.results_progress(event_id=event.id)
        state = "in corso" if event.ended_at is None else "concluso"
        title, subtitle = "Classifica evento", f"{event.name} ({state}) · risultati {done}/{total}"
    elif session:
        rows = await d.ranking(session_id=session.id, tag=tag)
        done, total = await d.results_progress(session_id=session.id)
        title, subtitle = "Classifica sessione", f"{session.name} · risultati {done}/{total}"
    else:
        rows = await d.ranking(chat_id=scope_chat, tag=tag)
        done, total = await d.results_progress(chat_id=scope_chat)
        title, subtitle = "Classifica globale", f"Tutti gli eventi · risultati {done}/{total}"
    pos = competition_positions([r.points for r in rows])
    table = [(p, r.display, r.points, r.answered) for p, r in zip(pos, rows)]
    footer = f"SU = voti dati su partite concluse · aggiornata {datetime.now(c.tz):%d/%m %H:%M}"
    head = f"🏆 <b>{escape(title)}</b>\n{escape(subtitle)}\n\n"

    async def send_text() -> None:
        lines = [_rank_line(*row) for row in table] or ["Nessun voto ancora."]
        chunk = head
        for line in lines:  # i messaggi Telegram hanno un limite di 4096 caratteri
            if len(chunk) + len(line) > 3900:
                await ctx.bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)
                chunk = ""
            chunk += line + "\n"
        await ctx.bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML)

    if text_only:
        await send_text()
        return
    top = "\n".join(_rank_line(*row) for row in table[:10]) or "Nessun voto ancora."
    caption = head + top
    if len(caption) > 1000:
        caption = head + "\n".join(_rank_line(*row) for row in table[:5])
    try:
        pages = images.chunks(table, 30)
        for i, page in enumerate(pages):
            t = title if len(pages) == 1 else f"{title} ({i + 1}/{len(pages)})"
            png = images.ranking_image(t, subtitle, page, footer)
            await ctx.bot.send_photo(chat_id, png, caption=caption if i == 0 else None, parse_mode=ParseMode.HTML)
    except TelegramError:
        log.exception("invio immagine classifica fallito, ripiego sul testo")
        await send_text()


def _is_txt_command(update: Update) -> bool:
    """/ranking_txt, /ranking_sessione_txt, /ranking_evento_txt: solo testo e nessun tag."""
    text = (update.effective_message.text or "") if update.effective_message else ""
    cmd = text.split(maxsplit=1)[0].split("@")[0].lower() if text else ""
    return cmd.endswith("_txt")


async def cmd_ranking(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    # Nel gruppo: tutte le sessioni lanciate in quel gruppo. In privato: tutte le sessioni.
    scope = None if chat.type == ChatType.PRIVATE else chat.id
    notag = _is_txt_command(update)
    await send_ranking(ctx, chat.id, scope_chat=scope, text_only=pop_txt_flag(ctx) or notag, tag=not notag)


async def cmd_ranking_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    notag = _is_txt_command(update)
    text_only = pop_txt_flag(ctx) or notag
    sid = parse_session_arg(ctx)
    d = db(ctx)
    if sid:
        s = await d.get_session(sid)
    elif chat.type != ChatType.PRIVATE:
        s = await d.latest_session_for_chat(chat.id)
    else:
        ss = await d.list_sessions(1)
        s = ss[0] if ss else None
    if not s:
        await reply(update, "Sessione non trovata. Uso: /ranking_sessione [<i>id</i>]")
        return
    await send_ranking(ctx, chat.id, session=s, text_only=text_only, tag=not notag)


async def cmd_ranking_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = db(ctx)
    notag = _is_txt_command(update)
    text_only = pop_txt_flag(ctx) or notag
    ref = " ".join(ctx.args).strip()
    ev = await d.find_event(ref) if ref else await d.latest_event()
    if not ev:
        await reply(update, "Evento non trovato. Uso: /ranking_evento [<i>id o nome</i>]")
        return
    await send_ranking(ctx, update.effective_chat.id, event=ev, text_only=text_only, tag=not notag)


# ---------------------------------------------------------------- eventi

async def cmd_start_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, ctx):
        return
    d = db(ctx)
    name = " ".join(ctx.args).strip()
    if not name or len(name) > 120:
        await reply(update, "Uso: /start_event <i>nome evento</i> (max 120 caratteri)")
        return
    active = await d.active_event()
    if active:
        await reply(update, f"C'è già un evento in corso: <b>#{active.id} {escape(active.name)}</b>.\n"
                            f"Chiudilo prima con <code>/end_event {active.id}</code>")
        return
    eid = await d.create_event(name, update.effective_user.id)
    await reply(update, f"🏁 Evento aperto: <b>{escape(name)}</b>\nID evento: <b>{eid}</b>\n\n"
                        f"Le sessioni create da ora con /crea_sessione faranno parte di questo evento.\n"
                        f"Classifica: <code>/ranking_evento {eid}</code> · chiusura: <code>/end_event {eid}</code>")


async def cmd_end_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, ctx):
        return
    d = db(ctx)
    ref = " ".join(ctx.args).strip()
    ev = await d.find_event(ref) if ref else await d.active_event()
    if not ev:
        await reply(update, "Evento non trovato. Uso: /end_event [<i>id o nome</i>] (vedi /eventi)")
        return
    if ev.ended_at:
        await reply(update, f"L'evento <b>#{ev.id} {escape(ev.name)}</b> era già chiuso.")
        return
    await d.end_event(ev.id)
    done, total = await d.results_progress(event_id=ev.id)
    miss = f"\n⚠️ Mancano ancora {total - done} risultati: si possono inserire anche ora." if done < total else ""
    await reply(update, f"🏁 Evento <b>#{ev.id} {escape(ev.name)}</b> chiuso.\n"
                        f"Classifica finale: <code>/ranking_evento {ev.id}</code>{miss}")


async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, ctx):
        return
    c = cfg(ctx)
    events = await db(ctx).list_events(15)
    if not events:
        await reply(update, "Nessun evento. Aprine uno con /start_event <i>nome</i>")
        return
    out = []
    for ev, n in events:
        state = "🟢 in corso" if ev.ended_at is None else f"🏁 chiuso il {ev.ended_at.astimezone(c.tz):%d/%m}"
        out.append(f"<b>#{ev.id}</b> {escape(ev.name)} – {state} · {n} sessioni")
    await reply(update, "\n".join(out))


async def cmd_session_event(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/sessione_evento <id_sessione> <id_evento|nessuno>: corregge l'evento di una sessione."""
    if not is_admin(update, ctx):
        return
    d = db(ctx)
    usage = "Uso: /sessione_evento <i>id_sessione</i> <i>id_evento</i> (oppure <i>nessuno</i>)"
    if len(ctx.args) < 2 or not ctx.args[0].lstrip("#").isdigit():
        await reply(update, usage)
        return
    s = await d.get_session(int(ctx.args[0].lstrip("#")))
    if not s:
        await reply(update, "Sessione non trovata.")
        return
    ref = " ".join(ctx.args[1:]).strip()
    if ref.lower() in ("nessuno", "none", "0", "-"):
        await d.set_session_event(s.id, None)
        await reply(update, f"Sessione #{s.id} tolta dall'evento.")
        return
    ev = await d.find_event(ref)
    if not ev:
        await reply(update, "Evento non trovato. " + usage)
        return
    await d.set_session_event(s.id, ev.id)
    await reply(update, f"Sessione <b>#{s.id} {escape(s.name)}</b> → evento <b>#{ev.id} {escape(ev.name)}</b>.")


# ---------------------------------------------------------------- feedback (/crea_feedback, /consigli)

FB_QUESTION, FB_OPTION, FB_MORE = range(10, 13)
FB_STOP_WORDS = {"stop", "stop feedback", "fine", "basta"}


def _fb_stop_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop feedback", callback_data="fbw:stop")]])


def _fb_more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Aggiungi risposta", callback_data="fbw:add"),
                                  InlineKeyboardButton("✅ Fine domanda", callback_data="fbw:endq")]])


async def fb_new_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update, ctx):
        await reply(update, "Solo gli admin possono creare feedback.")
        return ConversationHandler.END
    ctx.user_data["fb"] = {"questions": [], "current": None}
    await reply(update, "📝 <b>Nuovo feedback</b>\n\nScrivi il testo della <b>domanda 1</b>.\n\n"
                        "/annulla per uscire senza salvare.", reply_markup=_fb_stop_kb())
    return FB_QUESTION


async def fb_new_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() in FB_STOP_WORDS:
        return await _fb_finish(update, ctx, edit=None)
    if not text or len(text) > 300:
        await reply(update, "Domanda non valida (max 300 caratteri). Riprova.", reply_markup=_fb_stop_kb())
        return FB_QUESTION
    data = ctx.user_data["fb"]
    data["current"] = {"text": text, "options": []}
    await reply(update, f"Domanda {len(data['questions']) + 1}: <b>{escape(text)}</b>\n\n"
                        f"Scrivi la <b>prima risposta</b>.")
    return FB_OPTION


async def fb_new_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    cur = ctx.user_data["fb"]["current"]
    # una risposta diventa il testo di un bottone: Telegram ne taglia la lunghezza, teniamola corta
    if not text or len(text) > 60:
        await reply(update, "Risposta non valida (max 60 caratteri). Riprova.")
        return FB_OPTION
    if len(cur["options"]) >= 10:
        await reply(update, "Massimo 10 risposte per domanda: premi <b>Fine domanda</b>.", reply_markup=_fb_more_kb())
        return FB_MORE
    cur["options"].append(text)
    lines = "\n".join(f"{i}. {escape(o)}" for i, o in enumerate(cur["options"], 1))
    await reply(update, f"<b>{escape(cur['text'])}</b>\n{lines}", reply_markup=_fb_more_kb())
    return FB_MORE


async def fb_new_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    data = ctx.user_data.get("fb")
    if not data:
        await q.answer()
        await q.edit_message_reply_markup(None)
        return ConversationHandler.END
    action = q.data.split(":")[1]
    if action == "stop":
        await q.answer()
        return await _fb_finish(update, ctx, edit=q.edit_message_text)
    cur = data["current"]
    if action == "add":
        await q.answer()
        await q.edit_message_reply_markup(None)
        await ctx.bot.send_message(update.effective_chat.id, f"Scrivi la risposta {len(cur['options']) + 1}.")
        return FB_OPTION
    # fine domanda
    if len(cur["options"]) < 2:
        await q.answer("Servono almeno 2 risposte.", show_alert=True)
        return FB_MORE
    await q.answer()
    await q.edit_message_reply_markup(None)
    data["questions"].append(cur)
    data["current"] = None
    n = len(data["questions"]) + 1
    await ctx.bot.send_message(update.effective_chat.id,
                               f"✅ Domanda {n - 1} salvata.\n\nScrivi il testo della <b>domanda {n}</b>, "
                               f"oppure premi <b>Stop feedback</b> per finire.",
                               parse_mode=ParseMode.HTML, reply_markup=_fb_stop_kb())
    return FB_QUESTION


async def _fb_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit) -> int:
    data = ctx.user_data.pop("fb", None) or {"questions": []}
    questions = data["questions"]
    if not questions:
        text = "Feedback annullato: nessuna domanda inserita."
    else:
        pid = await db(ctx).create_feedback(update.effective_user.id,
                                            [(qq["text"], qq["options"]) for qq in questions])
        lines = "\n".join(f"{i}. {escape(qq['text'])} ({len(qq['options'])} risposte)"
                          for i, qq in enumerate(questions, 1))
        text = (f"✅ Feedback salvato.\n\nID feedback: <b>{pid}</b>\n\n{lines}\n\n"
                f"Nel gruppo lancia: <code>/consigli {pid}</code>\n"
                f"Risultati: <code>/risultati_feedback {pid}</code>")
    if edit:
        await edit(text, parse_mode=ParseMode.HTML)
    else:
        await reply(update, text)
    return ConversationHandler.END


async def fb_new_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("fb", None)
    await reply(update, "Creazione feedback annullata.")
    return ConversationHandler.END


def _deep_link(ctx: ContextTypes.DEFAULT_TYPE, payload: str) -> str:
    return f"https://t.me/{ctx.bot.username}?start={payload}"


async def cmd_consigli(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/consigli [ID]: nel gruppo pubblica l'invito con il bottone che apre le domande in privato."""
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        await reply(update, "Questo comando va lanciato nel gruppo: <code>/consigli ID</code>")
        return
    if not is_admin(update, ctx):
        return
    d = db(ctx)
    pid = parse_session_arg(ctx)
    if pid is None and not ctx.args:
        pid = await d.latest_feedback()
    if not pid or not await d.feedback_exists(pid):
        await reply(update, "Uso: /consigli <i>id_feedback</i> (crealo in privato con /crea_feedback)")
        return
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass
    caption = ("💬 <b>Dammi qualche feedback sul poll game!</b>\n\n"
               "Rispondi qui sotto 👇 Le domande arrivano in privato, bastano pochi click.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎯 Dai qualche consiglio sui sondaggi",
                                                     url=_deep_link(ctx, f"fb_{pid}"))]])
    try:
        await ctx.bot.send_photo(chat.id, images.feedback_image(cfg(ctx).cover_title), caption=caption,
                                 parse_mode=ParseMode.HTML, reply_markup=kb)
    except TelegramError as e:
        log.warning("immagine feedback non inviata: %s", e)
        await ctx.bot.send_message(chat.id, caption, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _fb_send_next(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, pool_id: int,
                        after: int = 0, redo: bool = False) -> None:
    """Manda la prossima domanda: in modalità redo quella dopo la posizione `after`,
    altrimenti la prima a cui l'utente non ha ancora risposto."""
    d = db(ctx)
    questions = await d.feedback_questions(pool_id)
    if redo:
        nxt = next((q for q in questions if q.position > after), None)
    else:
        done = await d.feedback_answered(pool_id, user_id)
        nxt = next((q for q in questions if q.id not in done), None)
    if nxt is None:
        await ctx.bot.send_message(chat_id, "🙏 Grazie! Le tue risposte sono state salvate.")
        return
    mode = "r" if redo else "n"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(o.text, callback_data=f"fba:{o.id}:{mode}")]
                               for o in nxt.options])
    await ctx.bot.send_message(chat_id, f"Domanda {nxt.position}/{len(questions)}\n\n<b>{escape(nxt.text)}</b>",
                               parse_mode=ParseMode.HTML, reply_markup=kb)


async def start_feedback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pool_id: int) -> None:
    """Arrivo dal bottone nel gruppo (/start fb_ID): parte il questionario in privato."""
    d = db(ctx)
    user = update.effective_user
    if not await d.feedback_exists(pool_id):
        await reply(update, "Questo feedback non esiste più.")
        return
    questions = await d.feedback_questions(pool_id)
    done = await d.feedback_answered(pool_id, user.id)
    if questions and all(q.id in done for q in questions):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Cambia le risposte", callback_data=f"fbr:{pool_id}")]])
        await reply(update, "Hai già risposto a tutte le domande, grazie! 🎯", reply_markup=kb)
        return
    n = len(questions)
    await reply(update, f"🎯 Grazie per il tuo aiuto! {'È una domanda' if n == 1 else f'Sono {n} domande'}: "
                        f"rispondi con i bottoni.")
    await _fb_send_next(ctx, update.effective_chat.id, user.id, pool_id)


async def cb_feedback_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    _, oid, mode = q.data.split(":")
    d = db(ctx)
    found = await d.feedback_option(int(oid))
    if not found:
        await q.answer("Questo feedback non esiste più.", show_alert=True)
        return
    qid, pool_id = found
    await q.answer()
    user = update.effective_user
    await register_user(ctx, user)
    await d.save_feedback_answer(qid, user.id, int(oid))
    questions = await d.feedback_questions(pool_id)
    this = next(x for x in questions if x.id == qid)
    chosen = next(o.text for o in this.options if o.id == int(oid))
    try:
        await q.edit_message_text(f"Domanda {this.position}/{len(questions)}\n\n<b>{escape(this.text)}</b>\n"
                                  f"✅ {escape(chosen)}", parse_mode=ParseMode.HTML)
    except BadRequest:  # stesso testo (doppio click): niente da cambiare
        pass
    await _fb_send_next(ctx, update.effective_chat.id, user.id, pool_id, after=this.position, redo=mode == "r")


async def cb_feedback_redo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pool_id = int(q.data.split(":")[1])
    await q.edit_message_reply_markup(None)
    if not await db(ctx).feedback_exists(pool_id):
        await ctx.bot.send_message(update.effective_chat.id, "Questo feedback non esiste più.")
        return
    await _fb_send_next(ctx, update.effective_chat.id, update.effective_user.id, pool_id, redo=True)


def _votes(n: int) -> str:
    return f"{n} voto" if n == 1 else f"{n} voti"


async def cmd_feedback_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/risultati_feedback [ID] [N]: senza ID elenco, con ID percentuali, con N chi ha votato cosa."""
    if not _private_admin(update, ctx):
        return
    d = db(ctx)
    args = [a.lstrip("#") for a in ctx.args]
    if not args:
        pools = await d.list_feedbacks()
        if not pools:
            await reply(update, "Nessun feedback. Creane uno con /crea_feedback")
            return
        lines = [f"<b>#{pid}</b> – {nq} domande · {nu} persone hanno risposto · "
                 f"{created.astimezone(cfg(ctx).tz):%d/%m/%Y}" for pid, nq, nu, created in pools]
        await reply(update, "📋 <b>Feedback</b>\n\n" + "\n".join(lines)
                    + "\n\nDettaglio: <code>/risultati_feedback ID</code>")
        return
    if not all(a.isdigit() for a in args[:2]):
        await reply(update, "Uso: /risultati_feedback <i>id</i> [<i>numero domanda</i>]")
        return
    pid = int(args[0])
    if len(args) > 1:
        await send_feedback_detail(update, ctx, pid, int(args[1]))
        return
    if not await d.feedback_exists(pid):
        await reply(update, f"Il feedback #{pid} non esiste.")
        return
    questions = await d.feedback_questions(pid)
    counts = await d.feedback_counts(pid)
    people = await d.feedback_respondents(pid)
    out = [f"📊 <b>Feedback #{pid}</b> – {people} {'persona ha' if people == 1 else 'persone hanno'} risposto"]
    for q in questions:
        tot = sum(counts.get(o.id, 0) for o in q.options)
        link = f'<a href="{_deep_link(ctx, f"fbd_{pid}_{q.position}")}">🔎 dettaglio</a>'
        out.append(f"\n<b>{q.position}. {escape(q.text)}</b>  {link}")
        for o in q.options:
            n = counts.get(o.id, 0)
            p = round(n * 100 / tot) if tot else 0
            out.append(f"<code>{p:>3}%</code> · {_votes(n)} – {escape(o.text)}")
    await reply(update, "\n".join(out))


async def send_feedback_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pool_id: int, pos: int) -> None:
    d = db(ctx)
    questions = await d.feedback_questions(pool_id)
    q = next((x for x in questions if x.position == pos), None)
    if q is None:
        await reply(update, f"Il feedback #{pool_id} non ha una domanda {pos}." if questions
                    else f"Il feedback #{pool_id} non esiste.")
        return
    voters = await d.feedback_voters(q.id)
    tot = sum(len(v) for v in voters.values())
    out = [f"🔎 <b>Feedback #{pool_id} – domanda {q.position}</b>\n<b>{escape(q.text)}</b>"]
    for o in q.options:
        names = voters.get(o.id, [])
        p = round(len(names) * 100 / tot) if tot else 0
        out.append(f"\n<b>{escape(o.text)}</b> – {p}% · {_votes(len(names))}\n"
                   + (escape(", ".join(names)) if names else "–"))
    await reply(update, "\n".join(out))


# ---------------------------------------------------------------- registro membri

async def on_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return
    await register_user(ctx, update.effective_user, update.effective_chat.id)
    for u in msg.new_chat_members or []:
        await register_user(ctx, u, update.effective_chat.id, "member")
    if msg.left_chat_member:
        await register_user(ctx, msg.left_chat_member, update.effective_chat.id, "left")


async def on_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cm = update.chat_member
    status = cm.new_chat_member.status
    st = "left" if status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) else "member"
    await register_user(ctx, cm.new_chat_member.user, cm.chat.id, st)


async def cmd_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quanti utenti del gruppo il bot conosce (e quanti ne dichiara Telegram)."""
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE or not is_admin(update, ctx):
        return
    known = await db(ctx).count_members(chat.id)
    try:
        total = await ctx.bot.get_chat_member_count(chat.id)
        # Gli admin si possono leggere tutti: li registriamo subito.
        for a in await ctx.bot.get_chat_administrators(chat.id):
            await register_user(ctx, a.user, chat.id, "member")
        known = await db(ctx).count_members(chat.id)
    except TelegramError:
        total = "?"
    await reply(update, f"👥 Utenti conosciuti dal bot: {known} su {total} membri.\n"
                        "Gli altri vengono registrati automaticamente appena votano o scrivono.")


# ---------------------------------------------------------------- errori

async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Errore gestendo un update", exc_info=ctx.error)


# ---------------------------------------------------------------- registrazione

def setup(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    # Il bottone "Salva" è nell'ultimo messaggio della conversazione: per_message non serve.
    warnings.filterwarnings("ignore", message="If 'per_message=False'", category=PTBUserWarning)
    conv = ConversationHandler(
        entry_points=[CommandHandler("crea_sessione", new_session_start, filters=private)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_session_name)],
            ASK_MATCHES: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_session_matches)],
            ASK_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_session_time)],
            ASK_CONFIRM: [CallbackQueryHandler(new_session_confirm, pattern=r"^new:")],
        },
        fallbacks=[CommandHandler("annulla", new_session_cancel)],
        conversation_timeout=timedelta(minutes=30),
    )
    app.add_handler(conv)
    fb_conv = ConversationHandler(
        entry_points=[CommandHandler("crea_feedback", fb_new_start, filters=private)],
        states={
            FB_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, fb_new_question),
                          CallbackQueryHandler(fb_new_button, pattern=r"^fbw:stop")],
            FB_OPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, fb_new_option)],
            # se invece di premere un bottone scrive un'altra risposta, la aggiungiamo
            FB_MORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, fb_new_option),
                      CallbackQueryHandler(fb_new_button, pattern=r"^fbw:(add|endq)")],
        },
        fallbacks=[CommandHandler("annulla", fb_new_cancel)],
        conversation_timeout=timedelta(minutes=30),
    )
    app.add_handler(fb_conv)
    app.add_handler(CommandHandler("consigli", cmd_consigli))
    app.add_handler(CommandHandler("risultati_feedback", cmd_feedback_results))
    app.add_handler(CallbackQueryHandler(cb_feedback_answer, pattern=r"^fba:"))
    app.add_handler(CallbackQueryHandler(cb_feedback_redo, pattern=r"^fbr:"))
    app.add_handler(CommandHandler(["start", "help", "aiuto"], cmd_start))
    app.add_handler(CommandHandler("mio_id", cmd_my_id))
    app.add_handler(CommandHandler(["impostazioni", "settings"], cmd_settings))
    app.add_handler(CallbackQueryHandler(cb_settings, pattern=r"^tag:(on|off)$"))
    app.add_handler(CommandHandler("sessioni", cmd_sessions))
    app.add_handler(CommandHandler("partite", cmd_matches))
    app.add_handler(CommandHandler(["delete_session", "elimina_sessione"], cmd_delete_session))
    app.add_handler(CommandHandler("delete_event", cmd_delete_event))
    app.add_handler(CommandHandler("delete_match", cmd_delete_match))
    app.add_handler(CommandHandler("lancia_sondaggi", cmd_launch))
    app.add_handler(CommandHandler("muta", cmd_mute))
    app.add_handler(CommandHandler("smuta", cmd_unmute))
    app.add_handler(CommandHandler("membri", cmd_members))
    app.add_handler(CommandHandler("risultati_sessione", cmd_results))
    app.add_handler(CommandHandler("risultato_partita", cmd_match_result))
    app.add_handler(CommandHandler(["ranking", "ranking_txt"], cmd_ranking))
    app.add_handler(CommandHandler(["ranking_sessione", "ranking_sessione_txt"], cmd_ranking_session))
    app.add_handler(CommandHandler(["ranking_evento", "ranking_evento_txt"], cmd_ranking_event))
    app.add_handler(CommandHandler("start_event", cmd_start_event))
    app.add_handler(CommandHandler("end_event", cmd_end_event))
    app.add_handler(CommandHandler("eventi", cmd_events))
    app.add_handler(CommandHandler("sessione_evento", cmd_session_event))
    app.add_handler(CallbackQueryHandler(cb_result, pattern=r"^r:"))
    app.add_handler(CallbackQueryHandler(cb_points, pattern=r"^p[wl]:"))
    app.add_handler(CallbackQueryHandler(cb_publish, pattern=r"^pub:"))
    app.add_handler(CallbackQueryHandler(cb_matches_to_group, pattern=r"^ptg:"))
    app.add_handler(CallbackQueryHandler(cb_delete, pattern=r"^del[xsem]"))
    app.add_handler(PollAnswerHandler(on_poll_answer))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    # gruppo separato: registra chi scrive anche quando un comando ha già gestito il messaggio
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_score_text))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, on_group_message), group=1)
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(close_due_polls, interval=30, first=5, name="close_due_polls")


BOT_COMMANDS_PRIVATE = [
    ("start_event", "Apri un evento: /start_event nome"),
    ("end_event", "Chiudi l'evento in corso"),
    ("eventi", "Elenco eventi"),
    ("crea_sessione", "Crea una sessione di sondaggi"),
    ("sessioni", "Elenco sessioni"),
    ("partite", "Elenco partite di una sessione: /partite id"),
    ("risultati_sessione", "Inserisci i risultati: /risultati_sessione id"),
    ("risultato_partita", "Risultato di una partita: /risultato_partita id"),
    ("ranking", "Classifica globale"),
    ("ranking_evento", "Classifica di un evento"),
    ("ranking_sessione", "Classifica di una sessione"),
    ("crea_feedback", "Crea un feedback (domande e risposte)"),
    ("risultati_feedback", "Risultati feedback: /risultati_feedback id [n]"),
    ("impostazioni", "Tag in classifica: attivo o disattivo"),
    ("annulla", "Annulla la creazione in corso"),
]
BOT_COMMANDS_GROUP = [
    ("ranking", "Classifica globale"),
    ("ranking_evento", "Classifica dell'evento in corso"),
    ("ranking_sessione", "Classifica dell'ultima sessione"),
    ("ranking_txt", "Classifica globale in testo, senza tag"),
    ("ranking_evento_txt", "Classifica evento in testo, senza tag"),
    ("ranking_sessione_txt", "Classifica sessione in testo, senza tag"),
]

__all__ = ["setup", "BOT_COMMANDS_PRIVATE", "BOT_COMMANDS_GROUP"]

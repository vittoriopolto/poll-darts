# PDC180 Poll Bot 🎯

Bot Telegram per i pronostici sulle partite di freccette: per ogni partita di una sessione pubblica un
sondaggio nel gruppo, registra chi vota cosa, e dopo che l'admin inserisce i vincitori genera la
classifica (+1 punto per ogni pronostico giusto), anche come immagine.

Stack: Python 3.12 · python-telegram-bot 22 · Postgres (Neon) · deploy su Render (webhook).

## Come si usa

| Dove | Comando | Cosa fa |
|---|---|---|
| privato o gruppo (admin) | `/start_event nome` | apre un evento e restituisce l'**ID evento**: tutte le sessioni create da ora ne fanno parte (un evento alla volta) |
| privato o gruppo (admin) | `/end_event [ID o nome]` | chiude l'evento (senza argomenti: quello in corso). I risultati si possono inserire anche dopo |
| privato (admin) | `/eventi` | elenco eventi con numero di sessioni |
| privato (admin) | `/sessione_evento ID_SESSIONE ID_EVENTO` | sposta una sessione in un altro evento (`nessuno` per toglierla) |
| privato (admin) | `/crea_sessione` | chiede nome, partite (`GIOCATORE 1 - GIOCATORE 2`, una per riga) e orario di inizio; salva e restituisce l'**ID sessione** |
| gruppo (admin) | `/lancia_sondaggi ID` | muta il gruppo, pubblica la copertina "PDC180 POLL" e un sondaggio per partita, poi riapre il gruppo |
| — | (automatico) | i sondaggi si chiudono **5 minuti prima** dell'inizio sessione |
| privato (admin) | `/risultati_sessione ID` | per ogni partita senza risultato: bottoni *Giocatore 1* / *Giocatore 2* / *Non disputata* / *Sessione non conclusa*. Rilanciandolo riprende da dove mancava |
| privato (admin) | `/risultati_sessione ID tutte` | ripassa tutte le partite per correggere un risultato |
| gruppo / privato | `/ranking` | classifica globale (nel gruppo: tutte le sessioni lanciate in quel gruppo) |
| gruppo / privato | `/ranking_evento [ID o nome]` | classifica di un evento (default: quello in corso, o l'ultimo chiuso) |
| gruppo / privato | `/ranking_sessione [ID]` | classifica di una sessione (default: l'ultima lanciata nel gruppo) |
| privato (admin) | `/sessioni` | elenco sessioni con stato e risultati inseriti, e sotto le partite con il loro **ID partita** (e il vincitore se c'è) |
| privato (admin) | `/risultato_partita ID` | dà o corregge il risultato di una sola partita (bottoni come in `/risultati_sessione`, più *Annulla*) |
| privato o gruppo (admin) | `/partite ID` | in testo: 🏆 nome evento, 📁 nome sessione, ⏰ orario di inizio e l'elenco delle partite. In privato poi chiede con un bottone se mandarlo nel gruppo (quello in cui è stata lanciata la sessione, altrimenti quello dell'ultima sessione lanciata); nel gruppo cancella il comando e lo manda come messaggio a sé |
| privato (admin) | `/delete_event ID o nome` | elimina un evento: bottoni "evento + sessioni" (cancella tutto, voti compresi) o "solo l'evento" (le sessioni restano, senza evento) |
| privato (admin) | `/delete_session ID` | elimina una sessione con partite e voti; chiude i suoi sondaggi se ancora aperti |
| privato (admin) | `/delete_match ID_SESSIONE [N]` | elimina la partita N (senza N mostra le partite come bottoni) e i suoi voti; le successive vengono rinumerate |
| | | Ogni eliminazione chiede conferma con un bottone. I messaggi già pubblicati nel gruppo restano |
| privato (admin) | `/crea_feedback` | crea un **feedback**: chiede il testo della domanda, poi le risposte una alla volta (*➕ Aggiungi risposta* / *✅ Fine domanda*), poi la domanda successiva, finché non premi *🛑 Stop feedback*. Restituisce l'**ID feedback** |
| gruppo (admin) | `/consigli [ID]` | pubblica un'immagine "Dammi qualche feedback" con il bottone **Dai qualche consiglio sui sondaggi**: chi lo preme apre il bot in privato e risponde alle domande con i bottoni (default: l'ultimo feedback creato) |
| privato (admin) | `/risultati_feedback [ID]` | per ogni domanda: % e numero di voti per risposta, con il link 🔎 *dettaglio*. Senza ID: elenco dei feedback |
| privato (admin) | `/risultati_feedback ID N` | chi ha votato cosa alla domanda N (lo stesso del link *dettaglio*) |
| gruppo (admin) | `/muta`, `/smuta`, `/membri` | mute manuale; quanti membri il bot conosce |
| privato (tutti) | `/impostazioni` | stato del tag con i bottoni ✅ Start Tag / 🛑 Stop Tag: con lo stop in classifica (testo, immagine, risultati feedback) compari col nome senza @, quindi niente notifica. Di default il tag è attivo |
| ovunque | `/mio_id` | mostra il tuo ID Telegram (serve per `ADMIN_IDS`) |

Formati orario accettati: `25/11/2026 15:30`, `25/11 15:30`, `domani 15:30`, `15:30` (fuso `Europe/Rome`).
Separatori partite accettati: `-`, `–`, `vs`. Numerazione iniziale (`1)`, `1.`) ignorata.

Tre livelli di classifica: **sessione** → **evento** (insieme di sessioni) → **globale** (tutto).
Il riepilogo di `/crea_sessione` mostra a quale evento verrà assegnata la sessione.

A fine inserimento risultati il bot offre il bottone **📣 Pubblica classifica nel gruppo**.

## Come funziona Telegram (cose importanti)

- **I voti arrivano a ogni click**, non a fine sondaggio: il bot riceve un update `poll_answer` ogni
  volta che qualcuno vota, cambia o ritira il voto. Quindi la classifica si può vedere anche a metà
  sessione, appena hai inserito qualche risultato. Questo vale **solo per i sondaggi non anonimi**:
  il bot li crea sempre con `is_anonymous=False`. Conseguenza: nel gruppo tutti possono vedere chi
  ha votato cosa.
- **Elenco iscritti**: la Bot API **non permette** di scaricare la lista dei membri di un gruppo
  (solo gli admin, con `getChatAdministrators`). Non serve: il bot salva ID, username e nome di
  chiunque **voti** (è l'unica cosa che conta per la classifica), scriva nel gruppo, entri o esca.
  `/membri` mostra quanti ne conosce. Se servisse davvero l'elenco completo bisognerebbe usare un
  "userbot" MTProto (Telethon/Pyrogram) con un account utente: sconsigliato e non incluso.
- **Chiusura dei sondaggi**: Telegram accetta una chiusura automatica (`close_date`) solo entro 10
  minuti. Se lanci i sondaggi entro 10 minuti dalla chiusura il bot usa `close_date`; altrimenti un
  job interno controlla ogni 30 secondi e chiama `stopPoll`. In più i voti arrivati dopo l'orario di
  chiusura vengono **ignorati nel conteggio** in ogni caso.
- **Mute**: il bot deve essere **admin del gruppo** con il permesso *Limitare utenti* (e meglio anche
  *Eliminare messaggi*, per far sparire il comando `/lancia_sondaggi`). Gli admin del gruppo possono
  scrivere anche col gruppo mutato. Con `MUTE_MODE=invio` (default) il gruppo resta mutato solo
  mentre il bot pubblica; con `MUTE_MODE=chiusura` resta mutato fino alla chiusura dei sondaggi.
- **Limite messaggi**: Telegram consente ai bot ~20 messaggi al minuto per gruppo. Con più di ~18
  partite il bot rallenta da solo (rate limiter), quindi la pubblicazione può durare 1-2 minuti.

## Setup

### 1. Bot (BotFather)
1. `/newbot` → copia il token (`BOT_TOKEN`).
2. Aggiungi il bot al gruppo e rendilo **admin** (Limitare utenti, Eliminare messaggi).
3. Scrivi `/mio_id` al bot in privato per scoprire il tuo ID (`ADMIN_IDS`). Se il bot non è ancora
   deployato, @userinfobot dà lo stesso numero.

### 2. Database (Neon)
1. Crea un progetto su [neon.tech](https://neon.tech) (regione EU, es. Frankfurt).
2. *Connect* → copia la connection string (`postgresql://...?sslmode=require`) → `DATABASE_URL`.
   Va bene sia quella diretta sia quella "pooled". Le tabelle vengono create in automatico al primo avvio.
3. Il free tier mette in pausa il database dopo 5 minuti di inattività: si risveglia da solo in ~1 secondo.

### 3. Codice su GitHub
Pubblica questa cartella in un repository GitHub (privato va benissimo).

### 4. Render
1. [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → scegli il repo:
   legge `render.yaml` e crea il web service. (In alternativa *New → Web Service* con build
   `pip install -r requirements.txt` e start `python -m pdc`.)
2. Imposta le variabili: `BOT_TOKEN`, `DATABASE_URL`, `ADMIN_IDS`.
3. Al deploy il bot registra da solo il webhook su `https://<tuo-servizio>.onrender.com/telegram`.

**Free tier di Render**: il servizio si addormenta dopo 15 minuti senza traffico e si risveglia alla
prima richiesta (~30-60 secondi; Telegram riprova da solo, quindi nessun voto si perde). Mentre dorme
però non esegue la chiusura programmata dei sondaggi: il bot li chiude al risveglio e comunque non
conta i voti arrivati in ritardo. Per l'evento conviene una di queste:
- un ping gratuito ogni 10 minuti su `https://<tuo-servizio>.onrender.com/health`
  (es. cron-job.org o UptimeRobot): 750 ore gratis al mese bastano per tenerlo sveglio sempre;
- oppure il piano *Starter* (sempre acceso).

### Variabili d'ambiente

| Variabile | Obbligatoria | Default | Note |
|---|---|---|---|
| `BOT_TOKEN` | sì | | da BotFather |
| `DATABASE_URL` | sì | | Neon |
| `ADMIN_IDS` | sì | | ID separati da virgola |
| `TIMEZONE` | | `Europe/Rome` | |
| `CLOSE_MINUTES_BEFORE` | | `5` | minuti prima dell'inizio in cui chiudere |
| `COVER_TITLE` | | `PDC180 POLL` | titolo sulla copertina generata |
| `MUTE_MODE` | | `invio` | `invio` o `chiusura` |
| `WEBHOOK_SECRET` | | derivato dal token | |
| `PUBLIC_URL` | | `RENDER_EXTERNAL_URL` | solo fuori da Render; se vuoto → polling |

**Copertina personalizzata**: metti un file `assets/cover.png` (o `.jpg`) nel repo e verrà usato al
posto di quella generata.

## In locale

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # compila i valori
set -a; source .env; set +a
python -m pdc --polling      # niente webhook: comodo per provare
```

Attenzione: finché il bot gira in polling in locale, il webhook di Render viene disattivato.
Ridistribuisci (o riavvia) su Render per riattivarlo.

Test (serve un Postgres vuoto, viene **svuotato**):
```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/pdc_test pytest -q
```

## Struttura

```
pdc/
  main.py       avvio: webhook (Starlette+uvicorn) o polling
  handlers.py   tutti i comandi, i bottoni e la registrazione dei voti
  db.py         query Postgres (asyncpg)
  schema.sql    tabelle (create in automatico)
  parsing.py    lettura partite/orari, piazzamenti a pari merito
  images.py     copertina e classifica in PNG (Pillow)
assets/logo.jpg logo PDC180 (usato nelle immagini)
assets/fonts    DejaVu Sans e Liberation Sans (licenze libere)
render.yaml     configurazione Render
```

Punteggio: +1 per ogni voto uguale al vincitore. Pari merito stile sportivo (1, 1, 3…). Le partite
"non disputate" non danno punti a nessuno. Nella classifica la colonna "SU" indica quanti voti
l'utente ha dato su partite che hanno già un risultato, e "%" la percentuale di pronostici giusti
su quei voti (arrotondata). Aggiungendo `txt` ai comandi di classifica (es. `/ranking txt`,
`/ranking_sessione 3 txt`, `/ranking_evento txt`) arriva solo il testo, senza immagine.

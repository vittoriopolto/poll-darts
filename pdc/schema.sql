-- Schema del bot. Viene applicato all'avvio (tutto idempotente).

CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chi abbiamo visto in quale gruppo (messaggi, voti, ingressi).
CREATE TABLE IF NOT EXISTS group_members (
    chat_id     BIGINT NOT NULL,
    user_id     BIGINT NOT NULL REFERENCES users(user_id),
    status      TEXT NOT NULL DEFAULT 'member',
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, user_id)
);

-- Un evento raggruppa più sessioni (es. "Open di Natale 2026").
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ              -- NULL = evento in corso
);

CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    starts_at   TIMESTAMPTZ NOT NULL,
    created_by  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    chat_id     BIGINT,              -- gruppo in cui sono stati lanciati i sondaggi
    launched_at TIMESTAMPTZ,
    closed_at   TIMESTAMPTZ,         -- quando il bot ha chiuso i sondaggi
    saved_permissions JSONB          -- permessi del gruppo prima del mute, per ripristinarli
);

CREATE TABLE IF NOT EXISTS matches (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    player1         TEXT NOT NULL,
    player2         TEXT NOT NULL,
    poll_id         TEXT UNIQUE,
    poll_message_id BIGINT,
    winner          SMALLINT CHECK (winner IN (0, 1, 2)),  -- NULL = da inserire, 0 = non disputata
    UNIQUE (session_id, position)
);

CREATE TABLE IF NOT EXISTS votes (
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    user_id     BIGINT  NOT NULL REFERENCES users(user_id),
    choice      SMALLINT NOT NULL CHECK (choice IN (1, 2)),
    voted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, user_id)
);

CREATE INDEX IF NOT EXISTS votes_user_idx ON votes(user_id);

-- Aggiunto dopo la prima versione: ogni sessione può appartenere a un evento.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS event_id INTEGER REFERENCES events(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS sessions_event_idx ON sessions(event_id);

-- Feedback: un "pool" di domande a risposta multipla, a cui gli utenti rispondono in privato.
CREATE TABLE IF NOT EXISTS feedback_pools (
    id          SERIAL PRIMARY KEY,
    created_by  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback_questions (
    id          SERIAL PRIMARY KEY,
    pool_id     INTEGER NOT NULL REFERENCES feedback_pools(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (pool_id, position)
);

CREATE TABLE IF NOT EXISTS feedback_options (
    id          SERIAL PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES feedback_questions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    UNIQUE (question_id, position)
);

-- Una risposta per utente e domanda (se risponde di nuovo, si sovrascrive).
CREATE TABLE IF NOT EXISTS feedback_answers (
    question_id INTEGER NOT NULL REFERENCES feedback_questions(id) ON DELETE CASCADE,
    user_id     BIGINT  NOT NULL REFERENCES users(user_id),
    option_id   INTEGER NOT NULL REFERENCES feedback_options(id) ON DELETE CASCADE,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (question_id, user_id)
);

-- Aggiunto dopo: l'utente può scegliere (/impostazioni) di non essere taggato con la @.
ALTER TABLE users ADD COLUMN IF NOT EXISTS tag_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- Aggiunto dopo: punteggio della partita (facoltativo, es. 6-4), oltre al vincitore.
ALTER TABLE matches ADD COLUMN IF NOT EXISTS score1 SMALLINT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS score2 SMALLINT;

-- Aggiunto dopo: tabellone a eliminazione diretta di un evento (accoppiamenti del 1° turno in ordine).
-- draw = lista JSON di giocatori [seed, nome, nazione], due per partita. I risultati si prendono dalle partite.
CREATE TABLE IF NOT EXISTS brackets (
    event_id    INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    title       TEXT,
    draw        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

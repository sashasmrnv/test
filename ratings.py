"""Рейтинг игроков по системе Эло, отдельный для каждого чата.

Хранится в SQLite-файле (по умолчанию ratings.db). Рейтинг считается
в пределах группы: у каждого чата свой топ.
"""

import os
import sqlite3

DB_PATH = os.getenv("RATINGS_DB", "ratings.db")
START_RATING = 1000
K_FACTOR = 32

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name    TEXT,
                rating  REAL    NOT NULL DEFAULT 1000,
                wins    INTEGER NOT NULL DEFAULT 0,
                losses  INTEGER NOT NULL DEFAULT 0,
                games   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        _conn.commit()
    return _conn


def get(chat_id, user_id):
    row = _db().execute(
        "SELECT rating, wins, losses, games, name FROM players "
        "WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if not row:
        return None
    return {
        "rating": row[0],
        "wins": row[1],
        "losses": row[2],
        "games": row[3],
        "name": row[4],
    }


def _rating(chat_id, user_id):
    r = get(chat_id, user_id)
    return r["rating"] if r else START_RATING


def _apply(chat_id, user_id, name, new_rating, win):
    cur = get(chat_id, user_id)
    dw, dl = (1, 0) if win else (0, 1)
    if cur:
        _db().execute(
            "UPDATE players SET rating=?, wins=wins+?, losses=losses+?, "
            "games=games+1, name=? WHERE chat_id=? AND user_id=?",
            (new_rating, dw, dl, name, chat_id, user_id),
        )
    else:
        _db().execute(
            "INSERT INTO players (chat_id, user_id, name, rating, wins, losses, games) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (chat_id, user_id, name, new_rating, dw, dl),
        )
    _db().commit()


def record_result(chat_id, winner_id, winner_name, loser_id, loser_name):
    """Обновляет рейтинги после партии. Возвращает новые значения и приросты."""
    rw = _rating(chat_id, winner_id)
    rl = _rating(chat_id, loser_id)
    expected_w = 1 / (1 + 10 ** ((rl - rw) / 400))
    new_w = rw + K_FACTOR * (1 - expected_w)
    new_l = rl + K_FACTOR * (0 - (1 - expected_w))
    _apply(chat_id, winner_id, winner_name, new_w, win=True)
    _apply(chat_id, loser_id, loser_name, new_l, win=False)
    return {
        "winner": (round(new_w), round(new_w - rw)),
        "loser": (round(new_l), round(new_l - rl)),
    }


def top(chat_id, limit=10):
    return _db().execute(
        "SELECT name, rating, wins, losses, games FROM players "
        "WHERE chat_id=? ORDER BY rating DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()

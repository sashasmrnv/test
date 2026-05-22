"""Telegram-бот «Русские шашки» для игры в группе — всё в одном файле.

aiogram 2.12. Доска рисуется inline-кнопками внутри одного сообщения.
Возможности: открытый и адресный (reply / @username) вызов, рейтинг Эло
по каждой группе (/top, /me), таймер на ход с предупреждением и
поражением по времени.

Запуск:
    pip install aiogram==2.12.1
    python checkers_bot.py
"""

import asyncio
import html
import logging
import sqlite3

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.utils.exceptions import MessageNotModified, TelegramAPIError

# ======================= НАСТРОЙКИ (правьте здесь) =======================

# Токен от @BotFather. ВНИМАНИЕ: не выкладывайте файл с реальным токеном
# в публичный репозиторий — это даст посторонним полный доступ к боту.
BOT_TOKEN = "123456:PASTE_YOUR_TOKEN_FROM_BOTFATHER"

# Лимит времени на один ход (секунды). При просрочке — поражение по времени.
MOVE_TIME_SECONDS = 60
# За сколько секунд до конца предупредить игрока (0 — не предупреждать).
WARN_BEFORE = 10
# Файл с рейтингом игроков.
RATINGS_DB = "ratings.db"

# =========================================================================

logging.basicConfig(level=logging.INFO)

# ============================= ДВИЖОК ШАШЕК ===============================
# Доска 8x8, играем на тёмных полях, где (row + col) нечётно.
# Строки 0..2 сверху — чёрные, строки 5..7 снизу — белые.
# Белые ходят вверх (строка уменьшается), чёрные — вниз.

WHITE_MAN, WHITE_KING = "w", "W"
BLACK_MAN, BLACK_KING = "b", "B"
WHITE, BLACK = "white", "black"
SIZE = 8
DIRS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def initial_board():
    board = [[None] * SIZE for _ in range(SIZE)]
    for r in range(3):
        for c in range(SIZE):
            if (r + c) % 2 == 1:
                board[r][c] = BLACK_MAN
    for r in range(5, SIZE):
        for c in range(SIZE):
            if (r + c) % 2 == 1:
                board[r][c] = WHITE_MAN
    return board


def owner(piece):
    if piece in (WHITE_MAN, WHITE_KING):
        return WHITE
    if piece in (BLACK_MAN, BLACK_KING):
        return BLACK
    return None


def is_king(piece):
    return piece in (WHITE_KING, BLACK_KING)


def in_bounds(r, c):
    return 0 <= r < SIZE and 0 <= c < SIZE


def man_captures(board, r, c):
    """Одиночные взятия простой шашкой (вперёд и назад)."""
    me = owner(board[r][c])
    res = []
    for dr, dc in DIRS:
        mr, mc = r + dr, c + dc
        lr, lc = r + 2 * dr, c + 2 * dc
        if in_bounds(lr, lc) and board[lr][lc] is None:
            mid = board[mr][mc]
            if mid is not None and owner(mid) != me:
                res.append(((lr, lc), (mr, mc)))
    return res


def king_captures(board, r, c):
    """Одиночные взятия дамкой (бьёт на любом расстоянии по диагонали)."""
    me = owner(board[r][c])
    res = []
    for dr, dc in DIRS:
        i = 1
        while in_bounds(r + dr * i, c + dc * i) and board[r + dr * i][c + dc * i] is None:
            i += 1
        if not in_bounds(r + dr * i, c + dc * i):
            continue
        cr, cc = r + dr * i, c + dc * i
        if owner(board[cr][cc]) == me:
            continue  # своя фигура закрывает диагональ
        j = i + 1
        while in_bounds(r + dr * j, c + dc * j) and board[r + dr * j][c + dc * j] is None:
            res.append(((r + dr * j, c + dc * j), (cr, cc)))
            j += 1
    return res


def captures_for(board, r, c):
    piece = board[r][c]
    if piece is None:
        return []
    return king_captures(board, r, c) if is_king(piece) else man_captures(board, r, c)


def man_moves(board, r, c):
    me = owner(board[r][c])
    fdir = -1 if me == WHITE else 1
    res = []
    for dc in (-1, 1):
        nr, nc = r + fdir, c + dc
        if in_bounds(nr, nc) and board[nr][nc] is None:
            res.append((nr, nc))
    return res


def king_moves(board, r, c):
    res = []
    for dr, dc in DIRS:
        i = 1
        while in_bounds(r + dr * i, c + dc * i) and board[r + dr * i][c + dc * i] is None:
            res.append((r + dr * i, c + dc * i))
            i += 1
    return res


def moves_for(board, r, c):
    piece = board[r][c]
    if piece is None:
        return []
    return king_moves(board, r, c) if is_king(piece) else man_moves(board, r, c)


def player_has_capture(board, player):
    for r in range(SIZE):
        for c in range(SIZE):
            if owner(board[r][c]) == player and captures_for(board, r, c):
                return True
    return False


def player_has_move(board, player):
    for r in range(SIZE):
        for c in range(SIZE):
            if owner(board[r][c]) == player and (
                captures_for(board, r, c) or moves_for(board, r, c)
            ):
                return True
    return False


def count_pieces(board, player):
    return sum(
        1 for r in range(SIZE) for c in range(SIZE) if owner(board[r][c]) == player
    )


def promote(board, r, c):
    piece = board[r][c]
    if piece == WHITE_MAN and r == 0:
        board[r][c] = WHITE_KING
        return True
    if piece == BLACK_MAN and r == SIZE - 1:
        board[r][c] = BLACK_KING
        return True
    return False


def apply_simple(board, r1, c1, r2, c2):
    board[r2][c2] = board[r1][c1]
    board[r1][c1] = None
    promote(board, r2, c2)


def apply_capture(board, r1, c1, r2, c2, cap):
    board[r2][c2] = board[r1][c1]
    board[r1][c1] = None
    board[cap[0]][cap[1]] = None
    return promote(board, r2, c2)


def compute_targets(board, player, r, c):
    """Куда может пойти фигура. При наличии взятий — только взятия.

    Возвращает dict: поле назначения -> позиция бьющейся фигуры (или None).
    """
    if owner(board[r][c]) != player:
        return {}
    if player_has_capture(board, player):
        return {dest: cap for dest, cap in captures_for(board, r, c)}
    return {dest: None for dest in moves_for(board, r, c)}


# ============================== ОТРИСОВКА ================================

GLYPHS = {"w": "⚪", "W": "\U0001f90d", "b": "⚫", "B": "\U0001f5a4"}
EMPTY_DARK = "⬛"
LIGHT = "⬜"
SELECTED = "\U0001f7e8"  # 🟨 выбранная фигура
TARGET = "\U0001f7e9"  # 🟩 куда можно пойти


def _format_duration(seconds):
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f"{minutes} мин {secs} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} сек"


MOVE_TIME_LABEL = _format_duration(MOVE_TIME_SECONDS)


def render_board(board, selected=None, targets=None):
    targets = targets or {}
    kb = types.InlineKeyboardMarkup(row_width=SIZE)
    for r in range(SIZE):
        row = []
        for c in range(SIZE):
            if (r + c) % 2 == 0:
                row.append(types.InlineKeyboardButton(LIGHT, callback_data="noop"))
                continue
            if selected == (r, c):
                text = SELECTED
            elif (r, c) in targets:
                text = TARGET
            else:
                text = GLYPHS.get(board[r][c], EMPTY_DARK)
            row.append(types.InlineKeyboardButton(text, callback_data=f"cell:{r}:{c}"))
        kb.row(*row)
    kb.row(types.InlineKeyboardButton("\U0001f3f3️ Сдаться", callback_data="resign"))
    return kb


def lobby_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Присоединиться", callback_data="join"))
    return kb


def status_text(game):
    turn_name = game.white_name if game.turn == WHITE else game.black_name
    turn_glyph = "⚪" if game.turn == WHITE else "⚫"
    lines = [
        "\U0001f3c1 <b>Русские шашки</b>",
        f"⚪ {game.white_name}  vs  ⚫ {game.black_name}",
        "",
        f"Ход: {turn_glyph} <b>{turn_name}</b>",
    ]
    if game.must_continue:
        lines.append("⚔️ Бейте дальше — серия взятий обязательна!")
    elif game.selected:
        lines.append("Выберите \U0001f7e9 куда пойти (или нажмите другую шашку).")
    else:
        lines.append("Нажмите на свою шашку, чтобы выбрать ход.")
    if MOVE_TIME_LABEL:
        lines.append(f"⏱ На ход: {MOVE_TIME_LABEL}")
    lines.append("")
    lines.append("⚪⚫ — простые, \U0001f90d\U0001f5a4 — дамки")
    return "\n".join(lines)


def result_text(game, winner, resigned=None, timeout=None, rating_text=None):
    name = game.white_name if winner == WHITE else game.black_name
    glyph = "⚪" if winner == WHITE else "⚫"
    head = "\U0001f3c6 <b>Игра окончена</b>"
    if timeout is not None:
        loser_name = game.white_name if timeout == WHITE else game.black_name
        body = (
            f"⏱ {loser_name} не успел сделать ход — поражение по времени.\n"
            f"Победитель: {glyph} <b>{name}</b>"
        )
    elif resigned is not None:
        loser_name = game.white_name if resigned == WHITE else game.black_name
        body = f"{loser_name} сдался.\nПобедитель: {glyph} <b>{name}</b>"
    else:
        body = f"Победитель: {glyph} <b>{name}</b>"
    msg = f"{head}\n\n{body}"
    if rating_text:
        msg += f"\n\n{rating_text}"
    return f"{msg}\n\nНапишите /play для новой партии."


# ============================ РЕЙТИНГ (Эло) =============================
# Отдельный рейтинг для каждого чата, хранится в SQLite.

START_RATING = 1000
K_FACTOR = 32
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(RATINGS_DB, check_same_thread=False)
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


def rating_get(chat_id, user_id):
    row = _db().execute(
        "SELECT rating, wins, losses, games, name FROM players "
        "WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if not row:
        return None
    return {"rating": row[0], "wins": row[1], "losses": row[2], "games": row[3], "name": row[4]}


def _rating(chat_id, user_id):
    r = rating_get(chat_id, user_id)
    return r["rating"] if r else START_RATING


def _rating_apply(chat_id, user_id, name, new_rating, win):
    cur = rating_get(chat_id, user_id)
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


def rating_record(chat_id, winner_id, winner_name, loser_id, loser_name):
    rw = _rating(chat_id, winner_id)
    rl = _rating(chat_id, loser_id)
    expected_w = 1 / (1 + 10 ** ((rl - rw) / 400))
    new_w = rw + K_FACTOR * (1 - expected_w)
    new_l = rl + K_FACTOR * (0 - (1 - expected_w))
    _rating_apply(chat_id, winner_id, winner_name, new_w, win=True)
    _rating_apply(chat_id, loser_id, loser_name, new_l, win=False)
    return {
        "winner": (round(new_w), round(new_w - rw)),
        "loser": (round(new_l), round(new_l - rl)),
    }


def rating_top(chat_id, limit=10):
    return _db().execute(
        "SELECT name, rating, wins, losses, games FROM players "
        "WHERE chat_id=? ORDER BY rating DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()


# ================================ БОТ ===================================

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# Партии в памяти: ключ (chat_id, message_id) -> Game.
# Хранилище эфемерное: при перезапуске процесса активные партии теряются
# (а вот рейтинг в ratings.db сохраняется).
games = {}


class Game:
    def __init__(self, chat_id, white_id, white_name):
        self.chat_id = chat_id
        self.message_id = None
        self.state = "waiting"  # waiting | playing | finished
        self.board = None
        self.turn = WHITE
        self.white_id = white_id
        self.white_name = white_name
        self.black_id = None
        self.black_name = None
        self.selected = None  # (r, c) выбранной фигуры
        self.targets = {}  # допустимые ходы выбранной фигуры
        self.must_continue = False  # идёт обязательная серия взятий
        # Адресный вызов: принять партию может только указанный игрок.
        self.target_id = None
        self.target_username = None
        self.target_label = None
        # Таймер на ход.
        self.timer_task = None
        self.move_seq = 0  # увеличивается при каждом перезапуске таймера

    def current_id(self):
        return self.white_id if self.turn == WHITE else self.black_id

    def reset_selection(self):
        self.selected = None
        self.targets = {}
        self.must_continue = False

    def switch_turn(self):
        self.turn = BLACK if self.turn == WHITE else WHITE


def key_of(message):
    return (message.chat.id, message.message_id)


async def safe_edit(message, text, markup):
    try:
        await message.edit_text(text, reply_markup=markup)
    except MessageNotModified:
        pass


def parse_target(message):
    """Определяет вызываемого соперника.

    Возвращает (kind, value, label, user):
      kind="id"       -> value=user_id   (reply или текстовое упоминание)
      kind="username" -> value=username  (упоминание @username, в нижнем регистре)
      kind=None       -> открытый вызов
    """
    reply = message.reply_to_message
    if reply and reply.from_user:
        u = reply.from_user
        return "id", u.id, u.full_name, u
    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user:
            u = ent.user
            return "id", u.id, u.full_name, u
        if ent.type == "mention":
            uname = message.text[ent.offset : ent.offset + ent.length]  # вида @name
            return "username", uname[1:].lower(), uname, None
    return None, None, None, None


def _side(game, color):
    if color == WHITE:
        return game.white_id, game.white_name
    return game.black_id, game.black_name


def _rating_block(chat_id, winner_id, winner_name, loser_id, loser_name):
    res = rating_record(chat_id, winner_id, winner_name, loser_id, loser_name)
    w_new, w_delta = res["winner"]
    l_new, l_delta = res["loser"]

    def fmt(d):
        return f"+{d}" if d >= 0 else f"−{abs(d)}"

    return (
        "📊 Рейтинг:\n"
        f"{winner_name}: {w_new} ({fmt(w_delta)})\n"
        f"{loser_name}: {l_new} ({fmt(l_delta)})"
    )


# --- Таймер на ход -------------------------------------------------------


def cancel_timer(game):
    if game.timer_task:
        game.timer_task.cancel()
        game.timer_task = None


def arm_timer(game):
    """Перезапускает таймер для игрока, который сейчас должен ходить."""
    cancel_timer(game)
    game.move_seq += 1
    game.timer_task = asyncio.ensure_future(_move_timer(game, game.move_seq))


async def _send_warning(game):
    pid, pname = _side(game, game.turn)
    mention = f"<a href='tg://user?id={pid}'>{pname}</a>"
    try:
        await bot.send_message(
            game.chat_id, f"⏰ {mention}, осталось {WARN_BEFORE} сек на ход!"
        )
    except TelegramAPIError:
        pass


async def _move_timer(game, seq):
    try:
        warn_delay = MOVE_TIME_SECONDS - WARN_BEFORE
        if WARN_BEFORE > 0 and warn_delay > 0:
            await asyncio.sleep(warn_delay)
            if game.state != "playing" or game.move_seq != seq:
                return
            await _send_warning(game)
            await asyncio.sleep(WARN_BEFORE)
        else:
            await asyncio.sleep(MOVE_TIME_SECONDS)
    except asyncio.CancelledError:
        return
    # За время ожидания мог произойти ход (move_seq) или игра завершиться.
    if game.state != "playing" or game.move_seq != seq:
        return
    loser = game.turn
    winner = BLACK if loser == WHITE else WHITE
    game.state = "finished"
    game.timer_task = None
    w_id, w_name = _side(game, winner)
    l_id, l_name = _side(game, loser)
    rating_text = _rating_block(game.chat_id, w_id, w_name, l_id, l_name)
    text = result_text(game, winner, timeout=loser, rating_text=rating_text)
    try:
        await bot.edit_message_text(
            text, chat_id=game.chat_id, message_id=game.message_id, reply_markup=None
        )
    except TelegramAPIError:
        pass
    games.pop((game.chat_id, game.message_id), None)


# --- Лобби ---------------------------------------------------------------


@dp.message_handler(commands=["play", "checkers", "shashki"])
async def cmd_play(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.reply(
            "Эта игра рассчитана на группы. Добавьте бота в группу и напишите /play."
        )
        return
    user = message.from_user
    name = html.escape(user.full_name)
    game = Game(message.chat.id, user.id, name)

    kind, value, label, target_user = parse_target(message)
    if kind == "id":
        if target_user and target_user.is_bot:
            await message.reply("С ботом сыграть нельзя 🙂")
            return
        if value == user.id:
            await message.reply("Нельзя вызвать самого себя 🙂")
            return
        game.target_id = value
        game.target_label = html.escape(label)
    elif kind == "username":
        if user.username and value == user.username.lower():
            await message.reply("Нельзя вызвать самого себя 🙂")
            return
        game.target_username = value
        game.target_label = html.escape(label)

    if game.target_label:
        text = (
            f"⚔️ <b>{name}</b> вызывает <b>{game.target_label}</b> "
            "на партию в русские шашки!\n\n"
            f"{game.target_label}, нажмите кнопку, чтобы принять вызов."
        )
    else:
        text = (
            f"🎲 <b>{name}</b> предлагает партию в русские шашки!\n\n"
            "Кто примет вызов? Нажмите кнопку ниже."
        )

    sent = await message.answer(text, reply_markup=lobby_keyboard())
    game.message_id = sent.message_id
    games[key_of(sent)] = game


@dp.callback_query_handler(lambda c: c.data == "join")
async def on_join(call: types.CallbackQuery):
    game = games.get(key_of(call.message))
    if not game or game.state != "waiting":
        await call.answer("Эта игра больше недоступна.", show_alert=True)
        return
    user = call.from_user
    if user.id == game.white_id:
        await call.answer("Нельзя играть с самим собой 🙂", show_alert=True)
        return
    if game.target_id is not None and user.id != game.target_id:
        await call.answer("Этот вызов адресован другому игроку.", show_alert=True)
        return
    if game.target_username is not None and (user.username or "").lower() != game.target_username:
        await call.answer("Этот вызов адресован другому игроку.", show_alert=True)
        return
    game.black_id = user.id
    game.black_name = html.escape(user.full_name)
    game.board = initial_board()
    game.turn = WHITE
    game.state = "playing"
    await safe_edit(call.message, status_text(game), render_board(game.board))
    arm_timer(game)
    await call.answer("Поехали!")


# --- Игровой процесс -----------------------------------------------------


@dp.callback_query_handler(lambda c: c.data == "noop")
async def on_noop(call: types.CallbackQuery):
    await call.answer()


@dp.callback_query_handler(lambda c: c.data == "resign")
async def on_resign(call: types.CallbackQuery):
    game = games.get(key_of(call.message))
    if not game or game.state != "playing":
        await call.answer("Партия не активна.")
        return
    uid = call.from_user.id
    if uid not in (game.white_id, game.black_id):
        await call.answer("Вы не участник этой партии.")
        return
    loser = WHITE if uid == game.white_id else BLACK
    winner = BLACK if loser == WHITE else WHITE
    game.state = "finished"
    cancel_timer(game)
    w_id, w_name = _side(game, winner)
    l_id, l_name = _side(game, loser)
    rating_text = _rating_block(game.chat_id, w_id, w_name, l_id, l_name)
    await safe_edit(
        call.message, result_text(game, winner, resigned=loser, rating_text=rating_text), None
    )
    await call.answer()
    games.pop(key_of(call.message), None)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("cell:"))
async def on_cell(call: types.CallbackQuery):
    game = games.get(key_of(call.message))
    if not game or game.state != "playing":
        await call.answer("Партия не активна.")
        return
    uid = call.from_user.id
    if uid != game.current_id():
        if uid in (game.white_id, game.black_id):
            await call.answer("Сейчас ход соперника ⏳")
        else:
            await call.answer("Вы не участник этой партии 👀")
        return
    _, rs, cs = call.data.split(":")
    await handle_tap(call, game, int(rs), int(cs))


async def handle_tap(call, game, r, c):
    board = game.board
    player = game.turn

    # Нажали на подсвеченную клетку назначения — делаем ход.
    if game.selected and (r, c) in game.targets:
        await do_move(call, game, r, c)
        return

    # В середине обязательной серии взятий менять фигуру нельзя.
    if game.must_continue:
        await call.answer("Сначала завершите взятие 🟩")
        return

    piece = board[r][c]
    if owner(piece) != player:
        if piece is None:
            await call.answer("Сначала выберите свою шашку.")
        else:
            await call.answer("Это не ваша шашка.")
        return

    targets = compute_targets(board, player, r, c)
    if not targets:
        if player_has_capture(board, player):
            await call.answer("Обязательно бить! Выберите шашку, которая может бить.")
        else:
            await call.answer("У этой шашки нет ходов.")
        return

    game.selected = (r, c)
    game.targets = targets
    await safe_edit(
        call.message, status_text(game), render_board(board, game.selected, game.targets)
    )
    await call.answer()


async def do_move(call, game, r, c):
    board = game.board
    sr, sc = game.selected
    cap = game.targets[(r, c)]

    if cap is None:
        apply_simple(board, sr, sc, r, c)
        game.reset_selection()
        game.switch_turn()
        await finish_or_continue(call, game)
        return

    apply_capture(board, sr, sc, r, c, cap)
    more = captures_for(board, r, c)
    if more:
        # Серия взятий продолжается той же фигурой.
        game.selected = (r, c)
        game.targets = {dest: m for dest, m in more}
        game.must_continue = True
        await safe_edit(
            call.message,
            status_text(game),
            render_board(board, game.selected, game.targets),
        )
        arm_timer(game)
        await call.answer("Бейте дальше! 🟩")
        return

    game.reset_selection()
    game.switch_turn()
    await finish_or_continue(call, game)


async def finish_or_continue(call, game):
    """После смены хода: проверяем конец игры, иначе перерисовываем доску."""
    board = game.board
    player = game.turn
    if count_pieces(board, player) == 0 or not player_has_move(board, player):
        winner = BLACK if player == WHITE else WHITE
        game.state = "finished"
        cancel_timer(game)
        w_id, w_name = _side(game, winner)
        l_id, l_name = _side(game, player)
        rating_text = _rating_block(game.chat_id, w_id, w_name, l_id, l_name)
        await safe_edit(call.message, result_text(game, winner, rating_text=rating_text), None)
        await call.answer()
        games.pop((game.chat_id, game.message_id), None)
        return
    await safe_edit(call.message, status_text(game), render_board(board))
    arm_timer(game)
    await call.answer()


# --- Рейтинг -------------------------------------------------------------


@dp.message_handler(commands=["top", "rating", "leaderboard"])
async def cmd_top(message: types.Message):
    rows = rating_top(message.chat.id, 10)
    if not rows:
        await message.reply("Пока нет сыгранных партий. Начните с /play.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Рейтинг игроков</b>", ""]
    for i, (name, rating, wins, losses, _games) in enumerate(rows):
        rank = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{rank} {name} — <b>{round(rating)}</b> ({wins}–{losses})")
    await message.reply("\n".join(lines))


@dp.message_handler(commands=["me", "stats"])
async def cmd_me(message: types.Message):
    st = rating_get(message.chat.id, message.from_user.id)
    if not st:
        await message.reply("У вас пока нет рейтинга. Сыграйте партию: /play.")
        return
    await message.reply(
        f"📊 <b>{html.escape(message.from_user.full_name)}</b>\n"
        f"Рейтинг: <b>{round(st['rating'])}</b>\n"
        f"Побед: {st['wins']}  Поражений: {st['losses']}  Партий: {st['games']}"
    )


if __name__ == "__main__":
    if "PASTE_YOUR_TOKEN" in BOT_TOKEN:
        raise SystemExit(
            "Укажите токен бота в переменной BOT_TOKEN в начале файла "
            "(получить можно у @BotFather)."
        )
    executor.start_polling(dp, skip_updates=True)

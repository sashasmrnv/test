"""Telegram-бот «Русские шашки» для игры в группе (aiogram 2.12).

Запуск:
    BOT_TOKEN=xxx python bot.py

Игра идёт прямо в сообщении группы через inline-кнопки. Один игрок
вызывает /play, второй присоединяется кнопкой — и партия начинается.
"""

import logging

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.utils.exceptions import MessageNotModified

import engine
import ratings
from config import BOT_TOKEN
from render import lobby_keyboard, render_board, result_text, status_text

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# Партии в памяти: ключ (chat_id, message_id) -> Game.
# Хранилище эфемерное: при перезапуске процесса активные партии теряются.
games = {}


class Game:
    def __init__(self, chat_id, white_id, white_name):
        self.chat_id = chat_id
        self.message_id = None
        self.state = "waiting"  # waiting | playing | finished
        self.board = None
        self.turn = engine.WHITE
        self.white_id = white_id
        self.white_name = white_name
        self.black_id = None
        self.black_name = None
        self.selected = None  # (r, c) выбранной фигуры
        self.targets = {}  # допустимые ходы выбранной фигуры
        self.must_continue = False  # идёт обязательная серия взятий
        # Адресный вызов: принять партию может только указанный игрок.
        self.target_id = None  # по user_id (reply / text_mention)
        self.target_username = None  # по @username (в нижнем регистре)
        self.target_label = None  # как показывать вызываемого в тексте

    def current_id(self):
        return self.white_id if self.turn == engine.WHITE else self.black_id

    def reset_selection(self):
        self.selected = None
        self.targets = {}
        self.must_continue = False

    def switch_turn(self):
        self.turn = engine.BLACK if self.turn == engine.WHITE else engine.WHITE


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
      kind="id"       -> value=user_id     (reply или текстовое упоминание)
      kind="username" -> value=username    (упоминание @username, в нижнем регистре)
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
    if color == engine.WHITE:
        return game.white_id, game.white_name
    return game.black_id, game.black_name


def _rating_block(chat_id, winner_id, winner_name, loser_id, loser_name):
    res = ratings.record_result(chat_id, winner_id, winner_name, loser_id, loser_name)
    w_new, w_delta = res["winner"]
    l_new, l_delta = res["loser"]

    def fmt(d):
        return f"+{d}" if d >= 0 else f"−{abs(d)}"

    return (
        "📊 Рейтинг:\n"
        f"{winner_name}: {w_new} ({fmt(w_delta)})\n"
        f"{loser_name}: {l_new} ({fmt(l_delta)})"
    )


# --- Лобби ---------------------------------------------------------------


@dp.message_handler(commands=["play", "checkers", "shashki"])
async def cmd_play(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.reply(
            "Эта игра рассчитана на группы. Добавьте бота в группу и напишите /play."
        )
        return
    user = message.from_user
    game = Game(message.chat.id, user.id, user.full_name)

    kind, value, label, target_user = parse_target(message)
    if kind == "id":
        if target_user and target_user.is_bot:
            await message.reply("С ботом сыграть нельзя 🙂")
            return
        if value == user.id:
            await message.reply("Нельзя вызвать самого себя 🙂")
            return
        game.target_id = value
        game.target_label = label
    elif kind == "username":
        if user.username and value == user.username.lower():
            await message.reply("Нельзя вызвать самого себя 🙂")
            return
        game.target_username = value
        game.target_label = label

    if game.target_label:
        text = (
            f"⚔️ <b>{user.full_name}</b> вызывает <b>{game.target_label}</b> "
            "на партию в русские шашки!\n\n"
            f"{game.target_label}, нажмите кнопку, чтобы принять вызов."
        )
    else:
        text = (
            f"🎲 <b>{user.full_name}</b> предлагает партию в русские шашки!\n\n"
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
    game.black_name = user.full_name
    game.board = engine.initial_board()
    game.turn = engine.WHITE
    game.state = "playing"
    await safe_edit(call.message, status_text(game), render_board(game.board))
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
    loser = engine.WHITE if uid == game.white_id else engine.BLACK
    winner = engine.BLACK if loser == engine.WHITE else engine.WHITE
    game.state = "finished"
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

    # В середине обязательной серии взятий выбирать другую фигуру нельзя.
    if game.must_continue:
        await call.answer("Сначала завершите взятие 🟩")
        return

    # Иначе — попытка выбрать (или сменить) фигуру.
    piece = board[r][c]
    if engine.owner(piece) != player:
        if piece is None:
            await call.answer("Сначала выберите свою шашку.")
        else:
            await call.answer("Это не ваша шашка.")
        return

    targets = engine.compute_targets(board, player, r, c)
    if not targets:
        if engine.player_has_capture(board, player):
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
        engine.apply_simple(board, sr, sc, r, c)
        game.reset_selection()
        game.switch_turn()
        await finish_or_continue(call, game)
        return

    engine.apply_capture(board, sr, sc, r, c, cap)
    more = engine.captures_for(board, r, c)
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
        await call.answer("Бейте дальше! 🟩")
        return

    game.reset_selection()
    game.switch_turn()
    await finish_or_continue(call, game)


async def finish_or_continue(call, game):
    """После смены хода: проверяем конец игры, иначе перерисовываем доску."""
    board = game.board
    player = game.turn
    if engine.count_pieces(board, player) == 0 or not engine.player_has_move(board, player):
        winner = engine.BLACK if player == engine.WHITE else engine.WHITE
        game.state = "finished"
        w_id, w_name = _side(game, winner)
        l_id, l_name = _side(game, player)
        rating_text = _rating_block(game.chat_id, w_id, w_name, l_id, l_name)
        await safe_edit(call.message, result_text(game, winner, rating_text=rating_text), None)
        await call.answer()
        games.pop((game.chat_id, game.message_id), None)
        return
    await safe_edit(call.message, status_text(game), render_board(board))
    await call.answer()


# --- Рейтинг -------------------------------------------------------------


@dp.message_handler(commands=["top", "rating", "leaderboard"])
async def cmd_top(message: types.Message):
    rows = ratings.top(message.chat.id, 10)
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
    st = ratings.get(message.chat.id, message.from_user.id)
    if not st:
        await message.reply("У вас пока нет рейтинга. Сыграйте партию: /play.")
        return
    await message.reply(
        f"📊 <b>{message.from_user.full_name}</b>\n"
        f"Рейтинг: <b>{round(st['rating'])}</b>\n"
        f"Побед: {st['wins']}  Поражений: {st['losses']}  Партий: {st['games']}"
    )


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)

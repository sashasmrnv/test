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


# --- Лобби ---------------------------------------------------------------


@dp.message_handler(commands=["play", "checkers", "shashki"])
async def cmd_play(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.reply(
            "Эта игра рассчитана на группы. Добавьте бота в группу и напишите /play."
        )
        return
    user = message.from_user
    text = (
        f"🎲 <b>{user.full_name}</b> предлагает партию в русские шашки!\n\n"
        "Кто примет вызов? Нажмите кнопку ниже."
    )
    sent = await message.answer(text, reply_markup=lobby_keyboard())
    game = Game(message.chat.id, user.id, user.full_name)
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
    await safe_edit(call.message, result_text(game, winner, resigned=loser), None)
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
        await safe_edit(call.message, result_text(game, winner), None)
        await call.answer()
        games.pop((game.chat_id, game.message_id), None)
        return
    await safe_edit(call.message, status_text(game), render_board(board))
    await call.answer()


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)

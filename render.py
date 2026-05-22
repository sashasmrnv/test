"""Отрисовка доски шашек в виде inline-клавиатуры Telegram."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from engine import WHITE, BLACK, SIZE

GLYPHS = {
    "w": "⚪",  # ⚪ белая простая
    "W": "\U0001f90d",  # 🤍 белая дамка
    "b": "⚫",  # ⚫ чёрная простая
    "B": "\U0001f5a4",  # 🖤 чёрная дамка
}
EMPTY_DARK = "⬛"  # ⬛ пустое игровое поле
LIGHT = "⬜"  # ⬜ неигровое поле
SELECTED = "\U0001f7e8"  # 🟨 выбранная фигура
TARGET = "\U0001f7e9"  # 🟩 куда можно пойти


def render_board(board, selected=None, targets=None):
    targets = targets or {}
    kb = InlineKeyboardMarkup(row_width=SIZE)
    for r in range(SIZE):
        row = []
        for c in range(SIZE):
            if (r + c) % 2 == 0:
                row.append(InlineKeyboardButton(LIGHT, callback_data="noop"))
                continue
            if selected == (r, c):
                text = SELECTED
            elif (r, c) in targets:
                text = TARGET
            else:
                text = GLYPHS.get(board[r][c], EMPTY_DARK)
            row.append(InlineKeyboardButton(text, callback_data=f"cell:{r}:{c}"))
        kb.row(*row)
    kb.row(InlineKeyboardButton("\U0001f3f3️ Сдаться", callback_data="resign"))
    return kb


def lobby_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Присоединиться", callback_data="join"))
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
    lines.append("")
    lines.append("⚪⚫ — простые, \U0001f90d\U0001f5a4 — дамки")
    return "\n".join(lines)


def result_text(game, winner, resigned=None):
    name = game.white_name if winner == WHITE else game.black_name
    glyph = "⚪" if winner == WHITE else "⚫"
    head = "\U0001f3c6 <b>Игра окончена</b>"
    if resigned is not None:
        loser_name = game.white_name if resigned == WHITE else game.black_name
        body = f"{loser_name} сдался.\nПобедитель: {glyph} <b>{name}</b>"
    else:
        body = f"Победитель: {glyph} <b>{name}</b>"
    return f"{head}\n\n{body}\n\nНапишите /play для новой партии."

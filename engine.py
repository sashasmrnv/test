"""Движок русских шашек.

Доска 8x8, играем только на тёмных полях, где (row + col) нечётно.
Строки 0..2 сверху занимают чёрные, строки 5..7 снизу — белые.
Белые ходят вверх (строка уменьшается), чёрные — вниз.

Обозначения фигур на доске:
    'w' — белая простая, 'W' — белая дамка
    'b' — чёрная простая, 'B' — чёрная дамка
    None — пустое поле
"""

WHITE_MAN = "w"
WHITE_KING = "W"
BLACK_MAN = "b"
BLACK_KING = "B"

WHITE = "white"
BLACK = "black"

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
        1
        for r in range(SIZE)
        for c in range(SIZE)
        if owner(board[r][c]) == player
    )


def promote(board, r, c):
    """Превращает простую шашку в дамку на последней горизонтали."""
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
    """Куда может пойти фигура (r, c). При наличии взятий — только взятия.

    Возвращает dict: поле назначения -> позиция бьющейся фигуры (или None для тихого хода).
    """
    if owner(board[r][c]) != player:
        return {}
    if player_has_capture(board, player):
        return {dest: cap for dest, cap in captures_for(board, r, c)}
    return {dest: None for dest in moves_for(board, r, c)}

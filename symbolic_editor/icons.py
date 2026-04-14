"""
icons.py — Procedurally generated QIcons for the toolbar.

Every icon is drawn via QPainter onto a QPixmap so we have zero
dependency on external image files.  All shapes use anti-aliased
vector strokes on a transparent canvas.
"""

from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QBrush, QFont, QPainterPath
from PySide6.QtCore import Qt, QRectF, QPointF

_CACHE: dict[str, QIcon] = {}

# Default palette
_FG = QColor("#e0e8f0")       # light foreground for dark toolbar
_ACCENT = QColor("#4a90d9")   # blue accent
_WARN = QColor("#e74c3c")     # red / destructive
_GREEN = QColor("#2ecc71")    # success green
_PINK = QColor("#d14d94")     # pink for dummies
_ORANGE = QColor("#f39c12")   # orange / merge


def _make_pixmap(size: int = 32) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def _icon_from_painter(pm: QPixmap, p: QPainter) -> QIcon:
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------
# Individual icon builders
# ------------------------------------------------------------------

def icon_undo() -> QIcon:
    if "undo" in _CACHE:
        return _CACHE["undo"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Curved arrow pointing left
    path = QPainterPath()
    path.moveTo(10, 12)
    path.cubicTo(10, 6, 22, 6, 22, 14)
    path.lineTo(22, 20)
    p.drawPath(path)
    # Arrowhead
    p.drawLine(QPointF(10, 12), QPointF(14, 8))
    p.drawLine(QPointF(10, 12), QPointF(14, 16))
    icon = _icon_from_painter(pm, p)
    _CACHE["undo"] = icon
    return icon


def icon_redo() -> QIcon:
    if "redo" in _CACHE:
        return _CACHE["redo"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(22, 12)
    path.cubicTo(22, 6, 10, 6, 10, 14)
    path.lineTo(10, 20)
    p.drawPath(path)
    p.drawLine(QPointF(22, 12), QPointF(18, 8))
    p.drawLine(QPointF(22, 12), QPointF(18, 16))
    icon = _icon_from_painter(pm, p)
    _CACHE["redo"] = icon
    return icon


def icon_fit_view() -> QIcon:
    if "fit_view" in _CACHE:
        return _CACHE["fit_view"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Four corner brackets
    m = 6; s = 7
    # Top-left
    p.drawLine(m, m, m + s, m)
    p.drawLine(m, m, m, m + s)
    # Top-right
    p.drawLine(32 - m, m, 32 - m - s, m)
    p.drawLine(32 - m, m, 32 - m, m + s)
    # Bottom-left
    p.drawLine(m, 32 - m, m + s, 32 - m)
    p.drawLine(m, 32 - m, m, 32 - m - s)
    # Bottom-right
    p.drawLine(32 - m, 32 - m, 32 - m - s, 32 - m)
    p.drawLine(32 - m, 32 - m, 32 - m, 32 - m - s)
    # Center small rect
    p.setPen(QPen(_ACCENT, 1.6))
    p.drawRect(QRectF(12, 12, 8, 8))
    icon = _icon_from_painter(pm, p)
    _CACHE["fit_view"] = icon
    return icon


def icon_zoom_in() -> QIcon:
    if "zoom_in" in _CACHE:
        return _CACHE["zoom_in"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Magnifying glass circle
    p.drawEllipse(QRectF(6, 6, 16, 16))
    # Handle
    p.drawLine(QPointF(20, 20), QPointF(27, 27))
    # Plus
    p.drawLine(QPointF(11, 14), QPointF(19, 14))
    p.drawLine(QPointF(14, 11), QPointF(14, 19))  # +  sign not | only
    # actually center the + inside the circle (cx=14, cy=14)
    icon = _icon_from_painter(pm, p)
    _CACHE["zoom_in"] = icon
    return icon


def icon_zoom_out() -> QIcon:
    if "zoom_out" in _CACHE:
        return _CACHE["zoom_out"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(6, 6, 16, 16))
    p.drawLine(QPointF(20, 20), QPointF(27, 27))
    # Minus
    p.drawLine(QPointF(11, 14), QPointF(19, 14))
    icon = _icon_from_painter(pm, p)
    _CACHE["zoom_out"] = icon
    return icon


def icon_zoom_reset() -> QIcon:
    if "zoom_reset" in _CACHE:
        return _CACHE["zoom_reset"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(6, 6, 16, 16))
    p.drawLine(QPointF(20, 20), QPointF(27, 27))
    # "1:1" text inside circle
    f = QFont("Segoe UI", 6, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(QRectF(6, 6, 16, 16), Qt.AlignmentFlag.AlignCenter, "1:1")
    icon = _icon_from_painter(pm, p)
    _CACHE["zoom_reset"] = icon
    return icon


def icon_select_all() -> QIcon:
    if "select_all" in _CACHE:
        return _CACHE["select_all"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Multiple overlapping rectangles
    p.drawRect(QRectF(6, 6, 12, 12))
    p.setPen(QPen(_ACCENT, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawRect(QRectF(14, 14, 12, 12))
    # Dashed line connecting
    p.setPen(QPen(_FG, 1.0, Qt.PenStyle.DotLine))
    p.drawLine(QPointF(18, 6), QPointF(26, 6))
    p.drawLine(QPointF(26, 6), QPointF(26, 14))
    icon = _icon_from_painter(pm, p)
    _CACHE["select_all"] = icon
    return icon


def icon_delete() -> QIcon:
    if "delete" in _CACHE:
        return _CACHE["delete"]
    pm, p = _make_pixmap()
    pen = QPen(_WARN, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Trash can
    p.drawLine(QPointF(10, 10), QPointF(10, 26))
    p.drawLine(QPointF(22, 10), QPointF(22, 26))
    p.drawLine(QPointF(10, 26), QPointF(22, 26))
    # Lid
    p.drawLine(QPointF(8, 10), QPointF(24, 10))
    p.drawLine(QPointF(13, 10), QPointF(13, 7))
    p.drawLine(QPointF(19, 10), QPointF(19, 7))
    p.drawLine(QPointF(13, 7), QPointF(19, 7))
    # Inner lines
    pen2 = QPen(_WARN, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(QPointF(14, 13), QPointF(14, 23))
    p.drawLine(QPointF(18, 13), QPointF(18, 23))
    icon = _icon_from_painter(pm, p)
    _CACHE["delete"] = icon
    return icon


def icon_swap() -> QIcon:
    if "swap" in _CACHE:
        return _CACHE["swap"]
    pm, p = _make_pixmap()
    pen = QPen(_ACCENT, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Two horizontal arrows in opposite directions
    # Top arrow →
    p.drawLine(QPointF(8, 11), QPointF(24, 11))
    p.drawLine(QPointF(20, 7), QPointF(24, 11))
    p.drawLine(QPointF(20, 15), QPointF(24, 11))
    # Bottom arrow ←
    p.drawLine(QPointF(24, 21), QPointF(8, 21))
    p.drawLine(QPointF(12, 17), QPointF(8, 21))
    p.drawLine(QPointF(12, 25), QPointF(8, 21))
    icon = _icon_from_painter(pm, p)
    _CACHE["swap"] = icon
    return icon


def icon_flip_h() -> QIcon:
    if "flip_h" in _CACHE:
        return _CACHE["flip_h"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Vertical dashed center line
    p.setPen(QPen(_FG, 1.2, Qt.PenStyle.DashLine))
    p.drawLine(QPointF(16, 6), QPointF(16, 26))
    # Left triangle
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path_l = QPainterPath()
    path_l.moveTo(6, 16)
    path_l.lineTo(13, 10)
    path_l.lineTo(13, 22)
    path_l.closeSubpath()
    p.setBrush(QBrush(_FG))
    p.drawPath(path_l)
    # Right triangle (outline)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path_r = QPainterPath()
    path_r.moveTo(26, 16)
    path_r.lineTo(19, 10)
    path_r.lineTo(19, 22)
    path_r.closeSubpath()
    p.drawPath(path_r)
    icon = _icon_from_painter(pm, p)
    _CACHE["flip_h"] = icon
    return icon


def icon_flip_v() -> QIcon:
    if "flip_v" in _CACHE:
        return _CACHE["flip_v"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    # Horizontal dashed center line
    p.setPen(QPen(_FG, 1.2, Qt.PenStyle.DashLine))
    p.drawLine(QPointF(6, 16), QPointF(26, 16))
    # Top triangle
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path_t = QPainterPath()
    path_t.moveTo(16, 6)
    path_t.lineTo(10, 13)
    path_t.lineTo(22, 13)
    path_t.closeSubpath()
    p.setBrush(QBrush(_FG))
    p.drawPath(path_t)
    # Bottom triangle (outline)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path_b = QPainterPath()
    path_b.moveTo(16, 26)
    path_b.lineTo(10, 19)
    path_b.lineTo(22, 19)
    path_b.closeSubpath()
    p.drawPath(path_b)
    icon = _icon_from_painter(pm, p)
    _CACHE["flip_v"] = icon
    return icon


def icon_merge_ss() -> QIcon:
    if "merge_ss" in _CACHE:
        return _CACHE["merge_ss"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_ORANGE, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Two small rects merging
    p.drawRoundedRect(QRectF(5, 9, 10, 14), 2, 2)
    p.drawRoundedRect(QRectF(17, 9, 10, 14), 2, 2)
    # Arrow connecting them
    p.setPen(QPen(_ORANGE, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(15, 16), QPointF(17, 16))
    # "SS" label
    f = QFont("Segoe UI", 6, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(_ORANGE)
    p.drawText(QRectF(0, 24, 32, 8), Qt.AlignmentFlag.AlignCenter, "S-S")
    icon = _icon_from_painter(pm, p)
    _CACHE["merge_ss"] = icon
    return icon


def icon_merge_dd() -> QIcon:
    if "merge_dd" in _CACHE:
        return _CACHE["merge_dd"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_ORANGE, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5, 9, 10, 14), 2, 2)
    p.drawRoundedRect(QRectF(17, 9, 10, 14), 2, 2)
    p.setPen(QPen(_ORANGE, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(15, 16), QPointF(17, 16))
    f = QFont("Segoe UI", 6, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(_ORANGE)
    p.drawText(QRectF(0, 24, 32, 8), Qt.AlignmentFlag.AlignCenter, "D-D")
    icon = _icon_from_painter(pm, p)
    _CACHE["merge_dd"] = icon
    return icon


def icon_add_dummy() -> QIcon:
    if "add_dummy" in _CACHE:
        return _CACHE["add_dummy"]
    pm, p = _make_pixmap()
    # Pink rounded-rect background
    p.setPen(QPen(_PINK, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(209, 77, 148, 40)))
    p.drawRoundedRect(QRectF(5, 5, 22, 22), 5, 5)
    # Bold "D" letter
    f = QFont("Segoe UI", 14, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(_PINK)
    p.drawText(QRectF(5, 5, 22, 22), Qt.AlignmentFlag.AlignCenter, "D")
    icon = _icon_from_painter(pm, p)
    _CACHE["add_dummy"] = icon
    return icon


def icon_panel_toggle() -> QIcon:
    """Sidebar / panel toggle icon — small rectangle with vertical divider."""
    if "panel_toggle" in _CACHE:
        return _CACHE["panel_toggle"]
    pm, p = _make_pixmap()
    pen = QPen(_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Outer rounded rectangle
    p.drawRoundedRect(QRectF(4, 6, 24, 20), 3, 3)
    # Vertical divider line (left panel separator)
    p.drawLine(QPointF(13, 6), QPointF(13, 26))
    # Small filled square representing the sidebar content
    p.setBrush(QBrush(QColor(_FG.red(), _FG.green(), _FG.blue(), 60)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(5.5, 7.5, 6, 17), 1.5, 1.5)
    icon = _icon_from_painter(pm, p)
    _CACHE["panel_toggle"] = icon
    return icon


def icon_tree_toggle() -> QIcon:
    """Alias for panel_toggle used in toolbar."""
    return icon_panel_toggle()


def icon_realize() -> QIcon:
    """'Realize' / commit icon — checkmark in a box."""
    if "realize" in _CACHE:
        return _CACHE["realize"]
    pm, p = _make_pixmap()
    pen = QPen(_GREEN, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5, 5, 22, 22), 4, 4)
    # Checkmark
    p.setPen(QPen(_GREEN, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(QPointF(10, 16), QPointF(14, 22))
    p.drawLine(QPointF(14, 22), QPointF(22, 10))
    icon = _icon_from_painter(pm, p)
    _CACHE["realize"] = icon
    return icon


def icon_optimize_2d() -> QIcon:
    """Grid / optimize layout icon — arranged squares."""
    if "optimize_2d" in _CACHE:
        return _CACHE["optimize_2d"]
    pm, p = _make_pixmap()
    pen = QPen(_ACCENT, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(74, 144, 217, 50)))
    # 2x2 grid of small squares
    p.drawRoundedRect(QRectF(5, 5, 10, 10), 2, 2)
    p.drawRoundedRect(QRectF(17, 5, 10, 10), 2, 2)
    p.drawRoundedRect(QRectF(5, 17, 10, 10), 2, 2)
    p.drawRoundedRect(QRectF(17, 17, 10, 10), 2, 2)
    # Arrows suggesting optimization
    p.setPen(QPen(_GREEN, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(15, 10), QPointF(17, 10))
    p.drawLine(QPointF(15, 22), QPointF(17, 22))
    icon = _icon_from_painter(pm, p)
    _CACHE["optimize_2d"] = icon
    return icon


# ------------------------------------------------------------------
# Modern UI Icons — Chat, Tabs, View Modes
# ------------------------------------------------------------------

# Standard icon color for modern UI (lighter, more neutral)
_ICON_FG = QColor("#b0b0b0")


def _make_pixmap_sized(size: int = 32) -> tuple[QPixmap, QPainter]:
    """Like _make_pixmap but used for size-variant icons."""
    return _make_pixmap(size)


def icon_sparkle(size: int = 32) -> QIcon:
    """Modern sparkle / AI icon — four-point star."""
    key = f"sparkle_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    cx, cy = s / 2, s / 2

    pen = QPen(QColor("#4a9eff"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(74, 158, 255, 80)))

    # Four-point star
    path = QPainterPath()
    r_out = s * 0.38
    r_in = s * 0.12
    import math
    for i in range(8):
        angle = math.pi / 4 * i - math.pi / 2
        r = r_out if i % 2 == 0 else r_in
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.closeSubpath()
    p.drawPath(path)

    # Small accent dot
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#4a9eff")))
    p.drawEllipse(QPointF(cx + s * 0.25, cy - s * 0.25), s * 0.06, s * 0.06)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_send_arrow(size: int = 32) -> QIcon:
    """Modern send arrow — minimal upward-right arrow."""
    key = f"send_arrow_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(QColor("#ffffff"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # Arrow pointing up-right (send direction)
    m = s * 0.22
    path = QPainterPath()
    path.moveTo(m, s - m)
    path.lineTo(s / 2, m)
    path.lineTo(s - m, s - m)
    p.drawPath(path)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_trash_modern(size: int = 32) -> QIcon:
    """Clean modern trash icon — outline style."""
    key = f"trash_modern_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    m = s * 0.22
    # Lid
    p.drawLine(QPointF(m - 2, s * 0.3), QPointF(s - m + 2, s * 0.3))
    # Handle on lid
    p.drawLine(QPointF(s * 0.38, s * 0.3), QPointF(s * 0.38, s * 0.2))
    p.drawLine(QPointF(s * 0.62, s * 0.3), QPointF(s * 0.62, s * 0.2))
    p.drawLine(QPointF(s * 0.38, s * 0.2), QPointF(s * 0.62, s * 0.2))
    # Body
    body = QPainterPath()
    body.moveTo(m + 1, s * 0.3)
    body.lineTo(m + 2, s - m)
    body.lineTo(s - m - 2, s - m)
    body.lineTo(s - m - 1, s * 0.3)
    p.drawPath(body)
    # Inner lines
    p.setPen(QPen(_ICON_FG, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.4, s * 0.38), QPointF(s * 0.4, s * 0.72))
    p.drawLine(QPointF(s * 0.6, s * 0.38), QPointF(s * 0.6, s * 0.72))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_copy(size: int = 32) -> QIcon:
    """Copy icon — two overlapping rectangles."""
    key = f"copy_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    m = s * 0.2
    # Back rectangle
    p.drawRoundedRect(QRectF(m + 3, m, s * 0.5, s * 0.55), 2, 2)
    # Front rectangle
    p.drawRoundedRect(QRectF(m, m + 3 + s * 0.05, s * 0.5, s * 0.55), 2, 2)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_regenerate(size: int = 32) -> QIcon:
    """Regenerate icon — circular arrow."""
    key = f"regenerate_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    cx, cy = s / 2, s / 2
    r = s * 0.3

    # Arc (3/4 circle)
    import math
    path = QPainterPath()
    start = -30
    span = 270
    for i in range(span + 1):
        angle = math.radians(start + i)
        px = cx + r * math.cos(angle)
        py = cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    p.drawPath(path)

    # Arrowhead at the end of the arc
    end_angle = math.radians(start + span)
    ex = cx + r * math.cos(end_angle)
    ey = cy + r * math.sin(end_angle)
    # Arrow pointing in the direction of the arc
    arrow_len = s * 0.12
    perp = end_angle + math.pi / 2
    p.drawLine(QPointF(ex, ey), QPointF(ex + arrow_len * math.cos(end_angle - 0.6),
                                         ey + arrow_len * math.sin(end_angle - 0.6)))
    p.drawLine(QPointF(ex, ey), QPointF(ex + arrow_len * math.cos(end_angle + 0.6 + math.pi),
                                         ey + arrow_len * math.sin(end_angle + 0.6 + math.pi)))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_attach(size: int = 32) -> QIcon:
    """Paperclip / attach icon."""
    key = f"attach_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # Paperclip shape
    path = QPainterPath()
    path.moveTo(s * 0.55, s * 0.75)
    path.lineTo(s * 0.55, s * 0.35)
    path.cubicTo(s * 0.55, s * 0.15, s * 0.35, s * 0.15, s * 0.35, s * 0.35)
    path.lineTo(s * 0.35, s * 0.65)
    path.cubicTo(s * 0.35, s * 0.78, s * 0.48, s * 0.78, s * 0.48, s * 0.65)
    path.lineTo(s * 0.48, s * 0.4)
    p.drawPath(path)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_minimize(size: int = 32) -> QIcon:
    """Minimize icon — horizontal line."""
    key = f"minimize_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    p.setPen(QPen(_ICON_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.25, s * 0.5), QPointF(s * 0.75, s * 0.5))
    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_maximize(size: int = 32) -> QIcon:
    """Maximize icon — empty square."""
    key = f"maximize_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    p.setPen(QPen(_ICON_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56), 2, 2)
    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_popout(size: int = 32) -> QIcon:
    """Popout / external window icon — box with arrow pointing out."""
    key = f"popout_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # Box (partial, open corner)
    path = QPainterPath()
    path.moveTo(s * 0.55, s * 0.25)
    path.lineTo(s * 0.25, s * 0.25)
    path.lineTo(s * 0.25, s * 0.75)
    path.lineTo(s * 0.75, s * 0.75)
    path.lineTo(s * 0.75, s * 0.45)
    p.drawPath(path)

    # Arrow pointing out
    p.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.78, s * 0.22))
    p.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.62, s * 0.22))
    p.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.78, s * 0.38))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_home(size: int = 32) -> QIcon:
    """Modern home icon."""
    key = f"home_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # House outline
    path = QPainterPath()
    path.moveTo(s * 0.5, s * 0.18)   # roof peak
    path.lineTo(s * 0.18, s * 0.48)  # left roof
    path.lineTo(s * 0.25, s * 0.48)
    path.lineTo(s * 0.25, s * 0.78)
    path.lineTo(s * 0.75, s * 0.78)
    path.lineTo(s * 0.75, s * 0.48)
    path.lineTo(s * 0.82, s * 0.48)
    path.closeSubpath()
    p.drawPath(path)

    # Door
    p.drawRect(QRectF(s * 0.42, s * 0.55, s * 0.16, s * 0.23))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_layout_view(size: int = 32) -> QIcon:
    """Layout view icon — grid of small transistor-like blocks."""
    key = f"layout_view_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(176, 176, 176, 30)))

    # Row of small device blocks
    bw = s * 0.18
    bh = s * 0.25
    gap = s * 0.02
    y1 = s * 0.2
    y2 = s * 0.55
    for i in range(3):
        x = s * 0.15 + i * (bw + gap)
        p.drawRoundedRect(QRectF(x, y1, bw, bh), 1.5, 1.5)
        p.drawRoundedRect(QRectF(x, y2, bw, bh), 1.5, 1.5)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_floorplan_view(size: int = 32) -> QIcon:
    """Floorplan view icon — larger blocks with labels."""
    key = f"floorplan_view_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(QColor("#2d8a6f"), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(26, 92, 76, 60)))

    # Two larger blocks
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.15, s * 0.35, s * 0.7), 3, 3)
    p.drawRoundedRect(QRectF(s * 0.55, s * 0.15, s * 0.35, s * 0.7), 3, 3)

    # Connection line
    p.setPen(QPen(QColor("#5dade2"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.45, s * 0.5), QPointF(s * 0.55, s * 0.5))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_both_view(size: int = 32) -> QIcon:
    """Both view icon — split pane with layout + floorplan."""
    key = f"both_view_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    p.setPen(QPen(_ICON_FG, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)

    # Outer frame
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.15, s * 0.8, s * 0.7), 3, 3)
    # Vertical divider
    p.drawLine(QPointF(s * 0.5, s * 0.15), QPointF(s * 0.5, s * 0.85))

    # Left side: small blocks (layout)
    p.setBrush(QBrush(QColor(176, 176, 176, 30)))
    bw = s * 0.12
    bh = s * 0.18
    p.drawRect(QRectF(s * 0.15, s * 0.25, bw, bh))
    p.drawRect(QRectF(s * 0.3, s * 0.25, bw, bh))
    p.drawRect(QRectF(s * 0.15, s * 0.55, bw, bh))
    p.drawRect(QRectF(s * 0.3, s * 0.55, bw, bh))

    # Right side: larger block (floorplan)
    p.setBrush(QBrush(QColor(26, 92, 76, 60)))
    p.setPen(QPen(QColor("#2d8a6f"), 1.2))
    p.drawRoundedRect(QRectF(s * 0.55, s * 0.25, s * 0.3, s * 0.5), 2, 2)

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_circuit(size: int = 32) -> QIcon:
    """Circuit / schematic icon for design tabs."""
    key = f"circuit_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(_ICON_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    # MOSFET symbol simplified
    cx, cy = s / 2, s / 2
    # Gate line
    p.drawLine(QPointF(cx - s * 0.2, cy), QPointF(cx - s * 0.08, cy))
    # Channel
    p.drawLine(QPointF(cx - s * 0.05, cy - s * 0.18), QPointF(cx - s * 0.05, cy + s * 0.18))
    # Source/Drain lines
    p.drawLine(QPointF(cx + s * 0.02, cy - s * 0.14), QPointF(cx + s * 0.2, cy - s * 0.14))
    p.drawLine(QPointF(cx + s * 0.02, cy + s * 0.14), QPointF(cx + s * 0.2, cy + s * 0.14))
    p.drawLine(QPointF(cx + s * 0.02, cy - s * 0.18), QPointF(cx + s * 0.02, cy + s * 0.18))

    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


def icon_close_tab(size: int = 16) -> QIcon:
    """Small X close icon for tabs."""
    key = f"close_tab_{size}"
    if key in _CACHE:
        return _CACHE[key]
    pm, p = _make_pixmap_sized(size)
    s = size
    pen = QPen(QColor("#7b8a9c"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = s * 0.28
    p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
    p.drawLine(QPointF(s - m, m), QPointF(m, s - m))
    icon = _icon_from_painter(pm, p)
    _CACHE[key] = icon
    return icon


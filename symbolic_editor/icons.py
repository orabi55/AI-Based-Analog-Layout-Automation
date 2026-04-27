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


def icon_open_file() -> QIcon:
    if "open_file" in _CACHE:
        return _CACHE["open_file"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 45)))
    body = QPainterPath()
    body.moveTo(7, 10)
    body.lineTo(13, 10)
    body.lineTo(16, 13)
    body.lineTo(25, 13)
    body.lineTo(25, 24)
    body.lineTo(7, 24)
    body.closeSubpath()
    p.drawPath(body)
    p.setPen(QPen(_ACCENT, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(QPointF(11, 18), QPointF(20, 18))
    p.drawLine(QPointF(16, 14), QPointF(20, 18))
    p.drawLine(QPointF(16, 22), QPointF(20, 18))
    icon = _icon_from_painter(pm, p)
    _CACHE["open_file"] = icon
    return icon


def icon_import_file() -> QIcon:
    if "import_file" in _CACHE:
        return _CACHE["import_file"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8, 7, 16, 18), 3, 3)
    p.setPen(QPen(_GREEN, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(QPointF(16, 6), QPointF(16, 18))
    p.drawLine(QPointF(12, 14), QPointF(16, 18))
    p.drawLine(QPointF(20, 14), QPointF(16, 18))
    p.drawLine(QPointF(11, 24), QPointF(21, 24))
    icon = _icon_from_painter(pm, p)
    _CACHE["import_file"] = icon
    return icon


def icon_save_file() -> QIcon:
    if "save_file" in _CACHE:
        return _CACHE["save_file"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 32)))
    p.drawRoundedRect(QRectF(7, 6, 18, 20), 3, 3)
    p.setBrush(QBrush(_FG))
    p.drawRect(QRectF(11, 8, 10, 5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_ACCENT, 1.8))
    p.drawRect(QRectF(11, 17, 10, 6))
    icon = _icon_from_painter(pm, p)
    _CACHE["save_file"] = icon
    return icon


def icon_export_file() -> QIcon:
    if "export_file" in _CACHE:
        return _CACHE["export_file"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 7, 18, 18), 3, 3)
    p.setPen(QPen(_ORANGE, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawLine(QPointF(16, 10), QPointF(16, 19))
    p.drawLine(QPointF(12, 15), QPointF(16, 11))
    p.drawLine(QPointF(20, 15), QPointF(16, 11))
    p.drawLine(QPointF(11, 22), QPointF(21, 22))
    icon = _icon_from_painter(pm, p)
    _CACHE["export_file"] = icon
    return icon


def icon_home() -> QIcon:
    if "home" in _CACHE:
        return _CACHE["home"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_FG.red(), _FG.green(), _FG.blue(), 28)))
    roof = QPainterPath()
    roof.moveTo(6, 15)
    roof.lineTo(16, 7)
    roof.lineTo(26, 15)
    p.drawPath(roof)
    p.drawRoundedRect(QRectF(9, 15, 14, 11), 2, 2)
    p.setPen(QPen(_ACCENT, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(16, 20), QPointF(16, 26))
    icon = _icon_from_painter(pm, p)
    _CACHE["home"] = icon
    return icon


def icon_new_tab() -> QIcon:
    if "new_tab" in _CACHE:
        return _CACHE["new_tab"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 24)))
    p.drawRoundedRect(QRectF(6, 8, 18, 16), 3, 3)
    p.setPen(QPen(_GREEN, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(16, 12), QPointF(16, 20))
    p.drawLine(QPointF(12, 16), QPointF(20, 16))
    icon = _icon_from_painter(pm, p)
    _CACHE["new_tab"] = icon
    return icon


def icon_bell() -> QIcon:
    if "bell" in _CACHE:
        return _CACHE["bell"]
    pm, p = _make_pixmap()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_FG, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    bell = QPainterPath()
    bell.moveTo(10, 21)
    bell.lineTo(22, 21)
    bell.cubicTo(20, 18, 21, 15, 20, 12)
    bell.cubicTo(19, 9, 17, 8, 16, 8)
    bell.cubicTo(15, 8, 13, 9, 12, 12)
    bell.cubicTo(11, 15, 12, 18, 10, 21)
    p.drawPath(bell)
    p.setPen(QPen(_ACCENT, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(13, 24), QPointF(19, 24))
    p.drawEllipse(QRectF(14.4, 23, 3.2, 3.2))
    icon = _icon_from_painter(pm, p)
    _CACHE["bell"] = icon
    return icon


def icon_circuit_tab() -> QIcon:
    if "circuit_tab" in _CACHE:
        return _CACHE["circuit_tab"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_GREEN, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_GREEN.red(), _GREEN.green(), _GREEN.blue(), 42)))
    p.drawRoundedRect(QRectF(8, 8, 16, 16), 3, 3)
    p.drawLine(QPointF(12, 12), QPointF(20, 20))
    p.drawLine(QPointF(20, 12), QPointF(12, 20))
    p.setPen(QPen(_FG, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x in (11, 16, 21):
        p.drawLine(QPointF(x, 6), QPointF(x, 8))
        p.drawLine(QPointF(x, 24), QPointF(x, 26))
    icon = _icon_from_painter(pm, p)
    _CACHE["circuit_tab"] = icon
    return icon


def icon_abutment() -> QIcon:
    if "abutment" in _CACHE:
        return _CACHE["abutment"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_ORANGE.red(), _ORANGE.green(), _ORANGE.blue(), 32)))
    p.drawRoundedRect(QRectF(5, 9, 8, 14), 2, 2)
    p.drawRoundedRect(QRectF(19, 9, 8, 14), 2, 2)
    p.setPen(QPen(_ORANGE, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(13, 16), QPointF(19, 16))
    p.drawLine(QPointF(16, 12), QPointF(16, 20))
    icon = _icon_from_painter(pm, p)
    _CACHE["abutment"] = icon
    return icon


def icon_colorize() -> QIcon:
    if "colorize" in _CACHE:
        return _CACHE["colorize"]
    pm, p = _make_pixmap()
    # A simple color palette / paint brush or color wheel
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Let's draw 3 overlapping colored circles to represent "Colorize"
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(231, 76, 60, 200)))  # Red
    p.drawEllipse(QRectF(8, 6, 12, 12))
    p.setBrush(QBrush(QColor(46, 204, 113, 200))) # Green
    p.drawEllipse(QRectF(14, 14, 12, 12))
    p.setBrush(QBrush(QColor(52, 152, 219, 200))) # Blue
    p.drawEllipse(QRectF(6, 14, 12, 12))
    
    # A small brush handle at the bottom
    p.setPen(QPen(_FG, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(16, 26), QPointF(24, 26))
    
    icon = _icon_from_painter(pm, p)
    _CACHE["colorize"] = icon
    return icon


def icon_ai_placement() -> QIcon:
    if "ai_placement" in _CACHE:
        return _CACHE["ai_placement"]
    pm, p = _make_pixmap()
    p.setPen(QPen(_ACCENT, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QBrush(QColor(_ACCENT.red(), _ACCENT.green(), _ACCENT.blue(), 28)))
    p.drawRoundedRect(QRectF(6, 6, 20, 20), 5, 5)
    p.setPen(QPen(_FG, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(10, 16), QPointF(14, 16))
    p.drawLine(QPointF(18, 16), QPointF(22, 16))
    p.drawLine(QPointF(16, 10), QPointF(16, 14))
    p.drawLine(QPointF(16, 18), QPointF(16, 22))
    p.setPen(QPen(_GREEN, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(12, 12), QPointF(20, 20))
    p.drawLine(QPointF(20, 12), QPointF(12, 20))
    icon = _icon_from_painter(pm, p)
    _CACHE["ai_placement"] = icon
    return icon

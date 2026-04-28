# -*- coding: utf-8 -*-
"""
Schematic View Panel — proper IEEE MOSFET symbols + connected netlist layout.

- Proper N/P-Channel MOSFET symbols (gate poly bar, oxide gap, channel, arrow)
- Auto-layout: PMOS on top rows, NMOS below, signal flow top→bottom
- Connected wires between shared nets with clickable net labels
- Mouse-wheel zoom, click-drag pan
- Click transistor → highlight all fingers in layout editor
- Click net label → highlight all devices on that net
"""
from __future__ import annotations
import math
from collections import defaultdict
from typing import Callable

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsLineItem, QGraphicsPathItem, QToolButton,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QLineF, Signal, QTimer
from PySide6.QtGui import (
    QColor as tcolor,
    QPainter,
    QPen,
    QColor,
    QBrush,
    QFont,
    QPainterPath,
    QTransform,
    QFontMetricsF,
)
from theme import apply_style

# ── Palette ──────────────────────────────────────────────────────────────────
_BG       = tcolor("#000000")
_DEV_COL  = tcolor("#00ff00")  # Green transistors
_WIRE_COL = tcolor("#00ccff")  # Cyan wires
_PIN_COL  = tcolor("#ff0000")  # Red square pins
_NAME_COL = tcolor("#ff0000")  # Red device name
_PARAM_COL= tcolor("#ffaa00")  # Orange parameters
_RAIL_VDD = tcolor("#ff0000")
_RAIL_GND = tcolor("#00ccff")
_DIM      = tcolor("#64748b")
_SEL      = tcolor("#facc15")
_HOVER    = tcolor("#fbbf24")

_POWER_NETS  = {"VDD", "AVDD", "VCC", "PWR", "VDDA", "VDDIO"}
_GROUND_NETS = {"GND", "VSS", "GNDA", "GND_A", "AGND"}

def _is_power(n: str)  -> bool: return n.upper() in _POWER_NETS  or n.upper().startswith("VDD")
def _is_ground(n: str) -> bool: return n.upper() in _GROUND_NETS or n.upper().startswith("VSS") or n.upper().startswith("GND")


# ── IEEE MOSFET symbol ────────────────────────────────────────────────────────
class MosfetItem(QGraphicsItem):
    """
    Standard IEEE enhancement-mode MOSFET symbol matching Virtuoso style.

    Coordinate origin = centre of the channel line.
    Exposed ports (scene coords via mapToScene):
      gate_port()   – left of gate stub
      drain_port()  – top of drain pin
      source_port() – bottom of source pin
    """
    # Geometry constants (pixels)
    _GP  = -14   # x of gate-poly bar
    _CH  =  -2   # x of channel line
    _CH_H = 26   # half-height of channel
    _GP_H = 32   # half-height of gate-poly bar
    _STUB =  16  # horizontal D/S stub length (rightward from channel)
    _PIN_EXT = 12  # extra length of D/S pin going up/down
    _GATE_X  = -34  # gate port x (left end of gate stub)

    def __init__(self, node_id: str, dev_type: str, label: str, info: str,
                 on_click: Callable, parent=None):
        super().__init__(parent)
        self._id      = node_id
        self._nmos    = dev_type.lower() != "pmos"
        self._label   = label
        self._info    = info
        self._click   = on_click
        self._sel     = False
        self._hov     = False
        self.setAcceptHoverEvents(True)

    # ── Ports ────────────────────────────────────────────────────────
    def gate_port(self)   -> QPointF: return self.mapToScene(self._GATE_X, 0)
    def drain_port(self)  -> QPointF:
        dy = -(self._CH_H + self._PIN_EXT)
        return self.mapToScene(self._CH + self._STUB, dy)
    def source_port(self) -> QPointF:
        sy = self._CH_H + self._PIN_EXT
        return self.mapToScene(self._CH + self._STUB, sy)

    # ── Bounding rect ────────────────────────────────────────────────
    def boundingRect(self) -> QRectF:
        left  = self._GATE_X - 6
        right = self._CH + self._STUB + 80  # Room for text
        top   = -(self._CH_H + self._PIN_EXT + 6)
        bot   = self._CH_H + self._PIN_EXT + 6
        return QRectF(left, top, right - left, bot - top)

    def _draw_pin(self, painter, x, y):
        # Draw small red square
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_PIN_COL))
        painter.drawRect(QRectF(x - 3, y - 3, 6, 6))
        painter.restore()

    # ── Paint ────────────────────────────────────────────────────────
    def paint(self, painter: QPainter, option, widget=None):
        col = _DEV_COL
        if self._sel: col = _SEL
        elif self._hov: col = _HOVER

        pen_main = QPen(col, 2.0, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        pen_thin = QPen(col, 1.4, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        gp, ch = self._GP, self._CH
        ch_h, gp_h = self._CH_H, self._GP_H
        stub, pin_ext = self._STUB, self._PIN_EXT
        gate_x = self._GATE_X

        # Gate poly bar (vertical)
        painter.setPen(pen_main)
        painter.drawLine(QLineF(gp, -gp_h, gp, gp_h))

        # Channel line (vertical)
        painter.drawLine(QLineF(ch, -ch_h, ch, ch_h))

        # Gate stub: gate_x → gate poly bar
        painter.setPen(pen_thin)
        painter.drawLine(QLineF(gate_x, 0, gp, 0))

        # Drain stub (top): channel → right
        dy = -ch_h
        painter.setPen(pen_main)
        painter.drawLine(QLineF(ch, dy, ch + stub, dy))
        # Drain pin (upward)
        painter.drawLine(QLineF(ch + stub, dy, ch + stub, dy - pin_ext))

        # Source stub (bottom): channel → right
        sy = ch_h
        painter.drawLine(QLineF(ch, sy, ch + stub, sy))
        # Source pin (downward)
        painter.drawLine(QLineF(ch + stub, sy, ch + stub, sy + pin_ext))

        # Body arrow (midpoint of channel → gate poly bar)
        mid_y = 0
        ax_from = gp + 2
        ax_to   = ch - 2
        painter.setPen(pen_main)
        painter.drawLine(QLineF(ax_from, mid_y, ax_to, mid_y))
        if self._nmos:
            # arrowhead pointing right (toward channel)
            painter.drawLine(QLineF(ax_to - 6, mid_y - 4, ax_to, mid_y))
            painter.drawLine(QLineF(ax_to - 6, mid_y + 4, ax_to, mid_y))
            # body pin extending right
            painter.drawLine(QLineF(ch, mid_y, ch + stub, mid_y))
            # connect body to source
            painter.drawLine(QLineF(ch + stub, mid_y, ch + stub, sy))
        else:
            # arrowhead pointing left (away from channel) + circle on gate
            painter.drawLine(QLineF(ax_from + 6, mid_y - 4, ax_from, mid_y))
            painter.drawLine(QLineF(ax_from + 6, mid_y + 4, ax_from, mid_y))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(gate_x - 8, -5, 10, 10))
            # body pin extending right
            painter.drawLine(QLineF(ch, mid_y, ch + stub, mid_y))
            # connect body to source
            painter.drawLine(QLineF(ch + stub, mid_y, ch + stub, dy))

        # Draw Red Pins
        self._draw_pin(painter, gate_x, 0)
        self._draw_pin(painter, ch + stub, dy - pin_ext)
        self._draw_pin(painter, ch + stub, sy + pin_ext)

        # Device label to the right
        text_x = ch + stub + 8
        lf = QFont("Segoe UI", 8)
        lf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        painter.setFont(lf)
        
        # Device Name
        painter.setPen(QPen(_NAME_COL))
        painter.drawText(QRectF(text_x, -18, 100, 14), Qt.AlignmentFlag.AlignLeft, self._label)
        
        # Parameters
        sf = QFont("Consolas", 7)
        painter.setFont(sf)
        painter.setPen(QPen(_PARAM_COL))
        painter.drawText(QRectF(text_x, 0, 100, 14), Qt.AlignmentFlag.AlignLeft, "l=28n")
        painter.drawText(QRectF(text_x, 14, 100, 14), Qt.AlignmentFlag.AlignLeft, self._info)

    # ── Events ───────────────────────────────────────────────────────
    def hoverEnterEvent(self, e):
        self._hov = True; self.setCursor(Qt.CursorShape.PointingHandCursor); self.update()
    def hoverLeaveEvent(self, e):
        self._hov = False; self.unsetCursor(); self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._click(self._id)

    def set_selected(self, v: bool):
        if self._sel != v: self._sel = v; self.update()


# ── Clickable net label ───────────────────────────────────────────────────────
class NetLabel(QGraphicsItem):
    PAD = 2

    def __init__(self, net: str, on_click: Callable, parent=None):
        super().__init__(parent)
        self._net   = net
        self._click = on_click
        self._sel   = False
        self._hov   = False
        fm = QFontMetricsF(QFont("Segoe UI", 8))
        self._tw = fm.horizontalAdvance(net)
        self._th = fm.height()
        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        return QRectF(-self._tw / 2 - self.PAD, -self._th / 2 - self.PAD,
                      self._tw + 2 * self.PAD, self._th + 2 * self.PAD)

    def paint(self, painter: QPainter, option, widget=None):
        col = _SEL if self._sel else (_HOVER if self._hov else _WIRE_COL)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.boundingRect()
        
        if self._sel or self._hov:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 60)))
            painter.drawRoundedRect(r, 2, 2)
            
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QPen(col))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, self._net)

    def hoverEnterEvent(self, e):
        self._hov = True; self.setCursor(Qt.CursorShape.PointingHandCursor); self.update()
    def hoverLeaveEvent(self, e):
        self._hov = False; self.unsetCursor(); self.update()
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self._click(self._net)

    def set_selected(self, v: bool):
        if self._sel != v: self._sel = v; self.update()


# ── Schematic layout engine ───────────────────────────────────────────────────
def _build_layout(nodes: list[dict], terminal_nets: dict):
    """
    Returns list of grouped logical devices and their (cx, cy) positions.
    """
    DUMMY = ("FILLER_DUMMY", "EDGE_DUMMY", "DUMMY")
    
    # ── Group physical fingers into logical symbols ──────────────────
    logical_devs = {}
    for n in nodes:
        nid = n["id"]
        if any(nid.startswith(p) for p in DUMMY):
            continue
            
        elec = n.get("electrical", {})
        parent = elec.get("parent")
        if not parent:
            # Fallback if parent not defined: strip _m / _f
            parent = nid.split("_m")[0].split("_f")[0]
            
        if parent not in logical_devs:
            logical_devs[parent] = {
                "id": parent,
                "type": n.get("type", "nmos"),
                "fingers": [],
                "m_max": 1,
                "nf_max": 1,
                "terminal_nets": terminal_nets.get(nid, {})
            }
            
        dev = logical_devs[parent]
        dev["fingers"].append(nid)
        # Track max m / max nf seen across the grouped fingers
        m = elec.get("m", elec.get("multiplier", 1))
        nf = elec.get("nf", elec.get("nf_per_device", elec.get("total_fingers", 1)))
        dev["m_max"] = max(dev["m_max"], m)
        dev["nf_max"] = max(dev["nf_max"], nf)

    devs = list(logical_devs.values())
    if not devs:
        return [], {}

    # ── 1. Assign ranks ──────────────────────────────────────────────
    def rank(node):
        typ  = node.get("type", "nmos").lower()
        nets = node["terminal_nets"]
        s_net = nets.get("S", "")
        d_net = nets.get("D", "")
        if typ == "pmos":
            return 0 if _is_power(s_net) or _is_power(d_net) else 1
        else:
            return 3 if _is_ground(s_net) or _is_ground(d_net) else 2

    ranked: dict[int, list] = defaultdict(list)
    for n in devs:
        ranked[rank(n)].append(n)

    # ── 2. X-order within rank ───────────────────────────────────────
    positions: dict[str, dict] = {}  # node_id → {cx, cy, rank}
    CELL_X = 110
    CELL_Y = 160
    RANK_Y = {0: 0, 1: CELL_Y, 2: 3 * CELL_Y, 3: 4 * CELL_Y}

    for rk in sorted(ranked):
        row_devs = ranked[rk]
        for idx, nd in enumerate(row_devs):
            positions[nd["id"]] = {"cx": idx * CELL_X, "cy": RANK_Y[rk], "rank": rk}

    # ── 3. Centre each rank horizontally ─────────────────────────────
    max_x = max((v["cx"] for v in positions.values()), default=0)
    for rk in sorted(ranked):
        row = [positions[n["id"]] for n in ranked[rk]]
        if not row: continue
        row_w = (len(row) - 1) * CELL_X
        shift = (max_x - row_w) / 2
        for p in row: p["cx"] += shift

    return devs, positions


# ── Schematic canvas ──────────────────────────────────────────────────────────
class SchematicCanvas(QGraphicsView):
    device_clicked = Signal(str)
    net_clicked    = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sc = QGraphicsScene(self)
        self.setScene(self._sc)
        self.setBackgroundBrush(QBrush(_BG))
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.TextAntialiasing,
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        apply_style(self, "background: transparent; border: none;")
        self._zoom = 1.0
        self._mos_items:  dict[str, MosfetItem] = {}
        self._net_labels: dict[str, list[NetLabel]] = {}
        self._sel_dev: str | None = None
        self._sel_net: str | None = None

    # ── Public build ─────────────────────────────────────────────────
    def build_schematic(self, nodes: list[dict], terminal_nets: dict):
        self._sc.clear()
        self._mos_items.clear()
        self._net_labels.clear()
        self._sel_dev = self._sel_net = None

        devs, positions = _build_layout(nodes, terminal_nets)
        if not devs:
            t = self._sc.addText("No devices", QFont("Segoe UI", 11))
            t.setDefaultTextColor(_DIM)
            return

        # ── Place MOSFET items ────────────────────────────────────────
        for nd in devs:
            nid  = nd["id"]
            pos  = positions[nid]
            nf   = nd["nf_max"]
            m    = nd["m_max"]
            item = MosfetItem(
                nid, nd.get("type", "nmos"), nid, f"nf={nf} m={m}",
                on_click=self._dev_clicked,
            )
            item.setPos(pos["cx"], pos["cy"])
            self._sc.addItem(item)
            self._mos_items[nid] = item

        # ── Draw wires for each net ───────────────────────────────────
        # Collect all terminal positions per net
        net_terminals: dict[str, list[tuple[str, str, QPointF]]] = defaultdict(list)
        for nd in devs:
            nid = nd["id"]
            item = self._mos_items[nid]
            tn = nd["terminal_nets"]
            port_map = {
                "G": item.gate_port(),
                "D": item.drain_port(),
                "S": item.source_port(),
            }
            for terminal, net in tn.items():
                if net:
                    net_terminals[net].append((nid, terminal, port_map[terminal]))

        for net, connections in net_terminals.items():
            self._draw_net(net, connections)

        self._sc.setSceneRect(self._sc.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        QTimer.singleShot(60, self.fit_all)

    def _draw_net(self, net: str, connections: list):
        """Draw wires + label for one net."""
        if len(connections) < 1:
            return

        is_pwr = _is_power(net)
        is_gnd = _is_ground(net)

        if is_pwr:
            wire_col = _RAIL_VDD
        elif is_gnd:
            wire_col = _RAIL_GND
        else:
            wire_col = _WIRE_COL

        pen = QPen(wire_col, 1.5, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        pts = [c[2] for c in connections]

        if len(pts) == 1:
            # Single connection — just draw a stub + label
            p = pts[0]
            lbl = NetLabel(net, self._net_clicked)
            lbl.setPos(p.x() + 18, p.y())
            self._sc.addItem(lbl)
            line = QGraphicsLineItem(QLineF(p, QPointF(p.x() + 12, p.y())))
            line.setPen(pen)
            self._sc.addItem(line)
            self._net_labels.setdefault(net, []).append(lbl)
            return

        # For multiple connections: draw a horizontal bus at mean-Y,
        # drop vertical legs from each terminal to the bus.
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        bus_y = sum(ys) / len(ys)
        x_min, x_max = min(xs), max(xs)

        # Horizontal bus
        bus = QGraphicsLineItem(QLineF(x_min, bus_y, x_max, bus_y))
        bus.setPen(pen)
        self._sc.addItem(bus)

        # Vertical legs
        for p in pts:
            leg = QGraphicsLineItem(QLineF(p.x(), p.y(), p.x(), bus_y))
            leg.setPen(pen)
            self._sc.addItem(leg)

        # Net label at bus midpoint
        mid_x = (x_min + x_max) / 2
        lbl = NetLabel(net, self._net_clicked)
        lbl.setPos(mid_x, bus_y)
        self._sc.addItem(lbl)
        self._net_labels.setdefault(net, []).append(lbl)

    # ── Click handlers ────────────────────────────────────────────────
    def _dev_clicked(self, nid: str):
        self._clear_sel()
        item = self._mos_items.get(nid)
        if item: item.set_selected(True)
        self._sel_dev = nid
        self.device_clicked.emit(nid)

    def _net_clicked(self, net: str):
        self._clear_sel()
        for lbl in self._net_labels.get(net, []): lbl.set_selected(True)
        self._sel_net = net
        self.net_clicked.emit(net)

    def _clear_sel(self):
        if self._sel_dev:
            item = self._mos_items.get(self._sel_dev)
            if item: item.set_selected(False)
        if self._sel_net:
            for lbl in self._net_labels.get(self._sel_net, []):
                lbl.set_selected(False)
        self._sel_dev = self._sel_net = None

    def clear_selection(self):
        self._clear_sel()
        self.device_clicked.emit("")

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        # If clicked on background (no item), clear highlights
        if not item:
            self.clear_selection()
        super().mousePressEvent(event)

    # ── Zoom / fit ───────────────────────────────────────────────────
    def wheelEvent(self, event):
        f = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        nz = self._zoom * f
        if 0.05 <= nz <= 10.0:
            self._zoom = nz
            self.scale(f, f)

    def fit_all(self):
        self.fitInView(self._sc.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()


# ── Panel ─────────────────────────────────────────────────────────────────────
class SchematicPanel(QFrame):
    highlight_device = Signal(str)
    highlight_net    = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editor = None
        self._nodes: list[dict] = []
        self._tnets: dict = {}

        self.canvas = SchematicCanvas()
        self.canvas.device_clicked.connect(self._on_dev)
        self.canvas.net_clicked.connect(self._on_net)

        header = QFrame()
        header.setFixedHeight(40)
        apply_style(header, "background:#111821; border-bottom:1px solid #2d3548;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 0, 8, 0); hl.setSpacing(6)
        lbl = QLabel("⚡  Schematic")
        apply_style(lbl, "color:#dbe5ef;font-family:'Segoe UI';font-size:10pt;font-weight:700;")
        hl.addWidget(lbl); hl.addStretch()
        for icon, tip, slot in [("⊞", "Fit to view", lambda: self.canvas.fit_all()),
                                 ("↺", "Refresh",     self.refresh),
                                 ("X", "Clear selection", self.canvas.clear_selection)]:
            b = QToolButton(); b.setText(icon); b.setToolTip(tip)
            b.setFixedSize(26, 26)
            apply_style(b, 
                "QToolButton{background:transparent;color:#8899aa;border:1px solid #2d3548;"
                "border-radius:4px;font-size:12pt;}"
                "QToolButton:hover{background:#1e2533;color:#dbe5ef;border-color:#4a90d9;}")
            b.clicked.connect(slot); hl.addWidget(b)


        hint = QLabel("Click transistor → highlight fingers  |  Click net → highlight connected")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        apply_style(hint, "color:#374151;font-family:'Segoe UI';font-size:8pt;padding:3px;")

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)
        vl.addWidget(header); vl.addWidget(self.canvas, 1); vl.addWidget(hint)
        apply_style(self, "background:#0d1117; border-left:1px solid #2d3548;")
        self.setMinimumWidth(280)

    def set_editor(self, editor):  self._editor = editor

    def load(self, nodes, terminal_nets):
        self._nodes = nodes or []
        self._tnets = terminal_nets or {}
        self.canvas.build_schematic(self._nodes, self._tnets)

    def refresh(self):
        self.canvas.build_schematic(self._nodes, self._tnets)

    def _on_dev(self, nid: str):
        self.highlight_device.emit(nid)

    def _on_net(self, net: str):
        self.highlight_net.emit(net)

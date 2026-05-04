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

try:
    from .schematic_layout import build_band_layout, ChannelRouter
    from .schematic_ai import ai_layout
except ImportError:
    from schematic_layout import build_band_layout, ChannelRouter
    from schematic_ai import ai_layout

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsLineItem, QGraphicsPathItem, QToolButton,
)
from PySide6.QtCore import Qt, QRectF, QPointF, QLineF, Signal, QTimer, QThread, QObject
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

# ── Net-label / routing constants ────────────────────────────────────────────
_GLOBAL_NETS: set[str] = {
    "CLK", "CLKB", "CLK_B", "CLKN", "RST", "RSTB",
    "EN", "ENB", "BIAS", "VBIAS", "VCM",
}
_LABEL_FANOUT_THRESH:    int   = 5   # only use labels for very high fanout
_LABEL_SPAN_THRESH_CELLS: int  = 6   # span check rarely kicks in
_CELL_X: float = 160.0
_CELL_Y: float = 220.0

# Prefix patterns for primary supply nets — always labelled, never wired
_PORT_PREFIXES = (
    "VDD", "VSS", "GND", "VBIAS", "BIAS",
    "VIN", "IN", "CLK", "RST", "EN"
)
_PORT_EXACT: set[str] = {
    # Ports named exactly these strings → label only
    "VDD", "VSS", "GND", "VBIAS", "BIAS",
    "VIN", "IN", "CLK", "RST", "EN"
}


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
                 on_click: Callable, mirrored: bool = False, parent=None):
        super().__init__(parent)
        self._id      = node_id
        self._nmos    = dev_type.lower() != "pmos"
        self._label   = label
        self._info    = info
        self._click   = on_click
        self._mirrored = mirrored
        # PMOS: source is at VDD (top), so flip drain/source vertically
        self._vflip   = not self._nmos
        self._sel     = False
        self._hov     = False
        self.setAcceptHoverEvents(True)

    @property
    def mirrored(self) -> bool:
        return self._mirrored

    # ── Ports ────────────────────────────────────────────────────────
    def gate_port(self) -> QPointF:
        gx = -self._GATE_X if self._mirrored else self._GATE_X
        return self.mapToScene(gx, 0)

    def drain_port(self) -> QPointF:
        # For PMOS (vflip): drain is at the BOTTOM (positive y)
        raw_dy = (self._CH_H + self._PIN_EXT) if self._vflip else -(self._CH_H + self._PIN_EXT)
        sx = -(self._CH + self._STUB) if self._mirrored else (self._CH + self._STUB)
        return self.mapToScene(sx, raw_dy)

    def source_port(self) -> QPointF:
        # For PMOS (vflip): source is at the TOP (negative y) — connects to VDD
        raw_sy = -(self._CH_H + self._PIN_EXT) if self._vflip else (self._CH_H + self._PIN_EXT)
        sx = -(self._CH + self._STUB) if self._mirrored else (self._CH + self._STUB)
        return self.mapToScene(sx, raw_sy)

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

        # Apply vertical flip transform for PMOS (source at top)
        if self._vflip:
            painter.save()
            painter.scale(1.0, -1.0)

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

        # Drain stub (top in local coords): channel → right
        dy = -ch_h
        painter.setPen(pen_main)
        painter.drawLine(QLineF(ch, dy, ch + stub, dy))
        # Drain pin (upward in local coords)
        painter.drawLine(QLineF(ch + stub, dy, ch + stub, dy - pin_ext))

        # Source stub (bottom in local coords): channel → right
        sy = ch_h
        painter.drawLine(QLineF(ch, sy, ch + stub, sy))
        # Source pin (downward in local coords)
        painter.drawLine(QLineF(ch + stub, sy, ch + stub, sy + pin_ext))

        # Body arrow
        mid_y = 0
        ax_from = gp + 2
        ax_to   = ch - 2
        painter.setPen(pen_main)
        painter.drawLine(QLineF(ax_from, mid_y, ax_to, mid_y))
        if self._nmos:
            painter.drawLine(QLineF(ax_to - 6, mid_y - 4, ax_to, mid_y))
            painter.drawLine(QLineF(ax_to - 6, mid_y + 4, ax_to, mid_y))
            painter.drawLine(QLineF(ch, mid_y, ch + stub, mid_y))
            painter.drawLine(QLineF(ch + stub, mid_y, ch + stub, sy))
        else:
            painter.drawLine(QLineF(ax_from + 6, mid_y - 4, ax_from, mid_y))
            painter.drawLine(QLineF(ax_from + 6, mid_y + 4, ax_from, mid_y))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(gate_x - 8, -5, 10, 10))
            painter.drawLine(QLineF(ch, mid_y, ch + stub, mid_y))
            painter.drawLine(QLineF(ch + stub, mid_y, ch + stub, dy))

        # Draw Red Pins
        self._draw_pin(painter, gate_x, 0)
        self._draw_pin(painter, ch + stub, dy - pin_ext)
        self._draw_pin(painter, ch + stub, sy + pin_ext)

        if self._vflip:
            painter.restore()

        # Device label — always in normal (non-flipped) coords
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
    Groups physical finger nodes into logical devices, then delegates
    to schematic_layout.build_band_layout() for topology-aware placement.

    Returns (devs, positions) identical to the old signature.
    """
    DUMMY = ("FILLER_DUMMY", "EDGE_DUMMY", "DUMMY")

    logical_devs: dict[str, dict] = {}
    for n in nodes:
        nid = n["id"]
        if any(nid.startswith(p) for p in DUMMY):
            continue
        elec = n.get("electrical", {})
        parent = elec.get("parent")
        if not parent:
            parent = nid.split("_m")[0].split("_f")[0]
        if parent not in logical_devs:
            logical_devs[parent] = {
                "id": parent,
                "type": n.get("type", "nmos"),
                "fingers": [],
                "m_max": 1,
                "nf_max": 1,
                "terminal_nets": terminal_nets.get(nid, {}),
            }
        dev = logical_devs[parent]
        dev["fingers"].append(nid)
        m  = elec.get("m",  elec.get("multiplier", 1))
        nf = elec.get("nf", elec.get("nf_per_device", elec.get("total_fingers", 1)))
        dev["m_max"] = max(dev["m_max"], m)
        dev["nf_max"] = max(dev["nf_max"], nf)

    devs = list(logical_devs.values())
    if not devs:
        return [], {}

    _, positions = build_band_layout(devs, terminal_nets, canvas_width=800.0)
    return devs, positions


# ── Schematic canvas ──────────────────────────────────────────────────────────
class SchematicCanvas(QGraphicsView):
    device_clicked = Signal(str)
    net_clicked    = Signal(str)
    ai_layout_started = Signal()
    ai_layout_finished = Signal()

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
        # State kept for AI-refresh after deterministic render
        self._last_nodes: list[dict] = []
        self._last_tnets: dict = {}
        self._ai_thread = None
        self._ai_worker = None
        self._ai_devs: list[dict] = []
        self._ai_tnets: dict = {}

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
            nid      = nd["id"]
            pos      = positions.get(nid, {"cx": 0, "cy": 0, "mirrored": False})
            nf       = nd["nf_max"]
            m        = nd["m_max"]
            mirrored = pos.get("mirrored", False)
            item = MosfetItem(
                nid, nd.get("type", "nmos"), nid, f"nf={nf} m={m}",
                on_click=self._dev_clicked,
                mirrored=mirrored,
            )
            item.setPos(pos["cx"], pos["cy"])
            self._sc.addItem(item)
            self._mos_items[nid] = item

        # ── Power rails ───────────────────────────────────────────────
        if devs and positions:
            all_ys = [v["cy"] for v in positions.values()]
            all_xs = [v["cx"] for v in positions.values()]
            rail_x0 = min(all_xs) - _CELL_X
            rail_x1 = max(all_xs) + _CELL_X
            vdd_y   = min(all_ys) - _CELL_Y * 0.7
            gnd_y   = max(all_ys) + _CELL_Y * 0.7

            vdd_pen = QPen(_RAIL_VDD, 2.5, Qt.PenStyle.SolidLine)
            gnd_pen = QPen(_RAIL_GND, 2.5, Qt.PenStyle.SolidLine)

            vdd_line = QGraphicsLineItem(QLineF(rail_x0, vdd_y, rail_x1, vdd_y))
            vdd_line.setPen(vdd_pen)
            self._sc.addItem(vdd_line)

            gnd_line = QGraphicsLineItem(QLineF(rail_x0, gnd_y, rail_x1, gnd_y))
            gnd_line.setPen(gnd_pen)
            self._sc.addItem(gnd_line)

            for rail_net, rail_y, rail_pen, lbl_col in [
                ("VDD", vdd_y, vdd_pen, _RAIL_VDD),
                ("GND", gnd_y, gnd_pen, _RAIL_GND),
            ]:
                lbl = NetLabel(rail_net, self._net_clicked)
                lbl.setPos(rail_x0 - 4, rail_y)
                self._sc.addItem(lbl)
                self._net_labels.setdefault(rail_net, []).append(lbl)

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
                # Map passive terminals to available MOSFET ports for visual continuity
                "1": item.source_port(),
                "2": item.drain_port(),
                "+": item.source_port(),
                "-": item.drain_port(),
            }
            for terminal, net in tn.items():
                if net and terminal in port_map:
                    net_terminals[net].append((nid, terminal, port_map[terminal]))


        for net, connections in net_terminals.items():
            self._draw_net(net, connections)

        self._sc.setSceneRect(self._sc.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        QTimer.singleShot(60, self.fit_all)

        # ── Launch AI-assisted re-layout in background ──────────────
        self._last_nodes = nodes
        self._last_tnets = terminal_nets
        self.ai_layout_started.emit()
        QTimer.singleShot(200, self._start_ai_refresh)

    def _start_ai_refresh(self):
        """Launch Gemini AI layout in a background thread (non-blocking)."""
        # Cancel any previous in-flight AI call
        if self._ai_thread and self._ai_thread.isRunning():
            self._ai_thread.quit()
            self._ai_thread.wait(500)

        nodes = self._last_nodes
        tnets = self._last_tnets
        if not nodes:
            return

        # Resolve logical devs + groups the same way _build_layout does
        try:
            from schematic_layout import detect_groups
        except ImportError:
            from .schematic_layout import detect_groups

        DUMMY = ("FILLER_DUMMY", "EDGE_DUMMY", "DUMMY")
        logical_devs: dict[str, dict] = {}
        for n in nodes:
            nid = n["id"]
            if any(nid.startswith(p) for p in DUMMY):
                continue
            elec = n.get("electrical", {})
            parent = elec.get("parent") or nid.split("_m")[0].split("_f")[0]
            if parent not in logical_devs:
                logical_devs[parent] = {
                    "id": parent,
                    "type": n.get("type", "nmos"),
                    "fingers": [],
                    "m_max": 1, "nf_max": 1,
                    "terminal_nets": tnets.get(nid, {}),
                }
            logical_devs[parent]["fingers"].append(nid)

        devs = list(logical_devs.values())
        if not devs:
            return

        groups = detect_groups(devs, tnets)

        self._ai_devs = devs
        self._ai_tnets = tnets

        self._ai_devs = devs
        self._ai_tnets = tnets

        # Emitter must be a QObject living on the main thread to safely relay the signal
        class AiSignalEmitter(QObject):
            finished = Signal(object)

        emitter = AiSignalEmitter(self)
        
        def background_task():
            try:
                result = ai_layout(devs, groups, tnets, cell_px=_CELL_X)
                emitter.finished.emit(result)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("AI background thread error: %s", e)
                emitter.finished.emit(None)

        emitter.finished.connect(self._apply_ai_layout)
        
        # We must keep a reference to emitter so it isn't garbage collected
        self._ai_worker = emitter 
        
        import threading
        self._ai_thread = threading.Thread(target=background_task, daemon=True)
        self._ai_thread.start()

    def _apply_ai_layout(self, result: dict | None):
        """
        Called on the main thread after AI returns.
        If result is valid, rebuild the scene with AI positions.
        """
        if result is None:
            return  # AI failed — keep deterministic layout

        positions = result.get("positions", {})
        
        # Filter AI wire_nets to guarantee no global/power nets are drawn as wires
        raw_wires = set(result.get("wire_nets", []))
        wire_nets_ai: set[str] = set()
        for w in raw_wires:
            w_up = w.upper()
            if _is_power(w) or _is_ground(w) or w_up in {"CLK", "CLKB", "VINN", "VINP", "VOUTN", "VOUTP", "VBIAS"}:
                continue
            if w_up.startswith("VIN") or w_up.startswith("VOUT"):
                continue
            wire_nets_ai.add(w)
            
        cross_wires = result.get("cross_wires", [])

        if not positions:
            return

        # Rebuild scene with AI positions
        self._sc.clear()
        self._mos_items.clear()
        self._net_labels.clear()
        self._sel_dev = self._sel_net = None

        devs = self._ai_devs
        tnets = self._ai_tnets

        for nd in devs:
            nid = nd["id"]
            pos = positions.get(nid, {"cx": 0, "cy": 0, "mirrored": False})
            nf = nd.get("nf_max", 1)
            m  = nd.get("m_max", 1)
            mirrored = pos.get("mirrored", False)
            item = MosfetItem(
                nid, nd.get("type", "nmos"), nid, f"nf={nf} m={m}",
                on_click=self._dev_clicked,
                mirrored=mirrored,
            )
            item.setPos(pos["cx"], pos["cy"])
            self._sc.addItem(item)
            self._mos_items[nid] = item

        # Power rails
        if positions:
            all_ys = [v["cy"] for v in positions.values()]
            all_xs = [v["cx"] for v in positions.values()]
            rail_x0 = min(all_xs) - _CELL_X
            rail_x1 = max(all_xs) + _CELL_X
            vdd_y = min(all_ys) - _CELL_Y * 0.7
            gnd_y = max(all_ys) + _CELL_Y * 0.7
            self._vdd_y = vdd_y
            self._gnd_y = gnd_y
            for net, rail_y, col in [("VDD", vdd_y, _RAIL_VDD), ("GND", gnd_y, _RAIL_GND)]:
                pen = QPen(col, 2.5, Qt.PenStyle.SolidLine)
                line = QGraphicsLineItem(QLineF(rail_x0, rail_y, rail_x1, rail_y))
                line.setPen(pen)
                self._sc.addItem(line)
                lbl = NetLabel(net, self._net_clicked)
                lbl.setPos(rail_x0 - 4, rail_y)
                self._sc.addItem(lbl)
                self._net_labels.setdefault(net, []).append(lbl)

        # Net wires — collect terminal positions
        net_terminals: dict[str, list] = defaultdict(list)
        for nd in devs:
            nid = nd["id"]
            item = self._mos_items.get(nid)
            if not item:
                continue
            tn = nd.get("terminal_nets", tnets.get(nid, {}))
            port_map = {
                "G": item.gate_port(), "D": item.drain_port(), "S": item.source_port(),
                "1": item.source_port(), "2": item.drain_port(),
            }
            for terminal, net in tn.items():
                if net and terminal in port_map:
                    net_terminals[net].append((nid, terminal, port_map[terminal]))

        # Draw: AI-specified wire nets as wires; everything else as labels
        for net, connections in net_terminals.items():
            if net in wire_nets_ai:
                self._draw_net_as_wire(net, connections)
            else:
                self._draw_net(net, connections)

            self._sc.setSceneRect(self._sc.itemsBoundingRect().adjusted(-40, -40, 40, 40))
            QTimer.singleShot(60, self.fit_all)
            
            print(f"DEBUG: AI layout applied successfully ({len(positions)} devices)")
            self.ai_layout_finished.emit()

    def _draw_net_as_wire(self, net: str, connections: list):
        """Force a net to be drawn as a physical wire (used for AI-specified wire_nets)."""
        if len(connections) < 2:
            return
            
        is_pwr = _is_power(net)
        is_gnd = _is_ground(net)
        wire_col = _RAIL_VDD if is_pwr else (_RAIL_GND if is_gnd else _WIRE_COL)
        pen = QPen(wire_col, 1.5, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        pts = [c[2] for c in connections]
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        y_range = y_max - y_min

        if y_range < 10:  # purely horizontal
            bus_y = sum(ys) / len(ys)
            bus = QGraphicsLineItem(QLineF(x_min, bus_y, x_max, bus_y))
            bus.setPen(pen)
            self._sc.addItem(bus)
            for p in pts:
                dot = QGraphicsLineItem(QLineF(p.x(), p.y(), p.x(), bus_y))
                dot.setPen(pen)
                self._sc.addItem(dot)
        else:
            # Vertical spine at the x of the topmost terminal
            spine_x = xs[ys.index(y_min)]
            spine = QGraphicsLineItem(QLineF(spine_x, y_min, spine_x, y_max))
            spine.setPen(pen)
            self._sc.addItem(spine)
            for p in pts:
                if abs(p.x() - spine_x) > 0.1:
                    rung = QGraphicsLineItem(QLineF(spine_x, p.y(), p.x(), p.y()))
                    rung.setPen(pen)
                    self._sc.addItem(rung)

        mid_x = (x_min + x_max) / 2.0
        mid_y = (y_min + y_max) / 2.0
        lbl = NetLabel(net, self._net_clicked)
        if y_range < 10:
            lbl.setPos(mid_x + 4, mid_y - 14)
        else:
            lbl.setPos(spine_x + 4, mid_y - 14)
        self._sc.addItem(lbl)
        self._net_labels.setdefault(net, []).append(lbl)

    def _draw_net(self, net: str, connections: list):
        """Draw wires + labels for one net.

        Strategy
        --------
        * VDD / GND / port / global nets  → short stub + net-label at every terminal
        * Internal nodes (net1, VX, …)    → Manhattan wire (H-bus + V-legs) + one label
        * Fanout > threshold              → label-only fallback to avoid rats-nest
        """
        if not connections:
            return

        is_pwr = _is_power(net)
        is_gnd = _is_ground(net)
        wire_col = _RAIL_VDD if is_pwr else (_RAIL_GND if is_gnd else _WIRE_COL)
        pen = QPen(wire_col, 1.5, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        pts    = [c[2] for c in connections]
        fanout = len(pts)

        # ── Classify net ──────────────────────────────────────────────────────
        net_up = net.upper()
        is_global_port = (
            net_up in _GLOBAL_NETS
            or net_up in _PORT_EXACT
            or any(net_up.startswith(p) for p in _PORT_PREFIXES)
            or any(net_up.startswith(g) for g in _GLOBAL_NETS)
        )

        # ── Helper: label stubs at every terminal ─────────────────────────────
        def _draw_stubs():
            for nid, terminal, p in connections:
                stub_dx = -14 if terminal.upper() == "G" else 10
                stub = QGraphicsLineItem(
                    QLineF(p.x(), p.y(), p.x() + stub_dx, p.y()))
                stub.setPen(pen)
                self._sc.addItem(stub)
                lbl = NetLabel(net, self._net_clicked)
                lbl.setPos(p.x() + stub_dx + (4 if stub_dx > 0 else -4), p.y())
                self._sc.addItem(lbl)
                self._net_labels.setdefault(net, []).append(lbl)

        # ── Power / Ground: Vertical drops directly to rails ──────────────────
        if is_pwr and hasattr(self, '_vdd_y'):
            for p in pts:
                riser = QGraphicsLineItem(QLineF(p.x(), p.y(), p.x(), self._vdd_y))
                riser.setPen(pen)
                self._sc.addItem(riser)
            return

        if is_gnd and hasattr(self, '_gnd_y'):
            for p in pts:
                riser = QGraphicsLineItem(QLineF(p.x(), p.y(), p.x(), self._gnd_y))
                riser.setPen(pen)
                self._sc.addItem(riser)
            return

        # ── Single terminal: stub + label ─────────────────────────────────────
        if fanout == 1:
            _draw_stubs()
            return

        # ── Port / Global (e.g. CLK): label only to prevent ratsnest ──────────
        if is_global_port:
            _draw_stubs()
            return

        # ── Very high fanout: label only (anti-rats-nest) ─────────────────────
        if fanout > 8:
            _draw_stubs()
            return

        # ── Internal net: draw Manhattan wire ─────────────────────────────────
        xs = [c[2].x() for c in connections]
        ys = [c[2].y() for c in connections]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        y_range = y_max - y_min
        avg_x = sum(xs) / len(xs)

        def _add_line(x1, y1, x2, y2):
            line = QGraphicsLineItem(QLineF(x1, y1, x2, y2))
            line.setPen(pen)
            self._sc.addItem(line)

        if y_range < 10:  # purely horizontal — simple H bus
            bus_y = sum(ys) / len(ys)
            _add_line(x_min, bus_y, x_max, bus_y)
            for p in pts:
                _add_line(p.x(), p.y(), p.x(), bus_y)
        else:
            # Smart spine placement
            if abs(avg_x) < 40:
                net_up = net.upper()
                if "P" in net_up and "N" not in net_up:
                    spine_x = 12
                elif "N" in net_up and "P" not in net_up:
                    spine_x = -12
                elif net_up == "VX":
                    spine_x = -24
                elif net_up == "VY":
                    spine_x = 24
                else:
                    spine_x = (hash(net) % 5) * 8 - 16
            elif avg_x < 0:
                spine_x = x_min - 40
            else:
                spine_x = x_max + 40

            extended_y_min = y_min
            
            for nid, term, p in connections:
                px, py = p.x(), p.y()
                mirrored = self._mos_items[nid].mirrored if nid in self._mos_items else False
                gate_on_left = not mirrored
                
                # Gate wiring detour to prevent crossing through the symbol
                if term.upper() == 'G':
                    if gate_on_left and spine_x > px:
                        clear_x = px - 40
                        clear_y = py - 60
                        _add_line(px, py, clear_x, py)
                        _add_line(clear_x, py, clear_x, clear_y)
                        _add_line(clear_x, clear_y, spine_x, clear_y)
                        extended_y_min = min(extended_y_min, clear_y)
                        continue
                    elif not gate_on_left and spine_x < px:
                        clear_x = px + 40
                        clear_y = py - 60
                        _add_line(px, py, clear_x, py)
                        _add_line(clear_x, py, clear_x, clear_y)
                        _add_line(clear_x, clear_y, spine_x, clear_y)
                        extended_y_min = min(extended_y_min, clear_y)
                        continue

                # Standard rung
                _add_line(spine_x, py, px, py)

            # Vertical spine
            _add_line(spine_x, extended_y_min, spine_x, y_max)

        # Net label snapped to spine
        mid_y = (y_min + y_max) / 2.0
        # If purely horizontal, use mid_x. Else use spine_x.
        label_x = (x_min + x_max) / 2.0 if y_range < 10 else spine_x
        
        lbl = NetLabel(net, self._net_clicked)
        
        # Adjust label placement for negative offset spines to prevent overlap
        if y_range >= 10 and spine_x < -5:
            tw = lbl.boundingRect().width()
            lbl.setPos(label_x - tw - 4, mid_y - 14)
        else:
            lbl.setPos(label_x + 6, mid_y - 14)
            
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
    ai_layout_started = Signal()
    ai_layout_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editor = None
        self._nodes: list[dict] = []
        self._tnets: dict = {}

        self.canvas = SchematicCanvas()
        self.canvas.device_clicked.connect(self._on_dev)
        self.canvas.net_clicked.connect(self._on_net)
        self.canvas.ai_layout_started.connect(self.ai_layout_started.emit)
        self.canvas.ai_layout_finished.connect(self.ai_layout_finished.emit)

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

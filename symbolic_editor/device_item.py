from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsItem,
    QStyleOptionGraphicsItem,
    QStyle,
    QMenu,
    QColorDialog,
)
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter, QLinearGradient
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF


class DeviceSignals(QObject):
    """Helper QObject so DeviceItem (a QGraphicsRectItem) can emit signals."""
    drag_started = Signal()   # emitted when user begins dragging
    drag_finished = Signal()  # emitted when user releases after drag


class DeviceItem(QGraphicsRectItem):

    def __init__(self, name, dev_type, x, y, width, height, nf=1):

        super().__init__(0, 0, width, height)

        self.setPos(x, y)
        self.device_name = name
        self.device_type = str(dev_type).strip().lower()
        self.nf = max(1, int(nf))
        self.signals = DeviceSignals()

        self._drag_active = False
        self._drag_start_pos = QPointF()
        self._snap_grid_x = None
        self._snap_grid_y = None
        self._flip_h = False
        self._flip_v = False
        self._render_mode = "detailed"
        # Candidate highlight (auto-detected, green glow)
        self._hl_left  = None   # net name on the left  edge that can abut, or None
        self._hl_right = None   # net name on the right edge that can abut, or None
        # Manual abutment override (user-set, amber stripe)
        self._manual_abut_left  = False
        self._manual_abut_right = False
        # Match / lock highlight color (set when device is in a matched group)
        self._match_color = None   # QColor or None
        # Net label overlay (toggled from Nets tab)
        self._show_net_labels = False
        self._net_names = {}       # {"D": "VDD", "G": "clk", "S": "VSS"}
        self._colorize_nets = False
        self._net_color_seed = 0
        self._highlighted_net = None

        # ── Hierarchical group movement ──
        # These are set by the editor when loading a layout.
        # _parent_id: the electrical parent (e.g. "MM0" for "MM0_f1")
        # _sibling_group: list of other DeviceItem references sharing the same parent
        self._parent_id = None
        self._sibling_group = []    # populated by editor after all items are created
        self._propagating_move = False  # guard against recursive move propagation

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

        # --- Premium color palette per device type ---
        dtype = self.device_type
        name_upper = str(name).upper()
        self._is_dummy = (name_upper.startswith("DUMMY")
                          or name_upper.startswith("FILLER_DUMMY")
                          or name_upper.startswith("EDGE_DUMMY"))
        self._apply_default_palette()

        # Transparent fill — we paint everything custom
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def _apply_default_palette(self):
        dtype = self.device_type
        if self._is_dummy:
            # Muted slate for dummies — visually distinct but unobtrusive
            self._source_color = QColor("#dfe6e9")
            self._gate_color   = QColor("#636e72")
            self._drain_color  = QColor("#dfe6e9")
            self._border       = QColor("#b2bec3")
            self._label_color  = QColor("#636e72")
            self._terminal_label_color = QColor("#f5f6fa")
            self._gradient_top    = QColor("#b2bec3")
            self._gradient_bottom = QColor("#636e72")
            self._name_color      = QColor("#2d3436")
        elif dtype == "nmos":
            # Rich teal / cyan palette
            self._source_color = QColor("#e0f7fa")
            self._gate_color   = QColor("#00838f")
            self._drain_color  = QColor("#b2ebf2")
            self._border       = QColor("#006064")
            self._label_color  = QColor("#004d40")
            self._terminal_label_color = QColor("#e0f2f1")
            self._gradient_top    = QColor("#26c6da")
            self._gradient_bottom = QColor("#00695c")
            self._name_color      = QColor("#ffffff")
        else:
            # Warm coral / rose palette for PMOS
            self._source_color = QColor("#fce4ec")
            self._gate_color   = QColor("#ad1457")
            self._drain_color  = QColor("#f8bbd0")
            self._border       = QColor("#880e4f")
            self._label_color  = QColor("#880e4f")
            self._terminal_label_color = QColor("#fce4ec")
            self._gradient_top    = QColor("#f06292")
            self._gradient_bottom = QColor("#880e4f")
            self._name_color      = QColor("#ffffff")
        self.update()

    def get_logical_name(self):
        display_name = self.device_name
        if "_" in display_name and not self._is_dummy:
            return display_name.split("_")[0]
        return display_name

    def set_custom_color(self, base_color: QColor):
        self._source_color = base_color.lighter(130)
        self._drain_color  = base_color.lighter(130)
        self._gate_color   = base_color.darker(150)
        self._border       = base_color.darker(200)
        self._gradient_top = base_color.lighter(110)
        self._gradient_bottom = base_color.darker(120)
        self._label_color  = base_color.darker(300)
        self._terminal_label_color = QColor("#ffffff")
        self._name_color   = QColor("#ffffff")
        self.update()

    def reset_custom_color(self):
        self._apply_default_palette()


    def set_snap_grid(self, grid_x, grid_y=None):
        """Enable snapping item movement to scene grid (separate X/Y pitch)."""
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = (
            float(grid_y) if grid_y else self._snap_grid_x
        )

    def set_candidate_highlight(self, left_net=None, right_net=None):
        """Highlight terminal edges that can participate in diffusion sharing.

        Args:
            left_net:  net name for the left  S/D edge, or None to clear.
            right_net: net name for the right S/D edge, or None to clear.
        """
        self._hl_left  = left_net  or None
        self._hl_right = right_net or None
        self.update()

    def clear_candidate_highlight(self):
        self._hl_left  = None
        self._hl_right = None
        self.update()

    def set_abut_left(self, state):
        self._manual_abut_left = bool(state)
        self.update()

    def set_abut_right(self, state):
        self._manual_abut_right = bool(state)
        self.update()

    def abut_left(self):
        return self._manual_abut_left

    def abut_right(self):
        return self._manual_abut_right

    def set_match_color(self, color):
        """Set matched-group highlight color (QColor or None)."""
        self._match_color = color
        self.update()

    def is_match_locked(self) -> bool:
        """Return True if this device is visually marked as part of a matched group."""
        return self._match_color is not None

    def set_net_labels(self, nets: dict, seed: int = 0):
        """Enable net name labels on terminals.  nets = {'D': 'VDD', 'G': 'clk', 'S': 'VSS'}."""
        self._net_names = nets or {}
        self._show_net_labels = bool(self._net_names)
        self._net_color_seed = seed
        self.update()

    def clear_net_labels(self):
        """Hide net name labels."""
        self._show_net_labels = False
        self._net_names = {}
        self.update()

    def set_net_colorize_enabled(self, enabled: bool, seed: int = 0):
        """Toggle coloring terminals by net name with an optional randomization seed."""
        self._colorize_nets = bool(enabled)
        self._net_color_seed = seed
        self.update()

    def set_highlighted_net(self, net_name):
        """Highlight terminal labels matching net_name and dim the rest."""
        self._highlighted_net = str(net_name) if net_name else None
        self.update()

    def clear_highlighted_net(self):
        self._highlighted_net = None
        self.update()

    def _net_focus_state(self, net_name):
        if not self._highlighted_net or not net_name:
            return "normal"
        return "focus" if str(net_name) == self._highlighted_net else "dim"

    def _net_display_color(self, net_name, fallback=None):
        state = self._net_focus_state(net_name)
        if state == "focus":
            return QColor("#111827")
        if self._colorize_nets and not self._is_dummy:
            color = QColor("#ffffff")
        else:
            color = QColor(fallback or self._get_net_color(net_name))
        if state == "dim":
            color.setAlpha(70)
        return color

    def _draw_net_focus_frame(self, painter, rect, net_name, radius=2.0):
        state = self._net_focus_state(net_name)
        if state == "focus":
            fill = QColor("#facc15")
            fill.setAlpha(175)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(QColor("#f59e0b"), 2.4))
            painter.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), radius, radius)
            return
        if state == "dim":
            veil = QColor("#0b0f16")
            veil.setAlpha(105)
            painter.setBrush(QBrush(veil))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(rect)

    def _get_net_color(self, net_name):
        """Return a consistent unique QColor for a given net name."""
        if not net_name or net_name == "?":
            return QColor("#808896")
            
        # Incorporate seed for "new configuration" request
        input_str = f"{net_name}_{self._net_color_seed}"
        import hashlib
        h = int(hashlib.md5(input_str.encode()).hexdigest(), 16) % 360
        
        # Power nets get specific semantic colors
        pnet = str(net_name).upper()
        if pnet in ("VDD", "VCC", "AVDD", "DVDD"):
            return QColor("#ffaa66") # Amber/Orange
        if pnet in ("VSS", "GND", "AVSS", "DVSS"):
            return QColor("#66aaff") # Blue
        
        # Signal nets get hashed colors
        c = QColor()
        c.setHsl(h, 200, 180) # High saturation, medium brightness for dark mode
        return c

    def flip_horizontal(self):
        """Mirror device left/right."""
        self._flip_h = not self._flip_h
        self.update()

    def flip_vertical(self):
        """Mirror device up/down."""
        self._flip_v = not self._flip_v
        self.update()

    def is_flip_h(self):
        return self._flip_h

    def set_flip_h(self, state):
        self._flip_h = bool(state)
        self.update()

    def get_abut_left(self) -> bool:
        return self._manual_abut_left

    def get_abut_right(self) -> bool:
        return self._manual_abut_right

    def set_abut_left(self, state: bool):
        self._manual_abut_left = bool(state)
        self.update()

    def set_abut_right(self, state: bool):
        self._manual_abut_right = bool(state)
        self.update()

    def toggle_abut_left(self):
        self._manual_abut_left = not self._manual_abut_left
        self.update()

    def toggle_abut_right(self):
        self._manual_abut_right = not self._manual_abut_right
        self.update()

    def is_flip_v(self):
        return self._flip_v

    def set_flip_v(self, state):
        self._flip_v = bool(state)
        self.update()

    def orientation_string(self):
        """Compact orientation token for save/export."""
        base = "R0"
        if self._flip_h and self._flip_v:
            return f"{base}_FH_FV"
        if self._flip_h:
            return f"{base}_FH"
        if self._flip_v:
            return f"{base}_FV"
        return base

    def set_render_mode(self, mode):
        self._render_mode = mode if mode in {"detailed", "outline"} else "detailed"
        self.update()

    def itemChange(self, change, value):
        """Snap dragged positions to grid so devices never float between tracks."""
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self._snap_grid_x
            and self._snap_grid_y
        ):
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        return super().itemChange(change, value)

    # --------------------------------------------------
    # Drag tracking
    # --------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self.pos()
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        old_pos = self.pos()
        super().mouseMoveEvent(event)
        new_pos = self.pos()

        if not self._drag_active and new_pos != self._drag_start_pos:
            self._drag_active = True
            self.signals.drag_started.emit()

        # ── Hierarchical group movement ──
        # If this item is part of a parent group, propagate the same
        # delta to all siblings so the entire transistor moves as one.
        if self._sibling_group and not self._propagating_move:
            dx = new_pos.x() - old_pos.x()
            dy = new_pos.y() - old_pos.y()
            if dx != 0 or dy != 0:
                for sibling in self._sibling_group:
                    if sibling is not self:
                        sibling._propagating_move = True
                        sibling.moveBy(dx, dy)
                        sibling._propagating_move = False

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self.signals.drag_finished.emit()
        self._propagating_move = False
        super().mouseReleaseEvent(event)

    # --------------------------------------------------
    # Painting — Premium Multi-finger MOS layout
    # --------------------------------------------------
    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        if self._render_mode == "outline":
            outline_color = QColor("#ff5252")
            fill_color = QColor(255, 82, 82, 18)

            # Draw border with cosmetic pen (constant pixel width on screen)
            border_px = 2.2 if not self.isSelected() else 2.8
            border_pen = QPen(outline_color, border_px, Qt.PenStyle.SolidLine)
            border_pen.setCosmetic(True)
            painter.setBrush(QBrush(fill_color))
            painter.setPen(border_pen)
            corner_r = min(rect.width(), rect.height()) * 0.08
            painter.drawRoundedRect(rect, corner_r, corner_r)

            # ── Draw name text in screen coordinates so it always fits ──
            display_name = self.device_name
            if "_" in display_name and not self._is_dummy:
                display_name = display_name.split("_")[0]

            # Map the item rect to screen (device) coordinates
            xform = painter.transform()
            screen_rect = xform.mapRect(rect)
            # Inset by a few pixels for padding
            pad = 4
            text_rect = screen_rect.adjusted(pad, pad, -pad, -pad)

            if text_rect.width() > 4 and text_rect.height() > 4:
                painter.save()
                painter.resetTransform()

                # Start with a reasonable font, shrink to fit
                font_size = max(6, min(14, int(text_rect.height() * 0.45)))
                name_font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
                painter.setFont(name_font)
                fm = painter.fontMetrics()
                while font_size > 5 and (fm.horizontalAdvance(display_name) > text_rect.width()
                                         or fm.height() > text_rect.height()):
                    font_size -= 1
                    name_font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
                    painter.setFont(name_font)
                    fm = painter.fontMetrics()

                painter.setPen(QColor("#ffffff"))
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    display_name,
                )
                painter.restore()

            if self._match_color is not None:
                lock_pen = QPen(self._match_color, 2.4)
                lock_pen.setCosmetic(True)
                painter.setPen(lock_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, corner_r, corner_r)

            # ── Net labels in outline mode (Scene-space, auto-scale to fit) ──
            if self._show_net_labels and self._net_names:
                labels_data = []
                for term in ("D", "G", "S"):
                    net = self._net_names.get(term)
                    if net:
                        labels_data.append((f"{term}:{net}", net))
                
                if labels_data:
                    # Calculate font size that fits the rect height (since rotated)
                    # Rect height is the available length for the rotated string
                    avail_len = h * 0.8
                    avail_thick = w / (len(labels_data) + 1)
                    
                    # Start with a reasonable size and shrink to fit
                    fs = max(0.5, h * 0.15)
                    net_font = QFont("Segoe UI", fs, QFont.Weight.ExtraBold)
                    net_font.setStretch(120)
                    painter.setFont(net_font)
                    fm = painter.fontMetrics()
                    
                    # Shrink loop
                    while fs > 0.1 and (fm.height() > avail_thick or fm.horizontalAdvance(labels_data[0][0]) > avail_len):
                        fs *= 0.9
                        net_font.setPointSizeF(fs)
                        painter.setFont(net_font)
                        fm = painter.fontMetrics()

                    center_pt = rect.center()
                    total_labels_thick = fm.height() * len(labels_data)
                    start_x = center_pt.x() - (total_labels_thick / 2)
                    
                    for idx, (lbl, net_str) in enumerate(labels_data):
                        painter.save()
                        lx = start_x + idx * fm.height() + fm.height()/2
                        ly = center_pt.y()
                        painter.translate(lx, ly)
                        painter.rotate(-90)
                        
                        # Use a taller rect to ensure perfect vertical centering within the column thick
                        rect_lbl = QRectF(-avail_len/2, -avail_thick/2, avail_len, avail_thick)
                        self._draw_net_focus_frame(
                            painter, rect_lbl, net_str,
                            radius=max(1.0, avail_thick * 0.18),
                        )
                        
                        # ── Stronger Omni-directional Glow ──
                        glow_off = fs * 0.05
                        glow_alpha = 90 if self._net_focus_state(net_str) == "dim" else 200
                        painter.setPen(QColor(0, 0, 0, glow_alpha))
                        for dx, dy in [(-glow_off,0), (glow_off,0), (0,-glow_off), (0,glow_off)]:
                            painter.drawText(rect_lbl.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, lbl)
                        
                        # Main text
                        painter.setPen(self._net_display_color(net_str))
                            
                        painter.drawText(rect_lbl, Qt.AlignmentFlag.AlignCenter, lbl)
                        painter.restore()

            return

        w    = rect.width()
        h    = rect.height()
        x0   = rect.x()
        y0   = rect.y()
        cx   = x0 + w / 2.0
        cy   = y0 + h / 2.0
        corner_r = min(4.0, w * 0.08, h * 0.08)

        num_fingers = self.nf
        num_sd      = num_fingers + 1   # S/D diffusion regions

        # --- Visual proportions ---
        total_regions = num_fingers + num_sd
        part_w = w / total_regions
        gate_w = part_w
        sd_w   = part_w

        # S/D identity per column (before flip)
        def _is_source_col(col):
            return (col % 2 == 0) ^ self._flip_h

        # ── Draw filled sections (with flip transform) ─────────────
        painter.save()
        painter.translate(cx, cy)
        painter.scale(-1.0 if self._flip_h else 1.0,
                       -1.0 if self._flip_v else 1.0)
        painter.translate(-cx, -cy)

        # ── Background gradient fill for the entire device ──────────
        bg_grad = QLinearGradient(x0, y0, x0, y0 + h)
        bg_top = QColor(self._source_color)
        bg_top.setAlpha(120)
        bg_bot = QColor(self._drain_color)
        bg_bot.setAlpha(80)
        bg_grad.setColorAt(0.0, bg_top)
        bg_grad.setColorAt(1.0, bg_bot)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg_grad))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), corner_r, corner_r)

        # ── Draw S/D diffusion regions and gate fingers ──────────────
        painter.setPen(Qt.PenStyle.NoPen)
        cursor_x = x0
        for i in range(num_sd):
            term = "S" if _is_source_col(i) else "D"
            net = self._net_names.get(term)
            
            # Use net color if colorize_nets is on and it's not a dummy
            if self._colorize_nets and net and not self._is_dummy:
                color = self._get_net_color(net)
            else:
                color = self._source_color if term == "S" else self._drain_color
            if self._net_focus_state(net) == "focus":
                color = QColor("#facc15")
            elif self._net_focus_state(net) == "dim":
                color = QColor(color)
                color.setAlpha(80)
                
            sd_grad = QLinearGradient(cursor_x, y0, cursor_x, y0 + h)
            sd_grad.setColorAt(0.0, color.lighter(110))
            sd_grad.setColorAt(1.0, color)
            painter.setBrush(QBrush(sd_grad))
            draw_rect = QRectF(cursor_x, y0, sd_w, h).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRect(draw_rect)
            self._draw_net_focus_frame(painter, draw_rect, net, radius=1.5)
            
            # --- Net Label (Detailed) ---
            if self._show_net_labels and self._net_names:
                net = self._net_names.get(term)
                if net:
                    # Use the center of the actual drawn rectangle for perfect alignment
                    col_center = draw_rect.center()
                    
                    # Dynamic font size
                    fs = max(0.2, sd_w * 0.5)
                    net_font = QFont("Segoe UI", fs, QFont.Weight.ExtraBold)
                    net_font.setStretch(120)
                    painter.save()
                    painter.setFont(net_font)
                    fm = painter.fontMetrics()
                    while fs > 0.05 and (fm.horizontalAdvance(net) > h * 0.8 or fm.height() > sd_w * 0.9):
                        fs *= 0.9
                        net_font.setPointSizeF(fs)
                        painter.setFont(net_font)
                        fm = painter.fontMetrics()

                    painter.translate(col_center)
                    painter.rotate(-90)
                    
                    # Full column rect for alignment
                    rect_lbl = QRectF(-h*0.45, -sd_w/2, h*0.9, sd_w)
                    
                    glow_off = fs * 0.05
                    painter.setPen(QColor(0, 0, 0, 200))
                    for dx, dy in [(-glow_off,0), (glow_off,0), (0,-glow_off), (0,glow_off)]:
                        painter.drawText(rect_lbl.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, net)
                    
                    painter.setPen(self._net_display_color(net))
                    painter.drawText(rect_lbl, Qt.AlignmentFlag.AlignCenter, net)
                    painter.restore()

            cursor_x += sd_w

            if i < num_fingers:
                # Gate strip
                g_net = self._net_names.get("G")
                if self._colorize_nets and g_net and not self._is_dummy:
                    g_color = self._get_net_color(g_net)
                    g_top = g_color.lighter(120)
                    g_bot = g_color.darker(110)
                else:
                    g_color = self._gate_color
                    g_top = self._gradient_top
                    g_bot = self._gradient_bottom
                if self._net_focus_state(g_net) == "focus":
                    g_color = QColor("#facc15")
                    g_top = QColor("#fde68a")
                    g_bot = QColor("#f59e0b")
                elif self._net_focus_state(g_net) == "dim":
                    g_color = QColor(g_color)
                    g_top = QColor(g_top)
                    g_bot = QColor(g_bot)
                    g_color.setAlpha(80)
                    g_top.setAlpha(80)
                    g_bot.setAlpha(80)
                
                gate_rect = QRectF(cursor_x, y0, gate_w, h)
                grad = QLinearGradient(gate_rect.topLeft(), gate_rect.bottomLeft())
                grad.setColorAt(0.0, g_top)
                grad.setColorAt(0.5, g_color)
                grad.setColorAt(1.0, g_bot)
                painter.setBrush(QBrush(grad))
                draw_gate_rect = gate_rect.adjusted(0.5, 0.5, -0.5, -0.5)
                painter.drawRect(draw_gate_rect)
                self._draw_net_focus_frame(painter, draw_gate_rect, g_net, radius=1.5)
                
                # --- Gate Net Label ---
                if self._show_net_labels and self._net_names:
                    g_net = self._net_names.get("G")
                    if g_net:
                        gate_center = draw_gate_rect.center()
                        fs = max(0.2, gate_w * 0.7)
                        net_font = QFont("Segoe UI", fs, QFont.Weight.ExtraBold)
                        net_font.setStretch(120)
                        painter.save()
                        painter.setFont(net_font)
                        fm = painter.fontMetrics()
                        while fs > 0.05 and (fm.horizontalAdvance(g_net) > h * 0.8 or fm.height() > gate_w * 0.9):
                            fs *= 0.9
                            net_font.setPointSizeF(fs)
                            painter.setFont(net_font)
                            fm = painter.fontMetrics()

                        painter.translate(gate_center)
                        painter.rotate(-90)
                        rect_lbl = QRectF(-h*0.45, -gate_w/2, h*0.9, gate_w)
                        
                        glow_off = fs * 0.05
                        painter.setPen(QColor(0, 0, 0, 200))
                        for dx, dy in [(-glow_off,0), (glow_off,0), (0,-glow_off), (0,glow_off)]:
                            painter.drawText(rect_lbl.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, g_net)

                        painter.setPen(self._net_display_color(g_net))
                        painter.drawText(rect_lbl, Qt.AlignmentFlag.AlignCenter, g_net)
                        painter.restore()

                cursor_x += gate_w

        # ── Rounded outer border with subtle shadow ──────────────────
        # Shadow layer (offset down-right by 1px)
        shadow_color = QColor(0, 0, 0, 40)
        painter.setPen(QPen(shadow_color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(1.5, 1.5, 0.5, 0.5), corner_r, corner_r)

        # Main border
        border_w = 1.8 if not self.isSelected() else 2.5
        painter.setPen(QPen(self._border, border_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), corner_r, corner_r)

        # ── Thin separator lines between S/D and gate columns ────────
        sep_pen = QPen(QColor(self._border.red(), self._border.green(),
                              self._border.blue(), 100), 0.6)
        painter.setPen(sep_pen)
        cursor_x = x0
        inset = max(2, h * 0.04)
        for i in range(num_fingers):
            cursor_x += sd_w
            painter.drawLine(QPointF(cursor_x, y0 + inset),
                             QPointF(cursor_x, y0 + h - inset))
            cursor_x += gate_w
            painter.drawLine(QPointF(cursor_x, y0 + inset),
                             QPointF(cursor_x, y0 + h - inset))

        # ── Dummy crosshatch overlay ─────────────────────────────────
        if self._is_dummy:
            hatch_color = QColor("#b2bec3")
            hatch_color.setAlpha(50)
            hatch_pen = QPen(hatch_color, 0.5)
            painter.setPen(hatch_pen)
            spacing = max(4, min(8, w * 0.15))
            x_cursor = x0
            while x_cursor < x0 + w:
                painter.drawLine(QPointF(x_cursor, y0),
                                 QPointF(x_cursor + h * 0.3, y0 + h))
                x_cursor += spacing

        # ── Text labels (always readable, no flip) ──────────────────
        sd_font_size   = max(4, min(9,  int(min(sd_w * 0.40, h * 0.22))))
        gate_font_size = max(4, min(9,  int(min(gate_w * 0.50, h * 0.22))))
        name_font_size = max(6, min(13, int(w * 0.09)))

        # ── S / D labels on each diffusion column ───────────────────
        sd_font = QFont("Segoe UI", sd_font_size, QFont.Weight.DemiBold)
        painter.setFont(sd_font)

        cursor_x = x0
        for i in range(num_sd):
            label = "S" if _is_source_col(i) else "D"
            col_rect = QRectF(cursor_x, y0, sd_w, h)
            painter.setPen(QColor(self._label_color.red(), self._label_color.green(),
                                  self._label_color.blue(), 200))
            painter.drawText(col_rect.adjusted(0, 0, 0, -2),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             label)
            cursor_x += sd_w
            if i < num_fingers:
                cursor_x += gate_w

        # ── G labels on each gate strip (hidden when nets are shown) ───────
        if not self._show_net_labels:
            g_font = QFont("Segoe UI", gate_font_size, QFont.Weight.DemiBold)
            painter.setFont(g_font)
            painter.setPen(self._terminal_label_color)

            cursor_x = x0 + sd_w
            for _ in range(num_fingers):
                gate_col_rect = QRectF(cursor_x, y0, gate_w, h)
                painter.drawText(gate_col_rect,
                                 Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                                 "G")
                cursor_x += gate_w + sd_w

        # ── Device name — pill badge in upper portion ────────────────
        # Compute display name (shorten FILLER_DUMMY_N_type to DUM_N)
        display_name = self.device_name
        if self._is_dummy:
            parts = self.device_name.split("_")
            # FILLER_DUMMY_3_nmos -> D3
            for j, p in enumerate(parts):
                if p.isdigit():
                    display_name = f"D{p}"
                    break
            else:
                display_name = "D"
        else:
            # Strip finger suffix (e.g. MM5_m1 -> MM5)
            if "_" in display_name:
                display_name = display_name.split("_")[0]

        # Auto-shrink font to fit name inside the device
        max_name_w = w * 0.85 - 8
        name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
        painter.setFont(name_font)
        fm = painter.fontMetrics()
        while name_font_size > 4 and fm.horizontalAdvance(display_name) > max_name_w:
            name_font_size -= 1
            name_font = QFont("Segoe UI", name_font_size, QFont.Weight.Bold)
            painter.setFont(name_font)
            fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(display_name) + 8
        text_h = fm.height() + 4
        pill_w = min(text_w, w * 0.85)
        pill_h = min(text_h, h * 0.32)
        pill_x = x0 + (w - pill_w) / 2.0
        pill_y = y0 + h * 0.08

        # Semi-transparent pill background
        pill_bg = QColor(0, 0, 0, 70) if not self._is_dummy else QColor(255, 255, 255, 70)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(pill_bg))
        pill_r = min(pill_h / 2.0, 6)
        painter.drawRoundedRect(QRectF(pill_x, pill_y, pill_w, pill_h), pill_r, pill_r)

        # Name text
        painter.setPen(self._name_color)
        painter.drawText(QRectF(pill_x, pill_y, pill_w, pill_h),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         display_name)

        # ── Type badge (N / P) bottom of G column ────────────────────
        type_label = "N" if self.device_type == "nmos" else "P"
        if self._is_dummy:
            type_label = "D"
            
        badge_h = h * 0.22
        badge_font = QFont("Segoe UI", max(4, int(badge_h * 0.6)), QFont.Weight.Bold)
        painter.setFont(badge_font)
        
        # We don't draw a background box for the type, just the text
        # in the G column bottom to match the user request.
        cursor_x = x0 + sd_w
        for _ in range(num_fingers):
            type_rect = QRectF(cursor_x, y0 + h - badge_h - 2, gate_w, badge_h)
            painter.setPen(self._terminal_label_color)
            painter.drawText(type_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, type_label)
            cursor_x += gate_w + sd_w

        painter.restore() # ── End of flipped device body (Balanced) ──────────

        # ── Manual abutment state (amber solid stripe) ────────────────────
        if self._manual_abut_left or self._manual_abut_right:
            ABUT_COLOR = QColor("#f39c12")    # amber
            ABUT_FILL  = QColor("#f39c12")
            ABUT_FILL.setAlpha(50)
            abut_w = max(3.5, sd_w * 0.20)

            if self._manual_abut_left:
                painter.setBrush(QBrush(ABUT_FILL))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(x0, y0, abut_w, h))
                painter.setPen(QPen(ABUT_COLOR, 3.0, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(x0 + 1.5, y0 + 3),
                                 QPointF(x0 + 1.5, y0 + h - 3))
                mid = y0 + h * 0.4
                painter.drawLine(QPointF(x0 + abut_w + 1, mid),
                                 QPointF(x0 + abut_w + 6, mid))
                mid2 = y0 + h * 0.6
                painter.drawLine(QPointF(x0 + abut_w + 1, mid2),
                                 QPointF(x0 + abut_w + 6, mid2))

            if self._manual_abut_right:
                painter.setBrush(QBrush(ABUT_FILL))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(x0 + w - abut_w, y0, abut_w, h))
                painter.setPen(QPen(ABUT_COLOR, 3.0, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(x0 + w - 1.5, y0 + 3),
                                 QPointF(x0 + w - 1.5, y0 + h - 3))
                mid = y0 + h * 0.4
                painter.drawLine(QPointF(x0 + w - abut_w - 1, mid),
                                 QPointF(x0 + w - abut_w - 6, mid))
                mid2 = y0 + h * 0.6
                painter.drawLine(QPointF(x0 + w - abut_w - 1, mid2),
                                 QPointF(x0 + w - abut_w - 6, mid2))

        # ── Selection highlight ──────────────────────────────────────
        if self.isSelected():
            sel_pen = QPen(QColor("#4fc3f7"), 2.5, Qt.PenStyle.SolidLine)
            painter.setPen(sel_pen)
            painter.setBrush(QBrush(QColor(79, 195, 247, 30)))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), corner_r, corner_r)

        # ── Matched-group (lock) highlight ───────────────────────────
        if self._match_color is not None:
            lock_pen = QPen(self._match_color, 2.5, Qt.PenStyle.SolidLine)
            painter.setPen(lock_pen)
            fill = QColor(self._match_color)
            fill.setAlpha(28)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), corner_r + 1, corner_r + 1)
            # Lock icon badge (top-right corner)
            lock_size = max(8, min(14, int(w * 0.10)))
            bx = x0 + w - lock_size - 2
            by = y0 + 2
            lock_font = QFont("Segoe UI", lock_size - 2, QFont.Weight.Bold)
            painter.setFont(lock_font)
            painter.setPen(self._match_color)
            painter.drawText(QRectF(bx, by, lock_size, lock_size),
                             Qt.AlignmentFlag.AlignCenter, "\U0001F512")

    def terminal_anchors(self):
        """Return scene positions for S, G, D terminal centers."""
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        x0 = rect.x()
        y0 = rect.y()

        num_fingers = self.nf
        num_sd = num_fingers + 1
        total_gate_w = w * 0.40
        total_sd_w = w * 0.60
        gate_w = total_gate_w / num_fingers
        sd_w = total_sd_w / num_sd

        mid_y = y0 + h / 2

        if self._flip_h:
            left_is_s = False
        else:
            left_is_s = True

        s_centers = []
        d_centers = []
        g_centers = []

        cursor_x = x0
        for i in range(num_sd):
            cx = cursor_x + sd_w / 2
            is_source = (i % 2 == 0)
            if left_is_s == is_source:
                s_centers.append(QPointF(cx, mid_y))
            else:
                d_centers.append(QPointF(cx, mid_y))
            cursor_x += sd_w

            if i < num_fingers:
                g_centers.append(QPointF(cursor_x + gate_w / 2, mid_y))
                cursor_x += gate_w

        s_anchor = s_centers[len(s_centers)//2] if s_centers else QPointF(x0, mid_y)
        d_anchor = d_centers[len(d_centers)//2] if d_centers else QPointF(x0+w, mid_y)
        g_anchor = g_centers[len(g_centers)//2] if g_centers else QPointF(x0+w/2, mid_y)

        return {
            "S": self.mapToScene(s_anchor),
            "G": self.mapToScene(g_anchor),
            "D": self.mapToScene(d_anchor),
        }

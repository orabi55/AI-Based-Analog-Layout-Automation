"""
hierarchy_group_item.py
=======================
Visual wrapper for hierarchical device groups (arrays, multipliers, fingers).

After AI placement, devices like MM5 (m=3, nf=4) consist of individual
finger DeviceItems.  HierarchyGroupItem wraps them as a single big
bounding box that can be dragged (moves all children together) and
double-clicked (descends/ascends the hierarchy).

Z-value is set BELOW DeviceItems so device drag events are not blocked.
"""

from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsItem
from PySide6.QtGui import QBrush, QPen, QColor, QFont, QPainter
from PySide6.QtCore import Qt, QRectF, QObject, Signal, QPointF


class HierarchyGroupSignals(QObject):
    """Helper QObject so HierarchyGroupItem can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()
    position_changed = Signal(object)
    descend_requested = Signal(object)
    ascend_requested = Signal(object)
    clicked = Signal(object)  # emitted on release when not dragging


class HierarchyGroupItem(QGraphicsRectItem):
    """
    Draggable bounding box around a group of DeviceItems.

    - Drag the header bar or empty parts of the box → moves all children
    - Click/drag on a DeviceItem → moves that device normally (Z-order ensures
      DeviceItems are ABOVE this overlay so they catch events first)
    - Double-click the header bar → descend / ascend hierarchy
    """

    def __init__(self, parent_name, device_items, hierarchy_info,
                 color=None, border_color=None):
        self._device_items = list(device_items)  # Direct child devices
        self._hierarchy_info = hierarchy_info
        self._parent_name = parent_name

        # Compute bounding box from children (or use a default size if no devices)
        union = QRectF()
        if device_items:
            union = device_items[0].sceneBoundingRect()
            for it in device_items[1:]:
                union = union.united(it.sceneBoundingRect())
        else:
            # Default size for symbolic view (no devices visible yet)
            union = QRectF(0, 0, 120, 80)

        # Header height for click detection
        self._header_height = min(20.0, union.height() * 0.35)
        if self._header_height < 12:
            self._header_height = 12

        super().__init__(0, 0, union.width(), union.height())
        self.setPos(union.x(), union.y())

        self.signals = HierarchyGroupSignals()

        # Colors - default red border for symbolic view
        self._fill_color = color or QColor(30, 40, 60, 60)
        self._border_color = border_color or QColor(220, 60, 60, 200)  # Red border

        # State
        self._drag_active = False
        self._drag_start_pos = self.pos()
        self._last_pos = self.pos()
        self._is_descended = False
        self._child_groups = []
        self._parent_group = None
        # Net label overlay (toggled from Nets tab)
        self._show_net_labels = False
        self._net_names = {}       # {"D": "VDD", "G": "clk", "S": "VSS"}
        self._net_color_seed = 0
        self._highlighted_net = None
        self._updating_geometry = False
        self._snap_grid_x = None
        self._snap_grid_y = None

        # Build a flat list of ALL descendant device items (recursive)
        self._all_descendant_devices = self._collect_all_descendant_devices()

        # Flags — movable, selectable, below DeviceItems in Z-order
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(-1)  # BELOW DeviceItems

        self.setVisible(True)
        
        # CRITICAL: When created, hide all child devices (symbolic view)
        # They will only be visible when this group is descended
        self._update_child_visibility()

    def set_net_labels(self, net_names: dict, seed: int = 0):
        """Enable and store net names for D, G, S terminals."""
        self._show_net_labels = True
        self._net_names = net_names
        self._net_color_seed = seed
        self.update()

    def clear_net_labels(self):
        """Hide net name labels."""
        self._show_net_labels = False
        self._net_names = {}
        self.update()

    def set_highlighted_net(self, net_name):
        self._highlighted_net = str(net_name) if net_name else None
        self.update()

    def clear_highlighted_net(self):
        self._highlighted_net = None
        self.update()

    def _net_focus_state(self, net_name):
        if not self._highlighted_net or not net_name:
            return "normal"
        return "focus" if str(net_name) == self._highlighted_net else "dim"

    def _net_label_color(self, net_name):
        state = self._net_focus_state(net_name)
        if state == "focus":
            return QColor("#111827")
        color = self._get_net_color(net_name)
        if state == "dim":
            color.setAlpha(70)
        return color

    def _get_net_color(self, net_name):
        """Consistent unique color for nets (shared logic with DeviceItem)."""
        if not net_name or net_name == "?":
            return QColor("#808896")
        
        # Power/Ground specific colors
        pnet = str(net_name).upper()
        if pnet in ("VDD", "VCC", "AVDD", "DVDD"):
            return QColor("#ffaa66")
        if pnet in ("VSS", "GND", "AVSS", "DVSS"):
            return QColor("#66aaff")
            
        input_str = f"{net_name}_{self._net_color_seed}"
        import hashlib
        h = int(hashlib.md5(net_name.encode()).hexdigest(), 16) % 360
        c = QColor()
        c.setHsl(h, 200, 180)
        return c

    def _collect_all_descendant_devices(self):
        """Recursively collect all device items from child groups."""
        devices = list(self._device_items)
        for child_group in self._child_groups:
            devices.extend(child_group._all_descendant_devices)
        return devices

    def _compute_mosfet_type_label(self):
        """Inspect descendants and return 'N', 'P', 'N+P', or ''."""
        kinds = set()
        for d in self._all_descendant_devices:
            t = str(getattr(d, "device_type", "")).lower()
            if t == "nmos":
                kinds.add("N")
            elif t == "pmos":
                kinds.add("P")
            if len(kinds) == 2:
                break
        if not kinds:
            return ""
        if len(kinds) == 1:
            return next(iter(kinds))
        return "N+P"

    def get_all_descendant_devices(self):
        """Return all device items that are descendants of this group."""
        return self._all_descendant_devices

    def _update_child_visibility(self):
        """Update visibility of child devices and groups based on descent state."""
        if self._is_descended:
            # When descended: hide this group, show children
            self.setVisible(False)
            # Show child groups if they exist, otherwise show devices
            if self._child_groups:
                for child in self._child_groups:
                    child._update_child_visibility()
            else:
                for dev in self._device_items:
                    dev.setVisible(True)
        else:
            # When NOT descended: show this group, hide children
            self.setVisible(True)
            # Hide child groups and devices
            for child in self._child_groups:
                child.setVisible(False)
                for dev in child._all_descendant_devices:
                    dev.setVisible(False)
            for dev in self._device_items:
                # CRITICAL: Do NOT change device position when hiding!
                # Just change visibility flag - position must stay intact
                dev.setVisible(False)

    def has_children(self):
        return bool(self._child_groups)

    def descend(self):
        """Descend into this group - hide group, show children/devices."""
        # Allow descend if has child groups OR direct devices
        if not self.has_children() and not self._device_items:
            return
        self._is_descended = True
        self._update_child_visibility()
        self.signals.descend_requested.emit(self)

    def ascend(self):
        """Ascend from this group - show group, hide children."""
        self._is_descended = False
        self._update_child_visibility()
        self.signals.ascend_requested.emit(self)

    def set_child_groups(self, child_groups):
        """Set child groups and rebuild descendant list."""
        self._child_groups = child_groups
        for child in self._child_groups:
            child._parent_group = self
        self._all_descendant_devices = self._collect_all_descendant_devices()
        # Update visibility based on current descent state
        self._update_child_visibility()

    def set_snap_grid(self, grid_x, grid_y=None):
        """Use the same movement grid as DeviceItem while dragging red groups."""
        self._snap_grid_x = float(grid_x) if grid_x else None
        self._snap_grid_y = float(grid_y) if grid_y else self._snap_grid_x
        for child in self._child_groups:
            if hasattr(child, "set_snap_grid"):
                child.set_snap_grid(grid_x, grid_y)

    def update_geometry(self):
        """Recompute this group's rectangle from child groups/devices."""
        for child in self._child_groups:
            child.update_geometry()

        items = self._child_groups if self._child_groups else self._device_items
        if not items:
            return

        union = QRectF()
        for item in items:
            union = union.united(item.sceneBoundingRect())

        if union.isNull():
            return

        self._updating_geometry = True
        try:
            self.setPos(union.x(), union.y())
            # The bounding rect needs to be in local coordinates
            # Since pos is union.topLeft(), the local rect is from 0,0
            self.setRect(0, 0, union.width(), union.height())
            self._last_pos = self.pos()
            self._drag_start_pos = self.pos()
            self._header_height = min(20.0, union.height() * 0.35)
            if self._header_height < 12:
                self._header_height = 12
            self._all_descendant_devices = self._collect_all_descendant_devices()
            self._update_child_visibility()
        finally:
            self._updating_geometry = False

    def _is_in_header(self, pos):
        """Check if a local position is in the header bar."""
        return pos.y() <= self._header_height

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and not self._updating_geometry
            and self._snap_grid_x
            and self._snap_grid_y
        ):
            x = round(value.x() / self._snap_grid_x) * self._snap_grid_x
            y = round(value.y() / self._snap_grid_y) * self._snap_grid_y
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            new_pos = self.pos()
            if self._updating_geometry:
                self._last_pos = new_pos
                return super().itemChange(change, value)
            delta = new_pos - self._last_pos
            if not delta.isNull():
                for dev in self._device_items:
                    dev.setPos(dev.pos() + delta)
                for child in self._child_groups:
                    child.setPos(child.pos() + delta)
                self._last_pos = new_pos
            self.signals.position_changed.emit(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self.pos()
            self._last_pos = self.pos()
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if not self._drag_active and self.pos() != self._drag_start_pos:
            self._drag_active = True
            self.signals.drag_started.emit()

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self.signals.drag_finished.emit()
            self._drag_start_pos = self.pos()
        else:
            if event.button() == Qt.MouseButton.LeftButton:
                self.signals.clicked.emit(self)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-click header bar to descend/ascend."""
        if self._is_in_header(event.pos()):
            # If already descended and has a parent, ascend to parent
            if self._is_descended and self._parent_group:
                self._parent_group.ascend()
                event.accept()
                return
            # If has children or devices, descend
            if self._child_groups or self._device_items:
                self.descend()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def paint(self, painter, option, widget=None):
        if not self.isVisible():
            return

        painter.save()  # Required: DontSavePainterState optimization is enabled
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        is_selected = self.isSelected()

        # Use the stored border color (per-group palette); fall back to red.
        base_border = QColor(self._border_color) if self._border_color else QColor(220, 60, 60, 255)
        base_border.setAlpha(255)
        border_color = base_border
        border_width = 2.5

        if is_selected:
            border_color = base_border.lighter(135)
            border_color.setAlpha(255)
            border_width = 3.5

        # Translucent fill from stored palette color (more opaque when selected).
        fill = QColor(self._fill_color) if self._fill_color else QColor(base_border)
        fill.setAlpha(85 if is_selected else 55)
        painter.setBrush(QBrush(fill))

        pen = QPen(border_color, border_width, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(rect)

        # ── Draw device name in screen coordinates so it fits inside ──
        xform = painter.transform()
        screen_rect = xform.mapRect(rect)
        pad = 4
        text_rect = screen_rect.adjusted(pad, pad, -pad, -pad)

        if text_rect.width() > 4 and text_rect.height() > 4:
            painter.save()
            painter.resetTransform()

            # Start with a reasonable font, shrink to fit
            font_size = max(6, min(16, int(min(text_rect.width(), text_rect.height()) * 0.45)))
            font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
            painter.setFont(font)
            fm = painter.fontMetrics()
            type_label = self._compute_mosfet_type_label()
            badge_size = max(6, int(font_size * 0.72))
            badge_font = QFont("Segoe UI", badge_size, QFont.Weight.Bold)

            def _combined_width():
                if not type_label:
                    return fm.horizontalAdvance(self._parent_name)
                painter.setFont(badge_font)
                bw = painter.fontMetrics().horizontalAdvance(type_label) + 10
                painter.setFont(font)
                return fm.horizontalAdvance(self._parent_name) + 8 + bw

            while font_size > 5 and (_combined_width() > text_rect.width()
                                     or fm.height() > text_rect.height()):
                font_size -= 1
                font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
                badge_size = max(6, int(font_size * 0.72))
                badge_font = QFont("Segoe UI", badge_size, QFont.Weight.Bold)
                painter.setFont(font)
                fm = painter.fontMetrics()

            if not type_label:
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._parent_name)
            else:
                # Layout name + pill side-by-side, centered together.
                name_w = fm.horizontalAdvance(self._parent_name)
                painter.setFont(badge_font)
                bfm = painter.fontMetrics()
                pill_w = bfm.horizontalAdvance(type_label) + 10
                pill_h = bfm.height() + 2
                spacing = 8
                total_w = name_w + spacing + pill_w
                start_x = text_rect.center().x() - total_w / 2

                painter.setFont(font)
                painter.setPen(QPen(QColor("#ffffff")))
                from PySide6.QtCore import QRectF as _QR
                name_rect = _QR(start_x, text_rect.y(), name_w, text_rect.height())
                painter.drawText(
                    name_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    self._parent_name,
                )

                if type_label == "N":
                    pill_color = QColor("#3a7bd5")
                elif type_label == "P":
                    pill_color = QColor("#d54a4a")
                else:
                    pill_color = QColor("#9b85d6")

                pill_rect = _QR(
                    start_x + name_w + spacing,
                    text_rect.center().y() - pill_h / 2,
                    pill_w,
                    pill_h,
                )
                painter.setBrush(QBrush(pill_color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)
                painter.setFont(badge_font)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, type_label)

            # ── Net labels centered and rotated (Scene-space, auto-scale) ─────────────
            if self._show_net_labels and self._net_names:
                # Collect labels
                labels_data = []
                for term in ("D", "G", "S"):
                    net = self._net_names.get(term)
                    if net:
                        labels_data.append((f"{term}:{net}", net))
                
                if labels_data:
                    # Target scene-space size
                    fs = max(0.4, h * 0.12)
                    net_font = QFont("Segoe UI", fs, QFont.Weight.ExtraBold)
                    net_font.setStretch(120)
                    painter.setFont(net_font)
                    fm = painter.fontMetrics()
                    
                    # Shrink to fit the block width/height
                    avail_thick = w / (len(labels_data) + 1)
                    avail_len = h * 0.7
                    while fs > 0.05 and (fm.height() > avail_thick or fm.horizontalAdvance(labels_data[0][0]) > avail_len):
                        fs *= 0.9
                        net_font.setPointSizeF(fs)
                        painter.setFont(net_font)
                        fm = painter.fontMetrics()

                    center_pt = rect.center()
                    total_labels_thick = fm.height() * len(labels_data)
                    start_x = center_pt.x() - (total_labels_thick / 2)
                    
                    # Shift down slightly from parent name
                    offset_y = h * 0.15
                    
                    for idx, (lbl, net_str) in enumerate(labels_data):
                        painter.save()
                        lx = start_x + idx * fm.height() + fm.height()/2
                        ly = center_pt.y() + offset_y
                        painter.translate(lx, ly)
                        painter.rotate(-90)
                        
                        tw = fm.horizontalAdvance(lbl)
                        th = fm.height()
                        rect_lbl = QRectF(-tw/2, -th/2, tw, th)
                        focus_state = self._net_focus_state(net_str)
                        if focus_state == "focus":
                            fill = QColor("#facc15")
                            fill.setAlpha(180)
                            painter.setBrush(QBrush(fill))
                            painter.setPen(QPen(QColor("#f59e0b"), 2.0))
                            painter.drawRoundedRect(
                                rect_lbl.adjusted(-4, -2, 4, 2),
                                3,
                                3,
                            )
                        elif focus_state == "dim":
                            fill = QColor("#0b0f16")
                            fill.setAlpha(110)
                            painter.setBrush(QBrush(fill))
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.drawRoundedRect(
                                rect_lbl.adjusted(-3, -1, 3, 1),
                                3,
                                3,
                            )
                        
                        # Omni-glow
                        glow_off = fs * 0.05
                        glow_alpha = 90 if focus_state == "dim" else 200
                        painter.setPen(QColor(0, 0, 0, glow_alpha))
                        for dx, dy in [(-glow_off,0), (glow_off,0), (0,-glow_off), (0,glow_off)]:
                            painter.drawText(rect_lbl.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, lbl)
                        
                        painter.setPen(self._net_label_color(net_str))
                        painter.drawText(rect_lbl, Qt.AlignmentFlag.AlignCenter, lbl)
                        painter.restore()

            painter.restore()

        painter.restore()  # Balance top-level save()

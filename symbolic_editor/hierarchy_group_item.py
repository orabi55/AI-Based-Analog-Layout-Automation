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
from PySide6.QtCore import Qt, QRectF, QObject, Signal


class HierarchyGroupSignals(QObject):
    """Helper QObject so HierarchyGroupItem can emit signals."""
    drag_started = Signal()
    drag_finished = Signal()
    position_changed = Signal(object)
    descend_requested = Signal(object)
    ascend_requested = Signal(object)


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
                    child.setVisible(True)
            else:
                for dev in self._device_items:
                    dev.setVisible(True)
        else:
            # When NOT descended: show this group, hide children
            self.setVisible(True)
            # Hide child groups and devices
            for child in self._child_groups:
                child.setVisible(False)
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

    def _is_in_header(self, pos):
        """Check if a local position is in the header bar."""
        return pos.y() <= self._header_height

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            new_pos = self.pos()
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

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        w = rect.width()
        h = rect.height()
        is_selected = self.isSelected()

        # Simple empty rectangle with RED border (cosmetic pen = constant px on screen)
        border_color = QColor(220, 60, 60, 255)  # Pure red
        border_width = 2.5

        if is_selected:
            border_color = QColor(255, 100, 100, 255)  # Lighter red when selected
            border_width = 3.5

        # Empty fill (transparent inside)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))

        # Red border — cosmetic so it stays constant pixel width on screen
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
            while font_size > 5 and (fm.horizontalAdvance(self._parent_name) > text_rect.width()
                                     or fm.height() > text_rect.height()):
                font_size -= 1
                font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
                painter.setFont(font)
                fm = painter.fontMetrics()

            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._parent_name)

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

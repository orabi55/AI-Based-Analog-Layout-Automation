"""
Symbolic Editor — the central QGraphicsView canvas for
interactive device placement, routing visualisation, and
grid-snapped editing.
"""

import math

from PySide6.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsItem,
    QMenu,
)
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPainter, QPen, QPainterPath, QColor, QBrush, QFont

from device_item import DeviceItem


class SymbolicEditor(QGraphicsView):

    device_clicked = Signal(str)
    optimize_2d_requested = Signal()   # emitted from context-menu or auto-load
    edit_terminals_requested = Signal(str)  # emitted with device ID

    def __init__(self):
        super().__init__()

        # Create scene
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.scene.changed.connect(self._on_scene_changed)

        # Better rendering
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Enable caching to speed up grid drawing
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)

        # Enable selection box
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # Enable pan with middle mouse
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        # Zoom parameters
        self.zoom_factor = 1.15
        self._zoom_level = 1.0

        # Device items lookup by id
        self.device_items = {}

        # Connectivity data
        self._edges = []          # raw edge list from JSON
        self._conn_map = {}       # device_id -> [(other_id, net_name), ...]
        self._conn_lines = []     # active QGraphicsPathItem items
        self._terminal_nets = {}  # {dev_id: {'D': net, 'G': net, 'S': net}}
        self._group_items   = []  # Legacy - no longer used (devices render abut states directly)
        self._shared_net_labels = []  # Text overlays for shared net names between abutted devices
        self._terminal_highlights = []  # Highlight overlays for specific terminal areas

        # Net color palette
        self._net_colors = {
            '__palette': [
                QColor("#e74c3c"),  # red
                QColor("#3498db"),  # blue
                QColor("#2ecc71"),  # green
                QColor("#9b59b6"),  # purple
                QColor("#f39c12"),  # orange
                QColor("#1abc9c"),  # teal
                QColor("#e67e22"),  # dark orange
                QColor("#e84393"),  # pink
                QColor("#00cec9"),  # cyan
                QColor("#6c5ce7"),  # indigo
            ]
        }

        # Grid settings
        self._grid_size = 20   # base grid spacing in scene coords
        self._grid_color = QColor("#dce1e8")
        self._grid_color_major = QColor("#b8c0cc")
        self._snap_grid = self._grid_size
        self._row_pitch = self._grid_size * 3

        # Dummy placement mode
        self._dummy_mode = False
        self._dummy_preview = None
        self._dummy_place_callback = None

        # When True, skip compaction in set_terminal_nets
        self._skip_compaction = False

        # Virtual grid extents (shown as empty slots when > actual device count)
        self._virtual_row_count = 0
        self._virtual_col_count = 0

        # Custom row gap override (None = auto)
        self._custom_row_gap = None

        # Completely disable scrollbars (policy is more reliable than CSS)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.setStyleSheet("""
            QGraphicsView {
                border: none;
                background-color: #0e1219;
            }
        """)

    @staticmethod
    def _norm_net_name(net):
        """Normalize net names for robust equality checks."""
        if net is None:
            return ""
        return str(net).strip().upper()

    def _visual_side_net(self, item, side, flip_h=None):
        """Return net name seen on visual left/right side for an item."""
        nets = self._terminal_nets.get(item.dev_id, {}) or {}
        if not nets:
            return ""

        if flip_h is None:
            flip_h = bool(getattr(item, "is_flip_h", lambda: False)())

        side = str(side).lower().strip()
        if side == "left":
            term = "D" if flip_h else "S"
        elif side == "right":
            term = "S" if flip_h else "D"
        else:
            return ""
        return self._norm_net_name(nets.get(term, ""))

    def _pair_interface_score(self, left_item, left_flip, right_item, right_flip):
        """Score diffusion continuity across one abutted boundary.

        A high score is given when the right terminal net of the left device
        equals the left terminal net of the right device, regardless of whether
        that shared net is a supply net or an internal signal.
        """
        left_boundary = self._visual_side_net(left_item, "right", flip_h=left_flip)
        right_boundary = self._visual_side_net(right_item, "left", flip_h=right_flip)
        if not left_boundary or not right_boundary:
            return 0

        if left_boundary == right_boundary:
            # Strongly reward exact boundary continuity.
            return 40

        # Smaller reward when devices still share some S/D-related connectivity.
        left_nets = {
            self._norm_net_name(v)
            for k, v in (self._terminal_nets.get(left_item.dev_id, {}) or {}).items()
            if k in ("S", "D") and v
        }
        right_nets = {
            self._norm_net_name(v)
            for k, v in (self._terminal_nets.get(right_item.dev_id, {}) or {}).items()
            if k in ("S", "D") and v
        }
        return 6 if left_nets & right_nets else 0

    def _optimize_row_flips(self, ordered_items):
        """Choose horizontal flips that maximize boundary net continuity."""
        if len(ordered_items) <= 1:
            return
        if not self._terminal_nets:
            return

        # Dynamic programming over binary flip states per device.
        dp = [{False: (-1e9, None), True: (-1e9, None)} for _ in ordered_items]
        first = ordered_items[0]
        cur_first_flip = bool(getattr(first, "is_flip_h", lambda: False)())
        dp[0][False] = (0.0 if not cur_first_flip else -0.2, None)
        dp[0][True] = (0.0 if cur_first_flip else -0.2, None)

        for i in range(1, len(ordered_items)):
            prev = ordered_items[i - 1]
            cur = ordered_items[i]
            cur_is_flip = bool(getattr(cur, "is_flip_h", lambda: False)())
            for cur_flip in (False, True):
                best_score = -1e9
                best_prev_flip = None
                for prev_flip in (False, True):
                    prev_score = dp[i - 1][prev_flip][0]
                    pair_score = self._pair_interface_score(
                        prev, prev_flip, cur, cur_flip
                    )
                    # Small penalty for orientation churn.
                    change_penalty = 0.0 if cur_flip == cur_is_flip else 0.2
                    score = prev_score + pair_score - change_penalty
                    if score > best_score:
                        best_score = score
                        best_prev_flip = prev_flip
                dp[i][cur_flip] = (best_score, best_prev_flip)

        # Backtrack best terminal state.
        last_false = dp[-1][False][0]
        last_true = dp[-1][True][0]
        flips = [False] * len(ordered_items)
        flips[-1] = True if last_true > last_false else False
        for i in range(len(ordered_items) - 1, 0, -1):
            flips[i - 1] = dp[i][flips[i]][1]

        for item, flip in zip(ordered_items, flips):
            if hasattr(item, "set_flip_h"):
                item.set_flip_h(bool(flip))

    def set_dummy_mode(self, enabled):
        """Enable/disable click-to-place dummy mode."""
        self._dummy_mode = bool(enabled)
        self.setMouseTracking(self._dummy_mode)
        self.viewport().setMouseTracking(self._dummy_mode)
        if not self._dummy_mode:
            self._clear_dummy_preview()

    def set_dummy_place_callback(self, callback):
        """Callback called with candidate dict when user places a dummy."""
        self._dummy_place_callback = callback

    def _snap_value(self, value):
        return round(value / self._snap_grid) * self._snap_grid

    def _snap_row(self, value):
        return round(value / self._row_pitch) * self._row_pitch

    def _snap_point(self, x, y):
        return QPointF(self._snap_value(x), self._snap_row(y))

    def _on_scene_changed(self, _regions):
        """Keep occupancy guides fresh when devices move/add/remove."""
        self.resetCachedContent()

    def _compute_dummy_candidate(self, scene_pos, snap_to_free=True):
        """Build a preview candidate aligned to nearest NMOS/PMOS row.

        Args:
            scene_pos: cursor position in scene coordinates.
            snap_to_free: if True, snap x to nearest free slot;
                          if False, follow cursor x exactly (for preview).
        """
        type_items = {"nmos": [], "pmos": []}
        for item in self.device_items.values():
            dev_type = str(getattr(item, "device_type", "")).strip().lower()
            if dev_type in type_items:
                type_items[dev_type].append(item)

        if not type_items["nmos"] and not type_items["pmos"]:
            return None

        # Build actual occupied rows with their average Y and dominant type.
        rows = []
        for dev_type, items in type_items.items():
            if not items:
                continue
            avg_y = sum(it.pos().y() for it in items) / len(items)
            rows.append((dev_type, avg_y))

        # Pick the row closest to cursor Y.
        target_type, target_y = min(rows, key=lambda r: abs(scene_pos.y() - r[1]))

        ref_item = type_items[target_type][0]
        width = ref_item.rect().width()
        height = ref_item.rect().height()
        if snap_to_free:
            # Dummies go at the ROW EDGES only (left or right end)
            row_items = type_items[target_type]
            row_y_snap = self._snap_row(target_y)

            # Find current leftmost and rightmost positions in this row
            row_xs = [it.pos().x() for it in row_items
                      if self._snap_row(it.pos().y()) == row_y_snap]

            if row_xs:
                leftmost = min(row_xs)
                rightmost = max(row_xs)
                row_center = (leftmost + rightmost) / 2.0

                # Pick nearest edge based on cursor position
                if scene_pos.x() <= row_center:
                    # Place at left edge: one slot before the leftmost device
                    target_x = leftmost - self._snap_grid
                else:
                    # Place at right edge: one slot after the rightmost device
                    ref_rightmost = [it for it in row_items
                                     if abs(it.pos().x() - rightmost) < 1]
                    right_w = ref_rightmost[0].rect().width() if ref_rightmost else width
                    target_x = rightmost + right_w
            else:
                target_x = self._snap_value(scene_pos.x())

            x = self.find_nearest_free_x(
                row_y=target_y,
                width=width,
                target_x=self._snap_value(target_x),
                exclude_id=None,
            )
        else:
            x = self._snap_value(scene_pos.x())
        y = self._snap_row(target_y)
        return {
            "type": str(target_type).lower(),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def _clear_dummy_preview(self):
        if self._dummy_preview is not None:
            try:
                if self._dummy_preview.scene() is self.scene:
                    self.scene.removeItem(self._dummy_preview)
            except RuntimeError:
                pass
            self._dummy_preview = None

    def _update_dummy_preview(self, scene_pos):
        # The preview follows the cursor position (not snapped to a free slot)
        candidate = self._compute_dummy_candidate(scene_pos, snap_to_free=False)
        if not candidate:
            self._clear_dummy_preview()
            return

        # Use a real DeviceItem as preview so it looks identical to final placement
        if self._dummy_preview is None or not isinstance(self._dummy_preview, DeviceItem):
            self._dummy_preview = DeviceItem(
                "PREVIEW_DUMMY",
                "DUMMY",
                candidate["type"],
                candidate["x"],
                candidate["y"],
                candidate["width"],
                candidate["height"],
                is_dummy=True,
            )
            self._dummy_preview.setOpacity(0.55)
            self._dummy_preview.setZValue(1000)
            # Disable interaction on the preview ghost
            self._dummy_preview.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False
            )
            self._dummy_preview.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False
            )
            self._dummy_preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(self._dummy_preview)
        else:
            # Update type colours if we crossed a row boundary
            if getattr(self._dummy_preview, 'device_type', '') != candidate["type"]:
                self._clear_dummy_preview()
                self._update_dummy_preview(scene_pos)
                return

        self._dummy_preview.setPos(candidate["x"], candidate["y"])

    def _commit_dummy_at(self, scene_pos):
        if not self._dummy_place_callback:
            return False
        candidate = self._compute_dummy_candidate(scene_pos)
        if not candidate:
            return False
        self._dummy_place_callback(candidate)
        return True

    # -------------------------------------------------
    # Load AI JSON Placement
    # -------------------------------------------------
    def load_placement(self, nodes, compact=True):
        """Load placement from a list of node dicts.

        Args:
            nodes: list of node dicts with geometry.
            compact: if True (default), run abutted row compaction.
                     Set to False to preserve exact positions from data
                     (e.g. after an AI swap/move command).
        """
        self._clear_dummy_preview()
        self.scene.clear()
        self.device_items.clear()

        self.scale_factor = 80  # visual scaling
        widths = []
        heights = []

        for node in nodes:
            geom = node.get("geometry", {})

            x = geom.get("x", 0) * self.scale_factor
            # Layout JSON uses math convention: y increases upward.
            # Qt uses screen convention: y increases downward.
            # Negate y so PMOS (layout y=0) stays at top and
            # NMOS (layout y < 0) renders BELOW PMOS.
            y = -geom.get("y", 0) * self.scale_factor

            width = geom.get("width", 1) * self.scale_factor
            height = geom.get("height", 0.5) * self.scale_factor
            widths.append(width)
            heights.append(height)

            # Check if this is a dummy device
            is_dummy = node.get("is_dummy", False)

            item = DeviceItem(
                node.get("id", "unknown"),
                node.get("name", node.get("id", "unknown")),
                node.get("type", "nmos"),
                x,
                y,
                width,
                height,
                is_dummy=is_dummy,
            )

            orientation = str(geom.get("orientation", "R0")).upper()
            if hasattr(item, "set_flip_h"):
                item.set_flip_h("FH" in orientation)
            if hasattr(item, "set_flip_v"):
                item.set_flip_v("FV" in orientation)

            self.scene.addItem(item)
            self.device_items[node.get("id", "unknown")] = item

            # Mark items from 2D grid placement to preserve their order
            if "grid_row" in node:
                item._preserve_grid_order = True

        # Abutted rows horizontally + visible spacing between rows.
        if widths:
            min_w = min(widths)
            col_gap = 0.0
            self._snap_grid = max(1.0, min_w + col_gap)
        if heights:
            max_h = max(heights)
            # Rows are always touching: no vertical gap.
            row_gap = 0.0
            self._row_pitch = max(1.0, max_h + row_gap)

        for item in self.device_items.values():
            item.set_snap_grid(self._snap_grid, self._row_pitch)

        if compact:
            for item in self.device_items.values():
                item.setPos(self._snap_point(item.pos().x(), item.pos().y()))
            self._compact_rows_abutted()
        else:
            self._skip_compaction = True

        # Practically unlimited canvas.
        self.scene.setSceneRect(-1000000, -1000000, 2000000, 2000000)

    def get_updated_positions(self):
        """Return a dict mapping device id -> (x, y) in original coordinates."""
        positions = {}
        for dev_id, item in self.device_items.items():
            pos = item.pos()
            positions[dev_id] = (
                pos.x() / self.scale_factor,
                # Un-negate y to restore layout convention (y increases upward)
                -pos.y() / self.scale_factor,
            )
        return positions

    def _abut_pair_score(self, left_item, right_item):
        """Score how desirable it is to place left_item immediately before right_item."""
        left_nets = self._terminal_nets.get(left_item.dev_id, {})
        right_nets = self._terminal_nets.get(right_item.dev_id, {})
        if not left_nets or not right_nets:
            return 0

        best = 0
        for left_flip in (False, True):
            for right_flip in (False, True):
                score = self._pair_interface_score(
                    left_item, left_flip, right_item, right_flip
                )
                if score > best:
                    best = score
        return best

    def _order_row_items(self, items):
        """Order row items so net-sharing neighbors (especially D-common) abut.

        Dummy devices are always placed at the edges (left/right) of the row,
        never between real instances.
        """
        ordered_by_x = sorted(items, key=lambda it: it.pos().x())
        if len(ordered_by_x) <= 1 or not self._terminal_nets:
            return ordered_by_x

        # Separate dummies from real devices
        real_items = [it for it in ordered_by_x if not getattr(it, 'is_dummy', False)]
        left_dummies = []
        right_dummies = []

        if real_items:
            real_center = (min(it.pos().x() for it in real_items) +
                           max(it.pos().x() for it in real_items)) / 2.0
            for it in ordered_by_x:
                if getattr(it, 'is_dummy', False):
                    if it.pos().x() <= real_center:
                        left_dummies.append(it)
                    else:
                        right_dummies.append(it)
        else:
            return ordered_by_x

        # Order only real devices for net optimization
        with_nets = [
            it for it in real_items if self._terminal_nets.get(it.dev_id)
        ]
        if len(with_nets) < 2:
            return left_dummies + real_items + right_dummies

        remaining = list(real_items)

        def total_score(candidate):
            return sum(
                self._abut_pair_score(candidate, other)
                + self._abut_pair_score(other, candidate)
                for other in remaining
                if other is not candidate
            )

        seed = max(
            remaining,
            key=lambda it: (total_score(it), -abs(it.pos().x())),
        )
        row = [seed]
        remaining.remove(seed)

        while remaining:
            left_anchor = row[0]
            right_anchor = row[-1]
            best_item = None
            best_side = None
            best_rank = None

            for cand in remaining:
                score_left = self._abut_pair_score(cand, left_anchor)
                score_right = self._abut_pair_score(right_anchor, cand)
                if score_left > score_right:
                    side = "left"
                    score = score_left
                    anchor = left_anchor
                else:
                    side = "right"
                    score = score_right
                    anchor = right_anchor

                # Prefer higher net score, then closer current position to anchor.
                rank = (score, -abs(cand.pos().x() - anchor.pos().x()))
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_item = cand
                    best_side = side

            if best_side == "left":
                row.insert(0, best_item)
            else:
                row.append(best_item)
            remaining.remove(best_item)

        # If no useful net signal exists, keep geometric order for real items.
        adjacency_gain = sum(
            self._abut_pair_score(row[i], row[i + 1])
            for i in range(len(row) - 1)
        )
        if adjacency_gain <= 0:
            return left_dummies + real_items + right_dummies
        return left_dummies + row + right_dummies

    def _compact_rows_abutted(self, row_keys=None):
        """Pack row devices with net-aware diffusion sharing.

        When adjacent devices share a boundary net (right-side net of left
        device == left-side net of right device), they overlap by the
        diffusion width (30 % of device width) so the shared terminal
        appears once in the middle, e.g. {D G S G D}.
        """
        # Clear any existing group overlays so DeviceItems are all visible
        # before positions are read / recomputed.
        self._clear_abut_groups()
        rows = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            key = (getattr(item, "device_type", ""), row_y)
            if row_keys is not None and key not in row_keys:
                continue
            rows.setdefault(key, []).append(item)

        for (_, row_y), items in rows.items():
            if not items:
                continue
            # If items came from 2D grid placement, preserve backend ordering
            if any(getattr(it, '_preserve_grid_order', False) for it in items):
                ordered = sorted(items, key=lambda it: it.pos().x())
            else:
                ordered = self._order_row_items(items)
            self._optimize_row_flips(ordered)

            # Reset all boundary labels & z-values before placement
            base_z = 0.0
            for it in ordered:
                it.set_boundary_label_visibility(show_left=True, show_right=True)
                it.setZValue(base_z)

            x_cursor = self._snap_value(min(it.pos().x() for it in ordered))

            for idx, it in enumerate(ordered):
                it.setPos(x_cursor, row_y)
                dev_w = it.rect().width()
                diffusion_w = dev_w * 0.30
                span = max(1, int(math.ceil(dev_w / self._snap_grid)))
                advance = span * self._snap_grid  # default: full width

                # Check if next device shares a boundary net
                if idx < len(ordered) - 1:
                    nxt = ordered[idx + 1]
                    
                    # For real devices, we check the actual visual terminal sides
                    right_net = self._visual_side_net(it, "right")
                    left_net = self._visual_side_net(nxt, "left")

                    # Dummies always try to abut if their supply net matches
                    # the facing neighbor's net, even if _visual_side_net returns empty
                    if getattr(it, 'is_dummy', False) and not right_net:
                        dummy_nets = self._terminal_nets.get(it.dev_id, {})
                        right_net = dummy_nets.get("D") or dummy_nets.get("S")
                    if getattr(nxt, 'is_dummy', False) and not left_net:
                        dummy_nets = self._terminal_nets.get(nxt.dev_id, {})
                        left_net = dummy_nets.get("S") or dummy_nets.get("D")

                    if right_net and left_net and right_net == left_net:
                        # Overlap by diffusion width
                        advance = dev_w - diffusion_w
                        # Snap to grid to prevent fractional stacking
                        advance = max(
                            self._snap_grid,
                            round(advance / self._snap_grid) * self._snap_grid,
                        )
                        
                        # Left device keeps its right label (shared terminal)
                        it.set_boundary_label_visibility(
                            show_left=not getattr(it, '_abut_hide_left', False),
                            show_right=True,
                        )
                        # Right device hides its left label (duplicate)
                        nxt._abut_hide_left = True
                        # Left device paints on top for clean shared terminal
                        it.setZValue(base_z + len(ordered) - idx)

                        # Set abut states inline for immediate rendering
                        if hasattr(it, 'set_abut_state'):
                            it.set_abut_state(right=True, shared_net_right=right_net)
                        if hasattr(nxt, 'set_abut_state'):
                            nxt.set_abut_state(left=True, shared_net_left=left_net)

                x_cursor += advance

            # Apply deferred left-hide flags (preserve existing right-side state)
            for it in ordered:
                if getattr(it, '_abut_hide_left', False):
                    it._hide_left_terminal_label = True
                    it.update()
                    del it._abut_hide_left

        # Abut states were set inline above during compaction

    def swap_devices(self, id_a, id_b):
        """Swap the positions of two devices on the canvas."""
        item_a = self.device_items.get(id_a)
        item_b = self.device_items.get(id_b)
        if item_a and item_b:
            pos_a = item_a.pos()
            pos_b = item_b.pos()
            item_a.setPos(self._snap_point(pos_b.x(), pos_b.y()))
            item_b.setPos(self._snap_point(pos_a.x(), pos_a.y()))
            self._compact_rows_abutted()
            return True
        return False

    def move_device(self, dev_id, x, y):
        """Move a device to an absolute position (in layout coordinates)."""
        item = self.device_items.get(dev_id)
        if item:
            old_row_key = (getattr(item, "device_type", ""), self._snap_row(item.pos().y()))
            pt = self._snap_point(x * self.scale_factor, y * self.scale_factor)
            free_x = self.find_nearest_free_x(
                row_y=pt.y(),
                width=item.rect().width(),
                target_x=pt.x(),
                exclude_id=dev_id,
            )
            item.setPos(free_x, pt.y())
            new_row_key = (getattr(item, "device_type", ""), self._snap_row(pt.y()))
            self._compact_rows_abutted({old_row_key, new_row_key})
            return True
        return False

    def move_device_to_grid(self, dev_id, row, col):
        """Move one device to explicit grid row/col indices."""
        item = self.device_items.get(dev_id)
        if not item:
            return False
        old_row_key = (getattr(item, "device_type", ""), self._snap_row(item.pos().y()))
        x = col * self._snap_grid
        y = row * self._row_pitch
        pt = self._snap_point(x, y)
        free_x = self.find_nearest_free_x(
            row_y=pt.y(),
            width=item.rect().width(),
            target_x=pt.x(),
            exclude_id=dev_id,
        )
        item.setPos(free_x, pt.y())
        new_row_key = (getattr(item, "device_type", ""), self._snap_row(pt.y()))
        self._compact_rows_abutted({old_row_key, new_row_key})
        return True

    def find_nearest_free_x(self, row_y, width, target_x, exclude_id=None):
        """Return nearest free x-slot on the target row without moving other devices."""
        row_y = self._snap_row(row_y)
        span = max(1, int(math.ceil(width / self._snap_grid)))
        desired = int(round(self._snap_value(target_x) / self._snap_grid))

        intervals = []
        for dev_id, item in self.device_items.items():
            if exclude_id and dev_id == exclude_id:
                continue
            if self._snap_row(item.pos().y()) != row_y:
                continue
            start = int(round(self._snap_value(item.pos().x()) / self._snap_grid))
            other_span = max(1, int(math.ceil(item.rect().width() / self._snap_grid)))
            intervals.append((start, start + other_span - 1))

        def free(start_slot):
            end_slot = start_slot + span - 1
            for s, e in intervals:
                if not (end_slot < s or start_slot > e):
                    return False
            return True

        dist = 0
        while True:
            candidates = [desired] if dist == 0 else [desired - dist, desired + dist]
            for c in candidates:
                if free(c):
                    return c * self._snap_grid
            dist += 1

    def ensure_grid_extent(self, row_count, col_count):
        """Ensure scene rect is large enough for requested row/col counts."""
        rect = self.scene.sceneRect()
        margin = 120
        min_right = max(rect.right(), (max(col_count, 1) + 1) * self._snap_grid + margin)
        min_bottom = max(rect.bottom(), (max(row_count, 1) + 1) * self._row_pitch + margin)
        self.scene.setSceneRect(rect.left(), rect.top(), min_right - rect.left(), min_bottom - rect.top())

    def set_virtual_extents(self, row_count, col_count):
        """Set virtual grid extents; empty bands are drawn for extra rows/cols."""
        self._virtual_row_count = max(0, int(row_count))
        self._virtual_col_count = max(0, int(col_count))
        self.resetCachedContent()

    def set_custom_row_gap(self, gap_px):
        """Deprecated: row spacing is fixed to zero (touching rows)."""
        self._custom_row_gap = 0.0

    def optimize_2d_layout(self, force_reorder=False):
        """Analyze all device terminal nets and apply optimal S/D abutment.

        Groups devices within each row that share diffusion boundary nets,
        reorders them for maximum sharing, then applies overlap abutment so
        shared terminals appear only once (contiguous diffusion region).

        This is the primary 'smart' action triggered by toolbar / context-menu /
        auto-on-load.

        Args:
            force_reorder: If True, clears _preserve_grid_order flags so devices
                are reordered for optimal diffusion sharing. Use this when user
                explicitly triggers optimization via toolbar/menu.

        Returns:
            str: Status message - "ok" if optimized, "no_devices" if empty,
                 "no_nets" if terminal nets not available.
        """
        if not self.device_items:
            return "no_devices"

        if not self._terminal_nets:
            # Still run compaction for visual cleanup, but no smart reordering
            self._compact_rows_abutted()
            self.resetCachedContent()
            return "no_nets"

        # When force_reorder is requested, clear grid-order preservation flags
        # so _order_row_items() actually reorders for optimal sharing.
        if force_reorder:
            for item in self.device_items.values():
                if hasattr(item, '_preserve_grid_order'):
                    delattr(item, '_preserve_grid_order')

        self._compact_rows_abutted()
        self.resetCachedContent()
        return "ok"

    # ── Abutment state management (per-device rendering) ─────────────────────

    def _clear_abut_groups(self):
        """Clear legacy group items list (no longer used in new approach)."""
        for grp in self._group_items:
            try:
                self.scene.removeItem(grp)
            except RuntimeError:
                pass
        self._group_items.clear()
        for item in self.device_items.values():
            item.setVisible(True)

    def _update_device_abut_states(self):
        """Analyze all devices and set their abut states based on neighbor sharing.

        This is the 'smart' auto-detection: for each device, check if its left/right
        neighbor shares a diffusion net. If so, set the abut state so the device
        renders with a merged edge appearance (green shared diffusion).

        Also creates text overlays showing shared net names between abutted devices.

        Works for ANY circuit - analyzes actual net labels automatically.
        """
        # Clear previous shared net labels
        for label in self._shared_net_labels:
            try:
                self.scene.removeItem(label)
            except RuntimeError:
                pass
        self._shared_net_labels.clear()

        if not self._terminal_nets:
            # No net info available - reset all abut states
            for item in self.device_items.values():
                if hasattr(item, 'set_abut_state'):
                    item.set_abut_state(left=False, right=False)
            return

        # Group devices by row
        rows: dict[float, list] = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            rows.setdefault(row_y, []).append(item)

        for row_y, items in rows.items():
            ordered = sorted(items, key=lambda it: it.pos().x())

            # First pass: reset all abut states
            for it in ordered:
                if hasattr(it, 'set_abut_state'):
                    it.set_abut_state(left=False, right=False,
                                      shared_net_left="", shared_net_right="")

            # Second pass: detect sharing pairs and create overlays
            for i in range(len(ordered) - 1):
                left_dev = ordered[i]
                right_dev = ordered[i + 1]

                # Check if devices are physically touching or overlapping
                left_rect = left_dev.sceneBoundingRect()
                right_rect = right_dev.sceneBoundingRect()
                gap = right_rect.left() - left_rect.right()

                # Devices are abutted if they touch (gap ≈ 0) or overlap (gap < 0)
                # Only skip if there's a real visible gap between them (gap > 2)
                if gap > 2:
                    continue

                # Get boundary nets
                left_boundary = self._visual_side_net(left_dev, 'right')
                right_boundary = self._visual_side_net(right_dev, 'left')

                # Check if they share a net (diffusion continuity)
                if left_boundary and right_boundary and left_boundary == right_boundary:
                    shared_net = left_boundary
                    # Mark both devices as abutted on their shared edge
                    if hasattr(left_dev, 'set_abut_state'):
                        left_dev.set_abut_state(right=True, shared_net_right=shared_net)
                    if hasattr(right_dev, 'set_abut_state'):
                        right_dev.set_abut_state(left=True, shared_net_left=shared_net)

    def _create_shared_net_label(self, left_dev, right_dev, net_name):
        """Create a text overlay showing the shared net name between two abutted devices."""
        from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsRectItem

        # Calculate position: between the two devices at their shared border
        left_rect = left_dev.sceneBoundingRect()
        right_rect = right_dev.sceneBoundingRect()

        # Position at the shared border (right edge of left device = left edge of right device)
        x_pos = left_rect.right()
        y_pos = (left_rect.top() + left_rect.bottom()) / 2

        # Create background rectangle first
        # Estimate text size for background
        temp_label = QGraphicsSimpleTextItem(net_name)
        temp_label.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        text_rect = temp_label.boundingRect()

        # Add padding around text
        padding = 4
        bg_width = text_rect.width() + padding * 2
        bg_height = text_rect.height() + padding

        # Create background centered at the border
        bg = QGraphicsRectItem(
            x_pos - bg_width / 2,
            y_pos - bg_height / 2,
            bg_width,
            bg_height
        )
        bg.setBrush(QBrush(QColor(76, 175, 80, 220)))  # Green background
        bg.setPen(QPen(QColor("#2e7d32"), 1.0))  # Darker green border
        bg.setZValue(99)
        self.scene.addItem(bg)
        self._shared_net_labels.append(bg)

        # Create text item on top
        label = QGraphicsSimpleTextItem(net_name)
        label.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        label.setBrush(QBrush(QColor("#ffffff")))  # White text

        # Center the text at the border
        label.setPos(x_pos - text_rect.width() / 2, y_pos - text_rect.height() / 2)
        label.setZValue(100)

        self.scene.addItem(label)
        self._shared_net_labels.append(label)

    def get_abut_analysis(self):
        """Return abut analysis data for LLM/backend use.

        Returns:
            dict: {
                'sharing_pairs': [{'left': dev_id, 'right': dev_id, 'net': shared_net}, ...],
                'device_states': {dev_id: {'abut_left': bool, 'abut_right': bool}, ...},
                'rows': [{
                    'row_y': float,
                    'devices': [dev_id, ...],
                    'adjacency_score': int
                }, ...]
            }
        """
        result = {
            'sharing_pairs': [],
            'device_states': {},
            'rows': []
        }

        if not self._terminal_nets:
            return result

        rows: dict[float, list] = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            rows.setdefault(row_y, []).append(item)

        for row_y, items in rows.items():
            ordered = sorted(items, key=lambda it: it.pos().x())
            row_data = {
                'row_y': row_y,
                'devices': [it.device_name for it in ordered],
                'adjacency_score': 0
            }

            for i in range(len(ordered) - 1):
                left_dev = ordered[i]
                right_dev = ordered[i + 1]

                left_boundary = self._visual_side_net(left_dev, 'right')
                right_boundary = self._visual_side_net(right_dev, 'left')

                if left_boundary and right_boundary and left_boundary == right_boundary:
                    result['sharing_pairs'].append({
                        'left': left_dev.device_name,
                        'right': right_dev.device_name,
                        'net': left_boundary
                    })
                    row_data['adjacency_score'] += 1

            result['rows'].append(row_data)

        # Device states
        for item in self.device_items.values():
            if hasattr(item, 'get_abut_state'):
                result['device_states'][item.device_name] = item.get_abut_state()

        return result

    def _rebuild_abut_groups(self):
        """After compaction, update device abut states for smart rendering.

        This replaces the old AbutGroupItem overlay approach with dynamic
        per-device abut states. Each device now renders its own merged edges
        based on whether it shares diffusion nets with neighbors.
        """
        # Update abut states for all devices
        self._update_device_abut_states()

        # Keep all devices visible (no overlays needed)
        for item in self.device_items.values():
            item.setVisible(True)

    def contextMenuEvent(self, event):
        """Show a context menu with layout-optimization actions."""
        menu = QMenu(self.viewport())
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e2636;
                border: 1px solid #3d5066;
                border-radius: 6px;
                padding: 4px;
                color: #c8d0dc;
                font-family: 'Segoe UI';
                font-size: 9pt;
            }
            QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; }
            QMenu::item:selected { background-color: #4a90d9; color: #ffffff; }
            QMenu::separator { height: 1px; background: #2d3548; margin: 4px 8px; }
        """)

        selected_ids = self.selected_device_ids()

        act_edit_terms = menu.addAction("Edit Terminals...")
        act_edit_terms.setEnabled(len(selected_ids) == 1)

        menu.addSeparator()

        act_opt2d = menu.addAction("Optimize Layout")
        act_opt2d.setToolTip("Reorder & abut all devices to maximize S/D sharing")

        menu.addSeparator()

        act_flip_h = menu.addAction("Flip Horizontal")
        act_flip_v = menu.addAction("Flip Vertical")
        act_flip_h.setEnabled(bool(selected_ids))
        act_flip_v.setEnabled(bool(selected_ids))

        chosen = menu.exec(event.globalPos())
        if chosen == act_opt2d:
            self.optimize_2d_requested.emit()
        elif chosen == act_edit_terms:
            self.edit_terminals_requested.emit(selected_ids[0])
        elif chosen == act_flip_h:
            for dev_id in selected_ids:
                item = self.device_items.get(dev_id)
                if item and hasattr(item, "flip_horizontal"):
                    item.flip_horizontal()
            self._update_device_abut_states()
        elif chosen == act_flip_v:
            for dev_id in selected_ids:
                item = self.device_items.get(dev_id)
                if item and hasattr(item, "flip_vertical"):
                    item.flip_vertical()
            self._update_device_abut_states()

    def get_row_col(self, dev_id):
        item = self.device_items.get(dev_id)
        if not item:
            return None
        row = int(round(item.pos().y() / self._row_pitch))
        col = int(round(item.pos().x() / self._snap_grid))
        return row, col

    def selected_device_ids(self):
        ids = []
        try:
            for it in self.scene.selectedItems():
                if hasattr(it, "dev_id"):
                    ids.append(it.dev_id)
        except RuntimeError:
            return []
        return ids

    def flip_devices_h(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_horizontal"):
                item.flip_horizontal()
        self._update_device_abut_states()

    def flip_devices_v(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_vertical"):
                item.flip_vertical()
        self._update_device_abut_states()

    def _interval_overlap(self, a_start, a_end, b_start, b_end):
        return not (a_end < b_start or b_end < a_start)

    def _item_slot_span(self, item):
        start = int(round(self._snap_value(item.pos().x()) / self._snap_grid))
        span = max(1, int(math.ceil(item.rect().width() / self._snap_grid)))
        return start, start + span - 1, span

    def resolve_overlaps(self, anchor_ids=None, compact=True):
        """Resolve overlaps locally around anchors so unaffected devices stay put."""
        anchors = set(anchor_ids or [])
        rows = {}
        for item in self.device_items.values():
            row_y = self._snap_row(item.pos().y())
            key = (getattr(item, "device_type", ""), row_y)
            rows.setdefault(key, []).append(item)

        for (_, row_y), items in rows.items():
            if not items:
                continue

            if anchors:
                row_anchors = [it for it in items if it.dev_id in anchors]
                if not row_anchors:
                    continue
            else:
                row_anchors = sorted(items, key=lambda it: it.dev_id)

            queue = list(row_anchors)
            seen = set()
            while queue:
                current = queue.pop(0)
                cur_start, cur_end, _ = self._item_slot_span(current)
                cur_x = current.pos().x()
                for other in items:
                    if other is current:
                        continue
                    oth_start, oth_end, oth_span = self._item_slot_span(other)
                    if not self._interval_overlap(cur_start, cur_end, oth_start, oth_end):
                        continue

                    # Push overlapped neighbors away from the collision side.
                    if other.pos().x() >= cur_x:
                        new_start = cur_end + 1
                    else:
                        new_start = cur_start - oth_span

                    target_x = new_start * self._snap_grid
                    if abs(other.pos().x() - target_x) > 1e-6:
                        other.setPos(target_x, row_y)
                        if other not in seen:
                            queue.append(other)
                seen.add(current)
        if compact:
            self._compact_rows_abutted()

    def set_edges(self, edges):
        """Store edge data and build connectivity lookup."""
        self._edges = edges or []
        self._conn_map.clear()
        for edge in self._edges:
            src = edge.get("source")
            tgt = edge.get("target")
            net = edge.get("net", "")
            if src and tgt:
                self._conn_map.setdefault(src, []).append((tgt, net))
                self._conn_map.setdefault(tgt, []).append((src, net))

    def set_terminal_nets(self, terminal_nets):
        """Store terminal-net mapping: {dev_id: {'D': net, 'G': net, 'S': net}}"""
        self._terminal_nets = terminal_nets or {}

        # Push net labels to each device item for visual annotation
        for dev_id, item in self.device_items.items():
            nets = self._terminal_nets.get(dev_id, {})
            if hasattr(item, "set_net_labels"):
                item.set_net_labels(nets)

        # Re-pack with net-aware adjacency — but NOT if compact was suppressed.
        if self._skip_compaction:
            self._skip_compaction = False
            # Still detect shared diffusion for green rendering (even without recompaction)
            self._update_device_abut_states()
            return
        if self.device_items:
            self.optimize_2d_layout()
            self.resetCachedContent()

    def _get_terminal_for_net(self, dev_id, net_name):
        """Return which terminal ('S','G','D') of dev_id connects to net_name."""
        term_map = self._terminal_nets.get(dev_id, {})
        for term, net in term_map.items():
            if net == net_name:
                return term
        return "G"  # fallback

    def _get_net_color(self, net_name):
        """Return a consistent color for a given net name."""
        if net_name not in self._net_colors:
            palette = self._net_colors['__palette']
            idx = (len(self._net_colors) - 1) % len(palette)
            self._net_colors[net_name] = palette[idx]
        return self._net_colors[net_name]

    def _clear_connections(self):
        """Remove all connection lines, labels, and terminal highlights from the scene."""
        if self._conn_lines:
            self.scene.blockSignals(True)
            for item in self._conn_lines:
                self.scene.removeItem(item)
            self._conn_lines.clear()
            self.scene.blockSignals(False)

        # Also clear terminal highlights
        for highlight in self._terminal_highlights:
            try:
                self.scene.removeItem(highlight)
            except RuntimeError:
                pass
        self._terminal_highlights.clear()

    def _show_connections(self, dev_id):
        """Draw curved lines from dev_id terminals to connected device terminals."""
        self._clear_connections()
        connections = self._conn_map.get(dev_id, [])
        if not connections:
            return

        src_item = self.device_items.get(dev_id)
        if not src_item:
            return

        src_anchors = src_item.terminal_anchors()

        self.scene.blockSignals(True)
        for i, (other_id, net_name) in enumerate(connections):
            tgt_item = self.device_items.get(other_id)
            if not tgt_item:
                continue

            tgt_anchors = tgt_item.terminal_anchors()
            color = self._get_net_color(net_name)

            # Look up correct terminals from SPICE data
            src_term = self._get_terminal_for_net(dev_id, net_name)
            tgt_term = self._get_terminal_for_net(other_id, net_name)
            p1 = src_anchors[src_term]
            p2 = tgt_anchors[tgt_term]

            # Build a curved bezier path
            path = QPainterPath()
            path.moveTo(p1)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            offset = max(abs(dx), abs(dy)) * 0.3
            sign = 1.0 if i % 2 == 0 else -1.0
            if abs(dx) > abs(dy):
                ctrl1 = QPointF(p1.x() + dx * 0.33, p1.y() + sign * offset)
                ctrl2 = QPointF(p1.x() + dx * 0.66, p2.y() + sign * offset)
            else:
                ctrl1 = QPointF(p1.x() + sign * offset, p1.y() + dy * 0.33)
                ctrl2 = QPointF(p2.x() + sign * offset, p1.y() + dy * 0.66)
            path.cubicTo(ctrl1, ctrl2, p2)

            path_item = QGraphicsPathItem(path)
            pen = QPen(color, 0.5, Qt.PenStyle.DashLine)
            path_item.setPen(pen)
            path_item.setZValue(10)
            path_item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)
            self.scene.addItem(path_item)
            self._conn_lines.append(path_item)
        self.scene.blockSignals(False)

    def _show_net_connections(self, dev_id, net_name):
        """Highlight only connections for a specific net from a device."""
        self._clear_connections()
        connections = [(oid, n) for oid, n in self._conn_map.get(dev_id, [])
                       if n == net_name]
        if not connections:
            return

        src_item = self.device_items.get(dev_id)
        if not src_item:
            return

        src_anchors = src_item.terminal_anchors()
        src_term = self._get_terminal_for_net(dev_id, net_name)
        color = self._get_net_color(net_name)

        self.scene.blockSignals(True)
        for i, (other_id, _) in enumerate(connections):
            tgt_item = self.device_items.get(other_id)
            if not tgt_item:
                continue

            tgt_anchors = tgt_item.terminal_anchors()
            tgt_term = self._get_terminal_for_net(other_id, net_name)
            p1 = src_anchors[src_term]
            p2 = tgt_anchors[tgt_term]

            path = QPainterPath()
            path.moveTo(p1)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            offset = max(abs(dx), abs(dy)) * 0.25
            sign = 1.0 if i % 2 == 0 else -1.0
            if abs(dx) > abs(dy):
                ctrl1 = QPointF(p1.x() + dx * 0.33, p1.y() + sign * offset)
                ctrl2 = QPointF(p1.x() + dx * 0.66, p2.y() + sign * offset)
            else:
                ctrl1 = QPointF(p1.x() + sign * offset, p1.y() + dy * 0.33)
                ctrl2 = QPointF(p2.x() + sign * offset, p1.y() + dy * 0.66)
            path.cubicTo(ctrl1, ctrl2, p2)

            path_item = QGraphicsPathItem(path)
            pen = QPen(color, 0.5, Qt.PenStyle.DashLine)
            path_item.setPen(pen)
            path_item.setZValue(10)
            self.scene.addItem(path_item)
            self._conn_lines.append(path_item)
        self.scene.blockSignals(False)

    def highlight_net_by_name(self, net_name, color):
        """Highlight all connections for a specific net across the layout using a custom color."""
        if not getattr(self, "_edges", None):
            return

        self.scene.blockSignals(True)
        drawn = set()
        for i, edge in enumerate(self._edges):
            if edge.get("net") == net_name:
                src = edge.get("source")
                tgt = edge.get("target")
                if not src or not tgt:
                    continue
                
                # Avoid drawing the exact same undirected edge twice if it exists
                edge_sig = tuple(sorted([src, tgt]))
                if edge_sig in drawn:
                    continue
                drawn.add(edge_sig)

                src_item = self.device_items.get(src)
                tgt_item = self.device_items.get(tgt)
                if not src_item or not tgt_item:
                    continue

                src_anchors = src_item.terminal_anchors()
                tgt_anchors = tgt_item.terminal_anchors()
                
                src_term = self._get_terminal_for_net(src, net_name)
                tgt_term = self._get_terminal_for_net(tgt, net_name)
                
                p1 = src_anchors[src_term]
                p2 = tgt_anchors[tgt_term]

                path = QPainterPath()
                path.moveTo(p1)
                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                offset = max(abs(dx), abs(dy)) * 0.25
                sign = 1.0 if i % 2 == 0 else -1.0
                if abs(dx) > abs(dy):
                    ctrl1 = QPointF(p1.x() + dx * 0.33, p1.y() + sign * offset)
                    ctrl2 = QPointF(p1.x() + dx * 0.66, p2.y() + sign * offset)
                else:
                    ctrl1 = QPointF(p1.x() + sign * offset, p1.y() + dy * 0.33)
                    ctrl2 = QPointF(p2.x() + sign * offset, p1.y() + dy * 0.66)
                path.cubicTo(ctrl1, ctrl2, p2)

                path_item = QGraphicsPathItem(path)
                # use solid thicker line for highlight
                pen = QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine)
                path_item.setPen(pen)
                path_item.setZValue(15)
                path_item.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, False)
                self.scene.addItem(path_item)
                self._conn_lines.append(path_item)
        self.scene.blockSignals(False)


    def _on_selection_changed(self):
        """Emit device_clicked when user selects a device on the canvas."""
        try:
            selected = [s for s in self.scene.selectedItems()
                        if hasattr(s, 'device_name')]
        except RuntimeError:
            return  # scene deleted during shutdown
        if selected:
            dev_id = selected[0].device_name
            self.device_clicked.emit(dev_id)
            self._show_connections(dev_id)
        else:
            self._clear_connections()

    def fit_to_view(self):
        """Zoom and pan to fit all devices in the viewport."""
        if not self.device_items:
            return
        rects = [item.sceneBoundingRect() for item in self.device_items.values()]
        union = rects[0]
        for r in rects[1:]:
            union = union.united(r)
        margin = max(union.width(), union.height()) * 0.08
        margin = max(margin, 30)
        self.fitInView(
            union.adjusted(-margin, -margin, margin, margin),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._zoom_level = self.transform().m11()

    def highlight_device(self, dev_id):
        """Highlight a device by its id without moving the view."""
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        item = self.device_items.get(dev_id)
        if item:
            item.setSelected(True)
        self.scene.blockSignals(False)

    def highlight_net(self, net_name):
        """Highlight only the specific terminal areas (S/D) connected to a net, not whole devices."""
        if not net_name or not self._terminal_nets:
            return

        # Clear previous terminal highlights
        for highlight in self._terminal_highlights:
            try:
                self.scene.removeItem(highlight)
            except RuntimeError:
                pass
        self._terminal_highlights.clear()

        # Find all devices and their specific terminals connected to this net
        terminals_on_net = []  # [(dev_id, terminal), ...]
        for dev_id, terminals in self._terminal_nets.items():
            for term, net in terminals.items():
                if self._norm_net_name(net) == self._norm_net_name(net_name):
                    terminals_on_net.append((dev_id, term))

        if not terminals_on_net:
            return

        # Clear device selection (we're only highlighting terminals)
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        self.scene.blockSignals(False)

        # IMPORTANT: clear existing connection paths first, otherwise _show_all_net_connections
        # will call _clear_connections() and destroy the highlights we're about to make.
        self._clear_connections()

        # Create semi-transparent highlight overlays for each terminal
        for dev_id, term in terminals_on_net:
            item = self.device_items.get(dev_id)
            if not item or not hasattr(item, 'terminal_rects'):
                continue

            term_rects = item.terminal_rects()
            term_rect = term_rects.get(term)
            if not term_rect:
                continue

            # Create highlight overlay
            highlight = QGraphicsRectItem(term_rect)
            highlight.setBrush(QBrush(QColor(255, 204, 0, 80)))  # Semi-transparent golden
            highlight.setPen(QPen(QColor("#ffcc00"), 2.0, Qt.PenStyle.SolidLine))
            highlight.setZValue(60)  # Above devices (which are at z=50)
            self.scene.addItem(highlight)
            self._terminal_highlights.append(highlight)

        # Show all connections on this net
        self._show_all_net_connections(net_name, clear_first=False)

    def _show_all_net_connections(self, net_name, clear_first=True):
        """Show all connections for a specific net between all devices."""
        if clear_first:
            self._clear_connections()

        if not self._terminal_nets:
            return

        # Find all devices connected to this net
        devices_on_net = []
        for dev_id, terminals in self._terminal_nets.items():
            for term, net in terminals.items():
                if self._norm_net_name(net) == self._norm_net_name(net_name):
                    devices_on_net.append((dev_id, term))
                    break

        if len(devices_on_net) < 2:
            return

        # Draw connections between all pairs of devices on this net
        color = self._get_net_color(net_name)

        for i, (dev1_id, term1) in enumerate(devices_on_net):
            item1 = self.device_items.get(dev1_id)
            if not item1:
                continue

            anchors1 = item1.terminal_anchors()
            p1 = anchors1.get(term1)
            if not p1:
                continue

            for dev2_id, term2 in devices_on_net[i+1:]:
                item2 = self.device_items.get(dev2_id)
                if not item2:
                    continue

                anchors2 = item2.terminal_anchors()
                p2 = anchors2.get(term2)
                if not p2:
                    continue

                # Draw connection line
                path = QPainterPath()
                path.moveTo(p1)
                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()

                # Slight curve for better visibility
                if abs(dy) < 5:
                    path.lineTo(p2)
                else:
                    cp1 = QPointF(p1.x() + dx * 0.3, p1.y())
                    cp2 = QPointF(p2.x() - dx * 0.3, p2.y())
                    path.cubicTo(cp1, cp2, p2)

                path_item = QGraphicsPathItem(path)
                path_item.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine))
                path_item.setZValue(-1)
                self.scene.addItem(path_item)
                self._conn_lines.append(path_item)

    # -------------------------------------------------
    # Background Grid
    # -------------------------------------------------
    def drawBackground(self, painter: QPainter, rect):
        """Draw a symmetric background panel representing the working canvas."""
        super().drawBackground(painter, rect)

        has_devices = bool(self.device_items)
        has_virtual = (self._virtual_row_count > 0 or self._virtual_col_count > 0)

        if not has_devices and not has_virtual:
            return

        # Gather bounds
        if has_devices:
            all_items = list(self.device_items.values())
            ref_min_x = min(it.pos().x() for it in all_items)
            ref_max_x = max(it.pos().x() + it.rect().width() for it in all_items)
            ref_min_y = min(self._snap_row(it.pos().y()) for it in all_items)
            ref_max_y = max(self._snap_row(it.pos().y()) + it.rect().height() for it in all_items)
            ref_max_dev_h = max(it.rect().height() for it in all_items)
        else:
            ref_min_x = 0.0
            ref_max_x = 0.0
            ref_min_y = 0.0
            ref_max_y = self._row_pitch
            ref_max_dev_h = self._row_pitch * 0.5

        # Factor in virtual limits
        virtual_right_x = (
            ref_min_x + self._virtual_col_count * self._snap_grid
            if self._virtual_col_count > 0
            else ref_max_x
        )

        virtual_bottom_y = (
            ref_min_y + self._virtual_row_count * self._row_pitch
            if self._virtual_row_count > 0
            else ref_max_y
        )

        # Compute a single symmetric left/right/top/bottom for the canvas
        global_left = ref_min_x
        global_right = max(ref_max_x, virtual_right_x)
        global_top = ref_min_y
        global_bottom = max(ref_max_y, virtual_bottom_y)

        # Drawing styles
        panel_fill = QBrush(QColor("#151c28"))
        panel_pen = QPen(QColor("#2d3548"), 1.0)
        
        # Give it a small symmetric padding
        pad_x = 16.0
        pad_y = 10.0
        
        panel_x = global_left - pad_x
        panel_w = (global_right - global_left) + (pad_x * 2)
        panel_y = global_top - pad_y
        panel_h = (global_bottom - global_top) + (pad_y * 2)

        # Only draw if within viewport
        if (
            panel_x > rect.right()
            or panel_x + panel_w < rect.left()
            or panel_y > rect.bottom()
            or panel_y + panel_h < rect.top()
        ):
            return

        painter.setPen(panel_pen)
        painter.setBrush(panel_fill)
        painter.drawRoundedRect(panel_x, panel_y, panel_w, panel_h, 4.0, 4.0)

    # -------------------------------------------------
    # Zoom with Mouse Wheel
    # -------------------------------------------------
    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.scale(self.zoom_factor, self.zoom_factor)
        else:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_in(self):
        self.scale(self.zoom_factor, self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_out(self):
        self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
        self._zoom_level = self.transform().m11()
        self.resetCachedContent()

    def zoom_reset(self):
        self.resetTransform()
        self._zoom_level = 1.0
        self.resetCachedContent()

    # -------------------------------------------------
    # Pan with Middle Mouse
    # -------------------------------------------------
    def mousePressEvent(self, event):
        if self._dummy_mode and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            if self._commit_dummy_at(scene_pos):
                self._update_dummy_preview(scene_pos)
                return
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            fake_event = type(event)(
                event.type(),
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(fake_event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._dummy_mode:
            self._update_dummy_preview(self.mapToScene(event.pos()))
        super().mouseMoveEvent(event)

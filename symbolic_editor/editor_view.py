"""
Symbolic Editor — the central QGraphicsView canvas for
interactive device placement, routing visualisation, and
grid-snapped editing.
"""

import math
import logging
import os
from typing import List, Dict


def _ip_ui_quiet() -> bool:
    """True while initial placement runs with step-only console mode."""
    return os.environ.get("PLACEMENT_STEPS_ONLY", "0").lower() in (
        "1", "true", "yes",
    )


def _dev_diag_print(*args, **kwargs) -> None:
    """Non-error hierarchy/matched-group messages.
    Suppressed by default. Set EDITOR_VERBOSE=1 to re-enable."""
    if os.environ.get("EDITOR_VERBOSE", "0").lower() not in ("1", "true", "yes"):
        return
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)

from PySide6.QtWidgets import (
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsItem,
    QGraphicsSimpleTextItem,
    QMenu,
)
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPainter, QPen, QPainterPath, QColor, QBrush, QAction, QKeySequence

try:
    from .device_item import DeviceItem
    from .passive_item import ResistorItem, CapacitorItem
    from .block_item import BlockItem
    from .hierarchy_group_item import HierarchyGroupItem
    from .abutment_engine import find_abutment_candidates, build_edge_highlight_map
except ImportError:
    from device_item import DeviceItem
    from passive_item import ResistorItem, CapacitorItem
    from block_item import BlockItem
    from hierarchy_group_item import HierarchyGroupItem
    try:
        from abutment_engine import find_abutment_candidates, build_edge_highlight_map
    except ImportError:
        find_abutment_candidates = None
        build_edge_highlight_map = None

class HierarchyAwareScene(QGraphicsScene):
    """Custom QGraphicsScene that blocks selection of devices in non-descended hierarchies."""
    
    def __init__(self, editor):
        super().__init__()
        self._editor = editor
    
    def selectionChanged(self):
        """Override selection to block devices in non-descended hierarchies."""
        # First, call parent implementation
        super().selectionChanged()
        
        # Now enforce hierarchy selection rules
        if self._editor is None:
            return
        
        try:
            selected = [s for s in self.selectedItems()
                        if hasattr(s, 'device_name')]
            
            if not selected:
                return
            
            # Block selection for devices in non-descended hierarchies
            should_block = []
            for item in selected:
                if hasattr(item, 'device_name'):
                    if not self._editor.can_select_device(item):
                        should_block.append(item)
            
            if should_block:
                # Block signals to prevent recursive calls
                self.blockSignals(True)
                try:
                    for item in should_block:
                        item.setSelected(False)
                finally:
                    self.blockSignals(False)
        except Exception:
            logging.debug("HierarchyAwareScene selection error", exc_info=True)


class SymbolicEditor(QGraphicsView):

    device_clicked = Signal(str)
    dummy_toggle_requested = Signal()

    def __init__(self):
        super().__init__()

        # Create scene with hierarchy-aware custom scene
        self.scene = HierarchyAwareScene(self)
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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Zoom parameters
        self.zoom_factor = 1.15
        self._zoom_level = 1.0
        self.scale_factor = 80

        # Device items lookup by id
        self.device_items = {}

        # Connectivity data
        self._edges = []          # raw edge list from JSON
        self._conn_map = {}       # device_id -> [(other_id, net_name), ...]
        self._conn_lines = []     # active QGraphicsPathItem items
        self._terminal_nets = {}  # {dev_id: {'D': net, 'G': net, 'S': net}}

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
        self._grid_color = QColor("#1c2535")
        self._grid_color_major = QColor("#2d3f54")
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

        # Block overlay items
        self._blocks = {}            # block data: {inst: {subckt, devices}}
        self._block_overlays = []    # list of QGraphicsItem for overlays
        self._block_items = []       # list of actual BlockItem instances (symbol view)
        self._block_overlays_visible = True
        self._view_level = "symbol"   # 'symbol' or 'transistor'
        self._device_render_mode = "detailed"
        self._centroid_items = []

        # Block colors
        # --- Fix 2: Single uniform color for ALL blocks ---
        self._block_fill_color = QColor(60, 130, 190, 45)      # steel blue, semi-transparent (overlay)
        self._block_border_color = QColor(80, 160, 220, 130)   # lighter steel border (overlay)
        self._block_fill_solid = QColor(60, 130, 190, 180)     # opaque fill (symbol view)
        self._block_border_solid = QColor(80, 160, 220, 220)   # opaque border (symbol view)

        # Hierarchy group items (arrays, multipliers, fingers)
        self._hierarchy_groups = []  # list of top-level HierarchyGroupItem
        self._hierarchy_visible = True  # whether hierarchy groups are shown

        # Completely disable scrollbars (policy is more reliable than CSS)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.setStyleSheet("""
            QGraphicsView {
                border: none;
                background-color: #0e1219;
            }
        """)

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

    def set_device_render_mode(self, mode):
        self._device_render_mode = mode if mode in {"detailed", "outline"} else "detailed"
        for item in self.device_items.values():
            if hasattr(item, "set_render_mode"):
                item.set_render_mode(self._device_render_mode)
        if self._dummy_preview is not None and hasattr(self._dummy_preview, "set_render_mode"):
            self._dummy_preview.set_render_mode(self._device_render_mode)
        self.viewport().update()

    def show_detailed_devices(self):
        self.set_view_level("transistor")
        self.set_device_render_mode("detailed")

    def show_outline_devices(self):
        self.set_view_level("transistor")
        self.set_device_render_mode("outline")

    def select_all_devices(self):
        for item in self.device_items.values():
            item.setSelected(True)

    def _snap_value(self, value):
        return round(value / self._snap_grid) * self._snap_grid

    def _snap_row(self, value):
        return round(value / self._row_pitch) * self._row_pitch

    def _snap_point(self, x, y):
        return QPointF(self._snap_value(x), self._snap_row(y))

    def _on_scene_changed(self, _regions):
        """Keep occupancy guides and block overlays fresh when devices move/add/remove."""
        self.resetCachedContent()
        
        # In Transistor view, we redraw overlays around the individual devices
        if self._view_level == "transistor" and self._block_overlays_visible and self._blocks:
            self._clear_block_overlays()
            self._draw_block_overlays()
            
        # Optional: could update block item sizes here too, but they maintain size during move


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

        rows = []
        for dev_type, items in type_items.items():
            if not items:
                continue
            avg_y = sum(it.pos().y() for it in items) / len(items)
            rows.append((dev_type, avg_y))

        target_type, target_y = min(rows, key=lambda r: abs(scene_pos.y() - r[1]))
        ref_item = type_items[target_type][0]
        width = ref_item.rect().width()
        height = ref_item.rect().height()
        if snap_to_free:
            x = self.find_nearest_free_x(
                row_y=target_y,
                width=width,
                target_x=self._snap_value(scene_pos.x()),
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
            self._clear_dummy_preview()
            self._dummy_preview = DeviceItem(
                "DUMMY",
                candidate["type"],
                candidate["x"],
                candidate["y"],
                candidate["width"],
                candidate["height"],
            )
            self._dummy_preview.set_render_mode(self._device_render_mode)
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
    def load_placement(self, nodes, compact=False):
        """Load placement from a list of node dicts.

        Args:
            nodes: list of node dicts with geometry.
            compact: if True, run abutted row compaction.
                     Set to False (default) to preserve exact positions from data
                     (e.g. after an OAS import or AI swap/move command).
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

            nf   = node.get("electrical", {}).get("nf", 1)
            dtype = node.get("type", "nmos")

            if dtype == "res":
                item = ResistorItem(node.get("id", "unknown"), x, y, width, height)
            elif dtype == "cap":
                item = CapacitorItem(node.get("id", "unknown"), x, y, width, height)
            else:
                item = DeviceItem(
                    node.get("id", "unknown"),
                    dtype,
                    x, y, width, height,
                    nf=nf,
                )
                item.set_render_mode(self._device_render_mode)
                # Restore abutment state from OAS / saved data
                abut = node.get("abutment", {})
                if abut:
                    item.set_abut_left(abut.get("abut_left", False))
                    item.set_abut_right(abut.get("abut_right", False))

                # Restore orientation (flip)
                orient = geom.get("orientation", "R0")
                if orient in ("R180", "R0_FH_FV"):
                    item.set_flip_h(True)
                    item.set_flip_v(True)
                elif "FH" in orient or orient == "MX":
                    item.set_flip_h(True)
                elif "FV" in orient or orient == "MY":
                    item.set_flip_v(True)

            self.scene.addItem(item)
            self.device_items[node.get("id", "unknown")] = item

        # ── Build hierarchy groups for ALL devices ──────────────────────
        # Always build hierarchy groups, even for standalone devices
        self._build_hierarchy_groups(nodes)

        # Compute grid metrics from device sizes (used for UI snap / row pitch).
        if widths:
            min_w = min(widths)
            col_gap = 0.0
            # Use a finer grid: half of the minimum device width
            self._snap_grid = max(1.0, (min_w + col_gap) / 2.0)
        if heights:
            max_h = max(heights)
            if self._custom_row_gap is not None:
                row_gap = self._custom_row_gap
            else:
                row_gap = max(24.0, max_h * 0.55)
            self._row_pitch = max(1.0, max_h + row_gap)

        if compact:
            # Grid-snap mode: apply snap grid to items and compact rows.
            for item in self.device_items.values():
                item.set_snap_grid(self._snap_grid, self._snap_grid)
            for item in self.device_items.values():
                item.setPos(self._snap_point(item.pos().x(), item.pos().y()))
            self._compact_rows_abutted()
        else:
            # Exact-coordinate mode (OAS import / AI placement):
            # Do NOT apply snap grid to items so itemChange never rounds
            # the original layout coordinates during subsequent operations.
            # Snap grid is still stored for UI helpers (row/col display etc.)
            # but items stay free until the user explicitly drags them.
            for item in self.device_items.values():
                item.set_snap_grid(None, None)   # disable per-item snapping
            self._skip_compaction = True          # skip the set_terminal_nets compaction

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

    # -------------------------------------------------
    # Hierarchy Group Building
    # -------------------------------------------------

    def _build_hierarchy_groups(self, nodes):
        """Group devices by parent and create HierarchyGroupItems.

        Only creates groups for devices with m>1, nf>1, or array suffix.
        Single devices are not grouped.
        """
        # ── Deduplication guard: skip if nodes haven't changed ────────
        import hashlib
        ids_hash = hashlib.md5(
            "|".join(sorted(n.get("id", "") for n in nodes)).encode()
        ).hexdigest()
        if hasattr(self, '_hierarchy_hash') and self._hierarchy_hash == ids_hash:
            _dev_diag_print("[HIERARCHY] Skipped — nodes unchanged since last build")
            return
        self._hierarchy_hash = ids_hash

        # Clear old groups
        for g in self._hierarchy_groups:
            try:
                self.scene.removeItem(g)
            except RuntimeError:
                logging.debug("Failed to remove hierarchy group from scene", exc_info=True)
        self._hierarchy_groups.clear()

        # ── Step 1: Group devices by parent ──────────────────────────────
        parent_groups = {}  # parent_name -> list of (node, device_item)
        standalone_devices = []  # devices without parent field
        
        for node in nodes:
            try:
                elec = node.get("electrical", {})
                parent = elec.get("parent")
                dev_id = node.get("id", "unknown")
                item = self.device_items.get(dev_id)
                
                if not item:
                    continue
                    
                if parent:
                    parent_groups.setdefault(parent, []).append((node, item))
                else:
                    # Device has no parent - treat it as its own hierarchy group
                    standalone_devices.append((node, item))
            except (KeyError, AttributeError, TypeError):
                logging.debug("Failed to group device by parent", exc_info=True)
                continue

        # ── Step 2: Build hierarchy info for each group ──────────────────
        _hierarchy_colors = [
            (QColor(40, 60, 100, 50), QColor(80, 140, 220, 160)),   # blue
            (QColor(60, 40, 100, 50), QColor(140, 100, 220, 160)),   # purple
            (QColor(40, 80, 60, 50), QColor(80, 200, 140, 160)),     # teal
            (QColor(80, 60, 40, 50), QColor(200, 140, 80, 160)),     # amber
        ]
        color_idx = 0

        for parent_name, members in list(parent_groups.items()):
            try:
                # Include ALL devices in hierarchy groups, even single devices
                # (removed: if len(members) <= 1: continue)

                # Sort by multiplier/finger index for correct ordering
                def _sort_key(m):
                    node, item = m
                    elec = node.get("electrical", {})
                    mi = elec.get("multiplier_index") or 0
                    fi = elec.get("finger_index") or 0
                    return (mi, fi)
                members.sort(key=_sort_key)

                nodes_list = [m[0] for m in members]
                items_list = [m[1] for m in members]

                # ── Pass 1: Wire up parent-level group movement ──
                # Every finger of this parent knows about ALL its siblings.
                # Dragging any one finger will move the entire transistor.
                for item in items_list:
                    if hasattr(item, '_parent_id'):
                        item._parent_id = parent_name
                        item._sibling_group = list(items_list)

                # Determine hierarchy structure
                has_mult = any(
                    n.get("electrical", {}).get("multiplier_index") is not None
                    for n in nodes_list
                )
                has_finger = any(
                    n.get("electrical", {}).get("finger_index") is not None
                    for n in nodes_list
                )

                # Count unique multipliers and fingers per multiplier
                if has_mult:
                    mult_indices = set()
                    for n in nodes_list:
                        mi = n.get("electrical", {}).get("multiplier_index")
                        if mi is not None:
                            mult_indices.add(mi)
                    m_count = len(mult_indices)
                else:
                    m_count = 1

                # Fingers per multiplier
                if has_finger:
                    fingers_per_mult = {}
                    for n in nodes_list:
                        mi = n.get("electrical", {}).get("multiplier_index", 0)
                        fi = n.get("electrical", {}).get("finger_index")
                        if fi is not None:
                            fingers_per_mult.setdefault(mi, set()).add(fi)
                    nf_per_mult = max(len(v) for v in fingers_per_mult.values()) if fingers_per_mult else 1
                else:
                    nf_per_mult = 1

                # For finger-only (no m-level), nf = total members
                if not has_mult:
                    nf_per_mult = len(members)

                # Include ALL devices, even single (m=1, nf=1)

                # Determine if array
                is_array = any(
                    n.get("electrical", {}).get("array_index") is not None
                    for n in nodes_list
                )

                # Build hierarchy info
                hierarchy_info = {
                    "m": m_count,
                    "nf": nf_per_mult,
                    "is_array": is_array,
                }

                # ── Create child groups for two-level hierarchy ──────────────
                child_groups = []
                if m_count > 1 and nf_per_mult > 1:
                    # Group by multiplier index
                    mult_buckets = {}
                    for node, item in members:
                        mi = node.get("electrical", {}).get("multiplier_index", 1)
                        mult_buckets.setdefault(mi, []).append((node, item))

                    for mi in sorted(mult_buckets.keys()):
                        bucket = mult_buckets[mi]
                        bucket.sort(key=lambda m: m[0].get("electrical", {}).get("finger_index", 0))
                        bucket_items = [m[1] for m in bucket]
                        child_name = f"{parent_name}_m{mi}"
                        child_hi = {"m": 1, "nf": len(bucket), "is_array": False}

                        fill, border = _hierarchy_colors[(color_idx + mi) % len(_hierarchy_colors)]
                        child_group = HierarchyGroupItem(
                            child_name, bucket_items, child_hi,
                            color=fill, border_color=border,
                        )
                        # Don't manually set visibility - will be managed by parent's _update_child_visibility()
                        self.scene.addItem(child_group)
                        child_groups.append(child_group)

                # ── Create the parent group ──────────────────────────────────
                fill, border = _hierarchy_colors[color_idx % len(_hierarchy_colors)]
                color_idx += 1

                group_item = HierarchyGroupItem(
                    parent_name, items_list, hierarchy_info,
                    color=fill, border_color=border,
                )

                # Wire up child groups using the new setter method
                if child_groups:
                    group_item.set_child_groups(child_groups)

                # Wire up signals
                group_item.signals.drag_finished.connect(self._on_hierarchy_drag_finished)
                group_item.signals.descend_requested.connect(self._on_hierarchy_descend)
                group_item.signals.ascend_requested.connect(self._on_hierarchy_ascend)

                self.scene.addItem(group_item)
                self._hierarchy_groups.append(group_item)
            except Exception as e:
                print(f"[HIERARCHY ERROR] Failed to create group for {parent_name}: {e}")
                import traceback
                traceback.print_exc()
                continue

        # ── Step 3: Create hierarchy groups for standalone devices ─────────
        _dev_diag_print(f"[HIERARCHY] Processing {len(standalone_devices)} standalone devices")
        for node, item in standalone_devices:
            try:
                dev_id = node.get("id", "unknown")
                
                # Get m and nf from the node data
                m = node.get("parameters", {}).get("m", 1)
                nf = node.get("parameters", {}).get("nf", 1)
                
                _dev_diag_print(f"[HIERARCHY] Standalone {dev_id}: m={m}, nf={nf}")
                
                hierarchy_info = {
                    "m": m,
                    "nf": nf,
                    "is_array": False,
                }
                
                fill, border = _hierarchy_colors[color_idx % len(_hierarchy_colors)]
                color_idx += 1
                
                group_item = HierarchyGroupItem(
                    dev_id, [item], hierarchy_info,
                    color=fill, border_color=border,
                )
                
                _dev_diag_print(
                    f"[HIERARCHY] Created group for {dev_id}: _is_descended={group_item._is_descended}, device visible={item.isVisible()}"
                )
                
                # Wire up signals
                group_item.signals.drag_finished.connect(self._on_hierarchy_drag_finished)
                group_item.signals.descend_requested.connect(self._on_hierarchy_descend)
                group_item.signals.ascend_requested.connect(self._on_hierarchy_ascend)
                
                self.scene.addItem(group_item)
                self._hierarchy_groups.append(group_item)
            except Exception as e:
                dev_id = node.get("id", "unknown")
                print(f"[HIERARCHY ERROR] Failed to create standalone group for {dev_id}: {e}")
                import traceback
                traceback.print_exc()
                continue

        # Save for later re-wiring when set_terminal_nets arrives
        self._last_parent_groups = parent_groups
        self._last_raw_nodes = nodes

        # ── Pass 2: Matched Group Rigid-Body Locking ──────────────────────
        self._wire_matched_group_locking(parent_groups, nodes)

    def _wire_matched_group_locking(self, parent_groups, raw_nodes):
        """Detect matched device groups and merge their sibling lists.

        After this, dragging ANY finger from ANY device in a matched group
        moves the ENTIRE group as one rigid body.

        Detection: terminal_nets — auto-detect current mirrors (shared G+S)
        with same-type filter and power-net exclusion.
        """
        from collections import defaultdict

        # ─── Reset sibling groups to per-parent defaults ─────────────
        # This prevents unbounded growth when called multiple times
        # (e.g. once from _build_hierarchy_groups, again from set_terminal_nets).
        for parent_name, members in parent_groups.items():
            items_list = [m[1] for m in members]
            for item in items_list:
                if hasattr(item, '_sibling_group'):
                    item._sibling_group = list(items_list)
                    item._match_locked = False

        # ─── Power nets that should NOT trigger matching ─────────────
        _POWER_NETS = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS",
                       "DVDD", "DVSS", "VDDQ", "VSSQ"}

        # ─── Determine device type per parent ────────────────────────
        parent_type = {}     # parent_name -> "nmos" | "pmos"
        for parent_name, members in parent_groups.items():
            for node, item in members:
                dev_type = str(node.get("type", "?")).lower()
                parent_type[parent_name] = dev_type
                break

        # ─── Auto-detect from terminal_nets ──────────────────────────
        matched_sets = []  # list of sets of parent names

        if hasattr(self, '_terminal_nets') and self._terminal_nets:
            # Build parent→nets map: for each parent, get G and S nets
            parent_gs = {}  # parent_name -> (G_net, S_net)
            for parent_name, members in parent_groups.items():
                for node, item in members:
                    fid = node.get("id", "")
                    tnets = self._terminal_nets.get(fid, {})
                    if tnets.get("G") and tnets.get("S"):
                        parent_gs[parent_name] = (tnets["G"], tnets["S"])
                        break  # only need one finger to read nets

            # Group parents sharing the same (G, S) nets
            gs_groups = defaultdict(list)
            for parent_name, (g, s) in parent_gs.items():
                gs_groups[(g, s)].append(parent_name)

            for (g_net, s_net), members in gs_groups.items():
                if len(members) <= 1:
                    continue

                # ── Filter 1: Skip if G is a power net ──────────
                if g_net.upper() in _POWER_NETS:
                    _dev_diag_print(f"[MATCHED GROUP] Skipped: {members} — "
                                    f"G={g_net} is a power net (dummy or static)")
                    continue

                # ── Filter 2: Only match same device type ────────────
                type_groups = defaultdict(list)
                for m in members:
                    type_groups[parent_type.get(m, "?")].append(m)

                for dev_type, typed_members in type_groups.items():
                    if len(typed_members) > 1:
                        matched_sets.append(set(typed_members))
                        _dev_diag_print(
                            f"[MATCHED GROUP] Auto-detected ({dev_type}): "
                            f"{typed_members} (shared G={g_net}, S={s_net})"
                        )

        # ─── Merge sibling groups ────────────────────────────────────
        for matched_parents in matched_sets:
            # Collect ALL DeviceItems from all parents in this matched set
            mega_group = []
            for parent_name in matched_parents:
                if parent_name in parent_groups:
                    for node, item in parent_groups[parent_name]:
                        if hasattr(item, '_sibling_group'):
                            mega_group.append(item)

            if len(mega_group) <= 1:
                continue

            # Replace the sibling_group of EVERY item in the matched set
            # with the mega_group — so dragging any one item drags them all
            for item in mega_group:
                item._sibling_group = mega_group
                item._match_locked = True  # flag for visual feedback

            _dev_diag_print(
                f"[MATCHED GROUP] Merged {len(mega_group)} items across "
                f"{len(matched_parents)} parents: {sorted(matched_parents)}"
            )

    def _on_hierarchy_drag_finished(self, group):
        """After moving a hierarchy group, sync positions back to node data."""
        try:
            self._sync_hierarchy_to_nodes(group)
        except Exception:
            logging.warning("Failed to sync hierarchy after drag", exc_info=True)

    def _on_hierarchy_descend(self, group):
        pass

    def _on_hierarchy_ascend(self, group):
        pass

    def can_select_device(self, device_item):
        """Check if a device item can be selected based on hierarchy state.
        
        Returns True only if the device is accessible at the current hierarchy level.
        Returns False if the device is hidden inside a non-descended hierarchy group.
        """
        try:
            # Check if this device item is currently visible in the scene
            if not device_item.isVisible():
                return False
            
            # Find if this device belongs to any hierarchy group
            for group in self._hierarchy_groups:
                # If this group is visible and NOT descended, block ALL its descendants
                if group.isVisible() and not group._is_descended:
                    # Check if device is in this group's descendants
                    if device_item in group._all_descendant_devices:
                        return False
            
            # Device is not blocked by any hierarchy group
            return True
        except Exception:
            logging.debug("Device selection check error, allowing selection", exc_info=True)
            return True

    def _is_hierarchy_descended(self, device_item, owning_group):
        """Check if the hierarchy chain from device to root is fully descended.
        
        Traverses up the hierarchy tree to ensure all parent groups are descended.
        Returns True if the device CAN be selected, False if it should be blocked.
        """
        current_group = owning_group
        
        # Walk up the hierarchy tree
        while current_group is not None:
            # If this group is not descended, check if it's a top-level group
            if not current_group._is_descended:
                # If this group has a parent, it means we need to block selection
                # because the parent is not descended
                if current_group._parent_group is not None:
                    return False
                # This is a top-level group (no parent), allow selection
                return True
            # Move to parent group
            current_group = current_group._parent_group
        
        # Reached root without issues
        return True

    def descend_selected_hierarchy(self):
        """Descend into the hierarchy group that contains the selected device(s).
        
        If a device is selected that belongs to a non-descended hierarchy group,
        this will descend into that group to allow child selection.
        """
        try:
            selected = [s for s in self.scene.selectedItems() 
                       if hasattr(s, 'device_name')]
            if not selected:
                return
            
            # Get the first selected device
            device_item = selected[0]
            
            # Find its hierarchy group
            for group in self._hierarchy_groups:
                if device_item in group._all_descendant_devices:
                    # Found the owning group
                    if not group._is_descended:
                        # Descend into this group
                        if group.has_children():
                            group.descend()
                    return
        except Exception:
            logging.debug("Failed to descend into selected hierarchy", exc_info=True)

    def descend_nearest_hierarchy(self):
        """Descend into the currently selected hierarchy group."""
        try:
            # Find which hierarchy group is selected
            for group in self._hierarchy_groups:
                if group.isSelected() and not group._is_descended:
                    group.descend()
                    return
        except Exception:
            logging.debug("Failed to descend into nearest hierarchy", exc_info=True)

    def ascend_all_hierarchy(self):
        """Ascend from all descended hierarchy groups."""
        try:
            for group in self._hierarchy_groups:
                if group._is_descended:
                    group.ascend()
        except Exception:
            logging.warning("Failed to ascend all hierarchies", exc_info=True)

    def _sync_hierarchy_to_nodes(self, group):
        """Update node data when a hierarchy group is moved."""
        try:
            self._sync_node_positions()
        except Exception:
            logging.warning("Failed to sync hierarchy to nodes", exc_info=True)

    # NOTE: keyPressEvent is defined later in the class (line ~1351)
    # The actual keyPressEvent handles both Escape (ascend) and D (descend)

    def _abut_pair_score(self, left_item, right_item):
        """Score how desirable it is to place left_item immediately before right_item."""
        left_nets = self._terminal_nets.get(left_item.device_name, {})
        right_nets = self._terminal_nets.get(right_item.device_name, {})
        if not left_nets or not right_nets:
            return 0

        score = 0

        def add_if_equal(term_a, term_b, weight):
            nonlocal score
            net_a = left_nets.get(term_a)
            net_b = right_nets.get(term_b)
            if net_a and net_b and net_a == net_b:
                score += weight

        # Strong preference for common drain/source sharing.
        add_if_equal("D", "D", 9)
        add_if_equal("S", "S", 7)
        add_if_equal("D", "S", 4)
        add_if_equal("S", "D", 4)
        # Gate commonality is weaker.
        add_if_equal("G", "G", 1)
        return score

    def _order_row_items(self, items):
        """Order row items so net-sharing neighbors (especially D-common) abut."""
        ordered_by_x = sorted(items, key=lambda it: it.pos().x())
        if len(ordered_by_x) <= 1 or not self._terminal_nets:
            return ordered_by_x

        with_nets = [
            it for it in ordered_by_x if self._terminal_nets.get(it.device_name)
        ]
        if len(with_nets) < 2:
            return ordered_by_x

        remaining = list(ordered_by_x)

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

        # If no useful net signal exists, keep geometric order.
        adjacency_gain = sum(
            self._abut_pair_score(row[i], row[i + 1])
            for i in range(len(row) - 1)
        )
        if adjacency_gain <= 0:
            return ordered_by_x
        return row

    def _compact_rows_abutted(self, row_keys=None):
        """Pack row devices edge-to-edge to emulate abutted placement rows."""
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
            ordered = self._order_row_items(items)
            x_cursor = self._snap_value(min(it.pos().x() for it in ordered))
            for i, it in enumerate(ordered):
                it.setPos(x_cursor, row_y)
                
                # Default: move by full width
                next_step = it.rect().width()
                
                # If this device and the NEXT device both have abutment flags on their touching edge,
                # use the validator's expected 0.070um spacing.
                if i < len(ordered) - 1:
                    next_it = ordered[i+1]
                    if it.get_abut_right() and next_it.get_abut_left():
                        # 0.070um is the magic "overlap" pitch for shared diffusion in the AI validator
                        next_step = 0.070 * self.scale_factor
                
                x_cursor += next_step

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
        """Override the automatic row gap.

        Args:
            gap_px: gap in scene pixels between rows, or None for auto.
        """
        self._custom_row_gap = float(gap_px) if gap_px is not None else None

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
                if hasattr(it, "device_name"):
                    ids.append(it.device_name)
        except RuntimeError:
            return []
        return ids

    def flip_devices_h(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_horizontal"):
                item.flip_horizontal()

    def flip_devices_v(self, dev_ids):
        for dev_id in dev_ids:
            item = self.device_items.get(dev_id)
            if item and hasattr(item, "flip_vertical"):
                item.flip_vertical()

    def _interval_overlap(self, a_start, a_end, b_start, b_end):
        return not (a_end < b_start or b_end < a_start)

    def _item_slot_span(self, item):
        start = int(round(self._snap_value(item.pos().x()) / self._snap_grid))
        span = max(1, int(math.ceil(item.rect().width() / self._snap_grid)))
        return start, start + span - 1, span

    def resolve_overlaps(self, anchor_ids=None):
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
                row_anchors = [it for it in items if it.device_name in anchors]
                if not row_anchors:
                    continue
            else:
                row_anchors = sorted(items, key=lambda it: it.device_name)

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

        # ── Re-run matched group locking now that nets are available ──
        # This fixes the timing bug: load_placement runs _build_hierarchy_groups
        # before terminal_nets is set, so the auto-detection found nothing.
        if (self._terminal_nets
                and hasattr(self, '_last_parent_groups')
                and self._last_parent_groups):
            _dev_diag_print("[TERMINAL NETS] Re-wiring matched groups with net data...")
            self._wire_matched_group_locking(
                self._last_parent_groups, self._last_raw_nodes)

        # Re-pack with net-aware adjacency — but NOT if compact was suppressed.
        if self._skip_compaction:
            self._skip_compaction = False
            return
        if self.device_items:
            self._compact_rows_abutted()
            self.resetCachedContent()

    # ------------------------------------------------------------------
    # Abutment candidate highlighting
    # ------------------------------------------------------------------
    def apply_abutment(self) -> list:
        """Detect abutment candidates and highlight their terminal edges.

        Scans ALL transistor pairs (regardless of current position) for shared
        Source/Drain nets.  Highlights the matching edge of each device with a
        bright green glow so the user can see which terminals can be shared.

        Returns:
            list of candidate dicts (see abutment_engine for schema).
        """
        if find_abutment_candidates is None or not self._terminal_nets:
            return []

        # Build nodes from current items (type info only — position not needed)
        nodes = []
        for dev_id, item in self.device_items.items():
            if isinstance(item, (ResistorItem, CapacitorItem)):
                continue
            nodes.append({
                "id":   dev_id,
                "type": getattr(item, "device_type", "nmos"),
            })

        candidates = find_abutment_candidates(nodes, self._terminal_nets)
        edge_map   = build_edge_highlight_map(candidates)   # {dev_id: {left: net, right: net}}

        # Apply highlights to each item
        for dev_id, item in self.device_items.items():
            if not hasattr(item, "set_candidate_highlight"):
                continue
            edges = edge_map.get(dev_id, {})
            item.set_candidate_highlight(
                left_net  = edges.get("left"),
                right_net = edges.get("right"),
            )

        self._abutment_candidates = candidates
        self._abutment_active     = True
        self.scene.update()
        return candidates

    def clear_abutment(self):
        """Remove all abutment candidate highlights."""
        for item in self.device_items.values():
            if hasattr(item, "clear_candidate_highlight"):
                item.clear_candidate_highlight()
        self._abutment_candidates = []
        self._abutment_active     = False
        self.scene.update()

    def is_abutment_active(self):
        return getattr(self, "_abutment_active", False)

    def get_abutment_candidates(self) -> list:
        """Return the last computed abutment candidate list."""
        return getattr(self, "_abutment_candidates", [])

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
        """Remove all connection lines and labels from the scene."""
        if self._conn_lines:
            self.scene.blockSignals(True)
            for item in self._conn_lines:
                self.scene.removeItem(item)
            self._conn_lines.clear()
            self.scene.blockSignals(False)

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
        """Emit device_clicked when user selects a device on the canvas.
        
        Also enforces hierarchy selection rules - devices cannot be selected
        if their parent hierarchy group is not descended.
        """
        # Prevent recursive selection updates
        if getattr(self, '_selection_updating', False):
            return
        
        try:
            selected = [s for s in self.scene.selectedItems()
                        if hasattr(s, 'device_name')]
        except RuntimeError:
            return  # scene deleted during shutdown
        
        if not selected:
            self._clear_connections()
            return
        
        # Block selection of devices whose hierarchy is not descended
        self.scene.blockSignals(True)
        try:
            for item in list(selected):
                if hasattr(item, 'device_name'):
                    if not self.can_select_device(item):
                        # Deselect this item - it's not accessible at current hierarchy level
                        item.setSelected(False)
            
            # Re-fetch the selection after blocking
            selected = [s for s in self.scene.selectedItems()
                        if hasattr(s, 'device_name')]
        finally:
            self.scene.blockSignals(False)
        
        # If no devices remain selectable, clear connections and return
        if not selected:
            self._clear_connections()
            return
        
        # Emit the signal for the first selectable device
        dev_id = selected[0].device_name
        self.device_clicked.emit(dev_id)
        self._show_connections(dev_id)

    def fit_to_view(self):
        """Zoom and pan to fit all devices in the viewport."""
        if not self.device_items:
            return
        rects = [item.sceneBoundingRect() for item in self.device_items.values()]
        # Also include hierarchy group bounding rects
        try:
            for g in self._hierarchy_groups:
                if g.isVisible():
                    rects.append(g.sceneBoundingRect())
                for child in g._child_groups:
                    if child.isVisible():
                        rects.append(child.sceneBoundingRect())
        except Exception:
            logging.debug("Failed to collect hierarchy bounding rects", exc_info=True)
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

    def keyPressEvent(self, event):
        """Handle global keyboard shortcuts."""
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all_devices()
            event.accept()
            return
        if event.key() == Qt.Key.Key_D and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.dummy_toggle_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            # Ascend from any descended hierarchy groups
            try:
                for g in self._hierarchy_groups:
                    if g._is_descended:
                        g.ascend()
                        event.accept()
                        return
            except Exception:
                logging.debug("Escape key hierarchy ascend failed", exc_info=True)
        elif event.key() == Qt.Key.Key_D and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+D - descend into hierarchy
            try:
                self.descend_nearest_hierarchy()
                event.accept()
                return
            except Exception:
                logging.debug("Ctrl+D descend failed", exc_info=True)
        super().keyPressEvent(event)

    # -------------------------------------------------
    # Block Overlays
    # -------------------------------------------------
    def set_blocks(self, blocks):
        """Store block data, draw overlays, and create BlockItems.

        Args:
            blocks: {inst_name: {"subckt": str, "devices": [str, ...]}}
        """
        self._blocks = blocks or {}
        
        # Clean up existing block items
        self.scene.blockSignals(True)
        for bitm in self._block_items:
            try:
                if bitm.scene() is self.scene:
                    self.scene.removeItem(bitm)
            except RuntimeError:
                pass
        self._block_items.clear()
        self.scene.blockSignals(False)
        
        self._clear_block_overlays()
        
        # Instantiate BlockItems for symbol view
        if self._blocks and self.device_items:
            for idx, (inst_name, info) in enumerate(self._blocks.items()):
                devices = info.get("devices", [])
                subckt = info.get("subckt", "?")

                # Collect device items
                member_items = [
                    self.device_items[d] for d in devices
                    if d in self.device_items
                ]
                if not member_items:
                    continue

                # --- Fix 3: Regroup PMOS above, NMOS below, tightly packed ---
                pmos_items = [it for it in member_items
                              if str(getattr(it, 'device_type', '')).strip().lower() == 'pmos']
                nmos_items = [it for it in member_items
                              if str(getattr(it, 'device_type', '')).strip().lower() != 'pmos']

                # Determine reference cell dimensions from the actual items
                ref_w = member_items[0].rect().width()
                ref_h = member_items[0].rect().height()

                # Pick a starting x from the leftmost current position of ANY
                # member so the block stays near its original screen location.
                x_start = min(it.pos().x() for it in member_items)
                # Use minimum y of PMOS items (or all items) as row top so
                # block stays near its original vertical position.
                y_start = min(it.pos().y() for it in member_items)

                # Place PMOS row
                x_cursor = x_start
                for it in pmos_items:
                    it.setPos(x_cursor, y_start)
                    x_cursor += it.rect().width()  # abutted, no gap

                # Place NMOS row immediately below PMOS (no gap, no overlap)
                nmos_y = y_start + ref_h
                x_cursor = x_start
                for it in nmos_items:
                    it.setPos(x_cursor, nmos_y)
                    x_cursor += it.rect().width()  # abutted, no gap

                # --- Fix 2: Uniform color for all blocks ---
                fill_color = QColor(self._block_fill_solid)
                border_color = QColor(self._block_border_solid)

                bitm = BlockItem(inst_name, subckt, member_items, fill_color, border_color)
                bitm.set_snap_grid(self._snap_grid, self._row_pitch)
                self.scene.addItem(bitm)
                self._block_items.append(bitm)
        
        # Apply the correct view state visibility
        self.set_view_level(self._view_level)

    def set_view_level(self, level):
        """Toggle between symbol view and transistor view."""
        self._view_level = level
        self.scene.blockSignals(True)
        
        if level == "symbol":
            # Show BlockItems, hide block overlays, hide internal DeviceItems
            for bitm in self._block_items:
                bitm.show()
                # Hide devices managed by this block
                for dev in bitm._device_items:
                    dev.hide()
                    
            self._clear_block_overlays()
            
        elif level == "transistor":
            # Hide BlockItems, show DeviceItems, show block overlays
            for bitm in self._block_items:
                bitm.hide()
                # Re-sync their bounds before showing devices (in case they moved)
                for dev in bitm._device_items:
                    dev.show()
                    
            if self._block_overlays_visible:
                self._draw_block_overlays()
                
        self.scene.blockSignals(False)
        # Force a refresh of connections
        self.resetCachedContent()


    def _clear_block_overlays(self):
        """Remove all block overlay items from the scene."""
        if self._block_overlays:
            self.scene.blockSignals(True)
            for item in self._block_overlays:
                try:
                    if item.scene() is self.scene:
                        self.scene.removeItem(item)
                except RuntimeError:
                    pass
            self._block_overlays.clear()
            self.scene.blockSignals(False)

    def _draw_block_overlays(self):
        """Draw semi-transparent colored rectangles around each block's devices."""
        if not self._blocks or not self.device_items:
            return

        from PySide6.QtGui import QFont
        from PySide6.QtCore import QRectF

        self.scene.blockSignals(True)
        for idx, (inst_name, info) in enumerate(self._blocks.items()):
            devices = info.get("devices", [])
            subckt = info.get("subckt", "?")

            # Collect bounding rects of member devices
            member_items = [
                self.device_items[d] for d in devices
                if d in self.device_items
            ]
            if not member_items:
                continue

            # Compute union bounding box — flush, no padding
            union = member_items[0].sceneBoundingRect()
            for item in member_items[1:]:
                union = union.united(item.sceneBoundingRect())

            # --- Fix 2: Uniform color for all overlays ---
            fill_color = self._block_fill_color
            border_color = self._block_border_color

            # Draw overlay rectangle flush around devices
            rect_item = QGraphicsRectItem(union)
            rect_item.setBrush(QBrush(fill_color))
            rect_item.setPen(QPen(border_color, 1.5, Qt.PenStyle.DashLine))
            rect_item.setZValue(-10)  # behind devices
            rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            rect_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            rect_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(rect_item)
            self._block_overlays.append(rect_item)

            # --- Fix 4: Label with subtle pill background for readability ---
            label_text = f"{inst_name}: {subckt}"
            label_item = QGraphicsSimpleTextItem(label_text)
            label_item.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            label_item.setBrush(QBrush(QColor("#d0e8ff")))
            label_item.setPos(union.x() + 3, union.y() - 14)
            label_item.setZValue(-9)
            label_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(label_item)
            self._block_overlays.append(label_item)

            # Pill background behind label text
            lbr = label_item.boundingRect()
            pill = QGraphicsRectItem(
                union.x() + 1, union.y() - 15,
                lbr.width() + 6, lbr.height() + 2,
            )
            pill.setBrush(QBrush(QColor(20, 30, 50, 180)))
            pill.setPen(QPen(Qt.PenStyle.NoPen))
            pill.setZValue(-9.5)
            pill.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            pill.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene.addItem(pill)
            self._block_overlays.append(pill)

        self.scene.blockSignals(False)

    def toggle_block_overlays(self, visible=None):
        """Toggle block overlay visibility.

        Args:
            visible: if None, toggle; if bool, set explicitly.
        """
        if visible is None:
            self._block_overlays_visible = not self._block_overlays_visible
        else:
            self._block_overlays_visible = bool(visible)

        if self._block_overlays_visible:
            self._clear_block_overlays()
            self._draw_block_overlays()
        else:
            self._clear_block_overlays()

    def highlight_block(self, inst_name):
        """Select all devices belonging to a block."""
        info = self._blocks.get(inst_name, {})
        devices = info.get("devices", [])
        if not devices:
            return
        self.scene.blockSignals(True)
        self.scene.clearSelection()
        for dev_id in devices:
            item = self.device_items.get(dev_id)
            if item:
                item.setSelected(True)
        self.scene.blockSignals(False)

    # -------------------------------------------------
    # Background Grid
    # -------------------------------------------------
    def drawBackground(self, painter: QPainter, rect):
        """Draw row track bands — occupied and virtual (empty) rows."""
        super().drawBackground(painter, rect)

        # Draw a beautiful infinite cartesian grid
        left = int(math.floor(rect.left()))
        right = int(math.ceil(rect.right()))
        top = int(math.floor(rect.top()))
        bottom = int(math.ceil(rect.bottom()))

        first_left = left - (left % self._grid_size)
        first_top = top - (top % self._grid_size)

        lines_minor = []
        lines_major = []
        
        from PySide6.QtCore import QLineF
        for x in range(first_left, right, self._grid_size):
            if x % (self._grid_size * 5) == 0:
                lines_major.append(QLineF(x, top, x, bottom))
            else:
                lines_minor.append(QLineF(x, top, x, bottom))
                
        for y in range(first_top, bottom, self._grid_size):
            if y % (self._grid_size * 5) == 0:
                lines_major.append(QLineF(left, y, right, y))
            else:
                lines_minor.append(QLineF(left, y, right, y))
                
        # Draw minor grid lines
        painter.setPen(QPen(self._grid_color, 1.0))
        painter.drawLines(lines_minor)
        
        # Draw major grid lines
        painter.setPen(QPen(self._grid_color_major, 1.2))
        painter.drawLines(lines_major)

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
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.viewport().setFocus(Qt.FocusReason.MouseFocusReason)
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

    # -------------------------------------------------
    # Right-click context menu — per-device abutment
    # -------------------------------------------------
    def set_centroid_markers(self, markers: List[Dict]):
        """
        Render crosshair (+) markers at given positions.
        markers: [{'x': 100, 'y': 200, 'color': QColor, 'label': 'M0'}]
        """
        for item in self._centroid_items:
            self.scene.removeItem(item)
        self._centroid_items.clear()
        
        for m in markers:
            x, y = m['x'], m['y']
            color = m.get('color', QColor(Qt.GlobalColor.white))
            
            size = 15
            # Vertical line
            v_line = self.scene.addLine(x, y - size, x, y + size, QPen(color, 2))
            v_line.setZValue(100)
            self._centroid_items.append(v_line)
            # Horizontal line
            h_line = self.scene.addLine(x - size, y, x + size, y, QPen(color, 2))
            h_line.setZValue(100)
            self._centroid_items.append(h_line)
            
            # Label
            if 'label' in m:
                txt = self.scene.addSimpleText(m['label'])
                txt.setBrush(QBrush(color))
                txt.setPos(x + 5, y + 5)
                txt.setZValue(100)
                self._centroid_items.append(txt)

    def contextMenuEvent(self, event):
        """Show a right-click menu to manually toggle abutment on a device."""
        scene_pos = self.mapToScene(event.pos())
        items = self.scene.items(scene_pos)

        # Find the topmost DeviceItem under the cursor
        target_item = None
        target_id   = None
        for it in items:
            if isinstance(it, DeviceItem):
                target_item = it
                # Reverse-lookup device id
                for dev_id, item in self.device_items.items():
                    if item is target_item:
                        target_id = dev_id
                        break
                break

        if target_item is None or target_id is None:
            super().contextMenuEvent(event)
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e2130;
                color: #e0e0e0;
                border: 1px solid #3a3f55;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI';
            }
            QMenu::item {
                padding: 5px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3a3f55;
            }
            QMenu::separator {
                height: 1px;
                background: #3a3f55;
                margin: 3px 8px;
            }
        """)

        # Title (non-interactive label)
        title_act = QAction(f"Device: {target_id}", self)
        title_act.setEnabled(False)
        menu.addAction(title_act)
        menu.addSeparator()

        # Left abutment toggle
        act_left = QAction("Left Abutment", self)
        act_left.setCheckable(True)
        act_left.setChecked(target_item.get_abut_left())
        act_left.setToolTip(
            "Enable leftAbut on this device.\n"
            "The left diffusion strip will be shared with the left neighbour."
        )
        menu.addAction(act_left)

        # Right abutment toggle
        act_right = QAction("Right Abutment", self)
        act_right.setCheckable(True)
        act_right.setChecked(target_item.get_abut_right())
        act_right.setToolTip(
            "Enable rightAbut on this device.\n"
            "The right diffusion strip will be shared with the right neighbour."
        )
        menu.addAction(act_right)

        menu.addSeparator()

        act_clear = QAction("Clear Both", self)
        menu.addAction(act_clear)

        chosen = menu.exec(event.globalPos())

        if chosen == act_left:
            target_item.toggle_abut_left()
        elif chosen == act_right:
            target_item.toggle_abut_right()
        elif chosen == act_clear:
            target_item.set_abut_left(False)
            target_item.set_abut_right(False)

    # -------------------------------------------------
    # Export helper — abutment state for OAS writer
    # -------------------------------------------------
    def get_device_abutment_states(self) -> dict:
        """Return per-device abutment flags for all transistors.

        Returns:
            {dev_id: {"abut_left": bool, "abut_right": bool}}
        Only entries where at least one flag is True are included.
        """
        result = {}
        for dev_id, item in self.device_items.items():
            if not isinstance(item, DeviceItem):
                continue
            al = item.get_abut_left()
            ar = item.get_abut_right()
            if al or ar:
                result[dev_id] = {"abut_left": al, "abut_right": ar}
        return result

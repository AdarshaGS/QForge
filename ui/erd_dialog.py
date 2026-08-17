"""Read-only ER diagram — table nodes + FK relationship lines on a
QGraphicsView canvas (issues #64/#65), rendering the graph built by
services/erd_model.py (issue #63).

No schema-modifying actions live here (issue #62's stated scope:
visualization and navigation only). Opening a table or viewing its
structure from the diagram delegates back to the caller via signals —
this dialog has no knowledge of ConnectionPanel.

Nodes are draggable and collapsible; layout (position + collapsed state)
persists per connection+database via utils/erd_layout.py, so edges must
recompute their geometry on demand rather than being fixed at build time
(see _RelationshipLineItem.update_geometry).
"""
import hashlib
import math
import threading

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsRectItem,
    QGraphicsSimpleTextItem, QGraphicsPathItem, QMenu, QTreeWidget,
    QTreeWidgetItem, QWidget,
)

from services.erd_model import build_erd_graph, fetch_table_indexes
from services.entitlements import Edition, Feature, Limit, entitlements
from ui.upgrade_dialog import UpgradeDialog
from utils import erd_layout

_HEADER_H = 26
_ROW_H = 18
_NODE_W = 220
_PAD = 6
_ROW_GAP_X = 100
_ROW_GAP_Y = 100
_MARK_SIZE = 9
_MINIMAP_W = 200
_MINIMAP_H = 130

# Per-table header color cycle (light, dark) — keyed by a stable hash of the
# table name so colors don't reshuffle between reloads.
_HEADER_PALETTE = [
    ("#d0e8ff", "#1a3a5c"),  # blue
    ("#e6d8ff", "#3a1a5c"),  # purple
    ("#d8f5e0", "#1a4d33"),  # green
    ("#fff2cc", "#5c4a1a"),  # gold
    ("#ffe0cc", "#5c331a"),  # orange
    ("#ffd8e8", "#5c1a3a"),  # pink
    ("#cceee8", "#1a4d47"),  # teal
    ("#e0e0ff", "#28285c"),  # indigo
    ("#f0f0d8", "#4d4d1a"),  # olive
    ("#e8e0d0", "#4d3d1a"),  # tan
]


def _header_color(table_name: str, is_dark: bool) -> QColor:
    # usedforsecurity=False: this is a cosmetic bucket-pick, not a security
    # hash (Python 3.9+; sha1 would otherwise get flagged as weak crypto).
    idx = int(hashlib.sha1(table_name.encode(), usedforsecurity=False).hexdigest(), 16) % len(_HEADER_PALETTE)
    light, dark = _HEADER_PALETTE[idx]
    return QColor(dark if is_dark else light)


class _CollapseToggle(QGraphicsSimpleTextItem):
    """Small header glyph that collapses/expands its parent table node,
    intercepting its own clicks so they don't fall through to node
    selection or drag."""

    def __init__(self, node, color: QColor):
        super().__init__("▾", node)
        self._node = node
        self.setBrush(QBrush(color))
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(2)

    def set_collapsed(self, collapsed: bool):
        self.setText("▸" if collapsed else "▾")

    def mousePressEvent(self, event):
        self._node.toggle_collapsed()
        event.accept()


class _TableNodeItem(QGraphicsRectItem):
    """One table box: header + one row per column. Draggable
    (ItemIsMovable) and collapsible; both notify connected edges via
    update_geometry() and persist through the node's on_layout_changed
    callback."""

    def __init__(self, table, is_dark: bool, header_color: QColor, on_select, on_open,
                 on_view_structure, on_layout_changed):
        columns = table.columns
        height = _HEADER_H + max(1, len(columns)) * _ROW_H + _PAD
        super().__init__(0, 0, _NODE_W, height)
        self.table_name = table.name
        self._on_select = on_select
        self._on_open = on_open
        self._on_view_structure = on_view_structure
        self._on_layout_changed = on_layout_changed
        self.column_y = {}  # column name -> box-local y at the row's center
        self.selected = False
        self.edges = []  # list[_RelationshipLineItem] touching this node
        self._collapsed = False
        self._expanded_height = height
        self._press_pos = self.pos()

        header_fg = QColor("#ffffff") if is_dark else QColor("#0a2540")
        body_bg = QColor("#1c1c1e") if is_dark else QColor("#ffffff")
        body_fg = QColor("#e5e5ea") if is_dark else QColor("#1c1c1e")
        self._border_color = QColor("#3a3a3c") if is_dark else QColor("#c8c8cc")

        self.setBrush(QBrush(body_bg))
        self.setPen(QPen(self._border_color, 1))
        self.setZValue(1)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.OpenHandCursor)

        header = QGraphicsRectItem(0, 0, _NODE_W, _HEADER_H, self)
        header.setBrush(QBrush(header_color))
        header.setPen(QPen(header_color))
        header.setAcceptedMouseButtons(Qt.NoButton)

        title = QGraphicsSimpleTextItem(table.name, self)
        f = QFont()
        f.setBold(True)
        title.setFont(f)
        title.setBrush(QBrush(header_fg))
        title.setPos(_PAD, 5)
        title.setAcceptedMouseButtons(Qt.NoButton)

        self._toggle = _CollapseToggle(self, header_fg)
        self._toggle.setPos(_NODE_W - 18, 4)

        self._column_items = []
        y = _HEADER_H
        for col in columns:
            marker = "\U0001F511 " if col.is_primary_key else ("\U0001F517 " if col.is_foreign_key else "")
            label = f"{marker}{col.name}"
            if col.data_type:
                label += f"  {col.data_type}"
            text = QGraphicsSimpleTextItem(label, self)
            cf = QFont()
            cf.setBold(col.is_primary_key)
            text.setFont(cf)
            text.setBrush(QBrush(body_fg))
            text.setPos(_PAD, y + 2)
            text.setAcceptedMouseButtons(Qt.NoButton)
            self.column_y[col.name] = y + _ROW_H / 2
            self._column_items.append(text)
            y += _ROW_H

    def set_highlighted(self, on: bool):
        self.selected = on
        self.setPen(QPen(QColor("#0A84FF") if on else self._border_color, 2 if on else 1))

    def anchor_point(self, column_name: str, from_right: bool) -> QPointF:
        y = (_HEADER_H / 2) if self._collapsed else self.column_y.get(column_name, _HEADER_H / 2)
        x = _NODE_W if from_right else 0
        return self.mapToScene(QPointF(x, y))

    def set_collapsed(self, collapsed: bool):
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        for item in self._column_items:
            item.setVisible(not collapsed)
        rect = self.rect()
        rect.setHeight((_HEADER_H + _PAD) if collapsed else self._expanded_height)
        self.setRect(rect)
        self._toggle.set_collapsed(collapsed)
        for edge in self.edges:
            edge.update_geometry()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)
        if self._on_layout_changed:
            self._on_layout_changed()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_geometry()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_select(self)
            self._press_pos = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.pos() != self._press_pos and self._on_layout_changed:
            self._on_layout_changed()

    def mouseDoubleClickEvent(self, event):
        self._on_open(self.table_name)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        open_action = menu.addAction("\U0001F4CB Open Table")
        structure_action = menu.addAction("\U0001F50D View Structure")
        action = menu.exec_(event.screenPos())
        if action == open_action:
            self._on_open(self.table_name)
        elif action == structure_action:
            self._on_view_structure(self.table_name)


def _curve_crosses_obstacles(p1: QPointF, c1: QPointF, c2: QPointF, p2: QPointF, obstacles: list) -> bool:
    path = QPainterPath(p1)
    path.cubicTo(c1, c2, p2)
    for i in range(1, 12):
        pt = path.pointAtPercent(i / 12)
        if any(rect.contains(pt) for rect in obstacles):
            return True
    return False


def _build_curved_path(p1: QPointF, p2: QPointF, obstacles: list):
    """Cubic-bezier connector, bowed perpendicular to the straight line so
    it reads clearly instead of cutting through other tables. Not a full
    obstacle-avoiding router (no graph-layout dependency in this repo) —
    just a few widening attempts, sampled against *other* nodes'
    bounding rects, before giving up and using the last attempt anyway.
    Returns (path, tangent_at_p1, tangent_at_p2) as unit vectors pointing
    away from each endpoint, for cardinality-mark placement.
    """
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    dist = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / dist, dx / dist
    base_offset = min(max(dist * 0.25, 20), 90)

    c1 = c2 = None
    for attempt in range(3):
        offset = base_offset * (1 + attempt * 0.7)
        c1 = QPointF(p1.x() + dx * 0.33 + nx * offset, p1.y() + dy * 0.33 + ny * offset)
        c2 = QPointF(p1.x() + dx * 0.67 + nx * offset, p1.y() + dy * 0.67 + ny * offset)
        if not _curve_crosses_obstacles(p1, c1, c2, p2, obstacles):
            break

    path = QPainterPath(p1)
    path.cubicTo(c1, c2, p2)

    t1x, t1y = c1.x() - p1.x(), c1.y() - p1.y()
    t1len = math.hypot(t1x, t1y) or 1.0
    t2x, t2y = c2.x() - p2.x(), c2.y() - p2.y()
    t2len = math.hypot(t2x, t2y) or 1.0
    return path, (t1x / t1len, t1y / t1len), (t2x / t2len, t2y / t2len)


def _append_cardinality_marks(path: QPainterPath, p: QPointF, tangent: tuple, many: bool, size: float = _MARK_SIZE):
    """Appends a crow's-foot ("many") or a single tick ("one") to *path* at
    endpoint *p*, oriented along *tangent* (unit vector pointing away from
    the table, into the connector)."""
    ux, uy = tangent
    perp_x, perp_y = -uy, ux
    if many:
        apex = QPointF(p.x() + ux * size, p.y() + uy * size)
        left = QPointF(p.x() + perp_x * size * 0.5, p.y() + perp_y * size * 0.5)
        right = QPointF(p.x() - perp_x * size * 0.5, p.y() - perp_y * size * 0.5)
        for end in (p, left, right):
            path.moveTo(apex)
            path.lineTo(end)
    else:
        center = QPointF(p.x() + ux * size, p.y() + uy * size)
        a = QPointF(center.x() + perp_x * size * 0.6, center.y() + perp_y * size * 0.6)
        b = QPointF(center.x() - perp_x * size * 0.6, center.y() - perp_y * size * 0.6)
        path.moveTo(a)
        path.lineTo(b)


class _RelationshipLineItem(QGraphicsPathItem):
    """One FK -> PK connector, drawn as a curved path with crow's-foot
    ("many"/FK side) or tick ("one"/PK side, both sides when
    is_one_to_one) cardinality marks baked into the same path. Recomputes
    on demand via update_geometry() since nodes can move/collapse."""

    def __init__(self, source_node, source_column: str, target_node, target_column: str,
                 is_one_to_one: bool, is_dark: bool, nodes_ref: dict):
        super().__init__()
        self.source_table = source_node.table_name
        self.target_table = target_node.table_name
        self._source_node = source_node
        self._source_column = source_column
        self._target_node = target_node
        self._target_column = target_column
        self._is_one_to_one = is_one_to_one
        self._nodes = nodes_ref
        self._default_pen = QPen(QColor("#5a5a5e") if is_dark else QColor("#b0b0b5"), 1.4)
        self._highlight_pen = QPen(QColor("#0A84FF"), 2.2)
        self.setPen(self._default_pen)
        self.setZValue(0)
        self.update_geometry()

    def set_highlighted(self, on: bool):
        self.setPen(self._highlight_pen if on else self._default_pen)

    def update_geometry(self):
        src_rect = self._source_node.sceneBoundingRect()
        tgt_rect = self._target_node.sceneBoundingRect()
        from_right = src_rect.center().x() <= tgt_rect.center().x()
        p1 = self._source_node.anchor_point(self._source_column, from_right=from_right)
        p2 = self._target_node.anchor_point(self._target_column, from_right=not from_right)
        obstacles = [n.sceneBoundingRect() for n in self._nodes.values()
                     if n is not self._source_node and n is not self._target_node and n.isVisible()]
        path, t1, t2 = _build_curved_path(p1, p2, obstacles)
        _append_cardinality_marks(path, p1, t1, many=not self._is_one_to_one)
        _append_cardinality_marks(path, p2, t2, many=False)
        self.setPath(path)


class _ErdView(QGraphicsView):
    """Wheel-to-zoom; left-drag on empty canvas (or empty space between
    nodes) pans, left-drag on a node moves it. Dragging starts only when
    nothing is under the cursor, so clicking a node still selects/drags it
    instead of panning."""

    _PAN_SPEED = 0.9  # < 1 damps drag-to-pan so large diagrams don't fly by

    resized = Signal()
    view_changed = Signal()

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self._panning = False
        self._pan_start = None

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self.view_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()
        self.view_changed.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.itemAt(event.pos()) is None:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - round(delta.x() * self._PAN_SPEED))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - round(delta.y() * self._PAN_SPEED))
            self.view_changed.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)


class _MinimapView(QGraphicsView):
    """Small always-fit overview sharing the main scene. Draws the main
    view's visible rect in drawForeground (not as a scene item) so the
    indicator never leaks into the main canvas. Click/drag recenters the
    main view via scene_pos_picked."""

    scene_pos_picked = Signal(QPointF)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setFixedSize(_MINIMAP_W, _MINIMAP_H)
        self.setRenderHint(QPainter.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setInteractive(False)
        self.setStyleSheet("QGraphicsView { border: 1px solid rgba(128,128,128,0.5); }")
        self._tracked_rect = QRectF()

    def refresh_fit(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)

    def set_tracked_rect(self, rect: QRectF):
        self._tracked_rect = rect
        self.viewport().update()

    def drawForeground(self, painter, rect):
        if self._tracked_rect.isEmpty():
            return
        painter.setPen(QPen(QColor("#0A84FF"), 2))
        painter.setBrush(QBrush(QColor(10, 132, 255, 40)))
        painter.drawRect(self._tracked_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.scene_pos_picked.emit(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.scene_pos_picked.emit(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)


class _InspectorPanel(QWidget):
    """Toggleable drawer showing a selected table's columns, indexes, and
    relationships. Hidden by default so large schemas keep full canvas
    width until the user opens it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.title_label = QLabel("")
        f = QFont()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.title_label.setFont(f)
        layout.addWidget(self.title_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree)

    def clear(self):
        self.title_label.setText("")
        self.tree.clear()

    def show_loading(self, table_name: str):
        self.title_label.setText(table_name)
        self.tree.clear()
        QTreeWidgetItem(self.tree, ["Loading indexes…"])

    def populate(self, table, relationships_in: list, relationships_out: list, indexes: list):
        self.title_label.setText(table.name)
        self.tree.clear()

        cols_root = QTreeWidgetItem(self.tree, [f"Columns ({len(table.columns)})"])
        for col in table.columns:
            marker = "PK " if col.is_primary_key else ("FK " if col.is_foreign_key else "")
            QTreeWidgetItem(cols_root, [f"{marker}{col.name}  {col.data_type}"])

        idx_root = QTreeWidgetItem(self.tree, [f"Indexes ({len(indexes)})"])
        for idx in indexes:
            unique = "UNIQUE " if idx.get("unique") else ""
            QTreeWidgetItem(idx_root, [f"{unique}{idx.get('name', '')}  ({idx.get('columns', '')})"])

        rel_root = QTreeWidgetItem(
            self.tree, [f"Relationships ({len(relationships_in) + len(relationships_out)})"])
        for rel in relationships_out:
            mark = "1:1" if rel.is_one_to_one else "1:N"
            QTreeWidgetItem(rel_root, [f"{rel.source_column} → {rel.target_table}.{rel.target_column}  [{mark}]"])
        for rel in relationships_in:
            mark = "1:1" if rel.is_one_to_one else "1:N"
            QTreeWidgetItem(rel_root, [f"{rel.source_table}.{rel.source_column} → {rel.target_column}  [{mark}]"])

        self.tree.expandAll()


class ErdDialog(QDialog):
    """Opens against a connection profile's config dict (same shape passed
    to DbService.connect) and builds its own dedicated connection via
    services.erd_model.build_erd_graph — never touches the caller's live
    connection."""

    open_table = Signal(str)
    view_structure = Signal(str)
    _graph_loaded = Signal(object)
    _graph_load_error = Signal(str)
    _indexes_loaded = Signal(str, list)

    def __init__(self, config: dict, is_dark: bool = True, parent=None, focus_table: str = None):
        super().__init__(parent)
        self._config = config
        self._is_dark = is_dark
        self._focus_table = focus_table
        label = config.get("database") or config.get("name") or ""
        self.setWindowTitle(f"ER Diagram — {label}" if label else "ER Diagram")
        self.resize(1000, 700)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter tables...")
        self.search_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_box)

        # Dedicated icon-button style: the default QPushButton padding
        # (5px 16px) leaves no room for a single glyph in a 28px-wide button,
        # squishing "+"/"−" into invisibility.
        _zoom_btn_style = """
            QPushButton {
                background: transparent;
                color: palette(text);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 0;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton:hover { background: palette(midlight); }
        """

        zoom_in = QPushButton("+")
        zoom_in.setFixedSize(28, 28)
        zoom_in.setStyleSheet(_zoom_btn_style)
        zoom_in.setToolTip("Zoom In")
        toolbar.addWidget(zoom_in)

        zoom_out = QPushButton("−")
        zoom_out.setFixedSize(28, 28)
        zoom_out.setStyleSheet(_zoom_btn_style)
        zoom_out.setToolTip("Zoom Out")
        toolbar.addWidget(zoom_out)

        fit_btn = QPushButton("Fit to Screen")
        toolbar.addWidget(fit_btn)

        refresh_btn = QPushButton("\U0001F504 Refresh")
        toolbar.addWidget(refresh_btn)

        self.status_label = QLabel("")
        toolbar.addWidget(self.status_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        toolbar2 = QHBoxLayout()
        self.zoom_label = QLabel("100%")
        toolbar2.addWidget(self.zoom_label)

        collapse_all_btn = QPushButton("Collapse All")
        toolbar2.addWidget(collapse_all_btn)

        expand_all_btn = QPushButton("Expand All")
        toolbar2.addWidget(expand_all_btn)

        reset_layout_btn = QPushButton("Reset Layout")
        toolbar2.addWidget(reset_layout_btn)

        self.details_btn = QPushButton("Details")
        self.details_btn.setCheckable(True)
        toolbar2.addWidget(self.details_btn)

        legend = QLabel("Marks:   |— One-to-One      |< One-to-Many")
        legend.setStyleSheet("color: gray;")
        toolbar2.addWidget(legend)
        toolbar2.addStretch()
        layout.addLayout(toolbar2)

        # Free-tier truncation banner (issue #86) — hidden unless a Free
        # diagram was actually capped below the database's real table count.
        self._upgrade_banner = QWidget()
        self._upgrade_banner.setStyleSheet("background:#1c3a1c; border-bottom:1px solid #2d6a2d;")
        banner_layout = QHBoxLayout(self._upgrade_banner)
        banner_layout.setContentsMargins(12, 6, 8, 6)
        self._upgrade_banner_label = QLabel("")
        self._upgrade_banner_label.setStyleSheet("color:#5cdb5c; font-weight:600;")
        banner_layout.addWidget(self._upgrade_banner_label, 1)
        upgrade_link_btn = QPushButton("Upgrade to Pro →")
        upgrade_link_btn.clicked.connect(self._show_upgrade_dialog)
        banner_layout.addWidget(upgrade_link_btn)
        self._upgrade_banner.hide()
        layout.addWidget(self._upgrade_banner)

        canvas_row = QHBoxLayout()
        self.scene = QGraphicsScene(self)
        self.view = _ErdView(self.scene, self)
        canvas_row.addWidget(self.view, 1)

        self.inspector = _InspectorPanel()
        self.inspector.setVisible(False)
        canvas_row.addWidget(self.inspector)
        layout.addLayout(canvas_row)

        zoom_in.clicked.connect(self._zoom_in)
        zoom_out.clicked.connect(self._zoom_out)
        fit_btn.clicked.connect(self._fit_to_screen)
        refresh_btn.clicked.connect(self._reload)
        collapse_all_btn.clicked.connect(self._collapse_all)
        expand_all_btn.clicked.connect(self._expand_all)
        reset_layout_btn.clicked.connect(self._reset_layout)
        self.details_btn.toggled.connect(self._toggle_inspector)

        bg = "#1c1c1e" if is_dark else "#ffffff"
        self.scene.setBackgroundBrush(QBrush(QColor(bg)))

        self.minimap = _MinimapView(self.scene, self.view.viewport())
        self.minimap.scene_pos_picked.connect(self._on_minimap_pick)
        self.view.resized.connect(self._position_minimap)
        self.view.view_changed.connect(self._update_minimap_tracking)
        self.view.view_changed.connect(self._update_zoom_label)
        self.view.horizontalScrollBar().valueChanged.connect(self._update_minimap_tracking)
        self.view.verticalScrollBar().valueChanged.connect(self._update_minimap_tracking)
        self._position_minimap()

        self._nodes = {}   # table name -> _TableNodeItem
        self._edges = []   # list[_RelationshipLineItem]
        self._selected_node = None
        self._graph = None
        self._index_cache = {}  # table name -> list[dict] (lazy, session-scoped)
        self._inspector_open = False  # explicit toggle state — isVisible()
        # only reflects reality once the whole dialog has been shown

        self._graph_loaded.connect(self._on_graph_loaded)
        self._graph_load_error.connect(self._on_graph_error)
        self._indexes_loaded.connect(self._on_indexes_loaded)

        self._reload()

    # ── data loading (background thread — same pattern as
    # ConnectionPanel._spawn_schema_fetch) ──────────────────────────────

    def _reload(self):
        self.status_label.setText("⏳ Loading schema metadata…")
        self.scene.clear()
        self._nodes = {}
        self._edges = []
        self._selected_node = None
        self._graph = None
        self._index_cache = {}
        self.inspector.clear()

        config = dict(self._config)
        sig_done = self._graph_loaded
        sig_error = self._graph_load_error
        # Only applies when loading the whole database (not a table_names-
        # scoped view) — build_erd_graph itself ignores max_tables whenever
        # table_names is given, so passing it unconditionally here is safe.
        max_tables = None
        if entitlements.edition() is Edition.FREE:
            max_tables = entitlements.limit(Limit.ER_DIAGRAM_TABLES)

        def _worker():
            try:
                sig_done.emit(build_erd_graph(config, max_tables=max_tables))
            except Exception as ex:
                sig_error.emit(str(ex))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_graph_error(self, msg: str):
        self.status_label.setText(f"⚠ Failed to load schema: {msg}")

    def _on_graph_loaded(self, graph):
        self._graph = graph
        if not graph.tables:
            self.status_label.setText("No tables found.")
            return
        self.status_label.setText(
            f"{len(graph.tables)} table(s), {len(graph.relationships)} relationship(s)")

        truncated = (
            entitlements.edition() is Edition.FREE
            and graph.total_tables_available > len(graph.tables)
        )
        if truncated:
            self._upgrade_banner_label.setText(
                f"Showing {len(graph.tables)} of {graph.total_tables_available} "
                "tables — Free is limited to this diagram size."
            )
            self._upgrade_banner.show()
        else:
            self._upgrade_banner.hide()

        self._build_scene(graph)
        if self._focus_table:
            self._focus_on_table(self._focus_table)
            self._focus_table = None  # only auto-focus once, on first load

    def _show_upgrade_dialog(self):
        cap = entitlements.limit(Limit.ER_DIAGRAM_TABLES)
        total = self._graph.total_tables_available if self._graph else cap
        UpgradeDialog(
            "Full ER Diagrams is a Pro feature",
            detail=f"Free accounts see at most {cap} tables per diagram.",
            usage_line=f"{cap} / {total} tables shown" if self._graph else "",
            cta_line="Upgrade to Pro for unlimited ER diagram tables.",
            parent=self,
        ).exec()
        # Re-run so a license activated from inside the dialog reflects
        # immediately without the user having to hit Refresh themselves.
        if entitlements.is_enabled(Feature.ADVANCED_ERD):
            self._upgrade_banner.hide()
            self._reload()

    def _focus_on_table(self, table_name: str):
        """Select *table_name*'s node (highlighting it and its relationship
        edges, same as clicking it) and center the view on it — how
        right-click → Show Diagram lands the user straight on the table
        they asked about instead of the whole-database overview."""
        node = self._nodes.get(table_name)
        if node is None:
            return
        self._on_node_selected(node)
        self.view.centerOn(node)
        self._update_minimap_tracking()

    # ── layout + rendering ───────────────────────────────────────────

    def _build_scene(self, graph):
        # Dependency-free stand-in for a real force-directed layout (no
        # networkx in requirements.txt): keep tables in their natural
        # (discovery) order and flow them into an even grid — roughly
        # sqrt(n) columns per row — left-to-right/top-to-bottom, wrapping by
        # a target row width. A saved per-connection layout (drag/collapse
        # history) takes priority per table; only tables missing from it
        # (new since the last save) fall into the auto-grid, placed below
        # everything the saved layout already occupies.
        ordered = list(graph.tables.values())
        connection_id = self._config.get("id", "")
        database = self._config.get("database", "")
        saved_layout = erd_layout.load(connection_id, database) or {}

        cols_per_row = max(1, math.ceil(len(ordered) ** 0.5))
        target_row_width = cols_per_row * (_NODE_W + _ROW_GAP_X)

        new_nodes = []
        max_saved_bottom = 0
        for table in ordered:
            color = _header_color(table.name, self._is_dark)
            node = _TableNodeItem(table, self._is_dark, color, self._on_node_selected,
                                   self._on_node_open, self._on_node_view_structure,
                                   self._on_layout_changed)
            self.scene.addItem(node)
            self._nodes[table.name] = node

            entry = saved_layout.get(table.name)
            if entry:
                node.setPos(entry.get("x", 0), entry.get("y", 0))
                if entry.get("collapsed"):
                    node.set_collapsed(True)
                max_saved_bottom = max(max_saved_bottom, node.pos().y() + node.rect().height())
            else:
                new_nodes.append(node)

        x = 0
        y = (max_saved_bottom + _ROW_GAP_Y) if saved_layout else 0
        row_height = 0
        for node in new_nodes:
            if x > 0 and x + _NODE_W > target_row_width:
                x = 0
                y += row_height + _ROW_GAP_Y
                row_height = 0
            node.setPos(x, y)
            row_height = max(row_height, node.rect().height())
            x += _NODE_W + _ROW_GAP_X

        for rel in graph.relationships:
            src = self._nodes.get(rel.source_table)
            tgt = self._nodes.get(rel.target_table)
            if src is None or tgt is None:
                continue
            edge = _RelationshipLineItem(src, rel.source_column, tgt, rel.target_column,
                                          rel.is_one_to_one, self._is_dark, self._nodes)
            self.scene.addItem(edge)
            src.edges.append(edge)
            tgt.edges.append(edge)
            self._edges.append(edge)

        if not saved_layout:
            self._save_layout()

        self._reset_view()
        self.minimap.refresh_fit()
        self._update_minimap_tracking()

    def _reset_view(self):
        # Load at a comfortable, readable 100% zoom rather than shrinking
        # the whole graph to fit the dialog — for large schemas that made
        # every table unreadably tiny. Users can still opt into an overview
        # via the "Fit to Screen" button.
        self.view.resetTransform()
        self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().minimum())
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().minimum())
        self._update_zoom_label()
        self._update_minimap_tracking()

    def _fit_to_screen(self):
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.view.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        self._update_zoom_label()
        self._update_minimap_tracking()

    def _zoom_in(self):
        self.view.scale(1.15, 1.15)
        self._update_zoom_label()
        self._update_minimap_tracking()

    def _zoom_out(self):
        self.view.scale(1 / 1.15, 1 / 1.15)
        self._update_zoom_label()
        self._update_minimap_tracking()

    def _update_zoom_label(self):
        pct = round(self.view.transform().m11() * 100)
        self.zoom_label.setText(f"{pct}%")

    def _position_minimap(self):
        margin = 10
        vp = self.view.viewport()
        x = vp.width() - self.minimap.width() - margin
        self.minimap.move(max(0, x), margin)

    def _update_minimap_tracking(self):
        rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        self.minimap.set_tracked_rect(rect)

    def _on_minimap_pick(self, pos):
        self.view.centerOn(pos)
        self._update_minimap_tracking()

    # ── layout persistence ──────────────────────────────────────────

    def _save_layout(self):
        connection_id = self._config.get("id", "")
        database = self._config.get("database", "")
        layout_data = {
            name: {"x": node.pos().x(), "y": node.pos().y(), "collapsed": node._collapsed}
            for name, node in self._nodes.items()
        }
        erd_layout.save(connection_id, database, layout_data)

    def _on_layout_changed(self):
        self._save_layout()

    def _reset_layout(self):
        connection_id = self._config.get("id", "")
        database = self._config.get("database", "")
        erd_layout.clear(connection_id, database)
        self._reload()

    def _collapse_all(self):
        for node in self._nodes.values():
            node.set_collapsed(True)
        self._save_layout()

    def _expand_all(self):
        for node in self._nodes.values():
            node.set_collapsed(False)
        self._save_layout()

    # ── interaction ──────────────────────────────────────────────────

    def _on_node_selected(self, node):
        previously_selected = self._selected_node
        if previously_selected is not None:
            previously_selected.set_highlighted(False)
            for edge in self._edges:
                edge.set_highlighted(False)
        if previously_selected is node:
            self._selected_node = None
            self.inspector.clear()
            return
        node.set_highlighted(True)
        for edge in self._edges:
            if edge.source_table == node.table_name or edge.target_table == node.table_name:
                edge.set_highlighted(True)
        self._selected_node = node
        if self._inspector_open:
            self._populate_inspector(node.table_name)

    def _on_node_open(self, table_name: str):
        self.open_table.emit(table_name)

    def _on_node_view_structure(self, table_name: str):
        self.view_structure.emit(table_name)

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        for name, node in self._nodes.items():
            node.setVisible((not text) or (text in name.lower()))
        for edge in self._edges:
            src_node = self._nodes.get(edge.source_table)
            tgt_node = self._nodes.get(edge.target_table)
            edge.setVisible(bool(src_node and tgt_node and src_node.isVisible() and tgt_node.isVisible()))

    # ── inspector panel ──────────────────────────────────────────────

    def _toggle_inspector(self, checked: bool):
        self._inspector_open = checked
        self.inspector.setVisible(checked)
        if checked and self._selected_node:
            self._populate_inspector(self._selected_node.table_name)

    def _populate_inspector(self, table_name: str):
        if self._graph is None:
            return
        table = self._graph.tables.get(table_name)
        if table is None:
            return
        rel_out = [r for r in self._graph.relationships if r.source_table == table_name]
        rel_in = [r for r in self._graph.relationships if r.target_table == table_name]

        cached = self._index_cache.get(table_name)
        if cached is not None:
            self.inspector.populate(table, rel_in, rel_out, cached)
            return

        self.inspector.show_loading(table_name)
        config = dict(self._config)
        sig = self._indexes_loaded

        def _worker():
            try:
                idx = fetch_table_indexes(config, table_name)
            except Exception:
                idx = []
            sig.emit(table_name, idx)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_indexes_loaded(self, table_name: str, indexes: list):
        self._index_cache[table_name] = indexes
        if self._graph is None:
            return
        if self._selected_node and self._selected_node.table_name == table_name and self._inspector_open:
            table = self._graph.tables.get(table_name)
            if table is None:
                return
            rel_out = [r for r in self._graph.relationships if r.source_table == table_name]
            rel_in = [r for r in self._graph.relationships if r.target_table == table_name]
            self.inspector.populate(table, rel_in, rel_out, indexes)

"""Visual Query Builder — table canvas & join graph (VQB.2, issue #192).

Column/aggregate picking, filters, GROUP BY/ORDER BY/LIMIT, and SQL sync
land in later sub-issues (#193-#196, tracked under #190); this dialog owns
only the canvas: add/remove tables, draw joins between columns (or accept
an auto-suggested FK join), and pick each join's type.

Reuses ui/erd_dialog.py's canvas mechanics via ui/graph_canvas.py (pan/zoom
view, minimap, card drawing, curve routing) rather than rebuilding them.
Table nodes and join edges are new classes here, since their interaction
model differs from ERD's read-only diagram: each column row is itself a
drag source/target for drawing a join, and joins carry an editable type
instead of a fixed FK cardinality mark.

Loads schema metadata (all tables + FK relationships, for the "Add table"
list and FK auto-suggestion) via services/erd_model.py's ErdGraph — the
same model ErdDialog renders — through its own dedicated connection,
never the caller's live one.
"""
import threading

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QHBoxLayout, QLabel, QMenu,
    QPushButton, QSplitter, QVBoxLayout,
)

from services.erd_model import build_erd_graph, build_erd_graph_from_snapshot
from services.query_builder_model import JOIN_TYPES, Join, QueryBuilderState
from ui.graph_canvas import (
    CanvasView, CollapseToggle, HEADER_H, HeaderItem, MinimapView, NODE_W,
    PAD, ROW_GAP_X, ROW_GAP_Y, ROW_H, build_curved_path, header_color,
    paint_card,
)
from ui.query_builder_panels import SelectPanel
from utils import schema_cache


class _QbColumnRow(QGraphicsRectItem):
    """Invisible full-width hit area over one column row. Pressing and
    dragging from here to another table's row draws a join — this row
    (not the parent node) owns the drag, so the node's own ItemIsMovable
    drag only fires from the header/margin, not from on top of a column."""

    def __init__(self, node, column_name: str, y: float,
                 on_drag_start, on_drag_move, on_drag_end):
        super().__init__(0, y, NODE_W, ROW_H, node)
        self.node = node
        self.column_name = column_name
        self._on_drag_start = on_drag_start
        self._on_drag_move = on_drag_move
        self._on_drag_end = on_drag_end
        self.setPen(QPen(Qt.NoPen))
        self.setBrush(QBrush(Qt.NoBrush))
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setCursor(Qt.CrossCursor)
        self.setZValue(2)

    def mousePressEvent(self, event):
        self._on_drag_start(self.node, self.column_name, event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event):
        self._on_drag_move(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event):
        self._on_drag_end(event.scenePos())
        event.accept()


class _QbTableNodeItem(QGraphicsRectItem):
    """Draggable table card for the join canvas. Visually modeled on
    ui/erd_dialog.py's _TableNodeItem (same header/column-row layout,
    collapse toggle, card shadow) but each column row is a join-drag
    target (_QbColumnRow) instead of inert text, and the context menu
    offers "Remove from canvas" instead of table-browsing actions."""

    def __init__(self, table, is_dark: bool, color: QColor, on_select,
                 on_join_drag_start, on_join_drag_move, on_join_drag_end, on_remove):
        columns = table.columns
        height = HEADER_H + max(1, len(columns)) * ROW_H + PAD
        super().__init__(0, 0, NODE_W, height)
        self.table_name = table.name
        self._on_select = on_select
        self._on_remove = on_remove
        self.column_y = {}
        self.selected = False
        self.edges = []  # list[_QbJoinEdgeItem] touching this node
        self._collapsed = False
        self._expanded_height = height

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
        self._shadow_color = QColor(0, 0, 0, 70 if is_dark else 40)

        header = HeaderItem(0, 0, NODE_W, HEADER_H, self)
        header.setBrush(QBrush(color))
        header.setPen(QPen(color))
        header.setAcceptedMouseButtons(Qt.NoButton)

        title = QGraphicsSimpleTextItem(table.name, self)
        f = QFont()
        f.setBold(True)
        title.setFont(f)
        title.setBrush(QBrush(header_fg))
        title.setPos(PAD, (HEADER_H - 16) / 2)
        title.setAcceptedMouseButtons(Qt.NoButton)

        self._toggle = CollapseToggle(self, header_fg)
        self._toggle.setPos(NODE_W - 18, (HEADER_H - 18) / 2)

        self._column_items = []
        self._column_rows = []
        y = HEADER_H
        for col in columns:
            marker = "\U0001F511 " if col.is_primary_key else ("\U0001F517 " if col.is_foreign_key else "")
            label = f"{marker}{col.name}"
            if col.data_type:
                label += f"  {col.data_type}"
            row = _QbColumnRow(self, col.name, y, on_join_drag_start, on_join_drag_move, on_join_drag_end)
            self._column_rows.append(row)
            text = QGraphicsSimpleTextItem(label, self)
            cf = QFont()
            cf.setBold(col.is_primary_key)
            text.setFont(cf)
            text.setBrush(QBrush(body_fg))
            text.setPos(PAD, y + (ROW_H - 16) / 2)
            text.setAcceptedMouseButtons(Qt.NoButton)
            self.column_y[col.name] = y + ROW_H / 2
            self._column_items.append(text)
            y += ROW_H

    def paint(self, painter, option, widget=None):
        paint_card(painter, self.rect(), self.brush(), self.pen(), self._shadow_color)

    def set_highlighted(self, on: bool):
        self.selected = on
        self.setPen(QPen(QColor("#0A84FF") if on else self._border_color, 2 if on else 1))

    def anchor_point(self, column_name: str, from_right: bool) -> QPointF:
        y = (HEADER_H / 2) if self._collapsed else self.column_y.get(column_name, HEADER_H / 2)
        x = NODE_W if from_right else 0
        return self.mapToScene(QPointF(x, y))

    def set_collapsed(self, collapsed: bool):
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        for item in self._column_items:
            item.setVisible(not collapsed)
        for row in self._column_rows:
            row.setVisible(not collapsed)
        rect = self.rect()
        rect.setHeight((HEADER_H + PAD) if collapsed else self._expanded_height)
        self.setRect(rect)
        self._toggle.set_collapsed(collapsed)
        for edge in self.edges:
            edge.update_geometry()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_geometry()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_select(self)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        remove_action = menu.addAction("Remove from canvas")
        action = menu.exec_(event.screenPos())
        if action == remove_action:
            self._on_remove(self.table_name)


class _QbJoinEdgeItem(QGraphicsPathItem):
    """One join between two column anchors. Right-click to change its
    type (INNER/LEFT/RIGHT/FULL) or remove it. join.suggested (an
    FK-inferred join not yet touched by the user) renders dashed; picking
    a type or redrawing it confirms it and switches to solid."""

    def __init__(self, join: Join, source_node, target_node, is_dark: bool, nodes_ref: dict, on_remove):
        super().__init__()
        self.join = join
        self.source_node = source_node
        self.target_node = target_node
        self._nodes = nodes_ref
        self._on_remove = on_remove

        color = QColor("#7a7a7f") if is_dark else QColor("#9a9a9f")
        self._solid_pen = QPen(color, 1.6)
        self._dashed_pen = QPen(color, 1.6, Qt.DashLine)
        self.setZValue(0)

        label_fg = QColor("#e5e5ea") if is_dark else QColor("#1c1c1e")
        label_bg_color = QColor("#3a3a3c") if is_dark else QColor("#eef1f5")
        self.label_bg = QGraphicsRectItem()
        self.label_bg.setBrush(QBrush(label_bg_color))
        self.label_bg.setPen(QPen(Qt.NoPen))
        self.label_bg.setZValue(0.5)
        self.label = QGraphicsSimpleTextItem()
        self.label.setBrush(QBrush(label_fg))
        f = QFont()
        f.setPointSize(max(7, f.pointSize() - 1))
        self.label.setFont(f)
        self.label.setZValue(0.6)

        self.update_geometry()

    def shape(self):
        # QGraphicsPathItem's default shape() hugs the thin curve itself,
        # which makes right-clicking a join nearly impossible — stroke a
        # wider band around the same path purely for hit-testing.
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def update_geometry(self):
        self.setPen(self._dashed_pen if self.join.suggested else self._solid_pen)
        src_rect = self.source_node.sceneBoundingRect()
        tgt_rect = self.target_node.sceneBoundingRect()
        from_right = src_rect.center().x() <= tgt_rect.center().x()
        p1 = self.source_node.anchor_point(self.join.left_column, from_right=from_right)
        p2 = self.target_node.anchor_point(self.join.right_column, from_right=not from_right)
        obstacles = [n.sceneBoundingRect() for n in self._nodes.values()
                     if n is not self.source_node and n is not self.target_node and n.isVisible()]
        path, _t1, _t2 = build_curved_path(p1, p2, obstacles)
        self.setPath(path)

        mid = path.pointAtPercent(0.5)
        self.label.setText(f"{self.join.join_type} JOIN")
        lb_rect = self.label.boundingRect()
        self.label.setPos(mid.x() - lb_rect.width() / 2, mid.y() - lb_rect.height() / 2)
        pad = 3
        self.label_bg.setRect(mid.x() - lb_rect.width() / 2 - pad, mid.y() - lb_rect.height() / 2 - pad,
                               lb_rect.width() + pad * 2, lb_rect.height() + pad * 2)

    def contextMenuEvent(self, event):
        menu = QMenu()
        type_actions = {}
        for jt in JOIN_TYPES:
            act = menu.addAction(f"{jt} JOIN")
            act.setCheckable(True)
            act.setChecked(self.join.join_type == jt)
            type_actions[act] = jt
        menu.addSeparator()
        remove_action = menu.addAction("Remove Join")
        action = menu.exec_(event.screenPos())
        if action in type_actions:
            self.join.join_type = type_actions[action]
            self.join.suggested = False
            self.update_geometry()
        elif action == remove_action:
            self._on_remove(self)


class QueryBuilderDialog(QDialog):
    """Opens against a connection profile's config dict (same shape passed
    to DbService.connect). The canvas starts empty; tables are added via
    the toolbar combo, and FK joins between tables already on the canvas
    are auto-suggested (dashed) as each new table is added."""

    _graph_loaded = Signal(object)
    _graph_load_error = Signal(str)

    def __init__(self, config: dict, is_dark: bool = True, parent=None):
        super().__init__(parent)
        self._config = config
        self._is_dark = is_dark
        label = config.get("database") or config.get("name") or ""
        self.setWindowTitle(f"Visual Query Builder — {label}" if label else "Visual Query Builder")
        self.resize(1000, 700)

        self.state = QueryBuilderState()
        self._graph = None  # full-schema ErdGraph (all tables/FKs), loaded once

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Add table:"))
        self.add_table_combo = QComboBox()
        self.add_table_combo.setMinimumWidth(220)
        self.add_table_combo.currentIndexChanged.connect(self._on_add_table_selected)
        toolbar.addWidget(self.add_table_combo)

        fit_btn = QPushButton("Fit to Screen")
        fit_btn.clicked.connect(self._fit_to_screen)
        toolbar.addWidget(fit_btn)

        self.status_label = QLabel("")
        toolbar.addWidget(self.status_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.scene = QGraphicsScene(self)
        self.view = CanvasView(self.scene, self)

        self.select_panel = SelectPanel(self.state, on_change=self._on_select_state_changed)
        self.select_panel.setMinimumWidth(260)
        self.select_panel.setMaximumWidth(360)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.select_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        layout.addWidget(splitter, 1)

        bg = "#1c1c1e" if is_dark else "#ffffff"
        self.scene.setBackgroundBrush(QBrush(QColor(bg)))

        self.minimap = MinimapView(self.scene, self.view.viewport())
        self.minimap.scene_pos_picked.connect(self._on_minimap_pick)
        self.view.resized.connect(self._position_minimap)
        self.view.view_changed.connect(self._update_minimap_tracking)
        self.view.horizontalScrollBar().valueChanged.connect(self._update_minimap_tracking)
        self.view.verticalScrollBar().valueChanged.connect(self._update_minimap_tracking)
        self._position_minimap()

        self._nodes = {}   # table name -> _QbTableNodeItem
        self._edges = []   # list[_QbJoinEdgeItem]
        self._selected_node = None
        self._drag_source = None  # (node, column_name) while a join is being drawn
        self._drag_line = None

        self._graph_loaded.connect(self._on_graph_loaded)
        self._graph_load_error.connect(self._on_graph_error)

        self._load_schema()

    # ── schema loading (mirrors ErdDialog._reload) ──────────────────────

    def _load_schema(self):
        config = dict(self._config)
        sig_done = self._graph_loaded
        sig_error = self._graph_load_error

        cached = schema_cache.load(config.get("id", ""), config.get("database", ""))
        if cached and cached.get("tables"):
            self._on_graph_loaded(build_erd_graph_from_snapshot(cached))
        else:
            self.status_label.setText("⏳ Loading schema metadata…")

        def _worker():
            try:
                sig_done.emit(build_erd_graph(config))
            except Exception as ex:
                sig_error.emit(str(ex))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_graph_error(self, msg: str):
        self.status_label.setText(f"⚠ Failed to load schema: {msg}")

    def _on_graph_loaded(self, graph):
        self._graph = graph
        self._refresh_add_table_combo()
        self.select_panel.refresh(self._graph, list(self._nodes))
        self.status_label.setText(f"{len(self._nodes)} table(s) on canvas · {len(graph.tables)} available")

    # ── "Add table" combo ────────────────────────────────────────────────

    def _refresh_add_table_combo(self):
        self.add_table_combo.blockSignals(True)
        self.add_table_combo.clear()
        self.add_table_combo.addItem("+ Add table…")
        if self._graph:
            for name in sorted(self._graph.tables):
                if name not in self._nodes:
                    self.add_table_combo.addItem(name)
        self.add_table_combo.blockSignals(False)

    def _on_add_table_selected(self, index: int):
        if index <= 0:
            return
        name = self.add_table_combo.currentText()
        self.add_table_combo.blockSignals(True)
        self.add_table_combo.setCurrentIndex(0)
        self.add_table_combo.blockSignals(False)
        self.add_table(name)

    # ── table add/remove ─────────────────────────────────────────────────

    def add_table(self, table_name: str):
        if self._graph is None or table_name in self._nodes:
            return
        table = self._graph.tables.get(table_name)
        if table is None or not self.state.add_table(table_name):
            return

        color = header_color(table_name, self._is_dark)
        node = _QbTableNodeItem(
            table, self._is_dark, color,
            on_select=self._on_node_selected,
            on_join_drag_start=self._begin_join_drag,
            on_join_drag_move=self._update_join_drag,
            on_join_drag_end=self._finish_join_drag,
            on_remove=self.remove_table,
        )

        # New nodes flow left-to-right in a row of up to 3, then wrap to a
        # new row below — good enough starting layout; the user drags
        # nodes wherever they actually want them from there.
        existing = list(self._nodes.values())
        col_index = len(existing) % 3
        row_index = len(existing) // 3
        node.setPos(col_index * (NODE_W + ROW_GAP_X), row_index * (node.rect().height() + ROW_GAP_Y))

        self.scene.addItem(node)
        self._nodes[table_name] = node
        self._refresh_add_table_combo()
        self._suggest_fk_joins(table_name)
        self.select_panel.refresh(self._graph, list(self._nodes))
        self.minimap.refresh_fit()
        self._update_minimap_tracking()
        self.status_label.setText(f"{len(self._nodes)} table(s) on canvas")

    def remove_table(self, table_name: str):
        node = self._nodes.pop(table_name, None)
        if node is None:
            return
        if self._selected_node is node:
            self._selected_node = None
        for edge in list(node.edges):
            self._remove_join_edge(edge)
        self.scene.removeItem(node)
        self.state.remove_table(table_name)
        self._refresh_add_table_combo()
        self.select_panel.refresh(self._graph, list(self._nodes))
        self.status_label.setText(f"{len(self._nodes)} table(s) on canvas")

    # ── FK auto-suggestion ───────────────────────────────────────────────

    def _suggest_fk_joins(self, new_table: str):
        """Auto-adds a (dashed, "suggested") join for every FK relationship
        between *new_table* and a table already on the canvas — the
        "accept an auto-suggested FK join" half of the issue's scope. The
        user can remove or retype any of these the same way as a manually
        drawn join; nothing about them is special once added besides the
        dashed pen until touched."""
        if self._graph is None:
            return
        for rel in self._graph.relationships:
            connects_to_canvas = (
                (rel.source_table == new_table and rel.target_table in self._nodes) or
                (rel.target_table == new_table and rel.source_table in self._nodes)
            )
            if not connects_to_canvas:
                continue
            src_table, src_col = rel.source_table, rel.source_column
            tgt_table, tgt_col = rel.target_table, rel.target_column
            if self.state.has_join_between(src_table, src_col, tgt_table, tgt_col):
                continue
            join = Join(left_table=src_table, left_column=src_col,
                        right_table=tgt_table, right_column=tgt_col,
                        join_type="INNER", suggested=True)
            self._add_join_edge(join)

    # ── join drawing ─────────────────────────────────────────────────────

    def _begin_join_drag(self, node, column_name: str, scene_pos: QPointF):
        self._drag_source = (node, column_name)
        self._drag_line = QGraphicsPathItem()
        self._drag_line.setPen(QPen(QColor("#0A84FF"), 1.6, Qt.DashLine))
        self._drag_line.setZValue(3)
        self.scene.addItem(self._drag_line)
        self._update_join_drag(scene_pos)

    def _update_join_drag(self, scene_pos: QPointF):
        if self._drag_source is None or self._drag_line is None:
            return
        node, column_name = self._drag_source
        from_right = node.sceneBoundingRect().center().x() <= scene_pos.x()
        p1 = node.anchor_point(column_name, from_right=from_right)
        path = QPainterPath(p1)
        path.lineTo(scene_pos)
        self._drag_line.setPath(path)

    def _finish_join_drag(self, scene_pos: QPointF):
        if self._drag_source is None:
            return
        source_node, source_column = self._drag_source
        self._drag_source = None
        if self._drag_line is not None:
            self.scene.removeItem(self._drag_line)
            self._drag_line = None

        target_row = self._column_row_at(scene_pos)
        if target_row is None or target_row.node is source_node:
            return
        target_node, target_column = target_row.node, target_row.column_name
        if self.state.has_join_between(source_node.table_name, source_column,
                                        target_node.table_name, target_column):
            self.status_label.setText("These columns are already joined.")
            return
        join = Join(left_table=source_node.table_name, left_column=source_column,
                    right_table=target_node.table_name, right_column=target_column,
                    join_type="INNER", suggested=False)
        self._add_join_edge(join)

    def _column_row_at(self, scene_pos: QPointF):
        for item in self.scene.items(scene_pos):
            if isinstance(item, _QbColumnRow):
                return item
        return None

    def _add_join_edge(self, join: Join):
        src = self._nodes.get(join.left_table)
        tgt = self._nodes.get(join.right_table)
        if src is None or tgt is None:
            return
        self.state.add_join(join)
        edge = _QbJoinEdgeItem(join, src, tgt, self._is_dark, self._nodes, on_remove=self._remove_join_edge)
        self.scene.addItem(edge)
        self.scene.addItem(edge.label_bg)
        self.scene.addItem(edge.label)
        src.edges.append(edge)
        tgt.edges.append(edge)
        self._edges.append(edge)

    def _remove_join_edge(self, edge: "_QbJoinEdgeItem"):
        if edge not in self._edges:
            return
        self._edges.remove(edge)
        self.state.remove_join(edge.join)
        if edge in edge.source_node.edges:
            edge.source_node.edges.remove(edge)
        if edge in edge.target_node.edges:
            edge.target_node.edges.remove(edge)
        self.scene.removeItem(edge.label_bg)
        self.scene.removeItem(edge.label)
        self.scene.removeItem(edge)

    # ── SELECT panel ─────────────────────────────────────────────────────

    def _on_select_state_changed(self):
        # No SQL preview yet (VQB.5) — just reflect the count so checking a
        # box has visible feedback.
        n = len(self.state.select_items)
        if n:
            self.status_label.setText(f"{len(self._nodes)} table(s) on canvas · {n} column(s) selected")

    # ── selection / view controls (shared logic, see ErdDialog) ─────────

    def _on_node_selected(self, node):
        previously_selected = self._selected_node
        if previously_selected is not None:
            previously_selected.set_highlighted(False)
        if previously_selected is node:
            self._selected_node = None
            return
        node.set_highlighted(True)
        self._selected_node = node

    def _fit_to_screen(self):
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.view.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        self._update_minimap_tracking()

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

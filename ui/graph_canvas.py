"""Shared QGraphicsView canvas primitives for node-and-edge diagrams —
extracted out of ui/erd_dialog.py (issues #64/#65) so ui/query_builder_dialog.py
(issue #192) can reuse the same pan/zoom/minimap/card-drawing/curve-routing
mechanics instead of re-implementing them. Node and edge *item* classes stay
in each dialog module since ERD nodes (read-only, cardinality marks) and
query-builder nodes (join handles, editable join type) behave differently
enough that a shared base class would just be a pass-through of callbacks.
"""
import hashlib
import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPen, QBrush, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsView, QGraphicsRectItem, QGraphicsSimpleTextItem

HEADER_H = 30
ROW_H = 20
NODE_W = 220
PAD = 8
ROW_GAP_X = 100
ROW_GAP_Y = 100
MINIMAP_W = 200
MINIMAP_H = 130
CARD_RADIUS = 8

# Per-table header color cycle (light, dark) — keyed by a stable hash of the
# table name so colors don't reshuffle between reloads, and shared between
# the ERD and query-builder canvases so the same table reads as the same
# color in both.
HEADER_PALETTE = [
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


def header_color(table_name: str, is_dark: bool) -> QColor:
    # usedforsecurity=False: this is a cosmetic bucket-pick, not a security
    # hash (Python 3.9+; sha1 would otherwise get flagged as weak crypto).
    idx = int(hashlib.sha1(table_name.encode(), usedforsecurity=False).hexdigest(), 16) % len(HEADER_PALETTE)
    light, dark = HEADER_PALETTE[idx]
    return QColor(dark if is_dark else light)


def rounded_top_path(rect: QRectF, radius: float) -> QPainterPath:
    """Rect path with only the top-left/top-right corners rounded — used
    for header bands so they match a card's outer curve up top while the
    bottom edge (an interior seam against the body) stays flat."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x, y + h)
    path.lineTo(x, y + radius)
    path.arcTo(x, y, radius * 2, radius * 2, 180, -90)
    path.lineTo(x + w - radius, y)
    path.arcTo(x + w - radius * 2, y, radius * 2, radius * 2, 90, -90)
    path.lineTo(x + w, y + h)
    path.closeSubpath()
    return path


def paint_card(painter, rect: QRectF, brush: QBrush, pen: QPen, shadow_color: QColor,
               radius: float = CARD_RADIUS):
    """Rounded-rect body + flat offset shadow, shared by every node card.
    A flat offset rect stands in for QGraphicsDropShadowEffect, which
    rasterizes+blurs every node on every repaint with no cache mode set —
    on a schema of more than a few tables that made dragging/panning
    visibly hang."""
    painter.setRenderHint(QPainter.Antialiasing)
    shadow_path = QPainterPath()
    shadow_path.addRoundedRect(rect.translated(0, 3), radius, radius)
    painter.fillPath(shadow_path, shadow_color)

    painter.setPen(pen)
    painter.setBrush(brush)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.drawPath(path)


class HeaderItem(QGraphicsRectItem):
    """Header band shaped with rounded top corners to match its card's
    outline."""

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        painter.drawPath(rounded_top_path(self.rect(), CARD_RADIUS))


class CollapseToggle(QGraphicsSimpleTextItem):
    """Small header glyph that collapses/expands its parent node,
    intercepting its own clicks so they don't fall through to node
    selection or drag. *node* must implement toggle_collapsed()."""

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


def curve_crosses_obstacles(p1: QPointF, c1: QPointF, c2: QPointF, p2: QPointF, obstacles: list) -> bool:
    path = QPainterPath(p1)
    path.cubicTo(c1, c2, p2)
    for i in range(1, 12):
        pt = path.pointAtPercent(i / 12)
        if any(rect.contains(pt) for rect in obstacles):
            return True
    return False


def build_curved_path(p1: QPointF, p2: QPointF, obstacles: list):
    """Cubic-bezier connector, bowed perpendicular to the straight line so
    it reads clearly instead of cutting through other nodes. Not a full
    obstacle-avoiding router — just a few widening attempts, sampled
    against *other* nodes' bounding rects, before giving up and using the
    last attempt anyway. Returns (path, tangent_at_p1, tangent_at_p2) as
    unit vectors pointing away from each endpoint, for endpoint decoration
    (cardinality marks, join-type labels, arrowheads, ...).
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
        if not curve_crosses_obstacles(p1, c1, c2, p2, obstacles):
            break

    path = QPainterPath(p1)
    path.cubicTo(c1, c2, p2)

    t1x, t1y = c1.x() - p1.x(), c1.y() - p1.y()
    t1len = math.hypot(t1x, t1y) or 1.0
    t2x, t2y = c2.x() - p2.x(), c2.y() - p2.y()
    t2len = math.hypot(t2x, t2y) or 1.0
    return path, (t1x / t1len, t1y / t1len), (t2x / t2len, t2y / t2len)


class CanvasView(QGraphicsView):
    """Wheel-to-zoom; left-drag on empty canvas (or empty space between
    nodes) pans, left-drag on a node moves it. Dragging starts only when
    nothing is under the cursor, so clicking a node still selects/drags it
    instead of panning."""

    _PAN_SPEED = 0.6  # < 1 damps drag-to-pan so large diagrams don't fly by
    _MAX_PAN_STEP = 60  # px/event cap so a fast flick can't send the view overshooting
    _ZOOM_STEP = 0.08  # scale change per "notch" (angleDelta of 120)

    resized = Signal()
    view_changed = Signal()

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self._panning = False
        self._pan_start = None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        # Scale the step by delta magnitude (clamped to one notch) instead of
        # a flat 1.15x/event: a mouse wheel's ±120-per-notch gets the full
        # step, while a trackpad's smaller, more frequent deltas zoom
        # proportionally gentler rather than jumping the same amount.
        notch = max(-120, min(120, delta)) / 120.0
        factor = 1.0 + notch * self._ZOOM_STEP
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
            dx = max(-self._MAX_PAN_STEP, min(self._MAX_PAN_STEP, delta.x()))
            dy = max(-self._MAX_PAN_STEP, min(self._MAX_PAN_STEP, delta.y()))
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - round(dx * self._PAN_SPEED))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - round(dy * self._PAN_SPEED))
            self.view_changed.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)


class MinimapView(QGraphicsView):
    """Small always-fit overview sharing the main scene. Draws the main
    view's visible rect in drawForeground (not as a scene item) so the
    indicator never leaks into the main canvas. Click/drag recenters the
    main view via scene_pos_picked."""

    scene_pos_picked = Signal(QPointF)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setFixedSize(MINIMAP_W, MINIMAP_H)
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

"""知识图谱视图 — 2D 力导向图可视化知识关联关系"""
from __future__ import annotations

import math
import random
from collections.abc import Hashable
from typing import TypeVar

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.gui.empty_state import EmptyState
from src.gui.icons import set_named_icon
from src.gui.theme import get_color
from src.services.db import Database
from src.services.graph_builder import GraphBuilder

# ---- 关系类型颜色映射 ----

RELATION_COLORS: dict[str, str] = {
    "related": "#8e99a5",
    "contains": "#6a9edf",
    "references": "#5ab8b8",
    "prerequisite": "#6db86d",
    "contradicts": "#d06a60",
    "part_of": "#d8a866",
}

RELATION_LABELS: dict[str, str] = {
    "related": "相关",
    "contains": "包含",
    "references": "引用",
    "prerequisite": "前置",
    "contradicts": "冲突",
    "part_of": "部分",
}

FILE_TYPE_COLORS: dict[str, str] = {
    "pdf": "#d06a60",
    "docx": "#6a9edf",
    "xlsx": "#6db86d",
    "md": "#9a6ad4",
    "txt": "#8e99a5",
    "code": "#d4a038",
    "html": "#d48548",
}


def _color_for_file_type(file_type: str) -> str:
    return FILE_TYPE_COLORS.get(file_type, "#9a6ad4")


def _hex_to_qcolor(hex_color: str, alpha: int = 255) -> QColor:
    """将 #RRGGBB 转为 QColor，支持 alpha。"""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return QColor(r, g, b, alpha)
    return QColor(100, 100, 100, alpha)


def _qcolor_from_role(role: str, alpha: int = 255) -> QColor:
    """从主题色 role 生成 QColor，支持 alpha。"""
    hex_str = get_color(role)
    if hex_str.startswith("rgba"):
        inner = hex_str[5:-1]
        parts = [p.strip() for p in inner.split(",")]
        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
        return QColor(r, g, b, alpha)
    return _hex_to_qcolor(hex_str, alpha)


def _style_for_unified_node(node: dict) -> dict:
    node_type = node.get("type", "")
    if node_type == "page":
        return {"shape": "ellipse", "color": "#6a9edf"}
    if node_type == "block":
        return {"shape": "rounded_rect", "color": "#6db86d"}
    if node_type == "tag":
        return {"shape": "diamond", "color": "#d8a866"}
    if node_type == "property":
        return {"shape": "hex", "color": "#9a6ad4"}
    return {"shape": "ellipse", "color": "#8e99a5"}


def _unified_node_detail_text(node: dict) -> str:
    lines = [
        node.get("label", node.get("id", "")),
        f"Type: {node.get('type', '')}",
    ]
    props = node.get("properties") or {}
    for key, value in sorted(props.items()):
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _mix_qcolor(a: QColor, b: QColor, amount: float, alpha: int | None = None) -> QColor:
    """Linearly blend two colors; optional alpha overrides the blended alpha."""
    amount = max(0.0, min(1.0, amount))
    inv = 1.0 - amount
    color = QColor(
        int(a.red() * inv + b.red() * amount),
        int(a.green() * inv + b.green() * amount),
        int(a.blue() * inv + b.blue() * amount),
        int(a.alpha() * inv + b.alpha() * amount),
    )
    if alpha is not None:
        color.setAlpha(alpha)
    return color


# ---- 力导向布局算法 ----

_REPULSION = 5000.0
_SPRING_K = 0.01
_SPRING_LEN = 150.0
_CENTER_K = 0.005
_DAMPING = 0.9
_MAX_ITERATIONS = 200
_LARGE_GRAPH_LAYOUT_NODE_LIMIT = 1000
NodeKey = TypeVar("NodeKey", bound=Hashable)


def _layout_iterations_for_node_count(node_count: int) -> int:
    if node_count >= _LARGE_GRAPH_LAYOUT_NODE_LIMIT:
        return 0
    return max(30, 200 - node_count)


def _compute_force_layout(
    initial_positions: dict[NodeKey, tuple[float, float, bool]],
    edge_pairs: list[tuple[NodeKey, NodeKey]],
    iterations: int = _MAX_ITERATIONS,
) -> dict[NodeKey, tuple[float, float]]:
    """纯计算的力导向布局 — 输入/输出均为原生类型，可在线程中安全运行。

    Args:
        initial_positions: {node_key: (x, y, is_pinned)}，缺失坐标会被随机初始化。
        edge_pairs: [(source_key, target_key), ...] 边连接的两端节点 key。
        iterations: 最大迭代次数。

    Returns:
        {node_key: (final_x, final_y)}，is_pinned 节点坐标保持原值不动。
    """
    if not initial_positions:
        return {}

    positions: dict[NodeKey, list[float]] = {}
    for key, (x, y, is_pinned) in initial_positions.items():
        if is_pinned:
            positions[key] = [x, y]
        elif x == 0 and y == 0:
            positions[key] = [random.uniform(-200, 200), random.uniform(-200, 200)]
        else:
            positions[key] = [x, y]

    pinned_keys = {k for k, (_, _, p) in initial_positions.items() if p}
    keys = list(positions.keys())
    velocities: dict[NodeKey, list[float]] = {k: [0.0, 0.0] for k in keys}

    for _ in range(iterations):
        forces: dict[NodeKey, list[float]] = {k: [0.0, 0.0] for k in keys}

        # 节点两两斥力 — O(n²) 纯数值运算，线程内安全
        for i, k1 in enumerate(keys):
            x1, y1 = positions[k1]
            for k2 in keys[i + 1:]:
                x2, y2 = positions[k2]
                dx = x1 - x2
                dy = y1 - y2
                dist = max(math.sqrt(dx * dx + dy * dy), 1.0)
                force = _REPULSION / (dist * dist)
                fx = force * dx / dist
                fy = force * dy / dist
                forces[k1][0] += fx
                forces[k1][1] += fy
                forces[k2][0] -= fx
                forces[k2][1] -= fy

        # 边弹簧力
        for src, tgt in edge_pairs:
            if src not in positions or tgt not in positions:
                continue
            dx = positions[tgt][0] - positions[src][0]
            dy = positions[tgt][1] - positions[src][1]
            dist = max(math.sqrt(dx * dx + dy * dy), 1.0)
            force = _SPRING_K * (dist - _SPRING_LEN)
            fx = force * dx / dist
            fy = force * dy / dist
            forces[src][0] += fx
            forces[src][1] += fy
            forces[tgt][0] -= fx
            forces[tgt][1] -= fy

        # 中心引力
        for k in keys:
            forces[k][0] -= _CENTER_K * positions[k][0]
            forces[k][1] -= _CENTER_K * positions[k][1]

        max_movement = 0.0
        for k in keys:
            if k in pinned_keys:
                continue
            velocities[k][0] = (velocities[k][0] + forces[k][0]) * _DAMPING
            velocities[k][1] = (velocities[k][1] + forces[k][1]) * _DAMPING
            positions[k][0] += velocities[k][0]
            positions[k][1] += velocities[k][1]
            movement = abs(velocities[k][0]) + abs(velocities[k][1])
            if movement > max_movement:
                max_movement = movement

        if max_movement < 0.5:
            break

    return {k: (v[0], v[1]) for k, v in positions.items()}


def apply_force_layout(
    nodes: list[GraphNodeItem],
    edges: list[GraphEdgeItem],
    iterations: int = _MAX_ITERATIONS,
) -> None:
    """弹簧-斥力-中心引力模型，原地更新节点位置。

    注意：此函数仍在主线程执行。当节点数 < _LARGE_GRAPH_LAYOUT_NODE_LIMIT 时
    调用 _compute_force_layout 阻塞运行；超过阈值时跳过布局（返回前不更新位置）。
    大图布局请改用 ``_ForceLayoutWorker`` 后台计算，避免 UI 冻结。
    """
    if not nodes:
        return

    if len(nodes) >= _LARGE_GRAPH_LAYOUT_NODE_LIMIT:
        return

    initial: dict[int, tuple[float, float, bool]] = {}
    for n in nodes:
        initial[id(n)] = (n.pos().x(), n.pos().y(), n.is_pinned)

    edge_pairs = [(id(e.source_node), id(e.target_node)) for e in edges]

    final_positions = _compute_force_layout(initial, edge_pairs, iterations)

    for n in nodes:
        pos = final_positions.get(id(n))
        if pos is not None:
            n.setPos(pos[0], pos[1])

    # Batch-write final positions to DB in a single transaction.
    positions = [(n.pos().x(), n.pos().y(), n.node_id) for n in nodes]
    Database.batch_update_node_positions(positions)


# ---- 图形节点 ----

class GraphNodeItem(QGraphicsItem):
    """知识图谱节点 — 椭圆形，大小按连接数动态缩放，悬停高亮。"""

    BASE_RADIUS = 24
    MIN_RADIUS = 20
    MAX_RADIUS = 48

    def __init__(
        self,
        node_id: str,
        knowledge_id: str,
        knowledge_title: str,
        file_type: str = "txt",
        is_pinned: bool = False,
    ):
        super().__init__()
        self.node_id = node_id
        self.knowledge_id = knowledge_id
        self.knowledge_title = knowledge_title
        self.file_type = file_type
        self.is_pinned = is_pinned

        self._color_hex = _color_for_file_type(file_type)
        self._display_text = (
            knowledge_title[:10] + ".." if len(knowledge_title) > 10 else knowledge_title
        )
        self._hovered = False
        self._muted = False
        self._edges: list[GraphEdgeItem] = []
        self._radius = self.BASE_RADIUS
        self._unified_node: dict | None = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(knowledge_title)

        self._pos_timer = QTimer()
        self._pos_timer.setSingleShot(True)
        self._pos_timer.timeout.connect(self._save_position)

    def add_edge(self, edge: GraphEdgeItem) -> None:
        self._edges.append(edge)
        # 连接数越多节点越大
        n_connections = len(self._edges)
        extra = min(n_connections * 3, self.MAX_RADIUS - self.BASE_RADIUS)
        self._radius = self.BASE_RADIUS + extra
        self.update()

    @property
    def radius(self) -> float:
        return self._radius

    def boundingRect(self) -> QRectF:
        r = self._radius
        extra = 10
        text_w = max(r * 2.5 + extra, r + extra)
        return QRectF(-text_w, -r - extra, 2 * text_w, 2 * r + 2 * extra + 30)

    def shape(self) -> QPainterPath:
        r = self._radius
        path = QPainterPath()
        path.addEllipse(-r, -r, 2 * r, 2 * r)
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self._radius
        scale = 1.1 if self._hovered else 1.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._muted:
            painter.setOpacity(0.42)
        painter.scale(scale, scale)

        # 发光效果（悬停时）
        if self._hovered:
            glow = _hex_to_qcolor(self._color_hex, 38)
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(-r - 9, -r - 9, 2 * (r + 9), 2 * (r + 9)))

        # 填充
        accent = _hex_to_qcolor(self._color_hex)
        surface = _qcolor_from_role("surface_alt", 255)
        border_base = _qcolor_from_role("border", 255)
        text_color = _qcolor_from_role("text", 245)
        dim_text = _qcolor_from_role("text_dim", 210)

        shadow_alpha = 42 if self._hovered or self.isSelected() else 24
        painter.setBrush(QBrush(QColor(0, 0, 0, shadow_alpha)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(-r + 2, -r + 4, 2 * r, 2 * r))

        # 边框
        border = _mix_qcolor(accent, border_base, 0.18, 210)
        painter.setBrush(QBrush(surface))
        painter.setPen(QPen(border, 2.2))
        painter.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))

        ring_width = 4.2 if self._hovered or self.isSelected() else 3.2
        painter.setPen(QPen(accent, ring_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(
            QRectF(-r + 4, -r + 4, 2 * (r - 4), 2 * (r - 4)),
            35 * 16,
            285 * 16,
        )

        accent_dot = max(5.0, r * 0.18)
        painter.setBrush(QBrush(accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(-accent_dot / 2, -accent_dot / 2, accent_dot, accent_dot))

        # 标题
        font = QFont()
        font.setPointSize(9)
        font.setBold(self._hovered or self.isSelected())
        painter.setFont(font)
        painter.setPen(QPen(text_color if self._hovered or self.isSelected() else dim_text))
        painter.drawText(
            QRectF(-r * 2.7, r + 6, r * 5.4, 24),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWrapAnywhere,
            self._display_text,
        )

        painter.restore()

    def set_highlight(self, highlighted: bool) -> None:
        """由场景控制高亮状态。"""
        self._hovered = highlighted
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        self._muted = dimmed
        self.update()

    def hoverEnterEvent(self, event) -> None:
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.highlight_node(self)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.clear_highlight()
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        scene = self.scene()
        if isinstance(scene, GraphScene):
            scene.node_double_clicked(self)
        super().mouseDoubleClickEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self._edges:
                edge.notify_geometry_changed()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        self._pos_timer.start(2000)
        super().mouseReleaseEvent(event)

    def _save_position(self) -> None:
        try:
            Database.update_node_position(self.node_id, self.pos().x(), self.pos().y())
        except RuntimeError:
            pass


# ---- 图形边 ----

class GraphEdgeItem(QGraphicsItem):
    """知识图谱边 — 关系类型颜色区分，箭头在目标节点边缘。"""

    def __init__(
        self,
        source_node: GraphNodeItem,
        target_node: GraphNodeItem,
        relation_type: str = "related",
        description: str = "",
        weight: float = 1.0,
    ):
        super().__init__()
        self.source_node = source_node
        self.target_node = target_node
        self.relation_type = relation_type
        self.description = description
        self.weight = weight
        self._highlighted = False
        self._muted = False

        self.setZValue(-1)
        self.setAcceptHoverEvents(True)

        tooltip = f"{relation_type}"
        if description:
            tooltip += f": {description}"
        self.setToolTip(tooltip)

        source_node.add_edge(self)
        target_node.add_edge(self)

        self._color_hex = RELATION_COLORS.get(relation_type, "#8e99a5")

    def set_highlight(self, highlighted: bool) -> None:
        self._highlighted = highlighted
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        self._muted = dimmed
        self.update()

    def boundingRect(self) -> QRectF:
        p1 = self.source_node.pos()
        p2 = self.target_node.pos()
        extra = 90
        x_min = min(p1.x(), p2.x()) - extra
        y_min = min(p1.y(), p2.y()) - extra
        x_max = max(p1.x(), p2.x()) + extra
        y_max = max(p1.y(), p2.y()) + extra
        return QRectF(x_min, y_min, x_max - x_min, y_max - y_min)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p1 = self.source_node.pos()
        p2 = self.target_node.pos()

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        dist = max(math.sqrt(dx * dx + dy * dy), 1.0)
        ux, uy = dx / dist, dy / dist

        # 从节点边缘开始画线（不穿过节点）
        r1 = self.source_node.radius
        r2 = self.target_node.radius
        start_x = p1.x() + ux * r1
        start_y = p1.y() + uy * r1
        end_x = p2.x() - ux * r2
        end_y = p2.y() - uy * r2

        normal_x, normal_y = -uy, ux
        curve_offset = max(-54.0, min(54.0, dist * 0.12))
        ctrl_x = (start_x + end_x) / 2 + normal_x * curve_offset
        ctrl_y = (start_y + end_y) / 2 + normal_y * curve_offset

        alpha = 210 if self._highlighted else (22 if self._muted else 50)
        line_color = _hex_to_qcolor(self._color_hex, alpha)
        line_width = max(1.1, min(self.weight * 1.45, 3.2))
        if self._highlighted:
            line_width = max(line_width, 2.7)
        painter.setPen(QPen(line_color, line_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

        edge_path = QPainterPath()
        edge_path.moveTo(start_x, start_y)
        edge_path.quadTo(ctrl_x, ctrl_y, end_x, end_y)
        painter.drawPath(edge_path)

        arrow_dx = end_x - ctrl_x
        arrow_dy = end_y - ctrl_y
        arrow_dist = max(math.sqrt(arrow_dx * arrow_dx + arrow_dy * arrow_dy), 1.0)
        aux, auy = arrow_dx / arrow_dist, arrow_dy / arrow_dist

        # 箭头在目标节点边缘
        arrow_size = 9
        ax = end_x
        ay = end_y

        arrow_path = QPainterPath()
        arrow_path.moveTo(ax, ay)
        arrow_path.lineTo(
            ax - aux * arrow_size - auy * arrow_size * 0.45,
            ay - auy * arrow_size + aux * arrow_size * 0.45,
        )
        arrow_path.lineTo(
            ax - aux * arrow_size + auy * arrow_size * 0.45,
            ay - auy * arrow_size - aux * arrow_size * 0.45,
        )
        arrow_path.closeSubpath()

        arrow_color = _hex_to_qcolor(self._color_hex, alpha)
        painter.setBrush(QBrush(arrow_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(arrow_path)

        # 关系类型标签（仅悬停时显示）
        if self._highlighted:
            mid_x = 0.25 * start_x + 0.5 * ctrl_x + 0.25 * end_x
            mid_y = 0.25 * start_y + 0.5 * ctrl_y + 0.25 * end_y
            label = RELATION_LABELS.get(self.relation_type, self.relation_type)
            label_rect = QRectF(mid_x - 34, mid_y - 12, 68, 20)
            painter.setBrush(QBrush(_qcolor_from_role("surface_alt", 238)))
            painter.setPen(QPen(_hex_to_qcolor(self._color_hex, 110), 1.0))
            painter.drawRoundedRect(label_rect, 7, 7)

            label_color = _hex_to_qcolor(self._color_hex, 240)
            painter.setPen(QPen(label_color))
            font = QFont()
            font.setPixelSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

    def notify_geometry_changed(self) -> None:
        """Notify Qt that edge geometry may have changed.

        Does not store a path — ``boundingRect`` and ``paint`` compute
        geometry dynamically from source/target node positions each frame.
        Calling ``prepareGeometryChange`` prevents stale cached painting.
        """
        self.prepareGeometryChange()
        self.update()


# ---- 图形场景 ----

class GraphLegendItem(QGraphicsItem):
    """Small non-interactive legend pinned by GraphCanvas to the visible scene."""

    WIDTH = 118
    PADDING = 10
    ROW_HEIGHT = 18

    def __init__(self):
        super().__init__()
        self.setZValue(20)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    def boundingRect(self) -> QRectF:
        height = self.PADDING * 2 + 18 + len(RELATION_COLORS) * self.ROW_HEIGHT
        return QRectF(0, 0, self.WIDTH, height)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.boundingRect()
        painter.setBrush(QBrush(_qcolor_from_role("surface_alt", 236)))
        painter.setPen(QPen(_qcolor_from_role("border", 130), 1.0))
        painter.drawRoundedRect(rect, 8, 8)

        title_font = QFont()
        title_font.setPixelSize(10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(_qcolor_from_role("text_dim", 230)))
        painter.drawText(
            QRectF(self.PADDING, self.PADDING - 1, self.WIDTH - self.PADDING * 2, 15),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "关系类型",
        )

        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)
        y = self.PADDING + 20
        for relation_type, color_hex in RELATION_COLORS.items():
            swatch = _hex_to_qcolor(color_hex, 230)
            painter.setBrush(QBrush(swatch))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(self.PADDING, y + 4, 8, 8))

            painter.setPen(QPen(_qcolor_from_role("text", 220)))
            painter.drawText(
                QRectF(self.PADDING + 16, y, self.WIDTH - self.PADDING * 2 - 16, self.ROW_HEIGHT),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                RELATION_LABELS.get(relation_type, relation_type),
            )
            y += self.ROW_HEIGHT

        painter.restore()


class GraphScene(QGraphicsScene):
    """管理节点和边的场景 — 加载数据、布局、高亮交互。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph_id: str | None = None
        self._nodes: list[GraphNodeItem] = []
        self._edges: list[GraphEdgeItem] = []
        self._knowledge_callback = None
        self._highlighted_node: GraphNodeItem | None = None
        self._legend: GraphLegendItem | None = None
        self._layout_worker: _ForceLayoutWorker | None = None
        self._ensure_legend()

    def set_knowledge_callback(self, callback) -> None:
        self._knowledge_callback = callback

    def _ensure_legend(self) -> None:
        if self._legend is None:
            self._legend = GraphLegendItem()
            self.addItem(self._legend)

    def load_graph(self, graph_id: str) -> None:
        self.clear_graph()
        self._graph_id = graph_id

        db_nodes = Database.get_graph_nodes(graph_id)
        if not db_nodes:
            return

        node_map: dict[str, GraphNodeItem] = {}

        for db_node in db_nodes:
            item = GraphNodeItem(
                node_id=db_node["id"],
                knowledge_id=db_node["knowledge_id"],
                knowledge_title=db_node.get("knowledge_title", ""),
                file_type=db_node.get("file_type", "txt"),
                is_pinned=bool(db_node.get("is_pinned", False)),
            )
            x = db_node.get("x", 0) or 0
            y = db_node.get("y", 0) or 0
            item.setPos(x, y)
            node_map[db_node["knowledge_id"]] = item
            self.addItem(item)
            self._nodes.append(item)

        db_rels = Database.get_graph_relations(graph_id)
        for db_rel in db_rels:
            source_node = node_map.get(db_rel["source_knowledge_id"])
            target_node = node_map.get(db_rel["target_knowledge_id"])
            if source_node is None or target_node is None:
                continue

            edge = GraphEdgeItem(
                source_node=source_node,
                target_node=target_node,
                relation_type=db_rel.get("relation_type", "related"),
                description=db_rel.get("description", ""),
                weight=db_rel.get("weight", 1.0),
            )
            self.addItem(edge)
            self._edges.append(edge)

        # 初始布局 — 节点位置全为 0 时执行一次力导向计算。
        # 大图（>= 1000 节点）跳过；中图改在 _ForceLayoutWorker 后台线程跑，
        # 避免主线程 O(n²) 阻塞导致 UI 冻结。
        all_zero = all(n.pos().x() == 0 and n.pos().y() == 0 for n in self._nodes)
        if all_zero and self._nodes:
            self._circular_layout()
            if len(self._nodes) < _LARGE_GRAPH_LAYOUT_NODE_LIMIT:
                self._run_force_layout_async()

    def _run_force_layout_async(self) -> None:
        """在后台线程执行力导向布局，完成后由 _on_layout_finished 回到主线程应用结果。"""
        if not self._nodes:
            return
        # 主线程采集数据 — QGraphicsItem 只能在主线程访问
        initial: dict[str, tuple[float, float, bool]] = {}
        for n in self._nodes:
            initial[n.node_id] = (n.pos().x(), n.pos().y(), n.is_pinned)
        edge_pairs = [
            (e.source_node.node_id, e.target_node.node_id)
            for e in self._edges
            if e.source_node.node_id in initial and e.target_node.node_id in initial
        ]
        iters = max(30, _MAX_ITERATIONS - len(self._nodes))

        # 取消并清理旧 worker
        if self._layout_worker is not None and self._layout_worker.isRunning():
            self._layout_worker.quit()
            self._layout_worker.wait(2000)

        self._layout_worker = _ForceLayoutWorker(initial, edge_pairs, iters)
        self._layout_worker.finished_with_positions.connect(self._on_layout_finished)
        self._layout_worker.start()

    def _on_layout_finished(self, positions: list) -> None:
        """主线程应用后台线程算出的布局结果。"""
        node_by_id = {n.node_id: n for n in self._nodes}
        for node_id, x, y, is_pinned in positions:
            node = node_by_id.get(node_id)
            if node is None or is_pinned:
                continue
            node.setPos(x, y)

        # 持久化到 DB（单次批量写）
        rows = [(n.pos().x(), n.pos().y(), n.node_id) for n in self._nodes]
        try:
            Database.batch_update_node_positions(rows)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Persist layout positions failed: %s", exc)

        # 释放 worker 引用
        if self._layout_worker is not None:
            self._layout_worker.deleteLater()
            self._layout_worker = None

    def clear_graph(self) -> None:
        self.clear()
        self._nodes.clear()
        self._edges.clear()
        self._graph_id = None
        self._highlighted_node = None
        self._legend = None
        self._ensure_legend()

    def load_unified_payload(self, payload: dict) -> None:
        """Load unified graph payload with Page/Block/Tag nodes."""
        self.clear_graph()
        self._graph_id = "unified"
        node_map: dict[str, GraphNodeItem] = {}

        for idx, node in enumerate(payload.get("nodes", [])):
            style = _style_for_unified_node(node)
            item = GraphNodeItem(
                node_id=node["id"],
                knowledge_id=node.get("source_id") or node["id"],
                knowledge_title=node.get("label") or node["id"],
                file_type=node.get("type", "txt"),
                is_pinned=False,
            )
            item._color_hex = style["color"]
            item._unified_node = node
            item.setToolTip(_unified_node_detail_text(node))
            columns = max(10, int(math.sqrt(max(len(payload.get("nodes", [])), 1))))
            item.setPos((idx % columns) * 120 - 300, (idx // columns) * 100 - 200)
            node_map[node["id"]] = item
            self.addItem(item)
            self._nodes.append(item)

        for edge in payload.get("edges", []):
            source_node = node_map.get(edge.get("source"))
            target_node = node_map.get(edge.get("target"))
            if source_node is None or target_node is None:
                continue
            edge_item = GraphEdgeItem(
                source_node=source_node,
                target_node=target_node,
                relation_type=edge.get("type", "related"),
                description=str((edge.get("properties") or {}).get("description", "")),
                weight=1.0,
            )
            self.addItem(edge_item)
            self._edges.append(edge_item)

        iterations = _layout_iterations_for_node_count(len(self._nodes))
        if self._nodes and iterations > 0:
            apply_force_layout(self._nodes, self._edges, iterations=iterations)

    def apply_layout(self) -> None:
        if not self._nodes:
            return
        iterations = _layout_iterations_for_node_count(len(self._nodes))
        if iterations > 0:
            apply_force_layout(self._nodes, self._edges, iterations=iterations)

    def _circular_layout(self) -> None:
        n = len(self._nodes)
        if n == 0:
            return
        radius = max(120, n * 30)
        for i, node in enumerate(self._nodes):
            angle = 2 * math.pi * i / n
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            node.setPos(x, y)

    def highlight_node(self, node: GraphNodeItem) -> None:
        """高亮节点及其所有关联节点和边，其余变暗。"""
        self._highlighted_node = node
        connected_ids = {node.knowledge_id}
        for edge in self._edges:
            if edge.source_node is node or edge.target_node is node:
                connected_ids.add(edge.source_node.knowledge_id)
                connected_ids.add(edge.target_node.knowledge_id)
                edge.set_highlight(True)
                edge.set_dimmed(False)
            else:
                edge.set_highlight(False)
                edge.set_dimmed(True)

        for n in self._nodes:
            if n.knowledge_id in connected_ids:
                n.set_highlight(True)
                n.set_dimmed(False)
            else:
                n.set_highlight(False)
                n.set_dimmed(True)

    def clear_highlight(self) -> None:
        """清除所有高亮。"""
        self._highlighted_node = None
        for n in self._nodes:
            n.set_highlight(False)
            n.set_dimmed(False)
        for e in self._edges:
            e.set_highlight(False)
            e.set_dimmed(False)

    @property
    def nodes(self) -> list[GraphNodeItem]:
        return self._nodes

    @property
    def edges(self) -> list[GraphEdgeItem]:
        return self._edges

    @property
    def legend(self) -> GraphLegendItem | None:
        return self._legend

    @property
    def graph_id(self) -> str | None:
        return self._graph_id

    def node_double_clicked(self, node: GraphNodeItem) -> None:
        if self._knowledge_callback:
            self._knowledge_callback(node)


# ---- 画布视图 ----

class GraphCanvas(QGraphicsView):
    """知识图谱画布 — 缩放、平移、背景网格点。"""

    def __init__(self, scene: GraphScene, parent=None):
        super().__init__(scene, parent)
        self._scene = scene
        self._zoom = 1.0

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        bg = get_color("bg")
        self.setStyleSheet(f"background: {bg}; border: none;")
        self._bg_color = bg

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        """绘制背景网格点（Obsidian 风格）。"""
        painter.save()
        painter.fillRect(rect, QBrush(_qcolor_from_role("bg", 255)))

        center = rect.center()
        glow_radius = max(rect.width(), rect.height()) * 0.42
        glow = QRadialGradient(center.x(), center.y(), glow_radius)
        glow.setColorAt(0.0, _qcolor_from_role("accent_soft", 70))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, glow_radius, glow_radius)
        # 淡色网格点
        dot_color = _qcolor_from_role("text_dim", 25)
        painter.setPen(QPen(dot_color))
        spacing = 34
        left = int(rect.left()) - (int(rect.left()) % spacing)
        top = int(rect.top()) - (int(rect.top()) % spacing)
        for x in range(left, int(rect.right()), spacing):
            for y in range(top, int(rect.bottom()), spacing):
                painter.drawPoint(x, y)
        painter.restore()

    def _nodes_bounding_rect(self) -> QRectF:
        rect: QRectF | None = None
        for node in self._scene.nodes:
            node_rect = node.sceneBoundingRect()
            rect = node_rect if rect is None else rect.united(node_rect)
        return rect if rect is not None else QRectF(-80, -80, 160, 160)

    def _position_overlays(self) -> None:
        legend = self._scene.legend
        if legend is None:
            return
        legend_rect = legend.boundingRect()
        margin = 18
        target = self.viewport().rect().bottomRight() - QPoint(
            int(legend_rect.width()) + margin,
            int(legend_rect.height()) + margin,
        )
        legend.setPos(self.mapToScene(target))

    def wheelEvent(self, event: QWheelEvent) -> None:
        """鼠标滚轮缩放 — 在当前 transform 上叠加，保留平移。"""
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1.0 / factor, 1.0 / factor)
        # 从实际 transform 读取缩放值，保持同步
        self._zoom = self.transform().m11()
        # 缩放范围 0.03x ~ 10x
        if self._zoom < 0.03:
            ratio = 0.03 / self._zoom
            self.scale(ratio, ratio)
            self._zoom = 0.03
        elif self._zoom > 10.0:
            ratio = 10.0 / self._zoom
            self.scale(ratio, ratio)
            self._zoom = 10.0
        self._position_overlays()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            from PySide6.QtGui import QMouseEvent
            fake = QMouseEvent(
                event.type(),
                event.position(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            super().mousePressEvent(fake)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mouseReleaseEvent(event)
        self._position_overlays()

    def fit_to_view(self) -> None:
        """自适应视图 — 将所有节点缩放到可视范围内。"""
        if not self._scene.nodes:
            return
        rect = self._nodes_bounding_rect().adjusted(-60, -60, 60, 60)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        # 同步 _zoom
        actual = self.transform().m11()
        self._zoom = max(0.1, min(5.0, actual))
        self._position_overlays()

    def reset_view(self) -> None:
        """重置视图到默认。"""
        self._zoom = 1.0
        self.setTransform(QTransform().scale(1.0, 1.0))
        self.centerOn(0, 0)
        self._position_overlays()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlays()


# ---- LLM 异步工作线程 ----

class GraphGenerateWorker(QThread):
    """异步执行图谱生成（LLM 分析），避免阻塞 UI。"""
    progress = Signal(str, int, int)
    done = Signal(list)
    error = Signal(str)

    def run(self) -> None:
        try:
            builder = GraphBuilder(
                progress_callback=lambda msg, cur=0, total=0: self.progress.emit(msg, cur or 0, total or 0)
            )
            ids = builder.auto_generate_by_categories()
            if not ids:
                ids = builder.auto_generate_all()
            if not ids:
                import logging
                logging.getLogger(__name__).warning(
                    "Graph generation returned no graphs. "
                    "categories=%s, knowledge_count=%s",
                    len(Database.get_all_categories()),
                    len(Database.list_knowledge(limit=10)),
                )
            self.done.emit(ids)
        except Exception as e:
            self.error.emit(str(e))


# ---- 主视图 ----

class GraphView(QWidget):
    """知识图谱视图 — 图谱列表 + 力导向图可视化。"""

    def __init__(self, llm_indicator=None):
        super().__init__()
        self._llm_indicator = llm_indicator
        self._worker = None
        self._layout_worker = None
        self._current_graph_id: str | None = None
        self._graph_mode = "legacy"

        self._setup_ui()
        self._setup_backend_indicator()
        # 首次列表加载延后到 showEvent，避免启动期一次性把 ~GraphView(1675 行)
        # 的数据库查询跑完。后续刷新由用户操作触发。
        self._graph_list_loaded = False

    def _setup_backend_indicator(self):
        """初始化后端状态定时刷新 — 延迟到 showEvent 启动 timer"""
        self._backend_timer = QTimer(self)
        self._backend_timer.timeout.connect(self._refresh_backend_indicator)
        # 缓存上次状态，避免 5s 一次无变化的 polish 浪费
        self._backend_last_status: str | None = None
        self._backend_last_text: str | None = None

    def showEvent(self, event):
        """首次显示时立即刷新一次 + 启动定时器；隐藏时停掉以释放 CPU/IO"""
        super().showEvent(event)
        if not self._graph_list_loaded:
            self._load_graph_list()
            self._graph_list_loaded = True
        if self._backend_timer is not None and not self._backend_timer.isActive():
            self._refresh_backend_indicator()
            self._backend_timer.start(5000)

    def _refresh_backend_indicator(self):
        """刷新工具栏上的 SQLite 图存储状态指示器。"""
        label_text = "SQLite"
        status_prop = "online"
        tooltip = "SQLite 图谱存储（本地内置）"

        # 仅在状态/文字真变化时触碰 widget — SQLite 后端永远 online/text 一样，跳过 polish
        changed = (
            status_prop != self._backend_last_status
            or label_text != self._backend_last_text
        )
        if not changed:
            return

        self._backend_dot.setProperty("status", status_prop)
        self._backend_dot.setText(label_text)
        self._backend_dot.setToolTip(tooltip)
        # polish 单次即可，无需 unpolish
        self._backend_dot.style().polish(self._backend_dot)
        self._backend_last_status = status_prop
        self._backend_last_text = label_text

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.setObjectName("pageSurface")
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # ---- 顶部标题栏 ----
        toolbar_card = QFrame()
        toolbar_card.setObjectName("pageHeader")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(16, 12, 16, 12)
        toolbar.setSpacing(8)

        title = QLabel("知识图谱")
        title.setObjectName("pageTitle")
        title_col = QVBoxLayout()
        subtitle = QLabel("可视化知识关联关系，通过 AI 自动梳理知识结构")
        subtitle.setObjectName("pageSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        toolbar.addLayout(title_col)
        toolbar.addStretch()

        # 适应视图
        self.btn_fit = QPushButton("适应视图")
        set_named_icon(self.btn_fit, "fullscreen", "text_dim", 15)
        self.btn_fit.clicked.connect(self._on_fit_view)
        toolbar.addWidget(self.btn_fit)

        # 新建图谱
        self.btn_new = QPushButton("新建图谱")
        self.btn_new.setObjectName("primaryBtn")
        set_named_icon(self.btn_new, "add", "on_accent", 15)
        self.btn_new.clicked.connect(self._on_new_graph)
        toolbar.addWidget(self.btn_new)

        # AI 自动生成
        self.btn_generate = QPushButton("AI 自动生成")
        set_named_icon(self.btn_generate, "graph_generate", "text_dim", 15)
        self.btn_generate.clicked.connect(self._on_auto_generate)
        toolbar.addWidget(self.btn_generate)

        self.generate_progress = QProgressBar()
        self.generate_progress.setMaximumHeight(18)
        self.generate_progress.setVisible(False)
        toolbar.addWidget(self.generate_progress)

        # 重新布局
        self.btn_layout = QPushButton("重新布局")
        set_named_icon(self.btn_layout, "layout", "text_dim", 15)
        self.btn_layout.clicked.connect(self._on_apply_layout)
        toolbar.addWidget(self.btn_layout)

        # 刷新
        self.btn_refresh = QPushButton("刷新")
        set_named_icon(self.btn_refresh, "refresh", "text_dim", 15)
        self.btn_refresh.clicked.connect(self._load_graph_list)
        toolbar.addWidget(self.btn_refresh)

        # 统一图谱模式
        self.btn_unified = QPushButton("统一图谱")
        set_named_icon(self.btn_unified, "graph_generate", "text_dim", 15)
        self.btn_unified.clicked.connect(self._load_unified_graph)
        toolbar.addWidget(self.btn_unified)

        # ---- 后端状态指示器 ----
        toolbar.addSpacing(12)
        self._backend_dot = QLabel("SQLite")
        self._backend_dot.setObjectName("indicatorDot")
        self._backend_dot.setProperty("status", "online")
        self._backend_dot.setFont(QFont("", -1, QFont.Weight.Bold))
        self._backend_dot.setFixedHeight(24)
        self._backend_dot.setContentsMargins(8, 2, 8, 2)
        toolbar.addWidget(self._backend_dot)

        layout.addWidget(toolbar_card)

        # ---- 主内容：左侧列表 + 右侧画布 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：图谱列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.graph_list = QListWidget()
        self.graph_list.setObjectName("graphListPanel")
        self.graph_list.setFixedWidth(250)
        self.graph_list.currentRowChanged.connect(self._on_graph_selected)
        self.graph_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graph_list.customContextMenuRequested.connect(self._on_list_context_menu)
        left_layout.addWidget(self.graph_list)

        splitter.addWidget(left_widget)

        # 右侧：空状态 / 画布
        self.canvas_stack = QStackedWidget()

        self.empty_state = EmptyState(
            title="暂无图谱",
            description="创建或生成知识图谱来可视化知识关联",
            buttons=[
                {"text": "新建图谱", "callback": self._on_new_graph, "objectName": "primaryBtn"},
                {"text": "AI 自动生成", "callback": self._on_auto_generate},
            ],
            icon_key="graph",
        )
        self.canvas_stack.addWidget(self.empty_state)

        self.graph_scene = GraphScene()
        self.graph_scene.set_knowledge_callback(self._show_node_detail)
        self.graph_canvas = GraphCanvas(self.graph_scene)
        self.canvas_stack.addWidget(self.graph_canvas)

        self.canvas_stack.setCurrentIndex(0)
        splitter.addWidget(self.canvas_stack)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # ---- 底部状态栏 ----
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        self._update_status("")
        layout.addWidget(self.status_label)

        # ---- 右侧详情面板（覆盖层） ----
        self._detail_width = 450
        self._detail_open = False
        self._detail_anim: QPropertyAnimation | None = None

        self.detail_panel = QFrame(self)
        self.detail_panel.setObjectName("detailCard")
        self.detail_panel.setFixedWidth(self._detail_width)
        self.detail_panel.setVisible(False)

        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(12, 12, 12, 12)

        detail_header = QHBoxLayout()
        self.detail_title = QLabel("")
        self.detail_title.setObjectName("sectionLabel")
        detail_header.addWidget(self.detail_title, 1)

        btn_close = QPushButton()
        btn_close.setFixedSize(28, 28)
        btn_close.setObjectName("closeDetailBtn")
        set_named_icon(btn_close, "close", "text_dim", 12)
        btn_close.clicked.connect(self._hide_detail_panel)
        detail_header.addWidget(btn_close)
        detail_layout.addLayout(detail_header)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("hintLabel")
        detail_layout.addWidget(self.detail_meta)

        self.detail_content = QTextEdit()
        self.detail_content.setReadOnly(True)
        detail_layout.addWidget(self.detail_content, 1)

    # ---- 图谱列表操作 ----

    def _load_graph_list(self) -> None:
        # 整批添加期间关闭更新，避免 N 次 addItem 触发重绘
        self.graph_list.setUpdatesEnabled(False)
        try:
            self.graph_list.clear()
            graphs = Database.list_graphs()

            for g in graphs:
                graph_id = g["id"]
                nodes = Database.get_graph_nodes(graph_id)
                rels = Database.get_graph_relations(graph_id)
                label = f"{g['name']} ({len(nodes)}节点 {len(rels)}关系)"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, g)
                self.graph_list.addItem(item)

            if graphs:
                self.graph_list.setCurrentRow(0)
            else:
                self.canvas_stack.setCurrentIndex(0)
                self._current_graph_id = None
                self._update_status("")
        finally:
            self.graph_list.setUpdatesEnabled(True)
            self.graph_list.viewport().update()

    def _on_graph_selected(self, row: int) -> None:
        item = self.graph_list.item(row)
        if not item:
            return
        graph_data = item.data(Qt.ItemDataRole.UserRole)
        if not graph_data:
            return

        self._current_graph_id = graph_data["id"]
        self.graph_scene.clear_graph()
        self.graph_scene.load_graph(self._current_graph_id)
        self.canvas_stack.setCurrentIndex(1)

        # 先居中，然后自适应
        self.graph_canvas.reset_view()
        if self.graph_scene.nodes:
            self.graph_canvas.fit_to_view()

        n_nodes = len(self.graph_scene.nodes)
        n_edges = len(self.graph_scene.edges)
        self._update_status(f"当前图谱: {graph_data['name']} | {n_nodes} 个节点 | {n_edges} 条关系")

    def _on_list_context_menu(self, pos) -> None:
        item = self.graph_list.itemAt(pos)
        if not item:
            return
        graph_data = item.data(Qt.ItemDataRole.UserRole)
        if not graph_data:
            return

        menu = QMenu(self)
        menu.setObjectName("contextMenu")

        act_rename = menu.addAction("重命名")
        set_named_icon(act_rename, "rename", "text_dim", 14)
        act_delete = menu.addAction("删除")
        set_named_icon(act_delete, "delete", "danger", 14)
        act_regenerate = menu.addAction("重新分析关系")
        set_named_icon(act_regenerate, "graph_generate", "text_dim", 14)

        chosen = menu.exec(self.graph_list.mapToGlobal(pos))
        if chosen == act_rename:
            self._rename_graph(graph_data)
        elif chosen == act_delete:
            self._delete_graph(graph_data)
        elif chosen == act_regenerate:
            self._regenerate_relations(graph_data)

    def _rename_graph(self, graph_data: dict) -> None:
        name, ok = QInputDialog.getText(
            self, "重命名图谱", "新名称:", text=graph_data["name"],
        )
        if ok and name.strip():
            Database.update_graph(graph_data["id"], name=name.strip())
            self._load_graph_list()

    def _delete_graph(self, graph_data: dict) -> None:
        reply = QMessageBox.question(
            self, "删除图谱",
            f"确定要删除图谱「{graph_data['name']}」吗？\n相关的节点和关系也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            Database.delete_graph(graph_data["id"])
            self._load_graph_list()

    def _regenerate_relations(self, graph_data: dict) -> None:
        graph_id = graph_data["id"]
        nodes = Database.get_graph_nodes(graph_id)
        knowledge_ids = [n["knowledge_id"] for n in nodes]
        if len(knowledge_ids) < 2:
            QMessageBox.information(self, "提示", "节点数量不足，至少需要 2 个节点才能分析关系。")
            return

        self.btn_generate.setEnabled(False)
        self._start_graph_progress("正在重新分析关系...")

        if self._llm_indicator:
            self._llm_indicator.set_status("running", "图谱关系分析")

        self._worker = _RegenerateWorker(graph_id, knowledge_ids)
        self._worker.progress.connect(self._on_generate_progress)
        self._worker.done.connect(self._on_regenerate_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    # ---- 工具栏按钮 ----

    def _on_fit_view(self) -> None:
        """适应视图按钮。"""
        if self.graph_scene.nodes:
            self.graph_canvas.fit_to_view()

    def _on_new_graph(self) -> None:
        name, ok = QInputDialog.getText(self, "新建图谱", "图谱名称:")
        if not ok or not name.strip():
            return

        graph_id = Database.insert_graph(name=name.strip(), source_type="manual")
        self._load_graph_list()

        for i in range(self.graph_list.count()):
            item = self.graph_list.item(i)
            g = item.data(Qt.ItemDataRole.UserRole)
            if g and g["id"] == graph_id:
                self.graph_list.setCurrentRow(i)
                break

    def _on_auto_generate(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        self.btn_generate.setEnabled(False)
        self.btn_new.setEnabled(False)
        self._start_graph_progress("正在通过 AI 分析知识关系并生成图谱...")

        if self._llm_indicator:
            self._llm_indicator.set_status("running", "知识图谱生成")

        self._worker = GraphGenerateWorker()
        self._worker.progress.connect(self._on_generate_progress)
        self._worker.done.connect(self._on_generate_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _start_graph_progress(self, msg: str) -> None:
        self.generate_progress.setVisible(True)
        self.generate_progress.setRange(0, 0)
        self.generate_progress.setValue(0)
        self.status_label.setText(msg)

    def _finish_graph_progress(self) -> None:
        self.generate_progress.setVisible(False)
        self.generate_progress.setRange(0, 100)
        self.generate_progress.setValue(0)

    def _on_generate_progress(self, msg: str, current: int = 0, total: int = 0) -> None:
        self.status_label.setText(msg)
        if total > 0:
            self.generate_progress.setVisible(True)
            self.generate_progress.setRange(0, total)
            self.generate_progress.setValue(min(max(current, 0), total))

    def _on_generate_finished(self, graph_ids: list) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_new.setEnabled(True)
        self._finish_graph_progress()
        if self._llm_indicator:
            self._llm_indicator.set_status("idle")

        if not graph_ids:
            count = len(Database.list_knowledge(limit=10))
            if count < 2:
                self.status_label.setText(f"知识条目不足（当前 {count} 条），至少需要 2 条才能生成图谱")
            else:
                self.status_label.setText("图谱生成未成功，请查看日志或重试")
            return

        self.status_label.setText(f"图谱生成完成，共创建/更新 {len(graph_ids)} 个图谱")
        self._load_graph_list()

        target_id = graph_ids[0]
        for i in range(self.graph_list.count()):
            item = self.graph_list.item(i)
            g = item.data(Qt.ItemDataRole.UserRole)
            if g and g["id"] == target_id:
                self.graph_list.setCurrentRow(i)
                break

    def _on_regenerate_finished(self) -> None:
        self.btn_generate.setEnabled(True)
        self._finish_graph_progress()
        self.status_label.setText("关系分析完成")
        if self._llm_indicator:
            self._llm_indicator.set_status("idle")
        if self._current_graph_id:
            self.graph_scene.clear_graph()
            self.graph_scene.load_graph(self._current_graph_id)
            if self.graph_scene.nodes:
                self.graph_canvas.fit_to_view()
            n_nodes = len(self.graph_scene.nodes)
            n_edges = len(self.graph_scene.edges)
            graph_data = Database.get_graph(self._current_graph_id)
            name = graph_data["name"] if graph_data else ""
            self._update_status(f"当前图谱: {name} | {n_nodes} 个节点 | {n_edges} 条关系")

    def _on_worker_error(self, error_msg: str) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_new.setEnabled(True)
        self._finish_graph_progress()
        self.status_label.setText(f"错误: {error_msg}")
        if self._llm_indicator:
            self._llm_indicator.set_status("error", error_msg)
        QMessageBox.warning(self, "操作失败", f"图谱操作失败:\n{error_msg}")

    def _on_apply_layout(self) -> None:
        if not self._current_graph_id or not self.graph_scene.nodes:
            return
        self.graph_scene.apply_layout()
        self.graph_canvas.fit_to_view()
        n_nodes = len(self.graph_scene.nodes)
        n_edges = len(self.graph_scene.edges)
        graph_data = Database.get_graph(self._current_graph_id)
        name = graph_data["name"] if graph_data else ""
        self._update_status(f"当前图谱: {name} | {n_nodes} 个节点 | {n_edges} 条关系")

    # ---- 状态栏 ----

    def _update_status(self, text: str) -> None:
        if text:
            self.status_label.setText(text)
        else:
            self.status_label.setText("选择或创建一个图谱以开始")

    # ---- 节点详情面板 ----

    def _load_unified_graph(self) -> None:
        """切换到统一图谱模式。"""
        from src.services.unified_graph import UnifiedGraphService
        self._graph_mode = "unified"
        payload = UnifiedGraphService(db=Database).build(
            include_blocks=True,
            include_tags=True,
            block_limit=400,
        )
        self.graph_scene.load_unified_payload(payload)
        self._update_status(f"统一图谱 — {len(payload['nodes'])} 节点, {len(payload['edges'])} 条边")

    def _show_node_detail(self, node: GraphNodeItem) -> None:
        unified = getattr(node, "_unified_node", None)
        if unified:
            self.detail_title.setText(unified.get("label", unified.get("id", "")))
            node_type = unified.get("type", "")
            self.detail_meta.setText(f"Type: {node_type}")
            self.detail_content.setPlainText(_unified_node_detail_text(unified))
            self._show_detail_panel()
            return

        knowledge = Database.get_knowledge(node.knowledge_id)
        if not knowledge:
            return

        self.detail_title.setText(knowledge.get("title", ""))
        file_type = knowledge.get("file_type", "txt")
        knowledge.get("source_path", "") or knowledge.get("source_type", "manual")
        import_time = (
            knowledge.get("created_at", "")[:16].replace("T", " ")
            if knowledge.get("created_at") else "未知"
        )
        n_connections = len(node._edges)
        self.detail_meta.setText(
            f"类型: {file_type} | 连接: {n_connections} 条 | 导入: {import_time}"
        )
        content = knowledge.get("content", "")
        self.detail_content.setPlainText(content[:10000] if len(content) > 10000 else content)

        self._show_detail_panel()

    def _show_detail_panel(self) -> None:
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None

        self.detail_panel.setFixedHeight(self.height())
        self.detail_panel.setVisible(True)
        self.detail_panel.raise_()

        target_x = self.width() - self._detail_width
        anim = QPropertyAnimation(self.detail_panel, b"pos")
        anim.setDuration(220)
        anim.setStartValue(QPoint(self.width(), 0))
        anim.setEndValue(QPoint(target_x, 0))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._detail_anim = anim
        self._detail_open = True

    def _hide_detail_panel(self) -> None:
        if not self._detail_open:
            return
        self._detail_open = False

        if self._detail_anim is not None:
            self._detail_anim.stop()

        anim = QPropertyAnimation(self.detail_panel, b"pos")
        anim.setDuration(180)
        anim.setStartValue(self.detail_panel.pos())
        anim.setEndValue(QPoint(self.width(), 0))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._safe_hide_panel)
        anim.start()
        self._detail_anim = anim

    def _safe_hide_panel(self) -> None:
        try:
            if self.detail_panel is not None:
                self.detail_panel.hide()
        except RuntimeError:
            pass

    # ---- 事件重写 ----

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._detail_open:
            try:
                self.detail_panel.setFixedHeight(self.height())
                self.detail_panel.move(self.width() - self._detail_width, 0)
            except RuntimeError:
                pass

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._backend_timer is not None and self._backend_timer.isActive():
            self._backend_timer.stop()
        if self._detail_anim is not None:
            self._detail_anim.stop()
            self._detail_anim = None
        try:
            self.detail_panel.setVisible(False)
        except RuntimeError:
            pass
        self._detail_open = False


class _RegenerateWorker(QThread):
    """重新分析单个图谱关系的异步线程。"""
    progress = Signal(str, int, int)
    done = Signal()
    error = Signal(str)

    def __init__(self, graph_id: str, knowledge_ids: list[str]):
        super().__init__()
        self._graph_id = graph_id
        self._knowledge_ids = knowledge_ids

    def run(self) -> None:
        try:
            builder = GraphBuilder(
                progress_callback=lambda msg, cur=0, total=0: self.progress.emit(msg, cur or 0, total or 0)
            )
            builder.build_from_knowledge(self._graph_id, self._knowledge_ids)
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


class _ForceLayoutWorker(QThread):
    """后台线程执行力导向布局 — 避免主线程 O(n²) 阻塞导致 UI 冻结。

    输入/输出均为原生 Python 类型（QGraphicsItem 仅在主线程创建/修改，
    跨线程访问会触发 Qt 警告甚至段错误），通过 ``finished_with_positions``
    信号把最终坐标发回主线程统一应用。
    """
    # 发出 [(node_id, x, y, is_pinned), ...]
    finished_with_positions = Signal(list)

    def __init__(
        self,
        initial_positions: dict[str, tuple[float, float, bool]],
        edge_pairs: list[tuple[str, str]],
        iterations: int,
    ):
        super().__init__()
        # key 统一是 node_id 字符串（避开 Python id() 回收问题）
        self._initial = initial_positions
        self._edge_pairs = edge_pairs
        self._iterations = iterations

    def run(self) -> None:
        try:
            final = _compute_force_layout(self._initial, self._edge_pairs, self._iterations)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Force layout worker failed: %s", exc)
            final = {}

        result = []
        for node_id, (x, y, is_pinned) in self._initial.items():
            fx, fy = final.get(node_id, (x, y))
            result.append((node_id, fx, fy, is_pinned))
        self.finished_with_positions.emit(result)

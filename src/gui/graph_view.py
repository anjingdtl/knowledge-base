"""知识图谱视图 — 2D 力导向图可视化知识关联关系"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import (
    Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QPoint, QRectF,
)
from PySide6.QtGui import (
    QColor, QPen, QBrush, QFont, QPainterPath, QPainter, QWheelEvent,
    QCursor, QTransform,
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QStackedWidget,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsDropShadowEffect,
    QGraphicsEllipseItem, QMenu, QInputDialog, QMessageBox,
)

from src.services.db import Database
from src.services.graph_builder import GraphBuilder
from src.gui.icons import set_named_icon
from src.gui.theme import get_color
from src.gui.empty_state import EmptyState


# ---- 关系类型颜色映射 ----

RELATION_COLORS: dict[str, str] = {
    "related": "#8899aa",
    "contains": "#4a9eff",
    "references": "#40c8c8",
    "prerequisite": "#5cb85c",
    "contradicts": "#e74c3c",
    "part_of": "#f0ad4e",
}

FILE_TYPE_COLORS: dict[str, str] = {
    "pdf": "#e74c3c",
    "docx": "#4a9eff",
    "xlsx": "#5cb85c",
    "md": "#a855f7",
    "txt": "#8899aa",
    "code": "#f59e0b",
    "html": "#f97316",
}


def _color_for_file_type(file_type: str) -> str:
    return FILE_TYPE_COLORS.get(file_type, "#a855f7")


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


# ---- 力导向布局算法 ----

_REPULSION = 5000.0
_SPRING_K = 0.01
_SPRING_LEN = 150.0
_CENTER_K = 0.005
_DAMPING = 0.9
_MAX_ITERATIONS = 200


def apply_force_layout(
    nodes: list[GraphNodeItem],
    edges: list[GraphEdgeItem],
    iterations: int = _MAX_ITERATIONS,
) -> None:
    """弹簧-斥力-中心引力模型，原地更新节点位置。"""
    if not nodes:
        return

    for n in nodes:
        if n.pos().x() == 0 and n.pos().y() == 0:
            n.setPos(random.uniform(-200, 200), random.uniform(-200, 200))

    velocities: dict[int, list[float]] = {id(n): [0.0, 0.0] for n in nodes}

    for _ in range(iterations):
        forces: dict[int, list[float]] = {id(n): [0.0, 0.0] for n in nodes}

        for i, n1 in enumerate(nodes):
            for n2 in nodes[i + 1:]:
                dx = n1.pos().x() - n2.pos().x()
                dy = n1.pos().y() - n2.pos().y()
                dist = max(math.sqrt(dx * dx + dy * dy), 1.0)
                force = _REPULSION / (dist * dist)
                fx = force * dx / dist
                fy = force * dy / dist
                forces[id(n1)][0] += fx
                forces[id(n1)][1] += fy
                forces[id(n2)][0] -= fx
                forces[id(n2)][1] -= fy

        for e in edges:
            n1, n2 = e.source_node, e.target_node
            dx = n2.pos().x() - n1.pos().x()
            dy = n2.pos().y() - n1.pos().y()
            dist = max(math.sqrt(dx * dx + dy * dy), 1.0)
            force = _SPRING_K * (dist - _SPRING_LEN)
            fx = force * dx / dist
            fy = force * dy / dist
            forces[id(n1)][0] += fx
            forces[id(n1)][1] += fy
            forces[id(n2)][0] -= fx
            forces[id(n2)][1] -= fy

        for n in nodes:
            forces[id(n)][0] -= _CENTER_K * n.pos().x()
            forces[id(n)][1] -= _CENTER_K * n.pos().y()

        max_movement = 0.0
        for n in nodes:
            if n.is_pinned:
                continue
            vid = id(n)
            velocities[vid][0] = (velocities[vid][0] + forces[vid][0]) * _DAMPING
            velocities[vid][1] = (velocities[vid][1] + forces[vid][1]) * _DAMPING
            n.moveBy(velocities[vid][0], velocities[vid][1])
            movement = abs(velocities[vid][0]) + abs(velocities[vid][1])
            max_movement = max(max_movement, movement)

        if max_movement < 0.5:
            break

    for n in nodes:
        Database.update_node_position(n.node_id, n.pos().x(), n.pos().y())


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
        self._edges: list[GraphEdgeItem] = []
        self._radius = self.BASE_RADIUS

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(knowledge_title)

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
        painter.scale(scale, scale)

        # 发光效果（悬停时）
        if self._hovered:
            glow = _hex_to_qcolor(self._color_hex, 30)
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(-r - 6, -r - 6, 2 * (r + 6), 2 * (r + 6))

        # 填充
        fill = _hex_to_qcolor(self._color_hex, 60)
        painter.setBrush(QBrush(fill))

        # 边框
        border = _hex_to_qcolor(self._color_hex, 230)
        pw = 3.0 if self.isSelected() else 2.0
        painter.setPen(QPen(border, pw))
        painter.drawEllipse(-r, -r, 2 * r, 2 * r)

        # 标题
        text_color = _qcolor_from_role("text", 240)
        painter.setPen(QPen(text_color))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(
            QRectF(-r * 2.5, r + 3, r * 5, 20),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWrapAnywhere,
            self._display_text,
        )

        painter.restore()

    def set_highlight(self, highlighted: bool) -> None:
        """由场景控制高亮状态。"""
        self._hovered = highlighted
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
                edge.update_path()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        Database.update_node_position(self.node_id, self.pos().x(), self.pos().y())
        super().mouseReleaseEvent(event)


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

        self.setZValue(-1)
        self.setAcceptHoverEvents(True)

        tooltip = f"{relation_type}"
        if description:
            tooltip += f": {description}"
        self.setToolTip(tooltip)

        source_node.add_edge(self)
        target_node.add_edge(self)

        self._color_hex = RELATION_COLORS.get(relation_type, "#8899aa")

    def set_highlight(self, highlighted: bool) -> None:
        self._highlighted = highlighted
        self.update()

    def boundingRect(self) -> QRectF:
        p1 = self.source_node.pos()
        p2 = self.target_node.pos()
        extra = 40
        x_min = min(p1.x(), p2.x()) - extra
        y_min = min(p1.y(), p2.y()) - extra
        x_max = max(p1.x(), p2.x()) + extra
        y_max = max(p1.y(), p2.y()) + extra
        return QRectF(x_min, y_min, x_max - x_min, y_max - y_min)

    def paint(self, painter: QPainter, option, widget=None) -> None:
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

        alpha = 200 if self._highlighted else 80
        line_color = _hex_to_qcolor(self._color_hex, alpha)
        line_width = max(1.2, min(self.weight * 1.5, 3.5))
        if self._highlighted:
            line_width = max(line_width, 2.5)
        painter.setPen(QPen(line_color, line_width))
        painter.drawLine(QPoint(int(start_x), int(start_y)), QPoint(int(end_x), int(end_y)))

        # 箭头在目标节点边缘
        arrow_size = 9
        ax = end_x
        ay = end_y

        arrow_path = QPainterPath()
        arrow_path.moveTo(ax, ay)
        arrow_path.lineTo(
            ax - ux * arrow_size - uy * arrow_size * 0.45,
            ay - uy * arrow_size + ux * arrow_size * 0.45,
        )
        arrow_path.lineTo(
            ax - ux * arrow_size + uy * arrow_size * 0.45,
            ay - uy * arrow_size - ux * arrow_size * 0.45,
        )
        arrow_path.closeSubpath()

        arrow_color = _hex_to_qcolor(self._color_hex, alpha)
        painter.setBrush(QBrush(arrow_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(arrow_path)

        # 关系类型标签（仅悬停时显示）
        if self._highlighted:
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2
            label_color = _hex_to_qcolor(self._color_hex, 240)
            painter.setPen(QPen(label_color))
            font = QFont()
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(
                QRectF(mid_x - 45, mid_y - 14, 90, 16),
                Qt.AlignmentFlag.AlignCenter,
                self.relation_type,
            )

    def update_path(self) -> None:
        self.prepareGeometryChange()
        self.update()


# ---- 图形场景 ----

class GraphScene(QGraphicsScene):
    """管理节点和边的场景 — 加载数据、布局、高亮交互。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph_id: str | None = None
        self._nodes: list[GraphNodeItem] = []
        self._edges: list[GraphEdgeItem] = []
        self._knowledge_callback = None
        self._highlighted_node: GraphNodeItem | None = None

    def set_knowledge_callback(self, callback) -> None:
        self._knowledge_callback = callback

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

        # 初始布局
        all_zero = all(n.pos().x() == 0 and n.pos().y() == 0 for n in self._nodes)
        if all_zero and self._nodes:
            self._circular_layout()
            iters = max(30, 200 - len(self._nodes))
            apply_force_layout(self._nodes, self._edges, iterations=iters)

    def clear_graph(self) -> None:
        self.clear()
        self._nodes.clear()
        self._edges.clear()
        self._graph_id = None
        self._highlighted_node = None

    def apply_layout(self) -> None:
        if not self._nodes:
            return
        apply_force_layout(self._nodes, self._edges)

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
            else:
                edge.set_highlight(False)

        for n in self._nodes:
            if n.knowledge_id in connected_ids:
                n.set_highlight(True)
            else:
                n.set_highlight(False)

    def clear_highlight(self) -> None:
        """清除所有高亮。"""
        self._highlighted_node = None
        for n in self._nodes:
            n.set_highlight(False)
        for e in self._edges:
            e.set_highlight(False)

    @property
    def nodes(self) -> list[GraphNodeItem]:
        return self._nodes

    @property
    def edges(self) -> list[GraphEdgeItem]:
        return self._edges

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

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """绘制背景网格点（Obsidian 风格）。"""
        super().drawBackground(painter, rect)
        # 淡色网格点
        dot_color = _qcolor_from_role("text_dim", 25)
        painter.setPen(QPen(dot_color))
        spacing = 30
        left = int(rect.left()) - (int(rect.left()) % spacing)
        top = int(rect.top()) - (int(rect.top()) % spacing)
        for x in range(left, int(rect.right()), spacing):
            for y in range(top, int(rect.bottom()), spacing):
                painter.drawPoint(x, y)

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

    def fit_to_view(self) -> None:
        """自适应视图 — 将所有节点缩放到可视范围内。"""
        if not self._scene.nodes:
            return
        rect = self._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        # 同步 _zoom
        actual = self.transform().m11()
        self._zoom = max(0.1, min(5.0, actual))

    def reset_view(self) -> None:
        """重置视图到默认。"""
        self._zoom = 1.0
        self.setTransform(QTransform().scale(1.0, 1.0))
        self.centerOn(0, 0)


# ---- LLM 异步工作线程 ----

class GraphGenerateWorker(QThread):
    """异步执行图谱生成（LLM 分析），避免阻塞 UI。"""
    progress = Signal(str)
    done = Signal(list)
    error = Signal(str)

    def run(self) -> None:
        try:
            builder = GraphBuilder(progress_callback=lambda msg: self.progress.emit(msg))
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
        self._current_graph_id: str | None = None

        self._setup_ui()
        self._load_graph_list()

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
        self._detail_anim = None

        self.detail_panel = QFrame(self)
        self.detail_panel.setObjectName("detailCard")
        self.detail_panel.setFixedWidth(self._detail_width)
        self.detail_panel.setVisible(False)

        detail_shadow = QGraphicsDropShadowEffect(self.detail_panel)
        detail_shadow.setBlurRadius(30)
        detail_shadow.setOffset(-4, 0)
        detail_shadow.setColor(QColor(0, 0, 0, 40))
        self.detail_panel.setGraphicsEffect(detail_shadow)

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
        self.status_label.setText("正在重新分析关系...")

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
        self.status_label.setText("正在通过 AI 分析知识关系并生成图谱...")

        if self._llm_indicator:
            self._llm_indicator.set_status("running", "知识图谱生成")

        self._worker = GraphGenerateWorker()
        self._worker.progress.connect(self._on_generate_progress)
        self._worker.done.connect(self._on_generate_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_generate_progress(self, msg: str) -> None:
        self.status_label.setText(msg)

    def _on_generate_finished(self, graph_ids: list) -> None:
        self.btn_generate.setEnabled(True)
        self.btn_new.setEnabled(True)
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

    def _show_node_detail(self, node: GraphNodeItem) -> None:
        knowledge = Database.get_knowledge(node.knowledge_id)
        if not knowledge:
            return

        self.detail_title.setText(knowledge.get("title", ""))
        file_type = knowledge.get("file_type", "txt")
        source = knowledge.get("source_path", "") or knowledge.get("source_type", "manual")
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
    progress = Signal(str)
    done = Signal()
    error = Signal(str)

    def __init__(self, graph_id: str, knowledge_ids: list[str]):
        super().__init__()
        self._graph_id = graph_id
        self._knowledge_ids = knowledge_ids

    def run(self) -> None:
        try:
            builder = GraphBuilder(progress_callback=lambda msg: self.progress.emit(msg))
            builder.build_from_knowledge(self._graph_id, self._knowledge_ids)
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))

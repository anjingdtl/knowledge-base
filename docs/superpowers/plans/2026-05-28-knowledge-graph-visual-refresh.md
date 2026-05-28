# Knowledge Graph Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the PySide6 knowledge graph so it uses a clean workspace visual style while preserving existing graph behavior.

**Architecture:** Keep rendering inside `src/gui/graph_view.py`, where graph nodes, edges, scene, and canvas already live. Add small rendering helpers and one lightweight legend item; do not change storage, graph generation, or page layout.

**Tech Stack:** Python 3, PySide6 `QGraphicsView`, `QGraphicsItem`, `QPainter`, existing theme helpers.

---

## File Structure

- Modify: `src/gui/graph_view.py`
  - Add relation display labels and rendering helpers.
  - Update `GraphNodeItem` paint behavior.
  - Update `GraphEdgeItem` paint behavior.
  - Add `GraphLegendItem`.
  - Update `GraphScene` and `GraphCanvas` to position the legend.
- Test: use `python -m py_compile src/gui/graph_view.py`
  - This is a GUI rendering change; automated visual assertions would require a larger Qt test harness that does not exist in this project.

## Task 1: Rendering Helpers

**Files:**
- Modify: `src/gui/graph_view.py`

- [ ] **Step 1: Add helper constants and functions**

Add near the existing color maps:

```python
RELATION_LABELS: dict[str, str] = {
    "related": "相关",
    "contains": "包含",
    "references": "引用",
    "prerequisite": "前置",
    "contradicts": "冲突",
    "part_of": "部分",
}

def _mix_qcolor(a: QColor, b: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    inv = 1.0 - amount
    return QColor(
        int(a.red() * inv + b.red() * amount),
        int(a.green() * inv + b.green() * amount),
        int(a.blue() * inv + b.blue() * amount),
        int(a.alpha() * inv + b.alpha() * amount),
    )

def _role_or_hex(role: str, fallback: str, alpha: int = 255) -> QColor:
    value = get_color(role) or fallback
    return _qcolor_from_role(role, alpha) if value else _hex_to_qcolor(fallback, alpha)
```

- [ ] **Step 2: Run compile check**

Run: `python -m py_compile src/gui/graph_view.py`

Expected: command exits with status 0.

## Task 2: Clean Workspace Nodes

**Files:**
- Modify: `src/gui/graph_view.py`

- [ ] **Step 1: Update `GraphNodeItem.paint()`**

Replace translucent fill with:

```python
accent = _hex_to_qcolor(self._color_hex)
surface = _qcolor_from_role("surface_alt", 255)
text_color = _qcolor_from_role("text", 245)
shadow = QColor(15, 23, 42, 28 if not self._hovered else 46)

painter.setBrush(QBrush(shadow))
painter.setPen(Qt.PenStyle.NoPen)
painter.drawEllipse(QRectF(-r + 2, -r + 4, 2 * r, 2 * r))

painter.setBrush(QBrush(surface))
painter.setPen(QPen(_mix_qcolor(accent, _qcolor_from_role("border", 255), 0.2), 2.2))
painter.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))

painter.setPen(QPen(accent, 4.0 if self.isSelected() or self._hovered else 3.0))
painter.drawArc(QRectF(-r + 3, -r + 3, 2 * (r - 3), 2 * (r - 3)), 35 * 16, 285 * 16)
```

Keep the text below the node, but use a slightly bolder font and selected/hover accent color.

- [ ] **Step 2: Run compile check**

Run: `python -m py_compile src/gui/graph_view.py`

Expected: command exits with status 0.

## Task 3: Curved Relationship Edges

**Files:**
- Modify: `src/gui/graph_view.py`

- [ ] **Step 1: Update `GraphEdgeItem.paint()`**

Replace direct `drawLine()` with a quadratic Bezier path:

```python
normal_x, normal_y = -uy, ux
curve_offset = max(-54.0, min(54.0, dist * 0.12))
ctrl_x = (start_x + end_x) / 2 + normal_x * curve_offset
ctrl_y = (start_y + end_y) / 2 + normal_y * curve_offset

path = QPainterPath()
path.moveTo(start_x, start_y)
path.quadTo(ctrl_x, ctrl_y, end_x, end_y)
painter.drawPath(path)
```

Use the derivative near the curve end for arrow direction:

```python
arrow_dx = end_x - ctrl_x
arrow_dy = end_y - ctrl_y
arrow_dist = max(math.sqrt(arrow_dx * arrow_dx + arrow_dy * arrow_dy), 1.0)
aux, auy = arrow_dx / arrow_dist, arrow_dy / arrow_dist
```

Use `aux` and `auy` for arrowhead geometry.

- [ ] **Step 2: Add readable highlighted labels**

When highlighted, draw a rounded background at the curve midpoint and show `RELATION_LABELS.get(self.relation_type, self.relation_type)`.

- [ ] **Step 3: Run compile check**

Run: `python -m py_compile src/gui/graph_view.py`

Expected: command exits with status 0.

## Task 4: Canvas Background And Legend

**Files:**
- Modify: `src/gui/graph_view.py`

- [ ] **Step 1: Update `GraphCanvas.drawBackground()`**

Fill the viewport with `get_color("bg")`, draw a soft central ellipse, then draw low-alpha dots at a wider spacing. Keep dark mode readable by using theme roles, not hardcoded light-only colors.

- [ ] **Step 2: Add `GraphLegendItem`**

Create a `QGraphicsItem` with fixed size, rounded panel background, relation color swatches, and labels from `RELATION_LABELS`.

- [ ] **Step 3: Wire legend into scene/canvas**

Create the legend in `GraphScene.__init__()`, add it after graph loading, ignore it for graph bounds, and position it in `GraphCanvas._position_overlays()` based on `mapToScene(self.viewport().rect())`.

- [ ] **Step 4: Run compile check**

Run: `python -m py_compile src/gui/graph_view.py`

Expected: command exits with status 0.

## Task 5: Final Verification

**Files:**
- Verify: `src/gui/graph_view.py`

- [ ] **Step 1: Run module compile**

Run: `python -m py_compile src/gui/graph_view.py`

Expected: command exits with status 0.

- [ ] **Step 2: Import the graph view module**

Run:

```bash
python -c "from src.gui.graph_view import GraphView, GraphNodeItem, GraphEdgeItem; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 3: Check git diff**

Run: `git diff -- src/gui/graph_view.py`

Expected: diff only contains rendering and legend changes.

## Self-Review

Spec coverage:

- Clean workspace background: Task 4.
- White nodes with file-type rings: Task 2.
- Curved relationship edges and labels: Task 3.
- Legend: Task 4.
- Preserve behavior and data flow: all tasks stay inside rendering classes.

Placeholder scan: no placeholder steps remain.

Type consistency: all referenced classes and helpers are in `src/gui/graph_view.py`.

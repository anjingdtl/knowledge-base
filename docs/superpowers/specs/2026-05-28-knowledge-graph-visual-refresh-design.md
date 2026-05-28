# Knowledge Graph Visual Refresh Design

## Context

The knowledge graph view is implemented in `src/gui/graph_view.py` with PySide6 `QGraphicsView` and custom `QGraphicsItem` rendering. The current graph uses translucent circular nodes, straight edges, and a simple dotted background. It works functionally, but the graph reads as flat and mechanical.

## Goal

Refresh the knowledge graph visual design so it feels like a clean knowledge-work canvas while preserving current behavior and data flow.

## Selected Direction

Use the "clean workspace" direction:

- Light, soft canvas background that matches the current light theme.
- White node bodies with file-type color rings.
- Curved relationship edges with restrained default opacity.
- Clear hover focus for the selected node neighborhood.
- A small relationship legend in the lower-right of the graph canvas.

## Scope

In scope:

- Update graph node rendering in `GraphNodeItem.paint()`.
- Update graph edge rendering in `GraphEdgeItem.paint()`.
- Update graph canvas background rendering in `GraphCanvas.drawBackground()`.
- Add a lightweight canvas legend for relation colors.
- Preserve existing controls, graph list, detail panel, zoom, pan, drag, double-click, and layout actions.

Out of scope:

- Database schema changes.
- Graph generation or LLM relationship analysis changes.
- Search/filter/minimap features.
- New third-party dependencies.
- Major layout changes to the page chrome.

## Visual Design

Nodes should use a solid white body in light mode and the theme surface in dark mode. The file type color should appear as a ring and small accent, not as a translucent fill. Larger or more connected nodes keep their existing size relationship. Text should stay readable below the node, with a stronger selected and hovered state.

Edges should use quadratic Bezier curves rather than straight lines. Default edges should be quiet and semi-transparent. Highlighted edges should increase opacity and width. Arrowheads remain at the target node edge. Highlighted relationship labels should render on a small rounded background so they remain readable over lines and background dots.

The canvas background should use a subtle workspace feel: theme background fill, very light dotted grid, and a soft central wash in light mode. It should not dominate the graph.

The legend should sit in the graph scene's bottom-right visible area and list relation colors with short labels. It should be non-interactive and remain readable without covering the graph heavily.

## Interaction Design

Existing interactions stay unchanged:

- Mouse drag pans the canvas.
- Wheel zooms around the cursor.
- Nodes can be dragged.
- Double-click opens the knowledge detail panel.
- "Fit view" and "Relayout" continue to work.

Hover focus should continue to highlight the hovered node, adjacent nodes, and related edges while dimming unrelated items. This should be implemented through rendering state only, without changing graph data.

## Implementation Notes

Keep changes concentrated in `src/gui/graph_view.py`. Add helper functions only where they make rendering clearer, such as color blending, rounded label drawing, or relation label mapping. Avoid introducing a new rendering framework.

The legend can be implemented as a `QGraphicsItem` that reads `RELATION_COLORS` and updates position after fitting or resizing. If a scene item proves awkward, a lightweight child overlay in `GraphCanvas` is also acceptable.

## Verification

Run Python compile checks for the touched module. If the GUI runtime is available, start the application and confirm the graph view imports without PySide exceptions. Visual verification should focus on whether the graph remains readable in the app and whether existing interactions still work.

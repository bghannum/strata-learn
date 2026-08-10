# ADR-005: Drawing questions use structured graph data, not raster images

**Status:** Accepted

## Context

Drawing questions ask the learner to sketch a component/flow diagram. Grading that submission requires comparing it against a reference answer. Comparing raster/pixel exports would require computer vision, which is unreliable and hard to give precise feedback from.

## Decision

Student submissions are captured as `{nodes: [...], edges: [...]}` via tldraw's shape API (`DrawingCanvas.tsx`, constrained to box + labeled-arrow shapes only), not as pixel/canvas exports.

## Consequences

- Grading (§10.3) is a deterministic graph diff — node fuzzy-matching plus edge/direction checks — not computer vision.
- The tldraw toolbar must be constrained (no freehand pen, no arbitrary shapes) so submissions are always structurally comparable.
- `Question.reference_graph` and `AnswerSubmission.graph_answer` share the same `{nodes, edges}` shape.

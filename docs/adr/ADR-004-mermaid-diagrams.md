# ADR-004: Mermaid for all diagrams

**Status:** Accepted

## Context

Study guides need architecture and (stretch) ER diagrams. Image-based generation (e.g., graphviz rendering to PNG/SVG server-side) requires a rendering pipeline and produces binary assets that are hard to diff or version.

## Decision

All diagrams are generated as Mermaid text and rendered client-side via `MermaidDiagram.tsx`. No image generation, no graphviz binaries in the backend.

## Consequences

- Diagrams are diffable and versionable as plain text, alongside `Section.diagram_mermaid`.
- No server-side rendering pipeline or binary asset storage/serving to build or maintain.
- Diagram edges come from Layer A (deterministic); the LLM only contributes labels/grouping (see ADR-006).

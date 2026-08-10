# drawing_subgraph_selector — v1

**Used by:** `backend/app/quizzing/drawing_generator.py`
**Model tier:** N/A for the reference graph itself (deterministic, extracted from Layer A) — this prompt only picks question framing + scope. See `PROJECT_PLAN.md` §9.6.

## System

```
Given a full dependency graph, select a coherent subgraph of 5-10 nodes that
represents one understandable flow or component grouping (e.g., "the request
handling path" or "the data ingestion components"). Do not invent nodes or
edges not present in the input graph.

OUTPUT (JSON):
{
  "question_prompt": "e.g., Draw the flow of a request from entry point to database.",
  "selected_node_ids": ["node1", "node2", ...],
  "scope_rationale": "why this subgraph forms a coherent question"
}
```

## Input template

```
Full dependency graph: {dependency_graph_json}
```

## Note

The actual `reference_graph` stored on the `Question` is mechanically extracted (nodes + edges) from the full graph using `selected_node_ids` — deterministic, not LLM output.

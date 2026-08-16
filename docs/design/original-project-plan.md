# Strata Learn — Original Project Plan

> **Historical/aspirational document.** This preserves the project vision, architecture sketch, and proposed implementation sequence as they stood before development. It is not the current implementation inventory, phase tracker, or canonical source for operational behavior. See the [documentation index](../README.md), [current architecture](../architecture.md), and [GitHub Issues](https://github.com/bghannum/strata-learn/issues) instead.

## Master project plan

| | |
|---|---|
| **Status** | Historical design blueprint; implementation is current through Phase 5, with Phases 5.5 and 8 added as future extensions |
| **Version** | 2.3 |
| **Last updated** | 2026-08-13 |
| **Owner** | Solo builder (primary user + developer) |
| **Intended reader** | Claude Code (as build context) + the builder |

**Original purpose:** This document was written as the starting context for scaffolding the repository. Its unchecked tasks, schemas, API surface, technology versions, and file tree record the intended plan rather than claiming to match the code today. Individual ADRs remain canonical for accepted decisions.

**Project goal:** A tool that ingests any repository (via git URL or zip upload) and generates a study guide (architecture diagrams, plain-English explanations, trade-off analysis) plus quizzes (multiple choice, fill-in-blank, and diagram-drawing questions) to help the user build real understanding of codebases — including ones built with heavy AI coding assistance, where the risk is shipping code you don't actually understand.

---

## Table of Contents

- [0. Ground Rules for Claude Code](#0-ground-rules-for-claude-code)
- [1. Tech Stack](#1-tech-stack-locked-in)
- [2. Decisions Log](#2-decisions-log) — at-a-glance summary of every locked decision
- [3. Architecture Decision Records](#3-architecture-decision-records)
- [4. Open Questions / Not Yet Decided](#4-open-questions--not-yet-decided)
- [5. Glossary](#5-glossary) — project-specific terms used throughout
- [6. Repository Structure](#6-repository-structure)
- [7. Data Model](#7-data-model)
- [8. Pipeline Orchestration](#8-pipeline-orchestration-workerpipelinepy)
- [9. Prompt Templates](#9-prompt-templates-layer-b)
- [10. Grading Logic](#10-grading-logic)
- [11. API Surface](#11-api-surface-v1)
- [12. Build Phases & Tasks](#12-build-phases--tasks)
- [13. Hosting](#13-hosting)
- [14. Suggested Timeline](#14-suggested-timeline)
- [15. Test Repos for Development](#15-test-repos-for-development)
- [16. Self-Verification Loop](#16-self-verification-loop-meta-but-load-bearing)

---

## 0. Ground Rules for Claude Code

When working from this document:

1. **Build in phase order.** Each phase has a checkpoint at the end — don't start Phase N+1 until Phase N's deliverables run end-to-end, even if rough.
2. **Phase 1 is deterministic — no LLM calls** (ADR-006). Keep the boundary between "facts extracted from code" (Layer A) and "things an LLM inferred" (Layer B) hard and visible in the code structure: separate modules, separate DB tables, never merged silently.
3. **Every LLM-generated claim in a study guide or quiz must carry a source citation** (`file_path`, `line_start`, `line_end`). If a prompt can't ground its output in a citation, redesign the prompt rather than dropping the citation requirement.
4. **Prefer boring, debuggable tech** over impressive tech. This is a solo-maintained project; optimize for the builder being able to reason about every layer.
5. **Decisions in §2 are locked.** Don't re-litigate them — build against them. Genuinely open items live in §4; anything in neither section, ask rather than assume.

---

## 1. Tech Stack (locked in)

| Layer | Choice | Rationale |
|---|---|---|
| Backend framework | Python 3.12 + FastAPI | Async-native, best tree-sitter bindings live in Python ecosystem |
| Job queue | `arq` (Redis-backed) | Lighter than Celery, async-native, good enough for single-user scale |
| Database | PostgreSQL 16 | Relational fit for the graph/citation-heavy schema below |
| ORM | SQLModel | Pydantic + SQLAlchemy combo, keeps API schemas and DB models close |
| Code parsing | `py-tree-sitter` + language grammars (Python + JS/TS only, v1) | Language-agnostic AST access, no regex hacking; narrow scope keeps Phase 1 tight, designed to be additive later |
| Repo access | `GitPython` (clone by URL) + zip upload — **no local filesystem ingestion** | Bounds the trust boundary to user-intentional snapshots; avoids relying on `.gitignore` as a safety mechanism (see ADR-008) |
| LLM access | Anthropic + OpenAI, behind a provider-abstraction interface | Swappable per-task model selection; abstraction is an explicit ADR below (ADR-003) |
| Auth | Self-implemented session-based auth (bcrypt/argon2 + HTTP-only cookie sessions) | Single-tenant, but built by hand deliberately for the learning value (ADR-007) |
| Frontend | React 18 + TypeScript + Vite + Tailwind | Standard, fast dev loop |
| Diagrams | Mermaid.js (client-rendered) | Text-based, versionable, cheap to regenerate, no binary asset pipeline |
| Drawing canvas | `tldraw` (OSS core) | Clean structured JSON output, not raster — makes grading tractable |
| Realtime progress | WebSocket (FastAPI native) | For indexing progress UI |
| Containerization | Docker Compose (postgres, redis, api, worker, web) | One-command local spin-up; same compose file is the deployment artifact later (see §13 Hosting) |
| Config | `pydantic-settings`, environment-driven from Phase 0 | Costs nothing now, removes friction when moving beyond local hosting (see §13 Hosting) |

---

## 2. Decisions Log

At-a-glance summary of every decision that's been settled. If a decision isn't here or in §4 (Open Questions), it hasn't been made — ask. Full reasoning for each lives in the referenced ADR.

| # | Decision | Choice | Rationale doc | Status |
|---|---|---|---|---|
| D1 | Service architecture | Modular monolith, not microservices | ADR-001 | Locked |
| D2 | Ingestion pipeline shape | Async job queue from day one | ADR-002 | Locked |
| D3 | LLM integration | Provider abstraction, called by all generation code | ADR-003 | Locked |
| D4 | Diagram format | Mermaid (text-based), no image generation | ADR-004 | Locked |
| D5 | Drawing-question data | Structured graph JSON, not raster | ADR-005 | Locked |
| D6 | Layer A / Layer B split | Hard separation, provenance-tagged | ADR-006 | Locked |
| D7 | Auth | Self-implemented session auth (bcrypt/argon2 + cookies) | ADR-007 | Locked |
| D8 | Repo ingestion sources | Git URL + zip upload only, no local filesystem | ADR-008 | Locked |
| D9 | LLM providers | Anthropic + OpenAI only, no OSS/self-hosted for now | ADR-009 | Locked |
| D10 | Language scope (v1) | Python + JS/TS only, additive later | §1 Tech Stack | Locked |
| D11 | Per-task model selection | One model everywhere in Phase 2, split later | §9.0 | Locked (allocation revisited post-Phase 2) |
| D12 | Hosting sequence | Local through Phase 6, VPS in Phase 7 | §13 Hosting | Locked (Option D/AWS = separate future project) |
| D13 | `POST /repos` in Phase 1 | Build synchronous first, refactor to queue in Phase 1.5 | §12 Phase 1 | Locked |

---

## 3. Architecture Decision Records

Write these as actual files in `/docs/adr/` in the repo — this is itself a study-guide-worthy artifact for the project. Each ADR carries a status line per standard ADR convention (Accepted / Proposed / Superseded).

### ADR-001: Modular monolith, not microservices
**Status:** Accepted
Single FastAPI app with clearly separated internal modules (`ingestion/`, `analysis/`, `generation/`, `grading/`). No premature service boundaries. Revisit only if a specific module needs independent scaling (unlikely at single-user scale).

### ADR-002: Async job queue from day one
**Status:** Accepted
Repo indexing is slow (seconds to minutes depending on size) and multi-stage (clone → parse → analyze → generate). Model this as a job pipeline with persisted state from the start, rather than a synchronous request, even though it adds infra complexity early. Retrofitting this later is expensive.

### ADR-003: LLM provider abstraction
**Status:** Accepted
Define an internal interface:
```python
class LLMProvider(Protocol):
    async def complete(self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None) -> LLMResponse: ...
```
All generation code calls this interface, never a vendor SDK directly. This lets you swap models per-task (e.g., cheaper/faster model for MCQ distractors, stronger model for trade-off reasoning) and A/B prompt quality.

### ADR-004: Mermaid for all diagrams
**Status:** Accepted
No image generation, no graphviz binaries. Mermaid renders client-side from text, which means diagrams are diffable, versionable, and re-renderable without a rendering pipeline.

### ADR-005: Drawing questions use structured graph data, not raster images
**Status:** Accepted
Student submissions are captured as `{nodes: [...], edges: [...]}` via tldraw's shape API, not as pixel/canvas exports. This is what makes automated grading feasible — you're diffing graphs, not doing computer vision.

### ADR-006: Hard separation between Layer A (structural/deterministic) and Layer B (semantic/LLM-inferred)
**Status:** Accepted
Every fact in the system is tagged with its provenance. Diagram edges from Layer A are ground truth; LLM only adds labels/grouping. This prevents hallucinated architecture from masquerading as fact and keeps the system debuggable — when a diagram looks wrong, you know immediately whether to debug the parser or the prompt.

### ADR-007: Self-implemented session auth, not a library
**Status:** Accepted
Auth is single-tenant (one user account, session-based) but deliberately hand-built — password hashing (bcrypt/argon2), `User` table, HTTP-only cookie sessions, login/register/logout endpoints — rather than reaching for `fastapi-users` or similar. This is an explicit trade of build speed for hands-on auth experience, since that's a stated learning goal independent of the study-guide product itself. Revisit if auth scope ever grows beyond single-tenant (OAuth, multi-user sharing) — that's a different, larger project and out of scope for now.

### ADR-008: No local-filesystem ingestion — git URL and zip upload only
**Status:** Accepted
Repos are ingested by cloning a remote URL or accepting a zip upload, never by walking a local path the user points at. This is a safety decision, not just a simplification: `.gitignore` filtering is a *signal-reduction* step (skip noise like `node_modules`), not a *permission boundary* — a walker bug or edge case could otherwise read outside the intended scope of an arbitrary local directory. Git-clone/zip-upload ingestion bounds the trust boundary to a scoped, user-intentional snapshot in a temp working directory fully controlled by the app. This also removes any architectural dependency on where the app is hosted (see §13) — there's no "local machine" the backend needs filesystem access to.

### ADR-009: LLM providers — Anthropic + OpenAI only, no self-hosted/open-source models (for now)
**Status:** Accepted (revisit as Phase 7+ stretch)
Both are used via the ADR-003 provider abstraction, with per-task model selection (see §9.0 below). Open-source models (self-hosted via Ollama/vLLM, or hosted via Together/Groq/Fireworks) were considered and explicitly deferred — not because they're unviable, but because introducing them now adds a second unknown variable (model quality vs. prompt quality) while prompts are still being iterated on, and self-hosted inference serving is a legitimate but separate scope of work (infra/ops, not product). Revisit as a deliberate Phase 7+ stretch project once the pipeline and prompts are stable, at which point OSS models can be A/B'd against the same fixed prompts for a clean comparison.

---

## 4. Open Questions / Not Yet Decided

These are genuinely unresolved — Claude Code should treat them as deferred by design, not oversights, and should surface them (not silently pick) if a phase forces the decision early.

| # | Open question | Context / when it needs deciding |
|---|---|---|
| Q1 | Sequence diagrams in study guides | Deferred from v1. Component + ER diagrams ship first (§9, §12 Phase 3); sequence diagrams require tracing call paths — revisit after Phase 3 proves the simpler diagrams work. |
| Q2 | ER diagram generation | Marked "stretch" in Phase 3 — build only if a DB schema is cleanly detectable; don't block Phase 3 on it. |
| Q3 | Exact model per task | §9.0 gives a starting allocation by tier, not specific model IDs. Pick concrete models once Phase 2 checkpoints show real output quality. |
| Q4 | Max zip upload size / file-count cap | A hard cap is needed in Phase 1 (§8 guard), but the specific numbers are unset — tune against the test repos. |
| Q5 | PDF export rendering approach | §12 Phase 7 lists Markdown/PDF export; the PDF rendering path (headless browser vs. library) is unpicked. Low stakes, decide at Phase 7. |
| Q6 | Multi-user / sharing | Explicitly out of scope (ADR-007), but if it ever comes back it reopens auth, the data model's ownership FKs, and permissions. Not for v1. |

---

## 5. Glossary

Load-bearing terms used throughout this document and intended to appear in the codebase's own naming:

| Term | Meaning |
|---|---|
| **Layer A** | Deterministic structural analysis extracted directly from code via tree-sitter — imports, call graph, entry points. No LLM involved. Treated as ground truth. |
| **Layer B** | Semantic understanding inferred by an LLM (module summaries, pattern detection, trade-off analysis), always grounded in Layer A facts and citations. |
| **CodeUnit** | One tree-sitter-extracted semantic unit — a module, class, or function — with its file path and line range. The atomic chunk the pipeline reasons over. |
| **AnalysisSnapshot** | The full structural + semantic analysis of a repo at one point in time (tied to a commit hash or zip). Study guides and quizzes are versioned against a snapshot. |
| **Trade-off card** | A structured Layer B output describing one design decision: the alternatives, likely reasoning, what was given up, confidence, and code citations. The product's differentiator. |
| **Reference graph** | The ground-truth `{nodes, edges}` subgraph (derived from Layer A) that a drawing-question submission is graded against. |
| **Citation** | A link from a generated claim back to specific source lines (`file_path`, `line_start`, `line_end`) plus the captured snippet text. Every Layer B claim carries one. |
| **Provenance** | The tag on every fact recording whether it came from Layer A (deterministic) or Layer B (LLM-inferred). Enforced by keeping them in separate modules/tables. |

---

## 6. Repository Structure

```
strata-learn/
├── docs/
│   ├── adr/                          # ADR-001.md, ADR-002.md, etc.
│   └── prompts/                      # versioned prompt templates (see §9)
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                      # DB migrations
│   ├── app/
│   │   ├── main.py                   # FastAPI app entrypoint
│   │   ├── config.py                 # settings via pydantic-settings
│   │   ├── db/
│   │   │   ├── models.py             # SQLModel table definitions (see §7)
│   │   │   └── session.py
│   │   ├── api/
│   │   │   ├── repos.py              # /repos endpoints
│   │   │   ├── study_guides.py       # /study-guides endpoints
│   │   │   ├── quizzes.py            # /quizzes endpoints
│   │   │   └── attempts.py           # /attempts endpoints (submit/grade)
│   │   ├── ingestion/
│   │   │   ├── source.py             # git clone / zip upload handling (no local path — ADR-008)
│   │   │   ├── walker.py             # file walking, gitignore (signal reduction, not a safety boundary)
│   │   │   └── language_detect.py
│   │   ├── analysis/                 # LAYER A — deterministic, no LLM
│   │   │   ├── parser.py             # tree-sitter wrapper
│   │   │   ├── dependency_graph.py
│   │   │   ├── entry_points.py
│   │   │   └── snapshot.py           # assembles AnalysisSnapshot
│   │   ├── semantics/                # LAYER B — LLM-assisted
│   │   │   ├── llm_provider.py       # ADR-003 interface + Anthropic impl
│   │   │   ├── chunking.py
│   │   │   ├── module_summarizer.py
│   │   │   ├── pattern_detector.py
│   │   │   └── tradeoff_extractor.py
│   │   ├── generation/
│   │   │   ├── study_guide_builder.py
│   │   │   ├── diagram_builder.py    # Mermaid generation (graph edges from Layer A)
│   │   │   └── citation.py
│   │   ├── quizzing/
│   │   │   ├── mcq_generator.py
│   │   │   ├── fill_blank_generator.py
│   │   │   ├── drawing_generator.py  # builds reference graph for drawing Qs
│   │   │   └── grading/
│   │   │       ├── mcq_grader.py
│   │   │       ├── fill_blank_grader.py
│   │   │       └── graph_diff_grader.py
│   │   └── worker/
│   │       ├── tasks.py              # arq task definitions
│   │       └── pipeline.py           # orchestrates the full ingest→guide pipeline
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── pages/
│   │   │   ├── AddRepo.tsx           # git URL or zip upload only — no local path (ADR-008)
│   │   │   ├── Dashboard.tsx
│   │   │   ├── StudyGuideView.tsx
│   │   │   ├── QuizTaker.tsx
│   │   │   └── AttemptResults.tsx
│   │   ├── components/
│   │   │   ├── MermaidDiagram.tsx
│   │   │   ├── CitationPanel.tsx     # click a citation, see code snippet
│   │   │   ├── DrawingCanvas.tsx     # tldraw wrapper, constrained to box+arrow
│   │   │   └── IndexingProgress.tsx  # WebSocket-driven progress UI
│   │   ├── api/                      # typed API client
│   │   └── App.tsx
├── docker-compose.yml
└── README.md
```

---

## 7. Data Model

```python
# --- Auth (Phase 4, but modeled from Phase 0 so it's additive not retrofitted) ---

class User(SQLModel, table=True):
    id: UUID
    email: str
    password_hash: str              # bcrypt/argon2 — see ADR-007
    created_at: datetime

# --- Core repo tracking ---

class Repo(SQLModel, table=True):
    id: UUID
    user_id: UUID | None            # nullable until Phase 4 auth lands; then required
    source_type: Literal["git_url", "zip_upload"]   # no "local" — see ADR-008
    source_uri: str                 # git URL, or original zip filename for reference
    display_name: str
    created_at: datetime
    latest_snapshot_id: UUID | None

class AnalysisSnapshot(SQLModel, table=True):
    id: UUID
    repo_id: UUID
    commit_hash: str | None         # null for zip uploads without embedded git history
    indexed_at: datetime
    status: Literal["pending", "parsing", "analyzing", "generating", "ready", "failed"]
    file_count: int
    language_summary: dict          # {"python": 42, "typescript": 18, ...}
    dependency_graph: dict          # {"nodes": [...], "edges": [...]}  -- LAYER A
    entry_points: list[dict]        # [{"file": ..., "kind": "cli"/"http"/"worker", ...}]

class CodeUnit(SQLModel, table=True):
    """One tree-sitter-extracted semantic unit: a class, function, or module."""
    id: UUID
    snapshot_id: UUID
    file_path: str
    unit_type: Literal["module", "class", "function"]
    name: str
    line_start: int
    line_end: int
    signature: str | None
    docstring: str | None

# --- Study guide ---

class StudyGuide(SQLModel, table=True):
    id: UUID
    repo_id: UUID
    snapshot_id: UUID               # ties guide to a specific commit/state
    version: int
    generated_at: datetime

class Section(SQLModel, table=True):
    id: UUID
    study_guide_id: UUID
    section_type: Literal["overview", "architecture", "tradeoffs", "glossary", "deep_dive"]
    title: str
    order: int
    content_md: str
    diagram_mermaid: str | None

class Citation(SQLModel, table=True):
    id: UUID
    section_id: UUID | None
    question_id: UUID | None        # citations also attach to quiz questions
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str              # the sentence/claim this citation supports
    snippet_text: str               # the actual source lines, captured at generation time —
                                     # the temp working dir is deleted after indexing (§8), so this
                                     # is what CitationPanel.tsx renders; avoids re-cloning to view a guide

# --- Quizzing ---

class Quiz(SQLModel, table=True):
    id: UUID
    repo_id: UUID
    study_guide_id: UUID
    generated_at: datetime

class Question(SQLModel, table=True):
    id: UUID
    quiz_id: UUID
    question_type: Literal["mcq", "fill_blank_code", "fill_blank_concept", "drawing"]
    prompt: str
    order: int
    # MCQ-specific
    choices: list[str] | None
    correct_choice_index: int | None
    # fill-in-blank specific
    blanked_text: str | None
    correct_answer: str | None
    # drawing-specific
    reference_graph: dict | None    # {"nodes": [...], "edges": [...]} — ground truth

class Attempt(SQLModel, table=True):
    id: UUID
    quiz_id: UUID
    user_id: UUID | None             # nullable until Phase 4 auth lands; then required
    started_at: datetime
    completed_at: datetime | None
    score: float | None             # 0.0 - 1.0

class AnswerSubmission(SQLModel, table=True):
    id: UUID
    attempt_id: UUID
    question_id: UUID
    # polymorphic answer storage
    selected_choice_index: int | None
    text_answer: str | None
    graph_answer: dict | None       # {"nodes": [...], "edges": [...]}
    is_correct: bool | None
    partial_credit: float | None    # 0.0 - 1.0, mainly for drawing/concept questions
    feedback: str | None
```

---

## 8. Pipeline Orchestration (worker/pipeline.py)

The core job pipeline, triggered when a repo is added or re-indexed:

```
1. clone_or_extract_source    → scoped temp dir, e.g. /tmp/strata-learn-jobs/{snapshot_id}/  (git clone OR zip extract — ADR-008)
2. walk_files                 → filtered file list (.gitignore for signal reduction, size caps)
3. detect_languages
4. parse_all_files            → CodeUnit records (tree-sitter)
5. build_dependency_graph     → AnalysisSnapshot.dependency_graph
6. detect_entry_points        → AnalysisSnapshot.entry_points
   [AnalysisSnapshot status: "parsing" complete → "analyzing"]
7. summarize_modules          → per-module semantic summaries (LLM)
8. detect_patterns            → architecture pattern claims + evidence (LLM)
9. extract_tradeoffs          → trade-off cards grounded in code (LLM)
   [status: "analyzing" complete → "generating"]
10. build_diagrams            → Mermaid strings (programmatic edges + LLM labels)
11. build_study_guide_sections → Section records with citations (snippet_text captured now, while source is still on disk)
12. generate_quiz_pool        → Question records (mcq, fill_blank, drawing)
13. delete_temp_dir           → wipe /tmp/strata-learn-jobs/{snapshot_id}/ — nothing downstream needs raw source after this point
    [status: "ready"]
```

Each step publishes progress over WebSocket keyed by `snapshot_id` so the frontend `IndexingProgress` component can show live status. Persist status at each stage transition so a failure is resumable/debuggable, not a silent black box.

**Temp directory lifecycle:** the cloned/extracted source only needs to exist for the duration of one pipeline run. Citations store `file_path` + line numbers *and* the actual `snippet_text` (captured at step 11, while the source is still available) specifically so the temp dir can be safely deleted at step 13 — the `CitationPanel.tsx` "click to view source" feature reads from the stored snippet, not a live re-fetch of the repo.

**Zip upload guard:** before committing to full processing, do a cheap pre-check on file count/size after extraction (step 1) and fail fast with a clear error if it's unreasonably large, rather than letting tree-sitter parsing silently run for minutes on an oversized upload. A hard cap is enough for v1 — no need for a full quota system.

---

## 9. Prompt Templates (Layer B)

### 9.0 Model Selection Per Task

The ADR-003 provider abstraction supports per-task model choice across Anthropic + OpenAI (ADR-009). Don't over-engineer this in Phase 2 — start with **one model everywhere** while prompts are still being iterated on (you don't want prompt quality and model quality as two simultaneously-changing variables), then split out once the pipeline is stable:

| Task | Reasoning demand | Recommended tier | Why |
|---|---|---|---|
| Trade-off extraction (§9.3) | High — counterfactual reasoning | Strongest available (e.g. Opus-tier / GPT-5-tier) | This is the product's differentiator; generic hand-wavy trade-off text defeats the point. Don't economize here. |
| Pattern detection (§9.2) | Medium — synthesize graph structure into a claim | Mid-tier (e.g. Sonnet-tier) | Needs to stay evidence-grounded, not just assert a plausible-sounding pattern. |
| Module summarization (§9.1) | Low-medium — describe what's in front of you | Mid-tier | Reliable at this tier with a well-grounded prompt. |
| MCQ / fill-blank generation (§9.4, §9.5) | Low — mechanical once grounded in Layer A/B facts | Cheapest capable tier | High-volume generation; optimize for cost. |
| Graph-diff grading (§10.3) | None — deterministic | N/A, no LLM in the critical path | LLM only as a fuzzy-match tiebreaker on ambiguous node labels. |

Revisit this table once Phase 2 checkpoints show real output quality — it's a starting allocation, not a permanent one.

Store prompt templates as versioned files in `docs/prompts/` (not inline strings) so they can be iterated on independently of code, and so `AnalysisSnapshot`/generated content could later record which prompt version produced it.

### 9.1 Module Summarizer

```
SYSTEM:
You are analyzing a single code module to explain it to a developer learning
software architecture. You will be given the module's file path, its parsed
structure (classes, functions, signatures, docstrings), and its import list.
Do not speculate about behavior you cannot see in the provided structure.

OUTPUT (JSON):
{
  "purpose": "1-2 sentence plain-English description of what this module does",
  "role_in_system": "1-2 sentences on how this fits into the broader repo, based on its imports/exports",
  "key_concepts": ["list of technical concepts a learner should know to understand this file"]
}

INPUT:
File: {file_path}
Imports: {import_list}
Structure:
{code_units_json}
```

### 9.2 Pattern Detector

```
SYSTEM:
You are identifying the architectural pattern(s) used in a codebase, given its
full dependency graph and directory structure. You must argue for your
conclusion using specific evidence from the graph — do not assert a pattern
without citing which folders/modules/edges support it. If evidence is mixed
or the codebase doesn't cleanly fit one pattern, say so explicitly rather
than forcing a label.

OUTPUT (JSON):
{
  "primary_pattern": "e.g., layered / hexagonal / MVC / event-driven / modular monolith",
  "confidence": "high | medium | low",
  "evidence": [
    {"claim": "...", "supporting_paths": ["path/to/evidence"]}
  ],
  "caveats": "any places where the codebase deviates from the labeled pattern"
}

INPUT:
Dependency graph: {dependency_graph_json}
Directory structure: {directory_tree}
Entry points: {entry_points_json}
```

### 9.3 Trade-off Extractor (the differentiator — spend the most iteration time here)

```
SYSTEM:
You are helping a developer understand WHY a specific technical decision was
likely made in this codebase, so they build architectural judgment rather
than just reading code. You will be given a specific decision point (e.g., a
module boundary, a choice of data structure, a queue vs. direct call, a
caching layer) along with the relevant code.

Reason about:
- What alternatives existed for this decision
- What likely motivated this choice (performance, decoupling, testability,
  team conventions, simplicity, scale requirements — be specific, not generic)
- What the trade-off cost is (what did they give up by choosing this)

Ground every claim in the provided code. If you cannot determine a plausible
reason from the evidence given, say "insufficient evidence" rather than
inventing a plausible-sounding but unfounded explanation.

OUTPUT (JSON):
{
  "decision": "short label for the decision point",
  "alternatives_considered": ["alternative 1", "alternative 2"],
  "likely_reasoning": "the argument for why this choice was probably made",
  "tradeoff_cost": "what was given up",
  "confidence": "high | medium | low",
  "evidence_refs": [{"file_path": "...", "line_start": N, "line_end": N}]
}

INPUT:
Decision point: {decision_description}
Relevant code:
{code_snippet}
Surrounding context (callers/callees):
{context_snippet}
```

### 9.4 MCQ Generator

```
SYSTEM:
Generate a multiple-choice question testing understanding of the following
code/concept. Distractors (wrong answers) must be PLAUSIBLE — pull them from
real alternative approaches, common misconceptions, or adjacent concepts in
the same codebase. Do not use obviously-wrong joke answers.

OUTPUT (JSON):
{
  "prompt": "the question text",
  "choices": ["choice A", "choice B", "choice C", "choice D"],
  "correct_index": 0,
  "explanation": "why the correct answer is correct and others are not",
  "source_refs": [{"file_path": "...", "line_start": N, "line_end": N}]
}

INPUT:
Concept/fact to test: {source_fact}
Source code context: {code_snippet}
```

### 9.5 Fill-in-the-Blank Generator

> **Superseded after Phase 8:** fill-in-the-blank was replaced in new quizzes by an open *short-answer* question type graded against a rubric of key points (`docs/prompts/short_answer_generator.v1.md`, `short_answer_grader.v1.md`). The blanked-term format, even at its most conceptual, tested recall of one word more than understanding. This section is kept as history; the grading in §10.2 likewise applies only to legacy fill-blank questions.


```
SYSTEM:
Generate a fill-in-the-blank question. Two modes:
- CODE mode: blank out a meaningful token from a real code snippet (a
  function name, a config value, a key parameter) — not trivial syntax.
- CONCEPT mode: blank out a key term in a conceptual sentence about the
  architecture (e.g., "This service uses ___ to decouple producers from
  consumers").
Prefer CONCEPT mode for architecture/pattern testing, CODE mode for testing
recall of actual implementation details.

OUTPUT (JSON):
{
  "mode": "code | concept",
  "blanked_text": "text with ___ marking the blank",
  "correct_answer": "the answer",
  "acceptable_alternatives": ["synonym or close-answer 1", "..."],
  "source_refs": [{"file_path": "...", "line_start": N, "line_end": N}]
}
```

### 9.6 Drawing Question Reference Graph Builder

No LLM needed for the ground-truth graph itself — it's derived directly from `AnalysisSnapshot.dependency_graph` (Layer A). LLM is only used to write the question prompt text and to pick a coherent, appropriately-scoped sub-graph (5-10 nodes, not the whole repo):

```
SYSTEM:
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

INPUT:
Full dependency graph: {dependency_graph_json}
```
The actual `reference_graph` stored on the `Question` is then mechanically extracted (nodes + edges) from the full graph using `selected_node_ids` — deterministic, not LLM output.

---

## 10. Grading Logic

### 10.1 MCQ — deterministic
`is_correct = (selected_choice_index == correct_choice_index)`. Score = 1.0 or 0.0.

### 10.2 Fill-in-blank
- **Code mode**: exact match (case-insensitive, whitespace-normalized) against `correct_answer`.
- **Concept mode**: check exact/alternative match first; if no match, fall back to LLM-judge with an explicit rubric:
```
SYSTEM:
Grade this fill-in-the-blank answer. The correct answer is "{correct_answer}"
(acceptable alternatives: {alternatives}). Judge whether the student's answer
is conceptually equivalent, even if worded differently. Do not require exact
wording. Award partial credit (0.5) if the answer is directionally correct
but imprecise, and 0.0 if wrong.

OUTPUT (JSON): {"score": 0.0 | 0.5 | 1.0, "feedback": "1-2 sentences"}

Student answer: {student_answer}
```

### 10.3 Drawing questions — graph diff (the core algorithm, build this deterministically, no LLM in the scoring loop except for label leniency)

```python
def grade_drawing(reference: Graph, submission: Graph) -> GradeResult:
    # 1. Node presence — did they include the right components?
    node_matches = fuzzy_match_nodes(reference.nodes, submission.nodes)
    # fuzzy match on label similarity (string similarity threshold, e.g. rapidfuzz)
    node_score = len(node_matches) / len(reference.nodes)

    # 2. Edge correctness — for each reference edge, does a corresponding
    #    edge exist between the matched submission nodes, in the right direction?
    edge_results = []
    for ref_edge in reference.edges:
        src_match = node_matches.get(ref_edge.source)
        tgt_match = node_matches.get(ref_edge.target)
        if src_match and tgt_match:
            found = submission.has_edge(src_match, tgt_match, direction=ref_edge.direction)
            edge_results.append(found)
    edge_score = sum(edge_results) / len(reference.edges)

    # 3. Extraneous elements — penalize (lightly) nodes/edges with no
    #    reference match, to discourage "draw everything and hope"
    extraneous_penalty = calc_extraneous_penalty(submission, node_matches)

    final_score = clamp(0.6 * edge_score + 0.4 * node_score - extraneous_penalty, 0, 1)

    # 4. Feedback generation — deterministic diff description, LLM only
    #    polishes phrasing, does not decide correctness
    missing_edges = [e for e in reference.edges if not found]
    feedback = build_structured_feedback(missing_edges, missing_nodes, extraneous)

    return GradeResult(score=final_score, feedback=feedback, ...)
```

Node label fuzzy-matching: use `rapidfuzz` string similarity with a threshold (~80%) before falling back to an LLM call for genuinely ambiguous cases (e.g., "Auth Service" vs "Authentication Layer" — same concept, different phrasing). Keep the LLM off the critical path for the common case; use it only as a tiebreaker.

---

## 11. API Surface (v1)

```
POST   /repos                          # add repo (git URL or zip upload — ADR-008)
GET    /repos                          # list repos
GET    /repos/{id}                     # repo detail + latest snapshot status
POST   /repos/{id}/reindex             # trigger re-analysis
WS     /repos/{id}/progress            # live indexing progress

GET    /study-guides/{id}              # full study guide with sections
GET    /study-guides/{id}/diff/{other_version}   # architectural diff between versions

POST   /quizzes/{repo_id}/generate     # generate a new quiz from latest study guide
GET    /quizzes/{id}                   # quiz + questions (no answers exposed)

POST   /attempts                       # start an attempt
PATCH  /attempts/{id}/answers/{qid}    # submit an answer to one question
POST   /attempts/{id}/complete         # finalize, trigger grading, return results
GET    /attempts/{id}                  # attempt detail with per-question feedback
```

---

## 12. Build Phases & Tasks

### Phase 0 — Scoping & Setup (2-3 days)
- [ ] Initialize repo with structure from §3
- [ ] Write ADR files (§2) into `docs/adr/`
- [ ] Docker Compose: postgres, redis, api, worker, web services
- [ ] FastAPI app skeleton with health check endpoint
- [ ] Alembic migration setup, initial empty migration
- [ ] **Checkpoint:** `docker compose up` brings up a working empty stack; health check responds.

### Phase 1 — Ingestion + Layer A (Structural Analysis) — 1-2 weeks
- [ ] `ingestion/source.py`: git clone (GitPython) by URL, and zip upload extraction — into a scoped temp dir per job (no local filesystem path option — ADR-008)
- [ ] Zip upload size/file-count guard: fail fast on oversized uploads before parsing begins
- [ ] `ingestion/walker.py`: file walk respecting `.gitignore` (use `pathspec` lib) for signal reduction, size caps, binary file skip
- [ ] `ingestion/language_detect.py`: extension-based + shebang detection
- [ ] `analysis/parser.py`: tree-sitter wrapper, start with Python + JavaScript/TypeScript grammars (expand later)
- [ ] `analysis/dependency_graph.py`: build import graph from parsed `CodeUnit`s
- [ ] `analysis/entry_points.py`: heuristics for common entry-point patterns (main.py, package.json scripts, Dockerfile CMD/ENTRYPOINT, manage.py, etc.)
- [ ] `analysis/snapshot.py`: assemble full `AnalysisSnapshot`, persist to DB
- [ ] `User` (unused until Phase 4), `Repo`, `AnalysisSnapshot`, `CodeUnit` DB models + migration — add the nullable `user_id` FK to `Repo` now so auth in Phase 4 is additive, not a retrofit. (`Attempt` also gets a `user_id` FK, but that table isn't created until Phase 5 — add it there, same additive pattern.)
- [ ] `POST /repos` endpoint — build synchronous here (no queue yet); it gets refactored to enqueue in Phase 1.5 (D13)
- [ ] **Checkpoint:** Point at a small real repo (recommend a small Flask/FastAPI app, ~20-50 files), get a persisted `AnalysisSnapshot` with an accurate dependency graph you can manually verify against the actual imports.

### Phase 1.5 — Job Queue Wiring — 2-3 days
- [ ] Set up `arq` worker, Redis connection
- [ ] Move Phase 1 pipeline steps into `worker/pipeline.py` as an async job
- [ ] Add `snapshot.status` state transitions + WebSocket progress publishing
- [ ] `POST /repos` now enqueues instead of running synchronously; `WS /repos/{id}/progress` streams status
- [ ] **Checkpoint:** Add a repo via API, watch status transition through pending → parsing → ready over the websocket.

### Phase 2 — Layer B (Semantic Analysis) — 1-2 weeks
- [ ] `semantics/llm_provider.py`: implement the ADR-003 provider interface with both Anthropic and OpenAI backends (ADR-009); wire model-per-task selection per §9.0
- [ ] `semantics/chunking.py`: group `CodeUnit`s into LLM-appropriately-sized chunks (by tree-sitter unit, not token windows)
- [ ] `semantics/module_summarizer.py`: implement prompt 9.1, store results
- [ ] `semantics/pattern_detector.py`: implement prompt 9.2
- [ ] `semantics/tradeoff_extractor.py`: implement prompt 9.3 — **iterate on this prompt against 2-3 real repos before moving on, this is the differentiator**
- [ ] Wire these into the pipeline after Layer A completes
- [ ] **Checkpoint:** Manually review trade-off extractions against ground truth in 2-3 test repos. Are the citations accurate? Is the reasoning plausible and non-generic?

### Phase 3 — Study Guide Generation — 1 week
- [ ] `generation/diagram_builder.py`: Mermaid component diagram from `dependency_graph` (deterministic edges, LLM labels only)
- [ ] `generation/diagram_builder.py`: Mermaid ER diagram if DB schema detected (stretch — can defer)
- [ ] `generation/study_guide_builder.py`: assemble `StudyGuide` + `Section` records (Overview, Architecture, Trade-offs, Glossary, Deep-Dives)
- [ ] `generation/citation.py`: attach `Citation` records to every generated claim
- [ ] `StudyGuide`, `Section`, `Citation` DB models + migration
- [ ] `GET /study-guides/{id}` endpoint
- [ ] **Checkpoint:** Full pipeline run produces a readable study guide with valid Mermaid syntax and accurate citations for a real repo.

### Phase 4 — Frontend Shell — 1-2 weeks
- [ ] Vite + React + TS + Tailwind scaffold
- [ ] Login/register/logout UI + session handling (ADR-007) — self-implemented, not a library
- [ ] `AddRepo.tsx`: form for git URL or zip upload (no local path — ADR-008)
- [ ] `IndexingProgress.tsx`: WebSocket-driven progress display
- [ ] `Dashboard.tsx`: repo list with status
- [ ] `StudyGuideView.tsx`: rendered markdown sections + `MermaidDiagram.tsx` inline rendering
- [ ] `CitationPanel.tsx`: click a citation → display the stored `snippet_text` (no re-fetch/re-clone needed — see §8 temp dir lifecycle)
- [ ] Backend: `User` model wired up for real, `bcrypt`/`argon2` password hashing, session cookie issuance/validation middleware, `/auth/register`, `/auth/login`, `/auth/logout` endpoints
- [ ] Backfill `user_id` on existing `Repo`/`Attempt` records if any test data exists from earlier phases
- [ ] **Checkpoint:** End-to-end flow works in the browser: register/login → add repo → watch progress → read generated study guide with working diagrams and citations, scoped to the logged-in user.

### Phase 5 — Quiz Generation & Taking (MCQ + Fill-blank) — 1-2 weeks
- [ ] `quizzing/mcq_generator.py`: implement prompt 9.4
- [ ] `quizzing/fill_blank_generator.py`: implement prompt 9.5
- [ ] `quizzing/grading/mcq_grader.py`, `fill_blank_grader.py`: implement §10.1, §10.2
- [ ] `Quiz`, `Question`, `Attempt`, `AnswerSubmission` DB models + migration — include the nullable `user_id` FK on `Attempt` here (per D13/Phase 1 note), keeping the additive-auth pattern
- [ ] `POST /quizzes/{repo_id}/generate`, `GET /quizzes/{id}`, `POST /attempts`, `PATCH /attempts/{id}/answers/{qid}`, `POST /attempts/{id}/complete`
- [ ] `QuizTaker.tsx`, `AttemptResults.tsx` frontend
- [ ] **Checkpoint:** Generate a quiz from a real study guide, take it in the UI, get accurate scoring and feedback with working citations back to the study guide.

### Phase 5.5 — UI Design Integration — 1-2 weeks

**Scope:** Apply the interactive prototype and Organic design system in the checked-in [`Strata-Learn UI mockups.zip`](../../Strata-Learn%20UI%20mockups.zip) to the working Phase 4–5 React application before expanding the interaction surface with drawing questions. The archive is the visual reference; the real APIs, authorization rules, persisted state, and behavior documented in the UI spec remain canonical.

- [ ] Extract the mockup's color, typography, spacing, radius, elevation, and interaction tokens into version-controlled frontend styles. Do not make the production app depend on loading assets from the zip at runtime.
- [ ] Add the required font and icon dependencies deliberately, with local/system fallbacks and no silent dependency on the prototype's support script.
- [ ] Refactor shared primitives and `AppLayout.tsx` so navigation, buttons, fields, cards, tags, dialogs, tables, and focus treatment consistently use the Organic system instead of page-specific approximations.
- [ ] Implement the prototype treatment across login/register, signed-out, dashboard, add-repository, repository/indexing, study-guide, quiz, and results screens while preserving real loading, empty, failure, permission, and long-content behavior.
- [ ] Resolve the design/behavior gaps tracked for runtime auth expiry, repository retry, result answer detail, previous-question navigation, retakes, feedback timing, and bounded quiz polling; do not reproduce prototype-only fake state where it conflicts with the application contract.
- [ ] Make the result responsive enough for supported desktop and narrow layouts, and verify semantic headings, labels, contrast, reduced-motion behavior, keyboard navigation, and visible focus.
- [ ] Add a frontend test runner and interaction coverage for the shared primitives and critical auth/repository/quiz flows before Phase 6 adds canvas state.
- [ ] Capture visual-regression references for every route and major state at representative wide and narrow viewports. Document any intentional deviations from the mockup in the UI spec.
- [ ] **Checkpoint:** Complete the real register/login → add repo → indexing → study guide → quiz → results flow using the integrated design, with automated frontend checks passing and a manual keyboard/responsive/visual comparison against the prototype.

### Phase 6 — Drawing Questions — 1-2 weeks (isolate, hardest phase)
- [ ] `quizzing/drawing_generator.py`: implement prompt 9.6 + deterministic subgraph extraction
- [ ] `DrawingCanvas.tsx`: tldraw integration constrained to box + labeled-arrow shapes only; export to `{nodes, edges}` JSON on submit
- [ ] `quizzing/grading/graph_diff_grader.py`: implement §10.3 algorithm (node fuzzy-match via `rapidfuzz`, edge connectivity/direction check, extraneous penalty, structured feedback)
- [ ] Wire drawing question type through the existing `Attempt`/`AnswerSubmission` flow
- [ ] **Checkpoint:** Take a drawing question, deliberately submit a partially-wrong graph (missing one edge, one mislabeled node), verify the grader gives sensible partial credit and specific, correct feedback.

### Phase 7 — Versioning, Diffing, Hosting, Polish — 1-2 weeks
- [ ] Re-index detection: compare new commit hash to `latest_snapshot_id`, flag study guide as stale in UI
- [ ] `GET /study-guides/{id}/diff/{other_version}`: architectural diff between two snapshots (diff the dependency graphs + trade-off cards)
- [ ] Mastery tracking: aggregate `Attempt` scores per `Section`/topic over time
- [ ] Markdown/PDF export of study guides
- [ ] **Checkpoint:** Re-index a repo after making a real code change, confirm the diff view accurately reflects what changed.
- [ ] Work through the VPS migration checklist in §13
- [ ] **Checkpoint:** Full flow works against the hosted instance over HTTPS with real auth cookies.

### Phase 8 — Voice Learning (Read-Aloud + Spoken Quiz Answers) — 1-2 weeks

**Scope:** Extend the existing text-first learning flows with a chained, request-based audio layer. The persisted study-guide text, citations, learner-confirmed transcript, and existing grading endpoints remain canonical; audio is an input/output convenience, not a parallel source of truth.

- [ ] Record the audio architecture and retention decisions in an ADR: separate transcription and speech provider boundaries, hosted OpenAI services initially, no raw-audio persistence, and explicit cost/size limits.
- [ ] Add `TranscriptionProvider` and `SpeechProvider` protocols plus deterministic fake implementations for automated tests. Keep these separate from the text-oriented `LLMProvider` interface.
- [ ] Add environment-driven audio configuration (`OPENAI_API_KEY`, transcription model, speech model/voice, request-size and duration limits) without requiring OpenAI credentials for non-audio features.
- [ ] **Read aloud:** add an authenticated, ownership-scoped API that accepts identifiers for persisted study-guide sections or quiz feedback and streams synthesized speech; do not expose an arbitrary paid text-to-speech proxy.
- [ ] Add accessible read-aloud controls to `StudyGuideView.tsx` and quiz feedback/results: play, pause/stop, replay, loading, and failure states, with a clear disclosure that the voice is AI-generated.
- [ ] **Spoken quiz answers:** add browser microphone capture via `MediaRecorder` for fill-in-the-blank questions and upload the completed recording to an authenticated transcription endpoint.
- [ ] Validate microphone uploads before any provider call: allowlisted audio formats, bounded bytes and duration, ownership checks, rate/cost limiting, and clear unsupported-browser/permission-denied errors.
- [ ] Return an editable transcript to the learner before submission. Only the confirmed text is sent through the existing `PATCH /attempts/{id}/answers/{qid}` path and persisted as `AnswerSubmission.answer_text`; never grade an unconfirmed transcript automatically.
- [ ] Supply bounded repository/question vocabulary hints where supported to improve transcription of technical terms, while preserving manual correction for identifiers such as `snake_case`, filenames, and library names.
- [ ] Do not persist raw microphone audio or generated speech in v1. Document the retention behavior and keep audio-provider calls observable without logging audio content or secrets.
- [ ] Add backend tests with fake audio providers and frontend tests with mocked media/audio browser APIs. Automated tests must not make real, paid, or nondeterministic speech calls.
- [ ] **Checkpoint — read aloud:** play a real generated study-guide section and quiz explanation in the browser, verify streaming/playback controls and the AI-voice disclosure, and confirm another user cannot request its audio.
- [ ] **Checkpoint — spoken answer:** record both a concept answer and a technical identifier, edit the returned transcript, submit it through the existing quiz flow, and confirm grading/results contain only the learner-approved text.

**Explicit non-goals for Phase 8:** realtime speech-to-speech sessions, interruption/barge-in, a conversational repository tutor, voice control for MCQ/drawing interactions, speaker diarization, and durable audio storage. These can be evaluated only after the two bounded workflows demonstrate real value.

> **Amended at Phase 8 kickoff (ADR-010):** self-hosted Whisper/model serving was originally listed here as a non-goal, and "hosted OpenAI services initially" was the scope. Both are reversed — each provider protocol gets a hosted *and* an in-process self-hosted backend, plus a word-error-rate evaluation comparing them. The reasoning is recorded in [ADR-010](../adr/ADR-010-voice-providers-and-self-hosted-backends.md); this file keeps the original text above as historical context.

---

## 13. Hosting

### Sequencing decision (locked)

Build and iterate entirely on **local Docker Compose** (Option A below) through Phase 6. Don't deploy what's still actively being rewritten — hosting decisions this early just add variables while prompts and the core pipeline are still stabilizing. Once Phase 7 is reached and the tool is stable, deploy to a **VPS** (Option B) as part of Phase 7 polish.

This sequencing is viable specifically because of ADR-008 (no local-filesystem ingestion) — since ingestion is just git-clone + zip-upload, there's no architectural fork between "runs on my laptop" and "runs on a rented server." The app does the same thing either way.

### Options considered

| Option | Setup effort | Monthly cost | Ops learning | Notes |
|---|---|---|---|---|
| **A. Local only** (`docker-compose up`) | Minimal | $0 | Low | Phase 0–6 default. No always-on access, but zero ops burden while iterating. |
| **B. Single VPS** (Hetzner/DigitalOcean droplet) | Low-medium | ~$12–24 | **High** — real Linux/Docker/TLS/firewall reps | **Chosen for Phase 7.** Same compose file as local; add Caddy as a reverse proxy for automatic TLS. Low-stakes environment for a single-user tool, so mistakes are cheap lessons. |
| C. PaaS (Fly.io, Railway, Render) | Low | ~$5–20 | Medium — platform-level, not Linux-level | Good middle ground if B ever feels like too much sysadmin overhead, but chosen against for now since B gives more ops learning at similar cost. |
| D. Full cloud (AWS/GCP — ECS/RDS/etc.) | High | $30–60+ | Highest, but highest time cost too | Deliberately deferred to a **separate future project**: re-deploying an already-working app to AWS is a clean way to practice cloud architecture specifically, without debugging product logic and infra at the same time. Good fit if dedicated SA-style cloud reps become a goal later. |

### Migration checklist (Phase 7, local → VPS)

Moving from Option A to B is deployment logistics, not a redesign — no ingestion, job queue, or data model changes required. The concrete steps:

- [ ] Provision VPS (2–4GB RAM is sufficient — no local LLM inference to size for), install Docker
- [ ] Copy `docker-compose.yml` to the VPS, bring up the stack
- [ ] `pg_dump` local Postgres → `pg_restore` on the VPS (one-time data migration if carrying over existing repos/study guides)
- [ ] Add Caddy as a reverse proxy container for automatic TLS/Let's Encrypt if using a real domain
- [ ] Move secrets (Anthropic/OpenAI API keys, session secret) to the VPS via `.env` with correct file permissions
- [ ] **Auth cookie config check** (easy to get wrong silently): set `Secure=True`, correct `SameSite`, and the real domain in cookie scope — these are no-ops on `localhost` but required once served over a real domain/HTTPS
- [ ] Confirm `pydantic-settings` environment values (`DATABASE_URL`, `REDIS_URL`, CORS origins) are pointed at VPS-internal hostnames rather than local compose defaults — this is why §1 calls for environment-driven config from Phase 0, so this step is a config swap, not code changes
- [ ] **Checkpoint:** full flow (register → add repo → study guide → quiz) works against the VPS-hosted instance over HTTPS.

---

## 14. Suggested Timeline

| Phase | Duration | Cumulative |
|---|---|---|
| 0. Scoping/Setup | 2-3 days | ~1 week |
| 1. Ingestion + Layer A | 1-2 weeks | ~3 weeks |
| 1.5 Job queue | 2-3 days | ~3.5 weeks |
| 2. Layer B (Semantic) | 1-2 weeks | ~5.5 weeks |
| 3. Study guides | 1 week | ~6.5 weeks |
| 4. Frontend shell | 1-2 weeks | ~8.5 weeks |
| 5. Quizzes (MCQ/blank) | 1-2 weeks | ~10.5 weeks |
| 5.5 UI design integration | 1-2 weeks | ~12 weeks |
| 6. Drawing questions | 1-2 weeks | ~14 weeks |
| 7. Polish + VPS hosting | 1-2 weeks | ~15.5 weeks |
| 8. Voice learning | 1-2 weeks | ~17 weeks |

~4 months solo, part-time-adjustable.

---

## 15. Test Repos for Development

Use these as fixtures throughout — small enough to manually verify output against, varied enough to stress different parts of the pipeline:

1. A small Flask or FastAPI app (~20-50 files) — clean layered structure, good Phase 1 sanity check
2. A repo with a queue/worker pattern (e.g., something using Celery or RQ) — good test for the trade-off extractor (direct call vs. queue)
3. A repo with a clear MVC or hexagonal structure — tests the pattern detector's accuracy
4. Your own prior project — the real dog-fooding test. Readable study guides exist to review from Phase 3; quizzes to take from Phase 5 (see §16).

---

## 16. Self-Verification Loop (meta, but load-bearing)

Since the entire point of this tool is verifying understanding rather than just shipping code, apply the same discipline to building it:

- After each phase checkpoint, write a short note (even 3-5 sentences) explaining *why* you built that piece the way you did — not what it does, but why this approach over the alternatives. If you can't do this fluently, that's a signal to slow down before the next phase compounds on shaky understanding.
- From Phase 3, review the study guide the tool generates for its own codebase — is it accurate about your own architecture? From Phase 5 onward, also take the generated quizzes. Use both as a running QA check on your own comprehension *and* the generator's output quality.

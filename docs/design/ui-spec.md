# Strata Learn — UI/UX Feature Spec

> **Target behavior.** This document specifies the intended Phase 4–6 user experience. The functional Phase 4–5 baseline now exists; Phase 5.5 applies the checked-in visual design before drawing-question work begins. See [Current architecture](../architecture.md) for implemented behavior and the status matrix below for known gaps.

| | |
|---|---|
| **Status** | Target UX — Phase 4–5 baseline implemented; Phase 5.5 design integration next; Phase 6 not started |
| **Related design** | [Original project plan](original-project-plan.md); this spec covers the Phase 4–6 frontend surface |
| **Owner** | Solo builder |
| **Intended reader** | Claude Design / Claude Code (frontend build) |

---

## 1. Problem

The backend pipeline (ingest → Layer A → Layer B → generation → assessment) needs a UI that lets the person get a repo into the system, see whether or where the pipeline is stuck, read what was generated, and take and grade a quiz. The functional baseline now exists. Phase 5.5 makes the interactive prototype in [`Strata-Learn UI mockups.zip`](../../Strata-Learn%20UI%20mockups.zip) the explicit visual reference for production design parity; this spec remains the behavioral reference for that integration and later drawing-question work.

## 2. Goals

- Make ingestion (git URL or zip) fast and unambiguous — one screen, two paths, clear feedback either way.
- Make pipeline state legible in real time — the person should never wonder "is it still working?"
- Make study guides genuinely readable, not a wall of markdown — diagrams inline, citations one click from the claim they support.
- Make quiz-taking feel like a quiz, not a form — one question in view at a time, immediate or end-of-quiz feedback (configurable), and drawing questions that don't fight the person's mouse.
- Keep the whole thing usable by one person on one account — no team/sharing chrome to build or maintain.

## 3. Non-Goals (v1)

- No multi-user collaboration, sharing, or commenting on study guides.
- No mobile-optimized layout — desktop-first, responsive-enough is fine, not a priority.
- No editing of generated study guide content in the UI (regenerate via re-index instead, per the master plan's diffing model).
- No real-time collaborative quiz-taking (single learner, single session).

## 4. Primary User & Core Jobs

One user (the builder). Four jobs, in the order they're typically done:

1. **"Get this repo into the tool"** — add a repo, watch it index.
2. **"Is it done yet / did it break?"** — check pipeline status without babysitting the tab.
3. **"Help me actually understand this codebase"** — read the study guide, drill into citations, see diagrams.
4. **"Test whether I actually learned it"** — generate and take a quiz, see graded results with feedback tied back to the guide.

## 5. Screen Inventory

Mapped to the component names from the original project plan — this spec elaborates their intended content and states.

| Screen/Component | Plan reference | Purpose |
|---|---|---|
| `AddRepo.tsx` | Phase 4 | Ingest a repo via git URL or zip upload |
| `Dashboard.tsx` | Phase 4 | List of repos + pipeline status at a glance |
| `IndexingProgress.tsx` | Phase 4 | Live per-stage pipeline progress (component, used within Dashboard and a repo's detail view) |
| `StudyGuideView.tsx` | Phase 4 | Read the generated study guide |
| `MermaidDiagram.tsx` | Phase 4 | Inline diagram rendering (component, used within StudyGuideView) |
| `CitationPanel.tsx` | Phase 4 | Click a citation → see the cited snippet (component) |
| `QuizTaker.tsx` | Phase 5 | Take a generated quiz |
| `DrawingCanvas.tsx` | Phase 6 | tldraw-based box/arrow canvas for drawing questions (component, used within QuizTaker) |
| `AttemptResults.tsx` | Phase 5 | Graded results + per-question feedback |
| `RepoDetail.tsx` | Phase 4 addition | Container for a single repo's status, study guide link, and quiz history |

---

## 5.1 Implementation Status

| Area | Current state | Remaining target work |
|---|---|---|
| Authentication and repository ingestion | Implemented through Phase 4b, including the single-account registration secret, Git URL/zip paths, client-side zip-size validation, and upload progress | Clear auth state after a runtime `401` ([#33](https://github.com/bghannum/strata-learn/issues/33)) |
| Indexing progress and repository detail | Implemented with shared chip/stepper states, WebSocket updates, failure details, and a raw-analysis viewer | Re-index/retry the same repository instead of creating a new one ([#26](https://github.com/bghannum/strata-learn/issues/26)); bound dashboard WebSocket fan-out ([#27](https://github.com/bghannum/strata-learn/issues/27)) |
| Study-guide reading | Implemented with section navigation, collapsible Markdown, Mermaid rendering, and a citation slide-over | Citations currently render as a per-section list because generated `claim_excerpt` values cannot always be anchored to a literal substring; precise inline markers remain a target, not an implemented claim |
| Quiz generation and taking | Implemented through Phase 5 with MCQ/fill-in-the-blank questions, resumable attempts, immediate grading, citations, and refreshable results | Completed-result answer detail ([#34](https://github.com/bghannum/strata-learn/issues/34)), previous-question navigation ([#35](https://github.com/bghannum/strata-learn/issues/35)), retakes ([#36](https://github.com/bghannum/strata-learn/issues/36)), feedback timing ([#37](https://github.com/bghannum/strata-learn/issues/37)), and bounded/cancellable polling ([#38](https://github.com/bghannum/strata-learn/issues/38)) |
| Visual design system | The current functional UI predates the checked-in Organic mockup | Phase 5.5 ports the mockup's tokens and interaction patterns into maintainable React/CSS, then verifies every existing screen, state, breakpoint, and keyboard path |
| Drawing questions | Not implemented; `DrawingCanvas.tsx` is a stub and the generator/grader modules are empty | All Phase 6 work in §6.6 and the drawing-specific result treatment in §6.7 |

This matrix is a concise reconciliation aid, not a second backlog. GitHub Issues remain canonical for actionable work, and [Current architecture](../architecture.md) remains canonical for implemented behavior.

---

## 6. Flow Specs

### 6.1 Ingest a repo — `AddRepo.tsx`

**Entry point:** "Add Repo" button on `Dashboard.tsx`.

**Layout:** single screen, two tabs or a toggle: **Git URL** / **Upload Zip**.

- **Git URL path:** one text input (validated as a URL on blur, not on every keystroke), optional display-name override (defaults to repo name parsed from URL). Submit → `POST /repos`.
- **Zip Upload path:** drag-and-drop zone + file picker fallback. Show filename and size once selected. Client-side size check before upload (reject obviously-oversized files immediately, rather than waiting on the server guard from the master plan's §8 zip guard — redundant but saves a round trip).
- Submit button disabled until a valid input exists in the active tab.
- On submit: navigate to `RepoDetail.tsx` for the new repo, which immediately shows `IndexingProgress.tsx` in its initial state.

**States:** default / validating URL / uploading (progress bar for zip) / submit error (e.g., unreachable git URL, malformed zip — surface the backend's error message directly, don't paraphrase it into something vaguer).

### 6.2 Pipeline health & status — `IndexingProgress.tsx` + `Dashboard.tsx`

This is the "is it done yet" job, and it deserves more than a spinner.

**Dashboard-level (list view):** each repo row shows a compact status chip reflecting `AnalysisSnapshot.status` (`pending` / `parsing` / `analyzing` / `generating` / `ready` / `failed`), driven by the same WebSocket channel the detail view uses. No need to open a repo to know it's stuck.

**Detail-level (`IndexingProgress.tsx`, expanded):** a horizontal stepper mirroring the 13-step pipeline from the master plan's §8, collapsed into the 5 stages the person actually cares about: **Ingesting → Structural Analysis (Layer A) → Semantic Analysis (Layer B) → Generating Study Guide → Ready.** Each stage shows one of: not started / in progress (with a light animated indicator, not a fake progress percentage — the backend doesn't expose granular percentages, don't imply precision that isn't there) / complete / failed.

**On failure:** show which stage failed, and surface the backend error message plus a "Retry" action that re-triggers `POST /repos/{id}/reindex`. Don't leave the person staring at a dead spinner — an explicit failed state with a next action is the whole point of this screen existing.

**Design note:** this is exactly the kind of thing worth a small live interactive rather than a static screenshot when it's actually built — the stepper's states are the interesting part.

### 6.3 Study guide — `StudyGuideView.tsx`

**Layout:** left-hand section nav (Overview / Architecture / Trade-offs / Glossary / Deep-Dives — matching `Section.section_type` from the master plan's schema) + main reading pane. Collapsible sections so the person can jump to what they need rather than scroll a monolith.

- Markdown content renders normally; any `diagram_mermaid` on a section renders inline via `MermaidDiagram.tsx` immediately after the section's prose, not detached into a separate tab.
- **Citations render as inline, unobtrusive markers** (e.g., a small superscript or bracketed reference) attached to the specific claim they support — not as a footnote list at the bottom, since the whole point is tying a claim to its evidence at the point of reading. Clicking one opens `CitationPanel.tsx`.
- `CitationPanel.tsx`: a side panel or slide-over (not a full navigation away from the guide) showing the cited file path, line range, and the stored `snippet_text` with basic syntax highlighting. Include a "copy path" affordance since the person may want to open the real file in their own editor.
- **Staleness banner:** if the repo has been re-indexed and a newer snapshot exists, show a persistent (not dismissible) banner at the top: "This guide reflects an earlier version of the repo. [View diff] [Regenerate]" — ties to the master plan's Phase 7 diffing feature.
- Entry point to quiz generation: a persistent "Generate Quiz" button, visible while reading — the natural moment to want one is mid-read, not just from the dashboard.

### 6.4 Materialized data visualization — **recommendation: defer, don't build a dedicated screen for v1**

You flagged this as a "maybe" and I'd resolve it that way deliberately, not leave it open:

- The dependency graph, entry points, and raw `AnalysisSnapshot` are Layer A facts — but they're already the *input* to the architecture diagram the person reads in `StudyGuideView.tsx`. A separate raw-data visualization screen would mostly duplicate what the diagram already shows, for an audience (you, debugging your own pipeline) that's better served by a database client or a `/repos/{id}/snapshot` JSON endpoint you hit directly during development.
- **Recommendation:** don't build a dedicated visualization screen in v1. Instead, add a lightweight **"View raw analysis" link** in `RepoDetail.tsx` that opens the `AnalysisSnapshot` JSON in a simple formatted viewer (a `<pre>` block with syntax highlighting is enough — this is a debug affordance, not a designed feature). This gets you visibility into Layer A output while you're building/debugging Phases 1–3, without committing design time to a screen whose main audience is "you, checking your own pipeline."
- If it turns out during Phase 1–3 dogfooding that you genuinely want to *browse* the dependency graph interactively (e.g., as its own study aid, independent of the generated diagram), that's a legitimate Phase 7+ addition — but build it only if the debug-viewer proves insufficient, not speculatively now.

### 6.5 Quiz generation & taking — `QuizTaker.tsx`

**Generation:** triggered from `StudyGuideView.tsx` or `RepoDetail.tsx`. While `POST /quizzes/{repo_id}/generate` runs, show a brief loading state (this should be fast relative to indexing — seconds, not minutes) then navigate into the quiz.

**Taking:** one question in view at a time (not a long scrolling form) with a progress indicator ("Question 3 of 12") and Previous/Next navigation. Question type determines the input control:

- **MCQ:** radio-button choices.
- **Fill-in-blank:** single text input inline with the surrounding sentence/code, so the blank reads in context rather than as a detached prompt.
- **Drawing:** `DrawingCanvas.tsx` — see §6.6.

**Feedback timing:** make this a per-quiz toggle at generation time ("Show answers as I go" vs. "Show results at the end") rather than a global setting — some sessions you'll want immediate correction, others you'll want to simulate a real test. Default to end-of-quiz, since that's closer to genuine self-assessment.

**Submit:** `PATCH /attempts/{id}/answers/{qid}` fires on each answer (so nothing is lost if the session is interrupted), `POST /attempts/{id}/complete` on final submit, which navigates to `AttemptResults.tsx`.

### 6.6 Drawing questions — `DrawingCanvas.tsx`

Per ADR-005, this is a constrained tldraw instance, not freehand drawing. UI implications:

- Toolbar limited to: add box (with an editable label), add labeled arrow between two boxes, delete, undo/redo. No freehand pen, no color picker, no text-only shapes — the constraint is a feature (it's what makes grading tractable), so the UI should make the constrained toolset feel intentional, not like a stripped-down version of something bigger.
- Show the question prompt persistently above or beside the canvas (e.g., "Draw the flow of a request from entry point to database") — easy to lose track of what's being asked once absorbed in the canvas.
- Submit button disabled until at least one box and one edge exist, to avoid trivial empty submissions.
- No live grading feedback while drawing — grading happens on quiz submission, consistent with the rest of the quiz flow.

### 6.7 Results & grading — `AttemptResults.tsx`

- Overall score prominent at the top (percentage + raw count).
- Per-question breakdown below: question, the person's answer, correct answer, and `feedback` text from the grader (per the master plan's §10 grading logic). For drawing questions, show the person's submitted graph alongside the reference graph, with mismatches visually distinguished (e.g., a missing edge highlighted in the reference, an extraneous one flagged in the submission) — this is where the graph-diff grader's structured feedback actually pays off, so don't collapse it into a text summary alone if the visual comparison is feasible.
- Each question's feedback links back to the relevant `StudyGuideView.tsx` section/citation, closing the loop the master plan's §16 self-verification section describes.
- A "Retake" action that generates a fresh quiz from the same study guide (new question selection, not a review of the same one).

---

## 7. Component States (apply across all screens)

Every data-bearing screen needs explicit design for: **loading**, **empty** (e.g., a repo with no quizzes generated yet), and **error** (network failure, backend 4xx/5xx) — not just the happy path. This is worth stating explicitly because it's the most common thing a spec-to-build handoff omits, and it's cheap to design for up front versus retrofit.

## 8. Design System Notes

- Tailwind utility classes per the master plan's stack — no separate design system to build from scratch.
- Diagrams (`MermaidDiagram.tsx`) and code snippets (`CitationPanel.tsx`) are the two places visual polish matters most, since they're the "does this feel like a real study tool" moments — worth disproportionate design attention relative to, say, the dashboard list view.
- Status/progress indicators (§6.2) should use a consistent color-and-icon language across `Dashboard.tsx` and `IndexingProgress.tsx` — same five states, same visual treatment, everywhere they appear.

## 9. Open Items for Design/Build

- Per-quiz feedback-timing toggle (§6.5) isn't in the master plan's `Quiz` schema — needs a field (e.g., `feedback_mode`) added if this is built as specified.
- Whether the "View raw analysis" debug viewer (§6.4) is worth gating behind anything, or just always visible — leaning toward always-visible since this remains a single-user tool.

## 10. Phasing

This spec's flows map directly onto the master plan's Phase 4–7 UI work. Phase 8 voice learning is scoped separately in the master plan and does not change these requirements:

- **Phase 4 — baseline implemented:** §6.1, §6.2, §6.3 (minus staleness and the known citation/retry gaps), and §6.4's debug viewer
- **Phase 5 — baseline implemented:** §6.5 and §6.7 for MCQ/fill-blank, with the known gaps linked in §5.1
- **Phase 5.5 — next:** apply the checked-in mockup's Organic visual system and screen treatments to the real Phase 4–5 flows; resolve the linked design-parity gaps; verify responsive, loading, empty, error, disabled, hover, pressed, and keyboard-focus states
- **Phase 6 — not started:** §6.6, plus the drawing-specific parts of §6.7
- **Phase 7 — planned:** staleness banner (depends on diffing), any expansion of §6.4 if the debug viewer proves insufficient

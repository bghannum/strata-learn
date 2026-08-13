# Documentation

This index identifies the canonical source for each kind of project information. When documents disagree, prefer the source listed here.

| Subject | Canonical source | Lifecycle |
|---|---|---|
| Setup, local development, and current phase | [Root README](../README.md) | Current |
| Implemented system and API surface | [Architecture](architecture.md) | Current |
| Phase roadmap | [GitHub Milestones](https://github.com/bghannum/strata-learn/milestones) | Current |
| Actionable and deferred work | [GitHub Issues](https://github.com/bghannum/strata-learn/issues) | Current |
| Branch, PR, CI, and review process | [Development workflow](development/workflow.md) | Current |
| Architectural rationale | [Architecture decision records](adr/) | Current unless superseded |
| Intended frontend experience | [UI/UX specification](design/ui-spec.md) | Planned |
| Original vision and implementation sequence | [Original project plan](design/original-project-plan.md) | Historical/aspirational |
| Resolved bugs and engineering lessons | [Resolved engineering issues](history/resolved-engineering-issues.md) | Historical |
| Previous AI-review experiments | [AI review experiments](history/ai-review-experiments.md) | Historical |
| LLM behavior | [Versioned prompt templates](prompts/) | Runtime assets |

## Lifecycle labels

- **Current** describes the code that exists now and should be maintained with implementation changes.
- **Planned** specifies intended behavior but does not imply that it has been implemented.
- **Historical/aspirational** preserves original reasoning and direction without serving as a live tracker.
- **Runtime asset** is loaded by application code. Changes affect behavior and require tests even though the file is Markdown.

## Runtime prompt warning

Files under [`prompts/`](prompts/) are loaded by `backend/app/semantics/prompts.py` during Layer B processing and mounted into the worker container by `docker-compose.yml`. Do not move, rename, or reformat their fenced `System` and `Input template` blocks as part of a documentation-only cleanup without updating the loader and its tests.

## Maintenance rules

- Keep the current phase in the root README, phase outcomes in GitHub Milestones, and actionable work in GitHub Issues.
- Update `architecture.md` when implemented endpoints, services, persistence, or pipeline behavior changes.
- Add or supersede ADRs when architectural decisions change; do not silently rewrite accepted history.
- Keep `AGENTS.md` and `CLAUDE.md` operational and synchronized. They are not status dashboards.
- Link to canonical material instead of copying it into design or history documents.

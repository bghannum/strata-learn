# Strata Learn frontend

**Status:** Scaffold only. Functional frontend work begins in Phase 4.

This package contains the React/Vite shell and placeholder routes for repository ingestion, progress, study guides, and quizzes. The component names establish the intended structure but do not imply that their flows are implemented.

The planned experience is documented in the [UI/UX specification](../docs/design/ui-spec.md). Current backend capabilities are documented in [Current architecture](../docs/architecture.md).

## Commands

```bash
npm install
npm run dev      # Vite development server
npm run lint     # Oxlint
npm run build    # TypeScript check + production build
npm run preview  # serve the production build locally
```

The development server expects the API at `VITE_API_URL`, which Docker Compose sets to `http://localhost:8000`.

## Structure

- `src/pages/` contains route-level placeholders.
- `src/components/` contains planned reusable UI components.
- `src/api/client.ts` is the frontend API boundary.
- `src/App.tsx` defines current routing.

When Phase 4 begins, update this file with real routing, authentication, state-management, and testing conventions as those decisions are implemented.

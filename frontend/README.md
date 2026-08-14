# Strata Learn frontend

**Status:** Functional through Phase 5; Phase 5.5 visual-design integration is next, and Phase 6 drawing questions are not implemented.

This React/Vite application supports the complete current browser flow: register or log in, add a repository from a Git URL or zip, watch indexing progress, inspect the generated study guide and citations, generate and take a quiz, and review the result. Requests use the backend's `HttpOnly` session cookie and are scoped to the logged-in user.

The target experience and known design-parity gaps are documented in the [UI/UX specification](../docs/design/ui-spec.md). The visual reference is the checked-in [`Strata-Learn UI mockups.zip`](../Strata-Learn%20UI%20mockups.zip); Phase 5.5 will translate its prototype and Organic design tokens into the production React application. The implemented frontend/backend behavior is documented in [Current architecture](../docs/architecture.md).

## Commands

```bash
npm install
npm run dev      # Vite development server
npm run lint     # Oxlint
npm run build    # TypeScript check + production build
npm run preview  # serve the production build locally
```

The development server expects the API at `VITE_API_URL`, which Docker Compose sets to `http://localhost:8000`.

There is not yet a frontend test runner. Adding Vitest and interaction coverage before the drawing-question UI expands the state surface is tracked in [GitHub issue #11](https://github.com/bghannum/strata-learn/issues/11).

## Structure

- `src/pages/` contains route-level authentication, repository, study-guide, quiz, and results screens.
- `src/components/` contains the shared app layout, indexing status, Mermaid, citation, and future drawing-canvas components.
- `src/auth/` owns session bootstrap and the current-user context.
- `src/api/client.ts` is the typed HTTP, upload, polling, and WebSocket boundary.
- `src/App.tsx` defines public authentication routes and authenticated application routes.

`DrawingCanvas.tsx` is intentionally still a stub for Phase 6. Open and deferred frontend work belongs in [GitHub Issues](https://github.com/bghannum/strata-learn/issues), not in this README.

# Strata Learn frontend

**Status:** Complete for the MVP (Phases 0–8 plus first-run auth). Drawing questions ([Phase 9](https://github.com/bghannum/strata-learn/milestone/8)) are deferred and `DrawingCanvas.tsx` is a stub.

This React/Vite application supports the full browser flow: first-run account setup or login, add a repository from a Git URL or zip, watch indexing progress live, read the generated study guide with inline Mermaid diagrams and clickable citations, see the architectural diff after a re-index, generate and take a quiz (immediate or end-of-quiz feedback, spoken answers, read-aloud), and review results and per-subsystem mastery. Requests carry the backend's `HttpOnly` session cookie and are scoped to the logged-in user.

The behavioral spec is the [UI/UX specification](../docs/design/ui-spec.md); the visual reference it was built to is the checked-in [`docs/design/strata-learn-ui-mockups.zip`](../docs/design/strata-learn-ui-mockups.zip). Implemented frontend/backend behavior is documented in [Current architecture](../docs/architecture.md).

## Commands

```bash
npm install
npm run dev        # Vite development server
npm run lint       # Oxlint
npm test           # Vitest (unit + component tests under src/**/__tests__)
npm run build      # TypeScript check + production build
npm run test:e2e   # Playwright smoke tests (e2e/); run `npx playwright install chromium` once
npm run preview    # serve the production build locally
```

The development server expects the API at `VITE_API_URL`, which Docker Compose sets to `http://localhost:8000`.

## Structure

- `src/pages/` — route-level screens: setup, login, dashboard, add repo, repo detail, study guide, quiz taker, results.
- `src/components/` — shared layout, indexing status, Mermaid, citation panel, architectural diff, rubric coverage, read-aloud and spoken-answer controls; `ui/` holds the design-system primitives.
- `src/auth/` — session bootstrap and the current-user context.
- `src/audio/` — the one module that touches `getUserMedia`/`MediaRecorder`, plus its React hook.
- `src/api/client.ts` — the typed HTTP, upload, polling, and WebSocket boundary.
- `src/styles/` — design tokens from the mockup.
- `src/App.tsx` — public auth routes and authenticated application routes.

Open and deferred frontend work belongs in [GitHub Issues](https://github.com/bghannum/strata-learn/issues), not in this README.

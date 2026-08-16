# Security

## Posture

Strata Learn is a **single-user, localhost tool**. The default `docker compose up` topology publishes the API on `localhost:8000`, the Vite dev server on `localhost:5173`, and PostgreSQL/Redis on their default ports, all bound to the local machine. It is not hardened for hosting:

- no CSRF protection ([#32](https://github.com/bghannum/strata-learn/issues/32));
- no login rate limiting ([#31](https://github.com/bghannum/strata-learn/issues/31));
- expired sessions are rejected but never deleted ([#29](https://github.com/bghannum/strata-learn/issues/29));
- the dev Compose file publishes database ports and bind-mounts source.

These are grouped in the [Productionization milestone](https://github.com/bghannum/strata-learn/milestone/9). Until it lands, do not expose the app beyond your own machine.

## If you expose it anyway

- Set `REGISTRATION_SECRET` in `.env` **before the first start**. Without it, the first visitor to a fresh install claims the single account.
- Put it behind a reverse proxy that terminates TLS. Browser microphone access (`getUserMedia`) requires a secure context, so the voice features won't work over plain HTTP from another host regardless.
- Keep `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` out of anything world-readable; the app never logs them, but `.env` on a public box is your responsibility.

## What the app does with your code

- Repositories are cloned (or unzipped) into a scoped temporary workspace, analysed, and deleted; the source is never re-read afterwards ([ADR-008](docs/adr/ADR-008-no-local-filesystem-ingestion.md)). Symlinks are excluded before parsing.
- Selected source spans — the cited snippets, plus structural facts — are sent to Anthropic during Layer B analysis, study-guide generation, quiz generation, and short-answer grading. Do not index code you are not permitted to send to a third-party API.
- Spoken answers are transcribed and the audio discarded; no raw audio is persisted. With the default local voice backends nothing leaves the machine; with `openai` backends the audio/text goes to OpenAI.

## Reporting a vulnerability

This is a solo, unfunded project. Please open a [GitHub issue](https://github.com/bghannum/strata-learn/issues/new) with the `bug` label. If the issue would let someone else's install be compromised in a way that shouldn't be public before a fix, say only that in the issue and the maintainer will follow up privately. There is no bounty programme.

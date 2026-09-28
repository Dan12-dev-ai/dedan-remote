# ADR 005: Multi-Stage Container Packaging with Integrated SPA Build

## Status
Accepted

## Context
The user interface is an interactive Single Page Application built with React 18 and Vite. The resulting static assets (`frontend/dist/`) are gitignored. In fresh container builds, failing to compile the frontend produces a running FastAPI server where root web requests return `404 Not Found`.

## Decision
We define a 3-stage `Dockerfile`:
1. `Stage 1 (Node 22)`: Runs `npm ci` and `npm run build` to generate compiled static assets.
2. `Stage 2 (Python 3.12 Builder)`: Installs wheel packages and system build dependencies (`gcc`).
3. `Stage 3 (Runtime)`: Minimal slim Debian image copying wheels from Stage 2 and static SPA assets from Stage 1 into `/app/frontend/dist`. FastAPI serves the API and falls back to `index.html` for client-side routing.

## Consequences
- **Positive**: Single container deployment image hosts both the full REST API and the React web application without requiring an external Nginx proxy for simple deployments.
- **Positive**: Clean, reproducible Docker images free of development tooling and Node.js runtime overhead.
- **Negative**: Container build times take slightly longer (~45-60s) due to the Node compile stage.

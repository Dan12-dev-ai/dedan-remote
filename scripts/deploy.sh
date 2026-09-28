#!/usr/bin/env bash
# ── DEDAN Remote — Cloud Deployment Script ───────────────────────────────
# Deploy the full stack: FastAPI + SPA, discovery scheduler, and optional AE-OS
# datastores (Postgres, Redis, Neo4j, Qdrant, Prometheus).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

# ── Config ──────────────────────────────────────────────────────────────────
REPO_URL="${REPO_URL:-https://github.com/Dan12-dev-ai/dedan-remote.git}"
COMPOSE_FILE="docker-compose.yml"
PROJECT_NAME="dedan-remote"

# ── Preflight ───────────────────────────────────────────────────────────────
echo "=================================================="
echo "DEDAN Remote — Cloud Deployment"
echo "=================================================="

for bin in git docker; do
    command -v "${bin}" >/dev/null 2>&1 || { echo "❌ ${bin} not found"; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "❌ docker compose v2 required"; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ Docker daemon not running (or no permission)"; exit 1; }
echo "✅ Docker: $(docker --version)"

# ── 1. Fetch source ─────────────────────────────────────────────────────────
if [[ ! -f "${ROOT_DIR}/Dockerfile" ]]; then
    echo "--- [1/6] Cloning repository ---"
    git clone "${REPO_URL}" "${ROOT_DIR}"
    cd "${ROOT_DIR}"
else
    echo "--- [1/6] Using existing checkout ---"
fi

# ── 2. Environment file ─────────────────────────────────────────────────────
echo "--- [2/6] Provisioning environment ---"
if [[ ! -f .env ]]; then
    cp .env.example .env
    # Generate a strong system token rather than shipping the empty default.
    SYSTEM_TOKEN="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    {
        echo ""
        echo "# ── Production overrides (generated $(date -u +%FT%TZ)) ──"
        echo "DEDAN_ENV=production"
        echo "DEDAN_ENABLE_HSTS=true"
        echo "DEDAN_TRUST_PROXY=true"
        echo "DEDAN_SYSTEM_TOKEN=${SYSTEM_TOKEN}"
        echo "DEDAN_CORS_ORIGINS=https://your-domain.com"
        echo "AEOS_IDEMPOTENCY_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    } >> .env
    chmod 600 .env
    echo "✅ Created .env with generated secrets (mode 600)."
    echo "   ⚠️  EDIT .env NOW: set EMAIL/Telegram/Discord + change all"
    echo "      AEOS_*_PASSWORD and AEOS_POSTGRES_PASSWORD values."
else
    echo "✅ .env already present — left untouched."
fi

# ── 3. Build ────────────────────────────────────────────────────────────────
echo "--- [3/6] Building image (frontend + python) ---"
# The frontend is built INSIDE the image: frontend/dist is gitignored, so it
# never exists in a fresh clone. Skipping this yields an API that 404s.
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" build

# ── 4. Start ────────────────────────────────────────────────────────────────
echo "--- [4/6] Starting services ---"
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" up -d

# Wait for the API readiness probe (checks the discovery DB, not just liveness).
echo "--- Waiting for API readiness ---"
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/api/ready >/dev/null 2>&1; then
        echo "✅ API ready after ${i}s"
        break
    fi
    [[ "${i}" -eq 60 ]] && { echo "❌ API not ready after 60s"; \
        docker compose -p "${PROJECT_NAME}" logs --tail=50 dedan-remote; exit 1; }
    sleep 1
done

# ── 5. Verify ───────────────────────────────────────────────────────────────
echo "--- [5/6] Verification ---"
echo "  Liveness : $(curl -fsS http://127.0.0.1:8000/api/health)"
echo "  Readiness: $(curl -fsS http://127.0.0.1:8000/api/ready)"
# Proves the SPA is actually inside the image (the fixed #1 failure mode).
SPA_STATUS="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)"
if [[ "${SPA_STATUS}" == "200" ]]; then
    echo "  SPA      : OK (HTTP 200)"
else
    echo "  SPA      : ⚠️  HTTP ${SPA_STATUS} — frontend bundle may be missing"
fi
docker compose -p "${PROJECT_NAME}" ps

# ── 6. Summary ──────────────────────────────────────────────────────────────
echo "--- [6/6] Done ---"
cat <<'EOF'

Next steps:
  1. Terminate TLS. Point a domain at this host and run Caddy:

       sudo apt install caddy
       # /etc/caddy/Caddyfile
       #   your-domain.com {
       #     reverse_proxy 127.0.0.1:8000
       #   }
       sudo systemctl reload caddy

     Caddy provisions and renews Let's Encrypt certificates automatically.
     With DEDAN_ENABLE_HSTS=true the API then emits HSTS headers.

  2. Open ONLY ports 22 (SSH) and 80/443 (HTTP/HTTPS) in the cloud firewall.
     Postgres/Redis/Neo4j/Qdrant/Prometheus are intentionally NOT published —
     they are reachable only on the internal compose network.

  3. Keep the system dashboard protected. /api/system/overview requires
     the token (the whole router is behind require_system_token):
       curl -H "X-System-Token: $(grep DEDAN_SYSTEM_TOKEN .env | cut -d= -f2)" \
            http://127.0.0.1:8000/api/system/overview

  4. Back up the discovery database and the AE-OS volumes:
       ./scripts/backup.sh

  5. Logs:  docker compose logs -f dedan-remote
     Stop:   docker compose down
     Update: git pull && ./scripts/deploy.sh
EOF


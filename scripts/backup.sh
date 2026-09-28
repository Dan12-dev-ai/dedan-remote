#!/usr/bin/env bash
# ── DEDAN Remote / AIJobFinder — Nightly Backup ───────────────────────────
# Backs up the app's discovery database and the AE-OS datastore volumes.
# Designed to run from cron; safe to run repeatedly (older archives are pruned).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

PROJECT_NAME="${PROJECT_NAME:-aijobfinder}"
COMPOSE_FILE="docker-compose.yml"
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "${BACKUP_DIR}"

dc() { docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" "$@"; }

# Only back up volumes for services that are actually running, so a partially
# deployed stack does not fail the whole job.
volume_exists() {
    docker volume inspect "${PROJECT_NAME}_$1" >/dev/null 2>&1
}

echo "[${STAMP}] Backup starting → ${BACKUP_DIR}"

# ── 1. App data (discovery SQLite DB) ──────────────────────────────────────
if dc ps --status running --services 2>/dev/null | grep -qx ai-opportunity-finder; then
    if dc exec -T ai-opportunity-finder tar czf - -C /app data \
        > "${BACKUP_DIR}/app-${STAMP}.tar.gz" 2>/dev/null; then
        echo "  ✅ app data      → app-${STAMP}.tar.gz"
    else
        echo "  ❌ app data backup failed" >&2
        exit 1
    fi
else
    echo "  ⚠️  app container not running — skipped app data"
fi

# ── 2. Datastore volumes ───────────────────────────────────────────────────
for vol in postgres-data neo4j-data qdrant-data redis-data prometheus-data; do
    if volume_exists "${vol}"; then
        if docker run --rm \
            -v "${PROJECT_NAME}_${vol}:/src:ro" \
            -v "${BACKUP_DIR}:/dst" \
            alpine tar czf "/dst/${vol}-${STAMP}.tar.gz" -C /src . 2>/dev/null; then
            echo "  ✅ ${vol} → ${vol}-${STAMP}.tar.gz"
        else
            echo "  ⚠️  ${vol} backup failed — continuing"
        fi
    else
        echo "  ⚠️  volume ${PROJECT_NAME}_${vol} not found — skipped"
    fi
done

# ── 3. Configuration (secrets excluded) ────────────────────────────────────
# .env holds credentials and is deliberately NOT copied here. Back it up
# separately through your secret manager, never alongside the data archives.
if [[ -f docker-compose.yml ]]; then
    cp docker-compose.yml "${BACKUP_DIR}/docker-compose-${STAMP}.yml"
    echo "  ✅ compose file   → docker-compose-${STAMP}.yml"
fi

# ── 4. Prune old archives ──────────────────────────────────────────────────
PRUNED="$(find "${BACKUP_DIR}" -name '*.tar.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)"
echo "  🧹 pruned ${PRUNED} archive(s) older than ${RETENTION_DAYS}d"

# ── 5. Verify the newest app archive is readable ───────────────────────────
LATEST="$(ls -1t "${BACKUP_DIR}"/app-*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -n "${LATEST}" ]]; then
    if tar tzf "${LATEST}" >/dev/null 2>&1; then
        SIZE="$(du -h "${LATEST}" | cut -f1)"
        echo "  ✔️  verified ${LATEST} (${SIZE})"
    else
        echo "  ❌ ${LATEST} is corrupt!" >&2
        exit 1
    fi
fi

echo "[$(date -u +%FT%TZ)] Backup complete"

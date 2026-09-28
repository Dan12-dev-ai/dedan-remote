#!/usr/bin/env bash
# ==============================================================================
# DEDAN Remote — Deployment Status Probing Script
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== DEDAN Remote Deployment Status ==="

# Check API
if [[ -f "${ROOT_DIR}/.api.pid" ]]; then
    API_PID=$(cat "${ROOT_DIR}/.api.pid" || true)
    if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
        echo "API Server:        RUNNING (PID ${API_PID})"
    else
        echo "API Server:        STOPPED (stale pid file)"
    fi
else
    echo "API Server:        NOT RUNNING"
fi

# Check Scheduler
if [[ -f "${ROOT_DIR}/.scheduler.pid" ]]; then
    SCHED_PID=$(cat "${ROOT_DIR}/.scheduler.pid" || true)
    if [[ -n "${SCHED_PID}" ]] && kill -0 "${SCHED_PID}" 2>/dev/null; then
        echo "Scheduler Engine:  RUNNING (PID ${SCHED_PID})"
    else
        echo "Scheduler Engine:  STOPPED (stale pid file)"
    fi
else
    echo "Scheduler Engine:  NOT RUNNING"
fi

echo "--- Probing HTTP Endpoints ---"
API_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/health || echo "N/A")
WEB_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo "N/A")
echo "API Health (/api/health): ${API_HTTP}"
echo "Web UI Root (/):          ${WEB_HTTP}"

if [[ "${API_HTTP}" == "200" ]]; then
    curl -s http://127.0.0.1:8000/api/status | jq . 2>/dev/null || curl -s http://127.0.0.1:8000/api/status
    echo ""
fi

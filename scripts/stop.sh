#!/usr/bin/env bash
# ==============================================================================
# DEDAN Remote / AIJobFinder — Stop All Deployment Services
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Stopping DEDAN Remote deployment services..."

if [[ -f "${ROOT_DIR}/.api.pid" ]]; then
    API_PID=$(cat "${ROOT_DIR}/.api.pid" || true)
    if [[ -n "${API_PID}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
        echo "Stopping API Server (PID ${API_PID})..."
        kill "${API_PID}" 2>/dev/null || true
    fi
    rm -f "${ROOT_DIR}/.api.pid"
fi

if [[ -f "${ROOT_DIR}/.scheduler.pid" ]]; then
    SCHED_PID=$(cat "${ROOT_DIR}/.scheduler.pid" || true)
    if [[ -n "${SCHED_PID}" ]] && kill -0 "${SCHED_PID}" 2>/dev/null; then
        echo "Stopping Scheduler (PID ${SCHED_PID})..."
        kill "${SCHED_PID}" 2>/dev/null || true
    fi
    rm -f "${ROOT_DIR}/.scheduler.pid"
fi

echo "✅ All DEDAN Remote services stopped."

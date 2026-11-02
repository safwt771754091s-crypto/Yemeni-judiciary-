#!/usr/bin/env bash
# Bring the whole stack up in one shot: Docker engine, pgvector, corpus, API.
#
# Written because the sandbox environment is recycled periodically, which wipes
# the Docker daemon, the database volume and the running server together. Run
# this after every recycle instead of the four manual steps.
#
#   ./scripts/serve_public.sh
#
# PORT defaults to 12000, the port the public sandbox URL forwards to.
set -uo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-12000}"
DOCKER="sudo -n docker"

log() { printf '\033[36m==>\033[0m %s\n' "$1"; }
die() { printf '\033[31mخطأ:\033[0m %s\n' "$1" >&2; exit 1; }

# 1. Docker engine -----------------------------------------------------------
if ! sudo -n docker info >/dev/null 2>&1; then
    log "تشغيل محرك Docker"
    sudo -n dockerd >/tmp/dockerd.log 2>&1 &
    for _ in $(seq 1 30); do
        sudo -n docker info >/dev/null 2>&1 && break
        sleep 1
    done
    sudo -n docker info >/dev/null 2>&1 || die "لم يبدأ Docker — راجع /tmp/dockerd.log"
fi
log "Docker يعمل"

# 2. pgvector ----------------------------------------------------------------
if ! $DOCKER ps --format '{{.Names}}' | grep -q '^project-postgres-1$'; then
    log "تشغيل قاعدة البيانات"
    $DOCKER compose up -d postgres >/dev/null 2>&1
fi
for _ in $(seq 1 30); do
    $DOCKER exec project-postgres-1 pg_isready -U legal -d legal_ai >/dev/null 2>&1 && break
    sleep 1
done
$DOCKER exec project-postgres-1 pg_isready -U legal -d legal_ai >/dev/null 2>&1 \
    || die "قاعدة البيانات لا تستجيب"
log "قاعدة البيانات جاهزة"

# 3. Corpus ------------------------------------------------------------------
# The volume is recreated empty on every recycle, so seed whenever it is empty.
COUNT=$($DOCKER exec project-postgres-1 psql -U legal -d legal_ai -tAc \
    "select count(*) from legal_articles;" 2>/dev/null | tr -d '[:space:]')
if [ "${COUNT:-0}" = "0" ]; then
    log "فهرسة المدوّنة القانونية (قد يستغرق دقيقة)"
    .venv/bin/python scripts/seed_corpus.py >/tmp/seed.log 2>&1 \
        || die "فشلت الفهرسة — راجع /tmp/seed.log"
    COUNT=$($DOCKER exec project-postgres-1 psql -U legal -d legal_ai -tAc \
        "select count(*) from legal_articles;" 2>/dev/null | tr -d '[:space:]')
fi
log "المدوّنة القانونية: ${COUNT:-0} مادة"

# 4. API ---------------------------------------------------------------------
pkill -f "uvicorn app.main:app" >/dev/null 2>&1
sleep 1
log "تشغيل الخادم على المنفذ $PORT"
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    >/tmp/uvicorn.log 2>&1 &

for _ in $(seq 1 30); do
    curl -sf -m 3 "http://localhost:$PORT/api/v1/health/ready" >/dev/null 2>&1 && break
    sleep 1
done
if curl -sf -m 5 "http://localhost:$PORT/api/v1/health/ready" >/dev/null 2>&1; then
    log "المنصة جاهزة ✔"
else
    die "الخادم لم يستجب — راجع /tmp/uvicorn.log"
fi

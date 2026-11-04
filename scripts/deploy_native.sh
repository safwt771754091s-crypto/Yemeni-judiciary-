#!/usr/bin/env bash
# Deploy the platform on a host with a native PostgreSQL (no Docker).
#
# Use this when the Docker daemon is unavailable (CI runners, restricted
# sandboxes). It installs PostgreSQL + pgvector if missing, starts the cluster,
# creates the role/database/extension, applies the SQL schema, seeds the bundled
# Yemeni legal corpus, and starts the API.
#
#   ./scripts/deploy_native.sh
#
# Idempotent: safe to re-run after a recycle. PORT defaults to 12000.
set -uo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-12000}"
PG_VERSION="${PG_VERSION:-17}"
DB_NAME="${DB_NAME:-legal_ai}"
DB_USER="${DB_USER:-legal}"
DB_PASSWORD="${DB_PASSWORD:-legal}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
PY="${PY:-.venv/bin/python}"

log() { printf '\033[36m==>\033[0m %s\n' "$1"; }
die() { printf '\033[31mخطأ:\033[0m %s\n' "$1" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ -x "$PY" ] || die "لا يوجد بيئة افتراضية في $PY — أنشئها أولاً: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"

# 1. PostgreSQL + pgvector ---------------------------------------------------
if ! have psql; then
    log "تثبيت PostgreSQL و pgvector"
    sudo -n apt-get update -qq
    sudo -n apt-get install -y postgresql "postgresql-${PG_VERSION}-pgvector" \
        >/tmp/pg_install.log 2>&1 || die "فشل التثبيت — راجع /tmp/pg_install.log"
fi
log "PostgreSQL مثبَّت"

# 2. Cluster -----------------------------------------------------------------
if ! sudo -n pg_ctlcluster "$PG_VERSION" main status >/dev/null 2>&1; then
    log "تشغيل عنقود PostgreSQL"
    sudo -n pg_ctlcluster "$PG_VERSION" main start >/dev/null 2>&1
    sleep 2
fi
sudo -n pg_ctlcluster "$PG_VERSION" main status >/dev/null 2>&1 \
    || die "عنقود PostgreSQL لا يعمل"
log "قاعدة البيانات تعمل"

# 3. Role / database / extension --------------------------------------------
log "تهيئة الدور وقاعدة البيانات"
sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" \
    | grep -q 1 \
    || sudo -n -u postgres psql -c \
        "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';" >/dev/null
sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" \
    | grep -q 1 \
    || sudo -n -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -n -u postgres psql -d "${DB_NAME}" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
sudo -n -u postgres psql -d "${DB_NAME}" \
    -c "GRANT ALL ON SCHEMA public TO ${DB_USER}; ALTER DATABASE ${DB_NAME} OWNER TO ${DB_USER};" \
    >/dev/null
log "الدور وقاعدة البيانات وامتداد pgvector جاهزة"

# 4. .env --------------------------------------------------------------------
if [ ! -f .env ]; then
    log "إنشاء .env"
    cat > .env <<EOF
APP_NAME="Legal AI Platform - Yemen"
APP_ENV=production
LOG_LEVEL=INFO
API_PREFIX=/api/v1

SUPABASE_DB_URL=postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}

# Swap to openai + a key for semantic embeddings / real legal reasoning.
EMBEDDING_PROVIDER=hash
EMBEDDING_MODEL=hash-1536
EMBEDDING_DIM=1536
LLM_PROVIDER=mock
LLM_MODEL=mock-legal-reasoner
LLM_API_KEY=
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=4096
LLM_TIMEOUT_SECONDS=120

RETRIEVAL_TOP_K=8
JURISDICTION="الجمهورية اليمنية"
DEFAULT_COURT_NAME="المحكمة الابتدائية"
DEFAULT_CASE_TYPE=مدني

# Change JWT_SECRET and OWNER_PASSWORD before exposing this publicly.
JWT_SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '\n=+/')
ACCESS_TOKEN_EXPIRE_MINUTES=720
OWNER_USERNAME=owner
OWNER_PASSWORD=Owner@Yemen2026
OWNER_FULL_NAME="مالك المنصة"

UPLOAD_DIR=./var/uploads
MAX_UPLOAD_MB=25
OCR_ENABLED=false
CLASSIFICATION_MIN_CONFIDENCE=0.35
INTAKE_AUTO_JUDGMENT=true
N8N_ENABLED=false
EOF
fi
set -a; . ./.env; set +a

# 5. Schema ------------------------------------------------------------------
log "تطبيق مخطط SQL"
for f in 001_init_pgvector 002_functions 003_auth_rbac 004_assistant 005_portal_workspace; do
    "$PY" scripts/apply_sql.py --file "sql/${f}.sql" >/dev/null 2>&1 \
        || die "فشل تطبيق ${f}.sql"
done
log "المخطط جاهز"

# 6. Corpus ------------------------------------------------------------------
COUNT=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -tAc "select count(*) from legal_articles;" 2>/dev/null | tr -d '[:space:]')
if [ "${COUNT:-0}" = "0" ]; then
    log "فهرسة المدوّنة القانونية (قد يستغرق دقيقة)"
    "$PY" scripts/seed_corpus.py --file app/data/corpus/yemeni_laws.json >/tmp/seed.log 2>&1 \
        || die "فشلت الفهرسة — راجع /tmp/seed.log"
    COUNT=$(PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "select count(*) from legal_articles;" 2>/dev/null | tr -d '[:space:]')
fi
log "المدوّنة القانونية: ${COUNT:-0} مادة"

# 7. API ---------------------------------------------------------------------
pkill -f "uvicorn app.main:app" >/dev/null 2>&1
sleep 1
log "تشغيل الخادم على المنفذ $PORT"
nohup "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    >/tmp/uvicorn.log 2>&1 &

for _ in $(seq 1 30); do
    curl -sf -m 3 "http://localhost:$PORT/api/v1/health/ready" >/dev/null 2>&1 && break
    sleep 1
done
if curl -sf -m 5 "http://localhost:$PORT/api/v1/health/ready" >/dev/null 2>&1; then
    log "المنصة جاهزة ✔ — http://localhost:$PORT  (لوحة الإدارة: /ui/admin.html)"
else
    die "الخادم لم يستجب — راجع /tmp/uvicorn.log"
fi

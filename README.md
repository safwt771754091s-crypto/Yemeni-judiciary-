# منصة الذكاء الاصطناعي القانوني — الجمهورية اليمنية

محرك القرار والاستدلال القانوني (Legal Decision & Reasoning Engine) للقوانين
والمحاكم اليمنية. يستقبل الدعوى والأدلة، يسترجع المواد القانونية اليمنية ذات الصلة
(RAG) من قاعدة متجهات Supabase/pgvector، يقيّم الأدلة وفق قانون الإثبات اليمني،
ثم يولّد **مسودة حكم قضائي** مُهيكلة (الديباجة، الوقائع، الأسباب والحيثيات، المنطوق).

> ⚠️ **تنبيه قانوني:** المخرجات **مسودات آلية** لا تُعدّ أحكاماً قضائية ولا تُعتد بها
> إلا بعد مراجعتها واعتمادها من القاضي المختص. النظام مصمَّم لمنع الاستشهاد بأي نص
> غير موجود في المدونة القانونية، ويُصرّح بمواطن القصور بدل التخمين.

---

## 1. المعمارية

```
                     POST /api/v1/cases/submit
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
  CaseRepository                                  RetrievalService  ◄── RAG
  (cases + case_evidence)                     (query planning → hybrid search)
        │                                               │
        │                                    Supabase / pgvector
        │                                    match_legal_articles()
        │                                    (dense + Arabic FTS + trigram, RRF)
        ▼                                               ▼
  EvidenceService  ◄──────────── retrieved statutes ───────────────┐
  (قانون الإثبات اليمني: rule layer + LLM)                          │
        │                                                          │
        ▼                                                          │
  JudgmentService ────────────────────────────────────────────────►┘
  (prompt grounded ONLY in retrieved statutes)
        │
        ├── CitationValidator  (يحذف كل استشهاد لا سند له + تحقق الاقتباس)
        ├── render_markdown    (مسودة حكم رسمية)
        └── PostgreSQL: judgments + audit_log   ──► n8n webhook (HMAC)
```

### الطبقات

| الطبقة | المجلد | المسؤولية |
|---|---|---|
| API | `app/api/v1/routes` | نقاط النهاية (FastAPI، async) |
| Schemas | `app/schemas` | عقود Pydantic (التحقق + التوثيق) |
| Services | `app/services` | الإدخال، الاسترجاع، تقييم الأدلة، توليد الحكم، n8n |
| LLM | `app/llm` | المتجهات، المزوّدون، تعليمات النظام، مدقّق الاستشهاد، السلاسل |
| DB | `app/db` | مجمّع asyncpg، مخزن المتجهات، المستودعات |
| Core | `app/core` | الإعدادات، التسجيل، معالجة النصوص العربية، التقطيع |
| SQL | `sql/` | مخطط pgvector والدوال (بحث هجين، upsert) |

### قرارات هندسية مهمة

- **التقطيع واعٍ بالمواد:** المادة (المادة القانونية) هي وحدة الاستشهاد، فلا تُقطَّع
  اعتباطاً؛ يُقسَّم النص على حدود المواد أولاً.
- **بحث هجين (RRF):** تشابه المتجهات + بحث نصي عربي (FTS) + trigram. العربية ذات
  تصريف غني، لذا الاعتماد على المتجهات وحدها غير كافٍ.
- **طبقة قواعد حاكمة + LLM:** تقييم الأدلة يبدأ بقواعد ثابتة (مراتب الإثبات)، ويحسّنها
  النموذج، لكن **لا يمكن للنموذج أن يرفع درجة قبول دليل** أُسقطته القواعد.
- **حقول الهوية من ملف القضية لا من النموذج:** اسم المحكمة والأطراف ورقم القضية
  تُفرض من البيانات المدخلة، فلا يمكن أن يُنسب الحكم لمحكمة أو طرف خاطئ.
- **تدهور رشيق:** يعمل النظام دون قاعدة بيانات (مخزن في الذاكرة) ودون نموذج سحابي
  (مزوّد حتمي للاختبارات)، فتظل المسار كاملاً قابلاً للاختبار.

---

## 2. المتطلبات

- Python 3.11+ (تم التطوير على 3.12/3.13)
- مشروع Supabase (PostgreSQL + pgvector) — أو Postgres محلي عبر docker-compose
- مفتاح مزوّد ذكاء اصطناعي (OpenAI أو Anthropic) للإنتاج — اختياري للتطوير

---

## 3. التركيب خطوة بخطوة

### 3.1 تهيئة المشروع

```bash
git clone <repo-url> legal-ai-yemen && cd legal-ai-yemen
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt     # أو: pip install -r requirements-dev.txt (يضيف pytest/ruff)
```

### 3.2 الإعدادات

```bash
cp .env.example .env
```

حرّر `.env`:

```dotenv
# قاعدة البيانات (Supabase → Project Settings → Database → Connection string)
SUPABASE_DB_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

# المتجهات: hash للتطوير دون شبكة، openai للإنتاج
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIM=1536

# النموذج
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...

# المصادقة — إلزامي في الإنتاج
JWT_SECRET=<قيمة عشوائية قوية: python -c "import secrets;print(secrets.token_urlsafe(48))">
OWNER_USERNAME=owner
OWNER_PASSWORD=<كلمة مرور قوية، غيّرها بعد أول دخول>
```

> `EMBEDDING_PROVIDER=hash` و `LLM_PROVIDER=mock` يعملان بلا أي شبكة أو مفاتيح —
> مناسبان للتطوير والاختبار. للإنتاج استخدم مزوّداً حقيقياً.

### 3.3 تهيئة قاعدة البيانات (pgvector)

في **Supabase → SQL Editor** نفّذ الملفين بالترتيب:

```sql
-- 1) المخطط والجداول والفهارس
--    انسخ محتوى sql/001_init_pgvector.sql والصقه
-- 2) الدوال (بحث هجين، تطبيع عربي، upsert)
--    انسخ محتوى sql/002_functions.sql والصقه
```

أو من الطرفية:

```bash
python scripts/apply_sql.py          # ينفّذ 001 ثم 002 عبر SUPABASE_DB_URL
```

تأكّد من وجود امتداد `vector`: في Supabase → Database → Extensions → فعّل `vector`.

### 3.4 إدخال المدونة القانونية اليمنية

```bash
python scripts/seed_corpus.py        # المدونة التجريبية المرفقة (4 قوانين)
python scripts/seed_corpus.py --dry-run   # تقطيع فقط دون كتابة
```

أو عبر الـ API:

```bash
curl -X POST http://localhost:8000/api/v1/ingestion/text \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "document": {
    "source_type": "civil_law",
    "title_ar": "القانون المدني اليمني",
    "law_number": "14",
    "promulgation_year": 2002
  },
  "text": "المادة (1) تسري النصوص التشريعية على جميع المسائل...\n\nالمادة (2) ..."
}
JSON
```

### 3.5 التشغيل

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# أو: make dev
```

- التوثيق التفاعلي: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- الفحص: http://localhost:8000/api/v1/health/ready
- واجهة المنصة: http://localhost:8000/
- **لوحة التحكم**: http://localhost:8000/ui/admin.html

### 3.6 حساب المالك وأول دخول

عند أول تشغيل يُنشئ النظام تلقائياً حساب مالك المنصة (كل الصلاحيات) اعتماداً على
`OWNER_USERNAME` و`OWNER_PASSWORD` في `.env`، كما يُنشئ الأقسام القانونية الافتراضية.

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"owner","password":"Owner@Yemen2026"}'
```

انسخ قيمة `access_token` واستخدمها في ترويسة `Authorization: Bearer <token>`.
ثم افتح **`/ui/admin.html`** لإدارة الأقسام والقوانين والمواد والمستخدمين.

> ⚠️ **إلزامي في الإنتاج:** اضبط `JWT_SECRET` بقيمة عشوائية قوية، وغيّر كلمة مرور
> المالك فور أول دخول، ولا تُبقِ القيم الافتراضية. عند غياب `JWT_SECRET` يستعمل
> النظام مفتاحاً افتراضياً غير آمن ويُصدر تحذيراً في السجل.

الأدوار المدمجة:

| الدور | الوصف | أمثلة الصلاحيات |
|---|---|---|
| `owner` | مالك المنصة | كل الصلاحيات دائماً، لا يُقيَّد ولا يُحذف |
| `admin` | مدير | إدارة المستخدمين والقوانين والمواد والأقسام |
| `editor` | محرر | إدخال وتعديل المواد والقوانين |
| `reviewer` | مراجع | مراجعة المسودات واعتمادها |
| `viewer` | مطّلع | قراءة فقط |

### 3.7 Docker (اختياري)

```bash
docker compose up -d --build       # API + Postgres/pgvector محلي
docker compose logs -f api
```

---

## 4. مثال طلب كامل (cURL)

### تقديم دعوى واستخراج مسودة حكم

```bash
curl -X POST http://localhost:8000/api/v1/cases/submit \
  -H "Content-Type: application/json" \
  -H "X-N8N-Secret: ${N8N_HMAC_SECRET}" \
  -d '{
    "case_number": "1445/2026",
    "case_type": "مدني",
    "court_name": "المحكمة الابتدائية - صنعاء",
    "judge_name": "القاضي/ محمد الحميري",
    "subject_matter": "المطالبة بسداد ثمن مبيع والتعويض عن التأخر",
    "claim_value": 3500000,
    "currency": "YER",
    "facts_summary": "اتفق المدعي والمدعى عليه على بيع سيارة بثمن محدد، وسلّم المدعي السيارة وتأخر المدعى عليه عن السداد دون سبب مشروع.",
    "plaintiff": { "name": "أحمد عبدالله ناصر", "role": "plaintiff", "address": "صنعاء" },
    "defendant": { "name": "صالح محمد المقطري", "role": "defendant", "address": "صنعاء" },
    "plaintiff_claims": [
      {
        "title": "إلزام المدعى عليه بأداء الثمن",
        "description": "إلزام المدعى عليه بسداد الثمن المتفق عليه",
        "amount": 3500000,
        "currency": "YER",
        "legal_basis": ["المادة 146", "المادة 147"]
      },
      {
        "title": "التعويض عن التأخر",
        "description": "التعويض عن الضرر اللاحق جراء التأخر في السداد",
        "amount": 200000,
        "currency": "YER",
        "legal_basis": ["المادة 191"]
      }
    ],
    "defendant_responses": [
      {
        "claim_ref": "إلزام المدعى عليه بأداء الثمن",
        "response_type": "denial",
        "description": "ينكر المدعى عليه وجود عيوب خفية في المبيع",
        "legal_basis": ["المادة 15"]
      }
    ],
    "evidence": [
      {
        "id": "ev-1",
        "submitted_by": "plaintiff",
        "evidence_type": "contract",
        "title": "عقد البيع",
        "description": "عقد موقّع من الطرفين يحدد الثمن وطريقة السداد",
        "document_ref": "storage://evidence/contract_1445.pdf",
        "extracted_text": "عقد بيع سيارة بثمن محدد، يلتزم المشتري بالسداد خلال ثلاثين يوماً."
      },
      {
        "id": "ev-2",
        "submitted_by": "defendant",
        "evidence_type": "digital",
        "title": "محادثات واتساب",
        "description": "محادثات بين الطرفين",
        "document_ref": "storage://evidence/chat_1445.txt"
      }
    ],
    "generate_judgment": true,
    "language": "ar",
    "max_retrieved_articles": 10
  }' | python -m json.tool
```

الاستجابة تحتوي على:

| الحقل | الوصف |
|---|---|
| `case` | سجل القضية المخزّن |
| `judgment_id` | معرّف المسودة المحفوظة |
| `judgment` | المسودة المُهيكلة (JSON): `preamble` / `facts` / `reasoning` / `verdict` |
| `judgment_markdown` | مسودة الحكم كاملة بصيغة Markdown |
| `retrieved_articles` | المواد اليمنية المسترجعة ودرجات ملاءمتها |
| `evidence_analysis` | تقييم كل دليل (الحجية، القبول، الوزن، التعليل) |
| `grounding_score` | نسبة الاستشهادات المسندة فعلاً إلى النصوص المسترجعة |
| `warnings` | تحذيرات (نقص المدونة، استشهادات محذوفة…) |

### استرجاع المسودة لاحقاً

```bash
CASE_ID=<case_id>
JUDGMENT_ID=<judgment_id>

curl http://localhost:8000/api/v1/cases/$CASE_ID/judgments/$JUDGMENT_ID | python -m json.tool
curl http://localhost:8000/api/v1/cases/$CASE_ID/judgments            | python -m json.tool
curl -X POST "http://localhost:8000/api/v1/cases/$CASE_ID/judgments/$JUDGMENT_ID/approve?decision=approved"
```

### البحث المباشر في النصوص القانونية

```bash
curl -X POST http://localhost:8000/api/v1/retrieval/search \
  -H "Content-Type: application/json" \
  -d '{"query": "إلزام المدين بأداء الثمن والتعويض عن الإخلال بالعقد", "top_k": 5}'
```

### إحصاءات المدونة

```bash
curl http://localhost:8000/api/v1/ingestion/stats | python -m json.tool
```

---

## 5. نقاط النهاية

| الطريقة | المسار | الوصف |
|---|---|---|
| GET | `/api/v1/health` | فحص الصحة |
| GET | `/api/v1/health/ready` | الجاهزية والاعتماديات |
| POST | `/api/v1/ingestion/text` | إدخال قانون (تقطيع + متجهات + تخزين) |
| POST | `/api/v1/ingestion/batch` | إدخال عدة قوانين |
| POST | `/api/v1/ingestion/upload` | رفع ملف نصي لقانون |
| GET | `/api/v1/ingestion/stats` | إحصاءات المدونة |
| POST | `/api/v1/retrieval/search` | بحث هجين في المواد |
| GET | `/api/v1/retrieval/search?q=...` | بحث سريع |
| **POST** | **`/api/v1/cases/submit`** | **المسار الكامل: دعوى + أدلة → مسودة حكم** |
| GET | `/api/v1/cases` | قائمة القضايا |
| GET | `/api/v1/cases/{id}` | تفاصيل قضية |
| POST | `/api/v1/cases/{id}/evidence` | إضافة أدلة |
| GET | `/api/v1/cases/{id}/judgments` | سجل المسودات |
| GET | `/api/v1/cases/{id}/judgments/{jid}` | مسودة كاملة |
| POST | `/api/v1/cases/{id}/judgments/{jid}/approve` | اعتماد/رفض (مراجعة بشرية) |

### نقاط نهاية المصادقة والصلاحيات (Auth / RBAC)

| الطريقة | المسار | الصلاحية | الوصف |
|---|---|---|---|
| POST | `/api/v1/auth/login` | عام | تسجيل الدخول واستلام رمز الوصول |
| GET | `/api/v1/auth/me` | مصادق | بيانات المستخدم وصلاحياته الفعّالة |
| GET | `/api/v1/auth/permissions` | عام | كتالوج الصلاحيات وأدوار النظام |
| GET | `/api/v1/auth/roles` | عام | الأدوار وتسمياتها العربية |
| GET | `/api/v1/auth/users` | `users.read` | قائمة المستخدمين |
| POST | `/api/v1/auth/users` | `users.write` | إنشاء مستخدم |
| PATCH | `/api/v1/auth/users/{id}` | `users.write` | تعديل الدور/الحالة/الصلاحيات |
| DELETE | `/api/v1/auth/users/{id}` | `users.delete` | حذف مستخدم (المالك فقط) |

### نقاط نهاية إدارة المنصة (Admin)

| الطريقة | المسار | الصلاحية | الوصف |
|---|---|---|---|
| GET | `/api/v1/admin/sections` | `sections.read` | أقسام المنصة |
| POST | `/api/v1/admin/sections` | `sections.write` | إضافة قسم جديد |
| PATCH | `/api/v1/admin/sections/{id}` | `sections.write` | تعديل قسم |
| DELETE | `/api/v1/admin/sections/{id}` | `sections.delete` | حذف قسم |
| GET | `/api/v1/admin/laws` | `laws.read` | القوانين المخزّنة |
| POST | `/api/v1/admin/laws/ingest` | `corpus.ingest` | إدخال نص قانون كامل |
| PATCH | `/api/v1/admin/laws/{id}` | `laws.write` | تعديل بيانات قانون |
| DELETE | `/api/v1/admin/laws/{id}` | `laws.delete` | حذف قانون وكل مواده |
| GET | `/api/v1/admin/laws/{id}/articles` | `articles.read` | مواد قانون |
| PATCH | `/api/v1/admin/articles/{id}` | `articles.write` | تعديل مادة وإعادة تضمينها |
| DELETE | `/api/v1/admin/articles/{id}` | `articles.delete` | حذف مادة |

> جميع المسارات المحمية تتطلب ترويسة `Authorization: Bearer <token>`.
> لوحة التحكم التفاعلية متاحة على **`/ui/admin.html`**.

---

## 6. مكافحة الهلوسة (Anti-Hallucination)

النظام يعتمد أربع طبقات متتالية:

1. **عقد إلزامي في تعليمات النظام** (`app/llm/prompts.py`): لا يجوز الاستشهاد بأي
   مادة أو قانون لم يرد نصّه في الكتلة المسترجعة، ويجب الاقتباس الحرفي، والإفصاح عن
   القصور بدل التخمين.
2. **حصر المصدر (RAG)**: النموذج لا يرى إلا المواد التي أعادها المسترجع.
3. **مدقّق الاستشهاد** (`app/llm/validator.py`): يحذف كل استشهاد لا يقابل مادة
   مسترجعة فعلاً، ويتحقق من أن الاقتباس موجود في نص المادة (مطابقة ضبابية عربية).
4. **حقول مُقيَّدة**: الهوية (المحكمة/الأطراف/رقم القضية) والمطالب تُفرض من ملف
   القضية، لا من مخرجات النموذج.

يُعاد في الاستجابة `grounding_score`، و`warnings`، وقائمة `unverified_citations`
المحذوفة — لتكون المراجعة البشرية قائمة على معرفة مواطن الضعف.

---

## 7. تكامل n8n

- **صادر:** بعد توليد المسودة يُرسل النظام حدث `judgment.created` إلى webhook في n8n
  موقَّعاً بـ HMAC-SHA256 في الترويسة `X-Legal-Signature`.
  فعّله بـ `N8N_ENABLED=true` و `N8N_WEBHOOK_URL=...` و `N8N_HMAC_SECRET=...`.
- **وارد:** n8n يستدعي `/api/v1/cases/submit` مباشرة؛ عند ضبط `N8N_HMAC_SECRET`
  يُتحقق من الترويسة `X-N8N-Secret`.

سير عمل جاهز للاستيراد: `n8n/legal_case_workflow.json`
(Webhook → تطبيع البيانات → استدعاء المحرك → تفريع حسب `grounding_score` → طابور
مراجعة القاضي أو تعليم نقص المدونة).

متغيّرات بيئة n8n المطلوبة:

```dotenv
LEGAL_AI_BASE_URL=http://legal-ai:8000
LEGAL_AI_N8N_SECRET=<نفس N8N_HMAC_SECRET>
```

---

## 8. الاختبارات

```bash
pip install -r requirements-dev.txt
pytest -q            # 58 اختباراً، بلا شبكة وبلا قاعدة بيانات
ruff check app scripts tests
```

تغطي الاختبارات: تطبيع النص العربي، التقطيع واعياً بالمواد، مدقّق الاستشهاد
(رفض المواد المُختلقة والاقتباسات المفبركة)، الاسترجاع الهجين وتثبيت المواد
المُستشهد بها، تقييم الأدلة وفق مراتب الإثبات، والمسار الكامل حتى Markdown.
كما تغطي طبقة المصادقة والصلاحيات: تجزئة كلمات المرور (PBKDF2)، توقيع الرموز
والتحقق من العبث بها وانتهائها، مصفوفة الأدوار والصلاحيات، إنشاء حساب المالك،
منع إنشاء مالك ثانٍ أو تعديله أو حذفه، وعزل صلاحيات كل دور على واجهات الإدارة.

---

## 9. بنية المشروع

```
.
├── app/
│   ├── main.py                     # مصنع تطبيق FastAPI
│   ├── api/
│   │   ├── deps.py                 # ربط التبعيات
│   │   └── v1/
│   │       ├── router.py
│   │       └── routes/             # health, ingestion, retrieval, cases, auth, admin
│   ├── core/                       # config, logging, errors, text_utils, chunking,
│   │                               #   security (تجزئة/رموز), permissions (RBAC)
│   ├── db/                         # pool, vector_store, repositories, user_repository
│   ├── llm/                        # embeddings, provider, prompts, chains, validator
│   ├── schemas/                    # common, legal, case, judgment, auth
│   ├── services/                   # ingestion, retrieval, evidence, judgment, pipeline,
│   │                               #   n8n, auth (المالك), admin (إدارة المنصة)
│   ├── static/                     # index.html (واجهة المنصة), admin.html (لوحة التحكم)
│   └── data/samples/               # مدونة يمنية تجريبية
├── sql/
│   ├── 001_init_pgvector.sql       # الجداول والفهارس (HNSW, GIN, trigram)
│   ├── 002_functions.sql           # match_legal_articles, normalize_arabic, upsert...
│   └── 003_auth_rbac.sql           # platform_users, roles, sections, permissions
├── scripts/
│   ├── seed_corpus.py              # إدخال المدونة القانونية
│   └── apply_sql.py                # تنفيذ مخطط SQL
├── n8n/legal_case_workflow.json    # سير عمل جاهز
├── tests/                          # 58 اختباراً
├── Dockerfile · docker-compose.yml · Makefile · requirements.txt
└── .env.example
```

---

## 10. المدونة القانونية الرسمية

المدونة المضمّنة هي `app/data/corpus/yemeni_laws.json`: **نصوص حقيقية** مستخرجة من
مصادر رسمية (المجلس الأعلى للقضاء / هيئة التفتيش القضائي والجريدة الرسمية)، بمجموع
4284 مادة من ثمانية قوانين. وتحمل كل وثيقة كتلة `provenance` توضح المصدر ودرجة
جودة النص (`quality`: `high` للنصوص الرقمية، و`medium` لما استُخرج آلياً من نسخ
ممسوحة ضوئياً) وهل النص حرفي (`verbatim`). وتُعرض هذه البيانات مع كل مادة مُسترجَعة
في مخرجات الواجهة، ويُلزم موجّه النموذج بالإفصاح عن جودة النص عند الاقتباس.

ملف `app/data/samples/yemeni_laws_excerpt.json` مقتطف صغير للاختبارات فقط.

لإضافة نصوص جديدة بنفس الصيغة:

```json
{
  "documents": [
    {
      "document": {
        "source_type": "civil_law",
        "title_ar": "القانون المدني اليمني",
        "law_number": "14",
        "promulgation_year": 2002
      },
      "text": "المادة (1) ...\n\nالمادة (2) ..."
    }
  ]
}
```

ثم:

```bash
python scripts/seed_corpus.py --file my_official_corpus.json
```

قيم `source_type` المدعومة: `civil_law`, `commercial_law`, `evidence_law`,
`civil_procedure`, `criminal_law`, `criminal_procedure`, `family_law`, `labour_law`,
`constitution`, `executive_regulation`, `judicial_precedent`, `fatwa`, `other`.

---

## 11. الأمان والتشغيل

- لا تُدرج `.env` في نظام إدارة الإصدارات (مستثنى في `.gitignore`).
- **المصادقة**: كل مسارات الإدارة محمية بـ JWT موقّع (HMAC-SHA256). كلمات المرور
  تُخزَّن بتجزئة PBKDF2-SHA256 مع ملح عشوائي لكل مستخدم — لا تُخزَّن أبداً كنص صريح.
- **JWT_SECRET إلزامي في الإنتاج**: بدونه يستعمل النظام مفتاحاً افتراضياً غير آمن.
  أنشئ مفتاحاً بـ `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
- **الصلاحيات الحسّاسة للمالك فقط**: `platform.manage`, `laws.delete`,
  `articles.delete`, `sections.delete`, `users.delete`. أدوار `admin` و`editor`
  و`reviewer` و`viewer` لا تملك أي صلاحية حذف أو إدارة مستخدمين.
- **حماية حساب المالك**: لا يمكن إنشاء مالك ثانٍ، ولا تعديل دور المالك، ولا حذفه،
  ولا حذف المستخدم لحسابه الخاص.
- للإنتاج: قيّد `CORS` (يُضيّق تلقائياً عند `APP_ENV=production`)، وسجّل كل تقديم
  قضية في `audit_log`.
- الاستشهادات المحذوفة والتحذيرات تُخزَّن مع كل مسودة — للمراجعة والتدقيق.

---

## الترخيص

يُحدَّد من مالك المشروع.

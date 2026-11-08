# منصة الذكاء الاصطناعي القانوني — الجمهورية اليمنية

محرك القرار والاستدلال القانوني (Legal Decision & Reasoning Engine) للقوانين
والمحاكم اليمنية. يستقبل الدعوى والأدلة، يسترجع المواد القانونية اليمنية ذات الصلة
(RAG) من قاعدة متجهات Supabase/pgvector، يقيّم الأدلة وفق قانون الإثبات اليمني،
ثم يولّد **مسودة حكم قضائي** مُهيكلة (الديباجة، الوقائع، الأسباب والحيثيات، المنطوق).

**ارفع أوراق القضية والنظام يتولّى الباقي.** نقطة النهاية `/api/v1/cases/intake`
تقبل ملفات القضية (PDF أو صور ممسوحة ضوئياً أو Word أو نصاً)، فتقوم بـ: **استخراج
النص** (مع **تعرّف ضوئي عربي** للنصوص الممسوحة)، ثم **تفصيل الدعوى (تفصيل الدعوى)**
— الأطراف والوكلاء والمحكمة ورقم القضية والطلبات والدفوع والوقائع وقيمة الدعوى —
ثم **تصنيف الدعوى** (مدني/تجاري/جنائي/أسري/عمالي/إداري/عقاري/تنفيذ) وتحديد القوانين
الواجبة التطبيق، ثم الاسترجاع الدلالي وتقييم الأدلة وتوليد مسودة الحكم. ولا يُختلق
أي حقل: ما تعذّر استخراجه يُدرج في `needs_human_review` ليؤكده القاضي.

> ⚠️ **تنبيه قانوني:** المخرجات **مسودات آلية** لا تُعدّ أحكاماً قضائية ولا تُعتد بها
> إلا بعد مراجعتها واعتمادها من القاضي المختص. النظام مصمَّم لمنع الاستشهاد بأي نص
> غير موجود في المدونة القانونية، ويُصرّح بمواطن القصور بدل التخمين.

---

## 1. المعمارية

```
                     POST /api/v1/cases/intake            POST /api/v1/cases/submit
                                │                                    │
                    ┌───────────┴───────────┐                        │
                    ▼                       ▼                        │
        DocumentExtraction          (نص مُلصَق)                      │
        (PDF/صورة/Word → OCR)           │                           │
                    └───────────┬───────────┘                        │
                                ▼                                    │
                    CaseDetailsExtractor                             │
                    (الأطراف، الطلبات، الدفوع، الوقائع، القيمة)      │
                                ▼                                    │
                    CaseClassifier (المعجم اليمني)                   │
                    (مدني/جنائي/تجاري/… + القوانين الواجبة)          │
                                ▼                                    │
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
- **Tesseract OCR مع حزمة اللغة العربية** (لاستخراج النصوص من الأوراق الممسوحة ضوئياً):
  ```bash
  # Debian/Ubuntu
  sudo apt-get install -y tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng
  # macOS
  brew install tesseract tesseract-lang
  ```
  اختياري: عند غيابه اضبط `OCR_ENABLED=false`، ويعمل الاستلام على الملفات الرقمية
  (PDF ذي طبقة نصية، Word، النص المُلصَق) مع الإفصاح عن عدم قراءة الممسوحات.

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

# التسجيل الذاتي (اختياري) — يسمح للمستخدمين بإنشاء حساباتهم بأنفسهم
ALLOW_SELF_SIGNUP=true
SIGNUP_DEFAULT_ROLE=viewer
```

> `EMBEDDING_PROVIDER=hash` و `LLM_PROVIDER=mock` يعملان بلا أي شبكة أو مفاتيح —
> مناسبان للتطوير والاختبار. للإنتاج استخدم مزوّداً حقيقياً.

> ⚠️ **تغيير موديل المتجهات يستلزم إعادة الفهرسة.** المتجهات المولَّدة بموديل
> مختلف تقع في فضاء آخر، فتغدو مقارنة التشابه بلا معنى وينخفض استرجاع النصوص
> القانونية صامتاً. يخزّن النظام اسم الموديل مع كل مادة، ويُظهر تحذيراً في
> `warnings` عند وجود اختلاف — عندها أعد إدخال النصوص (`POST /api/v1/ingestion/text`
> أو `make seed`).

### 3.3 تهيئة قاعدة البيانات (pgvector)

في **Supabase → SQL Editor** نفّذ الملفات بالترتيب:

```sql
-- 1) المخطط والجداول والفهارس        → sql/001_init_pgvector.sql
-- 2) الدوال (بحث هجين، تطبيع، upsert) → sql/002_functions.sql
-- 3) المصادقة والصلاحيات              → sql/003_auth_rbac.sql
-- … وبقية ملفات sql/ بالترتيب الرقمي
-- 8) البحث في المواد برقم/نص          → sql/008_article_search.sql
```

أو من الطرفية (ينفّذ كل ملفات `sql/*.sql` بالترتيب، وكلها idempotent):

```bash
python scripts/apply_sql.py          # ينفّذ 001 … 008 عبر SUPABASE_DB_URL
```

تأكّد من وجود امتداد `vector`: في Supabase → Database → Extensions → فعّل `vector`.

### 3.4 إدخال المدونة القانونية اليمنية

```bash
# المدونة اليمنية الكاملة المرفقة (8 قوانين، ~4300 مادة)
python scripts/seed_corpus.py --file app/data/corpus/yemeni_laws.json

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

شغّل الخادم **في الخلفية** مع توجيه المخرجات إلى ملف. لا تشغّله في المقدمة داخل
أمر يجب أن يعود: فهو لا ينتهي أبداً، فيتعلّق الأمر حتى تُقتل المهلة.

```bash
# شغّل في الخلفية وانتظر الجاهزية
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    > /tmp/uvicorn.log 2>&1 &
# انتظر حتى يستجيب
for _ in $(seq 1 30); do curl -sf -m 3 http://localhost:8000/api/v1/health/ready && break; sleep 1; done
# أوقفه بالمعرّف عند الانتهاء
pkill -f "uvicorn app.main:app"
```

بدلاً من ذلك، استخدم `make serve` (Docker) أو `make deploy` (Postgres أصلي)، وكلاهما
يشغّل الخادم في الخلفية ويعيد التحكم بعد التأكد من الجاهزية. أما `make dev` فيضيف
`--reload` ويبقى في المقدمة — لا تستخدمه في أمر آلي.

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

### 3.7 إنشاء الحساب الذاتي (على غرار Harvey)

تتيح المنصة للمستخدمين إنشاء حساباتهم بأنفسهم من الصفحة **`/ui/signup.html`** دون
انتظار موافقة المدير. عند نجاح التسجيل يُعاد رمز دخول فوراً ويُوجَّه المستخدم إلى
بوابة دوره. الأدوار المتاحة للتسجيل هي **البوابات المهنية فقط** — `viewer`,
`researcher`, `lawyer`, `judge`, `prosecutor` — ولا يمكن أبداً إنشاء حساب `owner`
أو `admin` أو `editor` عبر التسجيل العام.

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "username": "a.saleh",
    "password": "Trial12345",
    "full_name": "أحمد محمد صالح",
    "email": "a.saleh@example.com",
    "role": "lawyer"
  }'
```

الرد يحوي `access_token` وبيانات المستخدم مع `role_label_ar` و`role_portal_page`
(رابط بوابة الدور)، فينتقل العميل مباشرة إلى البوابة المناسبة.

لإيقاف التسجيل الذاتي واعتماد الدعوات فقط، اضبط `ALLOW_SELF_SIGNUP=false`. عند
تعطيله يبقى إنشاء الحسابات متاحاً لمالك المنصة والمدير عبر `POST /api/v1/auth/users`.

قواعد التحقق: اسم مستخدم من 3 أحرف (`a-z`, `0-9`, `.`, `_`, `-`)، كلمة مرور من 8
أحرف تجمع حروفاً وأرقاماً، اسم كامل إلزامي، وبريد إلكتروني اختياري وفريد. يُرفض
تكرار اسم المستخدم أو البريد برمز `409`.

### 3.8 Docker (اختياري)

قاعدة Postgres/pgvector المحلية تُنشر على المنفذ **5433** (وليس 5432، تجنباً للتعارض مع أي
Postgres مثبَّت على المضيف). لذلك يجب أن يشير `.env` إليها قبل التشغيل:

```dotenv
SUPABASE_DB_URL=postgresql://legal:legal@127.0.0.1:5433/legal_ai
```

> ⚠️ إذا تُركت قيمة `SUPABASE_DB_URL` على إعداد Supabase (`...pooler.supabase.com:5432`)
> فسيفشل اتصال الـ API بقاعدة البيانات، ويظهر `/health/ready` بحالة `degraded`.

```bash
docker compose up -d --build       # API + Postgres/pgvector محلي
docker compose logs --tail=50 api  # لا تستخدم -f: المتابعة لا تنتهي أبداً
curl -s localhost:8000/api/v1/health/ready   # يجب أن يكون status=ready
```

بعد أول تشغيل، حمّل المدونة القانونية داخل قاعدة البيانات المحلية:

```bash
python scripts/seed_corpus.py --file app/data/corpus/yemeni_laws.json
```

### 3.9 نشر بلا Docker (Postgres مثبَّت على المضيف)

عند تعذّر تشغيل Docker (بيئات مقيّدة، خوادم CI)، استعمل سكربت النشر الأصلي. يقوم بأمر
واحد بكل الخطوات: تثبيت PostgreSQL + pgvector إن لزم، تشغيل العنقود، إنشاء الدور
وقاعدة البيانات والامتداد، تطبيق مخطط SQL، فهرسة المدونة، ثم تشغيل الـ API. السكربت
**آمن لإعادة التشغيل** (idempotent) فلا يكرّر ما هو موجود.

```bash
./scripts/deploy_native.sh          # المنفذ الافتراضي 12000
PORT=8000 ./scripts/deploy_native.sh
# أو:
make deploy
```

يتطلب صلاحية `sudo` لتثبيت الحزم وإدارة عنقود PostgreSQL. على المنفذ 5432 (وليس 5433)،
ويولّد `JWT_SECRET` عشوائياً في `.env` عند أول تشغيل.

### 3.10 التشغيل بدون إنترنت (بيئة معزولة)

المنصة مُصمَّمة لتعمل بالكامل دون أي اتصال بالإنترنت. كل مكوّن له نظير محلي:

| المكوّن | المحلي (بلا شبكة) | المضيف (يتطلب إنترنت) |
|---|---|---|
| قاعدة البيانات | PostgreSQL + pgvector على الجهاز | Supabase |
| تضمين النصوص | `EMBEDDING_PROVIDER=hash` | `openai` |
| الاستدلال القانوني | `LLM_PROVIDER=mock` (محرّك حتمي) | `openai` / `anthropic` / `openai_compatible` |
| واجهة المستخدم | PWA + Service Worker (يعمل أوفلاين) | — |
| n8n | `N8N_ENABLED=false` | webhook |

للتشغيل في وضع معزول تماماً (لا يخرج أي طلب من الجهاز):

```bash
make offline            # أو: OFFLINE=1 PORT=12000 ./scripts/serve_public.sh
```

يضبط هذا الأمر `LLM_PROVIDER=mock` و`EMBEDDING_PROVIDER=hash` و`N8N_ENABLED=false`
قبل الفهرسة وقبل تشغيل الخادم، فيبقى كل شيء داخل الجهاز. تولّد المسودات من النصوص
المسترجعة فعلياً، وتظل مقيّدة بعقد منع الاستشهاد الوهمي (كل مرجع يجب أن يقابل مادة
مسترجعة، والاقتباس حرفي).

**التراجع التلقائي:** حتى مع مزوّد سحابي، إذا انقطع الإنترنت أثناء الخدمة فإن
`LLM_OFFLINE_FALLBACK=true` (الافتراضي) يجعل المحرّك يستخدم النظير المحلي بدل إرجاع
خطأ `502`، ويظهر اسم المزوّد في الاستجابة `... +offline` للدلالة على ذلك. اضبطه على
`false` لإظهار الخطأ بدلاً من التراجع.

> ⚠️ في الوضع المحلي (`mock`) تكون جودة الصياغة حتمية ومحدودة مقارنة بالنموذج السحابي:
> المحرّك المحلي يركّب المسودة من النصوص المسترجعة فقط ولا يولّد صياغة جديدة. للاستخدام
> الإنتاجي مع صياغة غنية، استخدم نموذجاً محلياً مفتوحاً (مثل Ollama) عبر
> `LLM_PROVIDER=openai_compatible` مع `LLM_BASE_URL=http://localhost:11434/v1`.

> ⚠️ **قبل الفتح للعامة:** غيّر `OWNER_PASSWORD` الافتراضية فور أول دخول، واضبط
> `LLM_PROVIDER`/`EMBEDDING_PROVIDER` على مزوّد حقيقي بمفتاح API. القيم الافتراضية
> (`mock`/`hash`) تعمل بلا شبكة لكنها للتطوير والاختبار فقط.

---

## 3.11 تطبيق أندرويد (ملف APK)

بجانب الـ PWA يوجد تطبيق أندرويد أصلي في `android/`: غلاف WebView رقيق يفتح المنصة
بشاشة كاملة، يحفظ جلسة الدخول، يفتح الروابط الخارجية في المتصفح، ويعرض شاشة عربية
«إعادة المحاولة» عند تعذّر الوصول. المنطق يبقى على الخادم، فيخدم نشرٌ واحد الويب
والتطبيق معاً.

**تنزيل جاهز:** من صفحة `/ui/install.html` (زر «تنزيل تطبيق أندرويد»)، والملف على
`/ui/downloads/yemeni-legal-ai.apk` مع بصمة SHA-256 بجانبه.

**البناء من المصدر** (يتطلب JDK 17+ وAndroid SDK 34):

```bash
cd android
printf 'sdk.dir=/path/to/android-sdk\n' > local.properties

# مفتاح التوقيع (مرة واحدة؛ لا ترفعه للمستودع أبداً)
keytool -genkeypair -v -keystore release.keystore -alias legal \
  -keyalg RSA -keysize 2048 -validity 10000
cat > keystore.properties <<'K'
storeFile=release.keystore
storePassword=...
keyAlias=legal
keyPassword=...
K

./gradlew assembleRelease          # أو: gradle assembleRelease
# الناتج: app/build/outputs/apk/release/app-release.apk
```

عنوان الخادم مدمج عند البناء، ويمكن تغييره دون تعديل الكود:

```bash
./gradlew assembleRelease -PplatformUrl=https://your-domain.example
```

ثم انسخ الناتج إلى `app/static/downloads/yemeni-legal-ai.apk` وأعد تشغيل الخادم.

> ⚠️ شهادة التوقيع هي هوية التطبيق: احفظ `release.keystore` في مكان آمن. فقدانه يعني
> أن التحديثات المستقبلية لن تُثبَّت فوق النسخة الحالية. الملفات مستثناة من git.

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
| `commercial` | التحليل التجاري: البنود المالية (المطالب به/المعترف به/المقضي به)، الشروط الجزائية، آجال السداد، والمستندات التجارية |
| `grounding_score` | نسبة الاستشهادات المسندة فعلاً إلى النصوص المسترجعة |
| `warnings` | تحذيرات (نقص المدونة، استشهادات محذوفة…) |

### دعوى تجارية (محرّك التقاضي التجاري والاستخلاص المالي)

عندما تكون `case_type` = `تجاري` (أو يظهر في الملف ورقة تجارية كالكمبيالة أو سند
الأمر)، يفعّل النظام **محرّك الاستخلاص المالي**: يستخرج البنود المالية من الطلبات
والمستندات، ويفصل بين *المطالب به* و*المعترف به* و*المقضي به*، ويستخرج الشرط
الجزائي ونسبته (مثال: 1% يومياً) وأجل السداد والمستندات التجارية المرفقة.

```bash
curl -X POST http://localhost:8000/api/v1/cases/submit \
  -H "Content-Type: application/json" \
  -d '{
    "case_number": "ت-200/2026",
    "case_type": "تجاري",
    "court_name": "المحكمة التجارية - صنعاء",
    "judge_name": "القاضي/ عمر",
    "subject_matter": "المطالبة بقيمة بضاعة موردة وغرامة تأخيرية",
    "facts_summary": "ورد المدعي للمدعى عليه بضاعة بموجب عقد مكتوب وكمبيالة، وتأخر المدعى عليه عن السداد.",
    "currency": "YER",
    "plaintiff": { "name": "شركة النور التجارية", "role": "plaintiff" },
    "defendant": { "name": "مؤسسة الأمانة", "role": "defendant" },
    "plaintiff_claims": [
      {
        "title": "قيمة البضاعة",
        "description": "مبلغ وقدره خمسة ملايين ريال يمني",
        "amount": 5000000,
        "currency": "YER",
        "legal_basis": ["المادة 146"]
      },
      {
        "title": "غرامة تأخيرية",
        "description": "غرامة تأخيرية بنسبة 1% يومياً وفق الشرط الجزائي",
        "legal_basis": []
      }
    ],
    "defendant_responses": [
      {
        "claim_ref": "قيمة البضاعة",
        "response_type": "admission",
        "description": "يعترف بقيمة البضاعة وقدرها خمسة ملايين ريال"
      },
      {
        "claim_ref": "غرامة تأخيرية",
        "response_type": "denial",
        "description": "ينكر صحة الشرط الجزائي"
      }
    ],
    "evidence": [
      {
        "id": "ev-1", "submitted_by": "plaintiff", "evidence_type": "contract",
        "title": "عقد التوريد المكتوب",
        "extracted_text": "يلتزم المشتري بالسداد خلال ثلاثين يوماً من تاريخ التسليم."
      },
      { "id": "ev-2", "submitted_by": "plaintiff", "evidence_type": "commercial_bill", "title": "كمبيالة رقم 12" },
      { "id": "ev-3", "submitted_by": "plaintiff", "evidence_type": "invoice", "title": "فاتورة رقم 55" },
      { "id": "ev-4", "submitted_by": "defendant", "evidence_type": "bank_transfer", "title": "حوالة بنكية" }
    ],
    "commercial": { "payment_deadline_days": 30 },
    "generate_judgment": true,
    "max_retrieved_articles": 10
  }' | python -m json.tool
```

يمكن تمرير الكتلة الاختيارية `commercial` لفرض تفاصيل تجارية معروفة مسبقاً
(آجال السداد، الشروط الجزائية، الفوائد)؛ وما يُمرَّر فيها يتقدّم على الاستخلاص الآلي.
أما `commercial.financial_summary` في الاستجابة فيُحسب آلياً من ملف القضية والأدلة،
ولا يُقرأ من مخرجات النموذج اللغوي، لضمان أن كل مبلغ في المسودة قابل للتتبع.

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

### البحث في المواد برقم المادة أو بجزء من نصها (يعمل بدون إنترنت)

بحث حتمي لا يحتاج إلى متجهات: يطابق رقم المادة (يقبل الأرقام العربية ٠-٩،
ويتجاهل الأصفار البادئة) أو يطابق جزءاً من نصها حسب تقاطع الكلمات بعد التطبيع
العربي. لذلك يعمل حتى لو تعذّر الوصول إلى موديل المتجهات.

```bash
# برقم المادة
curl "http://localhost:8000/api/v1/retrieval/articles?number=146&limit=5"

# بالأرقام العربية
curl "http://localhost:8000/api/v1/retrieval/articles?number=%D9%A2%D9%A5"

# بجزء من النص
curl "http://localhost:8000/api/v1/retrieval/articles?q=%D8%A7%D9%84%D8%A8%D9%8A%D9%86%D8%A9%20%D8%B9%D9%84%D9%89%20%D8%A7%D9%84%D9%85%D8%AF%D8%B9%D9%8A"

# بحث مركّب مع تحديد نوع القانون
curl -X POST http://localhost:8000/api/v1/retrieval/articles \
  -H "Content-Type: application/json" \
  -d '{"article_number": "146", "source_types": ["civil_law"], "limit": 10}'
```

**العمل بدون إنترنت:** يرفق الموقع فهرساً ثابتاً للمواد في
`app/static/legal-index.json` (يُبنى بالأمر
`python scripts/build_search_index.py`). يخزّنه Service Worker في الجهاز،
وتبحث الصفحة الرئيسية فيه مباشرة عند انقطاع الشبكة، ثم تدمج النتائج مع نتائج
الخادم عند توفر الاتصال. يعكس مُطبِّع البحث في المتصفح
`app/core/text_utils.normalize_arabic` حرفياً، فيتطابق الترتيب بين الوضعين.

### إحصاءات المدونة

```bash
curl http://localhost:8000/api/v1/ingestion/stats | python -m json.tool
```

---

### استلام قضية من ملفات مرفوعة (تفصيل + تصنيف + مسودة)

المسار الأبسط: ارفع أوراق القضية (PDF أو صورة ممسوحة أو Word أو نص) وسيقوم النظام
بالخطوات الخمس كاملةً دون أي إدخال آخر:

```bash
# 1. الحصول على رمز الوصول (أو استخدم مفتاح خدمة للأنظمة الآلية)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"owner","password":"'"$OWNER_PASSWORD"'"}' | jq -r .access_token)

# 2. رفع أوراق القضية
curl -X POST http://localhost:8000/api/v1/cases/intake \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@عقد_البيع.pdf" \
  -F "files=@صورة_الإنذار.jpg" \
  -F "generate_judgment=true"
```

يقوم النظام تلقائياً بـ:

1. **استخراج النص** — طبقة نصية إن وُجدت، وإلا **تعرّف ضوئي عربي (OCR)** عبر Tesseract.
2. **تفصيل الدعوى (تفصيل الدعوى)** — المحكمة، الدائرة، رقم القضية، تاريخ الجلسة،
   الأطراف ووكلاؤهم، موضوع الدعوى، قيمة الدعوى وعملتها، الطلبات، الدفوع، الوقائع،
   والمواد التي استند إليها الطرفان.
3. **تصنيف الدعوى** — مدني/تجاري/جنائي/أسري/عمالي/إداري/عقاري/تنفيذ، وتحديد القوانين
   الواجبة التطبيق (مثال: الجنائي ← قانون الجرائم والعقوبات + الإجراءات الجزائية + الإثبات).
4. **الاسترجاع الدلالي (RAG)** — بحث هجين (متجهات + FTS عربي + trigram مدموجة بـ RRF)
   في المواد القانونية اليمنية.
5. **تقييم الأدلة وتوليد المسودة** — تقييم وفق قانون الإثبات، ثم مسودة حكم مُسنَدة
   يخضع كل استشهاد فيها لتحقق حرفي.

نموذج الاستجابة (مختصر):

```json
{
  "status": "judgment_ready",
  "classification": {
    "case_type": "جنائي",
    "label_ar": "جنائي",
    "confidence": 0.92,
    "criminal_flag": true,
    "source_types": ["criminal_law", "criminal_procedure", "evidence_law"]
  },
  "details": {
    "court_name": "المحكمة الابتدائية - الدائرة الجزائية",
    "case_number": "2201/2026",
    "plaintiff_name": "النيابة العامة",
    "defendant_name": "سامي عبده قائد",
    "subject_matter": "إدانة المتهم بجريمة الاعتداء على سلامة الجسم",
    "claims": ["إدانة المتهم بجريمة الاعتداء على سلامة الجسم"],
    "defences": ["ينكر المتهم ما نُسب إليه"],
    "legal_basis": ["236"],
    "confidence": 0.9
  },
  "needs_human_review": ["رقم القضية"],
  "case_id": "…",
  "judgment_id": "…",
  "pipeline": { "retrieved_articles": ["…"], "grounding_score": 1.0 }
}
```

**تجاوز أي حقل مستخرج:** أرسل الحقل ضمن النموذج (form field) وسيحلّ محلّ القيمة
المستخرجة، مثال: `-F "plaintiff_name=الاسم الصحيح" -F "claim_value=5000000"`.

**الحقول التي يلزم تأكيدها بشرياً** تُعاد في `needs_human_review` (مثل غياب اسم أحد
الطرفين أو انخفاض ثقة التصنيف أو وجود مؤشرات جنائية في دعوى مصنَّفة مدنياً) — ولا
يُختلق أي حقل غائب.

### تصنيف دعوى من نصها فقط

```bash
curl -X POST http://localhost:8000/api/v1/cases/classify \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"قام المتهم بسرقة المسروقات بالإكراه، وأحالت النيابة العامة القضية للمحكمة الجزائية."}'
```

### تفصيل دعوى من نصها فقط

```bash
curl -X POST http://localhost:8000/api/v1/cases/extract \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"المحكمة الابتدائية\nالقضية رقم 77/2026\nالوقائع: …\nالمدعي: …\nالمدعى عليه: …\nالطلبات\n1. …\nالدفوع\n- …"}'
```

### واجهة الاستلام التفاعلية

واجهة جاهزة لرفع الأوراق ومراجعة التفصيل والتصنيف والمسودة:

```
http://localhost:8000/ui/intake.html
```

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
| GET | `/api/v1/retrieval/articles?number=…&q=…` | بحث في المواد برقم المادة أو بجزء من نصها (بدون متجهات، يعمل بدون إنترنت) |
| POST | `/api/v1/retrieval/articles` | نفس البحث بصيغة JSON |
| **POST** | **`/api/v1/cases/intake`** | **استلام قضية من ملفات مرفوعة: استخراج + تفصيل + تصنيف + مسودة حكم** |
| POST | `/api/v1/cases/classify` | تصنيف دعوى من نصها (مدني/جنائي/تجاري/…) |
| POST | `/api/v1/cases/extract` | تفصيل دعوى من نصها (الأطراف والطلبات والدفوع) |
| GET | `/api/v1/cases/lexicon` | دليل أنواع الدعاوى والقوانين الواجبة التطبيق |
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
| GET | `/api/v1/auth/signup` | عام | خيارات التسجيل الذاتي والأدوار المتاحة |
| POST | `/api/v1/auth/signup` | عام | إنشاء حساب جديد (تسجيل ذاتي) |
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
pytest -q            # 111 اختباراً، بلا شبكة وبلا قاعدة بيانات
ruff check app scripts tests
```

تغطي الاختبارات: تطبيع النص العربي، التقطيع واعياً بالمواد، مدقّق الاستشهاد
(رفض المواد المُختلقة والاقتباسات المفبركة)، الاسترجاع الهجين وتثبيت المواد
المُستشهد بها، تقييم الأدلة وفق مراتب الإثبات، والمسار الكامل حتى Markdown.
كما تغطي **استلام القضية**: تفصيل الدعوى (استخراج الأطراف والطلبات والدفوع وقيمة
الدعوى وعملتها دون اختلاق)، وتصنيف الدعوى إلى الفروع اليمنية الثمانية، وعدم
التقاط المصطلحات داخل كلمات أخرى (مثل «سب» داخل «مناسباً»)، وعدم إغفال المؤشرات
الجنائية، ومسار الاستلام من نص أو من ملف مرفوع حتى المسودة.
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
│   │       └── routes/             # health, ingestion, retrieval, cases, intake, auth, admin
│   ├── core/                       # config, logging, errors, text_utils, chunking,
│   │                               #   security (تجزئة/رموز), permissions (RBAC),
│   │                               #   legal_lexicon (معجم أنواع الدعاوى والمؤشرات)
│   ├── db/                         # pool, vector_store, repositories, user_repository
│   ├── llm/                        # embeddings, provider, prompts, chains, validator
│   ├── schemas/                    # common, legal, case, intake, judgment, auth
│   ├── services/                   # ingestion, retrieval, evidence, judgment, pipeline,
│   │                               #   intake (الاستلام والتفصيل), case_details (تفصيل الدعوى),
│   │                               #   classification (تصنيف الدعوى), document_extraction (OCR),
│   │                               #   n8n, auth (المالك), admin (إدارة المنصة)
│   ├── static/                     # index.html, admin.html, intake.html, signup.html
│   └── data/samples/               # مدونة يمنية تجريبية
├── sql/
│   ├── 001_init_pgvector.sql       # الجداول والفهارس (HNSW, GIN, trigram)
│   ├── 002_functions.sql           # match_legal_articles, normalize_arabic, upsert...
│   ├── 003_auth_rbac.sql           # platform_users, roles, sections, permissions
│   ├── 004_assistant.sql           # محادثات المساعد القانوني
│   ├── 005_portal_workspace.sql    # مساحات عمل البوابات
│   └── 006_portal_roles.sql        # أدوار البوابات على نوع user_role
├── scripts/
│   ├── seed_corpus.py              # إدخال المدونة القانونية
│   └── apply_sql.py                # تنفيذ مخطط SQL
├── n8n/legal_case_workflow.json    # سير عمل جاهز
├── tests/                          # 111 اختباراً (منها اختبارات التفصيل والتصنيف والاستلام)
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

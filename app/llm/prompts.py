"""System prompts for strict, grounded Yemeni legal reasoning.

Design goals
------------
1. Grounding: the model may cite ONLY articles supplied in the
   RETRIEVED_STATUTES block. Inventing article numbers or statute names is
   explicitly forbidden.
2. Uncertainty: when the retrieved corpus does not cover an issue, the model
   must record it as a gap instead of improvising a rule.
3. Structure: the output must be valid JSON matching the JudgmentDraft schema.
4. Judicial register: formal Yemeni judicial Arabic (لغة قضائية فصحى).
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings

GROUNDING_CONTRACT = """
قواعد إلزامية غير قابلة للتفاوض:

1. الاستناد الحصري: لا يجوز لك الاستشهاد بأي مادة قانونية أو قانون لم يَرِد نصّه في
   قسم «النصوص القانونية المسترجعة» (RETRIEVED_STATUTES). يُمنع منعاً باتاً اختراع
   أرقام مواد أو أسماء قوانين أو نصوص قانونية من عندك.
2. عند نقص النصوص: إذا لم تكفِ النصوص المسترجعة للفصل في مسألة معينة، فاذكر ذلك صريحاً
   في الحقل المخصص (gap/uncertainty) ولا تُصدر حكماً مبنياً على قاعدة غير مُستشهد بها.
3. الاقتباس الحرفي: عند الاستشهاد بمادة، انقل نصها أو جزءاً حرفياً منها كما ورد في النص
   المسترجع دون أي تعديل، واذكر رقم المادة واسم القانون تماماً كما وردا.
4. عدم الترجيح بغير دليل: لا تبنِ أي ترجيح على معلومات عامة أو على افتراضات لم تُقدَّم
   كأدلة في ملف القضية.
5. الفصل بين الوقائع والقانون: ميّز دائماً بين ما أثبته الأطراف من وقائع وما تُطبّقه أنت
   من قواعد قانونية.
6. الحياد: لا تنحز لطرف على الآخر؛ قيّم أدلة الطرفين بالمعيار القانوني ذاته.
7. الإفصاح عن القصور: إذا كانت الأدلة المقدمة غير كافية للإثبات وفق القانون، فصرّح بذلك
   ولا تُكمل النقص بالتخمين.
8. جودة النص المصدر: كل نص مسترجع يحمل حقل «جودة النص» (high أو medium أو low). إذا كانت
   الجودة medium أو low فالنص مستخرج آلياً من نسخة ممسوحة ضوئياً وقد يحتمل خطأً في النقل،
   فعندها: (أ) استند إلى النص كما ورد، (ب) نبّه صريحاً في الحيثيات إلى ضرورة مطابقة النص
   مع النسخة الرسمية الورقية قبل الاعتماد النهائي، (ج) لا تُصلح أنت أي كلمة أو رقم تراه
   مشوّهاً، بل أشر إلى موضع الشك فقط.
""".strip()

EVIDENCE_SYSTEM_PROMPT = f"""
أنت خبير قانوني يمني متخصص في قانون الإثبات، تعمل مساعداً للقاضي اليمني.
مهمتك تقييم الأدلة المقدمة في القضية وفقاً لأحكام قانون الإثبات اليمني والقواعد العامة
في الإثبات، وتحديد حجية كل دليل ووزنه الترجيحي.

مراتب الأدلة في القانون اليمني التي يجب أن تُراعى:
- الإقرار: أقوى الأدلة، ولا يحتاج إلى دليل آخر إذا كان صحيحاً وخالياً من العيوب.
- الكتابة (المحررات الرسمية ثم العرفية): المحرر الرسمي له حجية كاملة ما لم يُطعن بالتزوير.
- شهادة الشهود: تُقدَّر بحسب عدد الشهود وعدالتهم وتطابق شهاداتهم، وتختلف شروطها بحسب
  نوع الحق (مدني/تجاري/جزائي).
- القرائن: قرائن قانونية (تُغني عن الإثبات) وقرائن قضائية (يستنبطها القاضي).
- الخبرة: يُرجع إليها في المسائل الفنية، وتكون حجيتها بحسب تعليل الخبير واقتناع القاضي.
- اليمين: يمين الحكم أو يمين الحلف، وتُقدَّر بحسب موضعها في الدعوى.
- المعاينة: ما يدركه القاضي بنفسه.

{GROUNDING_CONTRACT}

أخرج النتيجة بصيغة JSON فقط وفق المخطط المطلوب، دون أي نص خارج JSON.
""".strip()

REASONING_SYSTEM_PROMPT = f"""
أنت مستشار قانوني يمني رفيع المستوى، تُساعد القاضي اليمني في صياغة أسباب وحيثيات
الحكم (الأسباب والحيثيات) في ضوء النصوص القانونية اليمنية المسترجعة.

منهجية التحليل الإلزامية (طبّقها بالترتيب):
1. تكييف الدعوى: تحديد طبيعتها القانونية (مدني/تجاري/عقاري/عمالي/أسري/جزائي).
2. تحديد المسائل القانونية محل البحث.
3. توزيع عبء الإثبات بين المدعي والمدعى عليه وفق القواعد العامة وقانون الإثبات.
4. تقييم أدلة كل طرف.
5. بحث الدفوع الشكلية قبل الموضوعية (الاختصاص، التقادم، البطلان).
6. تطبيق النص القانوني الأنسب على الوقائع الثابتة، مع الاستشهاد الحرفي برقم المادة
   واسم القانون.
7. الوصول إلى نتيجة معلَّلة لكل مسألة.

{GROUNDING_CONTRACT}

أخرج النتيجة بصيغة JSON فقط وفق المخطط المطلوب، دون أي نص خارج JSON.
""".strip()

JUDGMENT_SYSTEM_PROMPT = f"""
أنت مساعد قضائي يمني متخصص في صياغة مسودات الأحكام القضائية بصياغة رسمية فصيحة.
تُنتج مسودة حكم كاملة البنية تتكون من أربعة أقسام بالترتيب:

1. الديباجة: اسم المحكمة والدائرة ورقم القضية وتاريخ الجلسة وبيانات الطرفين وأسماء
   وكلائهم، بصياغة «بجلسة علنية… حضر… فقررت المحكمة…».
2. الوقائع: سرد موضوعي لدعوى المدعي وردود المدعى عليه والوقائع غير المتنازع عليها
   ونقاط الخلاف.
3. الأسباب والحيثيات: التحليل القانوني المستند إلى النصوص المسترجعة، مع الاستشهاد
   الحرفي برقم المادة واسم القانون، وتقييم الأدلة، ودرء الدفوع.
4. المنطوق: نتيجة الفصل بشكل قاطع («حكمت المحكمة: أولاً… ثانياً…») مع المصاريف
   وميعاد الطعن إذا كان الحكم ابتدائياً.

الأسلوب المطلوب:
- لغة عربية قضائية رسمية فصيحة، خالية من العامية.
- عبارات ربط قضائية معتادة («وحيث إن…»، «ومن حيث إن…»، «لما كان ذلك…»).
- تجنّب أي لغة توكيدية مطلقة غير مسندة إلى نص أو دليل.
- لا تُصدر حكماً بإدانة جزائية إن لم يكن ملف القضية جزائياً أو لم تتوفر أركان الجريمة
  وأدلتها.

{GROUNDING_CONTRACT}

أخرج النتيجة بصيغة JSON فقط وفق المخطط المطلوب، دون أي نص خارج JSON.
""".strip()

QUERY_EXPANSION_PROMPT = """
أنت خبير في صياغة استعلامات البحث في قواعد النصوص القانونية اليمنية.
بالنظر إلى وقائع القضية والمطالب، استخرج المصطلحات القانونية اليمنية الدقيقة التي
يُرجّح أن ترد في نصوص القانون (مثل: «الالتزام»، «التعويض»، «الفسخ»، «التقادم»،
«بطلان العقد»، «المسؤولية التقصيرية»، «الربا»، «حيازة»، «شفعة»، «إنفاق»).
أخرج النتيجة بصيغة JSON: {"queries": ["...", "..."]} بحيث لا يتجاوز كل استعلام 12 كلمة،
وبين 2 و 6 استعلامات. لا تكتب أي شيء خارج JSON.
""".strip()

CLASSIFICATION_PROMPT = """
أنت خبير في تصنيف الدعاوى أمام المحاكم اليمنية. تُعطى نص قضية وقائمة مرشحين محسوبة
آلياً بأوزانها، ومطلوب منك اختيار الأنسب فقط من بين المرشحين المعطاة.

أنواع الدعاوى:
- جنائي: الجرائم والعقوبات (جناية، جنحة، مخالفة) وتكون النيابة العامة طرفاً فيها.
- مدني: الالتزامات والعقود المدنية والتعويض.
- تجاري: الأعمال التجارية والأوراق التجارية والشركات والإفلاس.
- أسري: النكاح والطلاق والنفقة والحضانة والنسب والميراث والوصية.
- عمالي: علاقات العمل والأجور ومكافأة نهاية الخدمة والفصل التعسفي.
- إداري: القرارات الإدارية والوظيفة العامة.
- عقاري: الملكية والحيازة والشفعة والحدود والأوقاف.
- تنفيذ: إجراءات التنفيذ الجبري والسندات التنفيذية.

قواعد إلزامية:
1. اختر من بين المرشحين المعطاة فقط، ولا تخترع نوعاً غير مدرج.
2. إذا ظهرت مؤشرات جريمة (عقوبة، متهم، النيابة العامة، سرقة، قتل، اعتداء) فالنوع
   جنائي ولو غلب على النص طابع مدني، لأن الفصل في الجريمة من اختصاص القضاء الجزائي.
3. عند التساوي أو الغموض، أبقِ التصنيف المبدئي المذكور.
4. اذكر سبباً موجزاً (سطر واحد) يبيّن السند القانوني للاختيار.

أخرج النتيجة بصيغة JSON فقط: {"case_type": "...", "reason": "..."} دون أي نص خارج JSON.
""".strip()


def build_retrieved_statutes_block(articles: list[dict]) -> str:
    """Render the retrieved statutes as the ONLY citable corpus."""
    if not articles:
        return "«النصوص القانونية المسترجعة»: (لا توجد نصوص قانونية مطابقة في قاعدة البيانات)"
    lines = ["«النصوص القانونية المسترجعة» (لا يجوز الاستشهاد بغيرها):", ""]
    for index, article in enumerate(articles, start=1):
        law = article.get("law_title") or "قانون"
        if article.get("law_number"):
            law = f"{law} رقم ({article['law_number']})"
        lines.append(f"[{index}] {article.get('article_label') or 'مادة'} من {law}")
        if article.get("chapter"):
            lines.append(f"    الباب/الفصل: {article['chapter']}")
        if article.get("heading"):
            lines.append(f"    العنوان: {article['heading']}")
        lines.append(f"    النص: {str(article.get('content', '')).strip()}")
        if article.get("text_quality"):
            lines.append(f"    جودة النص: {article['text_quality']}")
        if article.get("source_url"):
            lines.append(f"    المصدر: {article['source_url']}")
        if article.get("similarity") is not None:
            lines.append(f"    درجة الملاءمة: {float(article['similarity']):.3f}")
        lines.append("")
    return "\n".join(lines)


def build_commercial_block(commercial: Any) -> str:
    """Render the extracted commercial facts as a prompt block.

    The figures here are computed deterministically from the case file. The
    model must reason over them, not invent new ones - so the block states the
    claimed/acknowledged/awarded split explicitly and forbids inventing amounts.
    """
    summary = commercial.financial_summary
    terms = commercial.terms
    lines = ["«البيانات التجارية والمالية المستخرجة» (لا تُغيّر أي مبلغ ورد فيها):", ""]
    if summary.lines:
        lines.append("| البند | المطالب به | المعترف به | المقضي به | العملة | السند |")
        lines.append("|---|---|---|---|---|---|")
        for line in summary.lines:
            lines.append(
                f"| {line.subject} | {line.claimed_amount if line.claimed_amount is not None else '—'} "
                f"| {line.acknowledged_amount if line.acknowledged_amount is not None else '—'} "
                f"| {line.awarded_amount if line.awarded_amount is not None else '—'} "
                f"| {line.currency} | {line.basis or '—'} |"
            )
        lines.append("")
        lines.append(
            f"إجمالي المطالب به: {summary.total_claimed} {summary.currency} — "
            f"المعترف به: {summary.total_acknowledged} — المقضي به: {summary.total_awarded}."
        )
    else:
        lines.append("(لا توجد بنود مالية محددة)")
    if summary.reconciled is False:
        lines.append(
            "تنبيه: يوجد تعارض بين المبلغ الرقمي والمبلغ المكتوب؛ يجب أن تُصرّح المحكمة بذلك "
            "ولا تعتمد أيّاً منهما دون تحقق."
        )
    if terms.payment_deadline_days is not None:
        lines.append(f"أجل السداد المتفق عليه: {terms.payment_deadline_days} يوماً.")
    if terms.interest_rate_percent is not None:
        lines.append(
            f"نسبة الفائدة/العائد المذكورة: {terms.interest_rate_percent:g}% — يجب بحث مدى "
            "موافقتها لأحكام الشريعة والقانون اليمني (الربا والفوائد)."
        )
    if terms.penalties:
        lines.append("")
        lines.append("الشروط الجزائية / الغرامات التأخيرية:")
        for penalty in terms.penalties:
            detail = penalty.description
            if penalty.rate_percent is not None:
                detail += f" (النسبة: {penalty.rate_percent:g}% {penalty.rate_period or ''})"
            if penalty.amount is not None:
                detail += f" (المقدار: {penalty.amount} {penalty.currency})"
            lines.append(f"  - {detail.strip()}")
    if terms.milestones:
        lines.append("")
        lines.append("آجال السداد:")
        for milestone in terms.milestones:
            extra = f" ({milestone.due_days} يوماً)" if milestone.due_days else ""
            lines.append(f"  - {milestone.description}{extra}")
    if summary.evidence_kinds:
        lines.append("")
        lines.append(
            "المستندات التجارية المرفقة: "
            + "، ".join(kind.value for kind in summary.evidence_kinds)
            + "."
        )
    lines.append("")
    lines.append(
        "«المطلوب»: اعتمد هذه المبالغ كما هي في الحيثيات والمنطوق، وأضف في الأسباب "
        "المبادئ التجارية ذات الصلة (حسن النية، إلزام المدين بالوفاء، حدود الشرط الجزائي "
        "والفوائد) مستنداً حصراً إلى النصوص المسترجعة."
    )
    return "\n".join(lines)


def build_case_block(case: dict) -> str:
    """Render the case facts for the prompt."""
    claims = case.get("plaintiff_claims") or []
    responses = case.get("defendant_responses") or []
    lines = [
        "«بيانات القضية»:",
        f"- المحكمة: {case.get('court_name') or settings.default_court_name}",
        f"- الدائرة: {case.get('circuit') or '—'}",
        f"- رقم القضية: {case.get('case_number') or '—'}",
        f"- القاضي: {case.get('judge_name') or '—'}",
        f"- نوع الدعوى: {case.get('case_type')}",
        f"- موضوع الدعوى: {case.get('subject_matter')}",
        f"- قيمة الدعوى: {case.get('claim_value')} {case.get('currency', 'YER')}",
        f"- المدعي: {case.get('plaintiff_name')}",
        f"- المدعى عليه: {case.get('defendant_name')}",
    ]
    if case.get("facts_summary"):
        lines.append(f"- ملخص الوقائع: {case['facts_summary']}")
    lines += ["", "«دعوى المدعي»:"]
    for index, claim in enumerate(claims, start=1):
        amount = (
            f" — المبلغ: {claim.get('amount')} {claim.get('currency', 'YER')}"
            if claim.get("amount")
            else ""
        )
        lines.append(f"  {index}. {claim.get('title')}: {claim.get('description')}{amount}")
        if claim.get("legal_basis"):
            lines.append(f"     سند المدعي: {', '.join(claim['legal_basis'])}")
    lines += ["", "«ردود المدعى عليه»:"]
    if not responses:
        lines.append("  (لم تُقدَّم ردود)")
    for index, response in enumerate(responses, start=1):
        lines.append(f"  {index}. [{response.get('response_type')}] {response.get('description')}")
        if response.get("legal_basis"):
            lines.append(f"     سند المدعى عليه: {', '.join(response['legal_basis'])}")
    return "\n".join(lines)


def build_evidence_block(evidence: list[dict]) -> str:
    if not evidence:
        return "«الأدلة المقدمة»: (لا توجد أدلة مرفقة)"
    lines = ["«الأدلة المقدمة»:"]
    for index, item in enumerate(evidence, start=1):
        lines.append(
            f"  {index}. [{item.get('submitted_by')}] ({item.get('evidence_type')}) "
            f"{item.get('title')}"
        )
        if item.get("description"):
            lines.append(f"     الوصف: {item['description']}")
        if item.get("proof_category"):
            lines.append(f"     تصنيف الإثبات المقترح: {item['proof_category']}")
        if item.get("extracted_text"):
            lines.append(f"     نص مستخرج: {str(item['extracted_text'])[:1200]}")
    return "\n".join(lines)

"""Yemeni legal lexicon: case types, their indicators and their governing codes.

This module is the single source of truth for "which court does this case
belong to, and which Yemeni codes does a judge apply to it". Both the intake
classifier and the retrieval planner read from here, so a case's classification
and the corpus it is searched against can never drift apart.

The keyword lists are drawn from the vocabulary that actually appears in Yemeni
pleadings and codes - not generic Arabic. They are used as *evidence* for a
weighted score, never as a hard rule, and the classifier records which terms
fired so a reviewer can audit the decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CaseTypeProfile:
    """A branch of the Yemeni court system and the codes it applies."""

    key: str
    label_ar: str
    #: Legal source types searched when this case type is chosen.
    source_types: tuple[str, ...]
    #: Terms that strongly indicate this case type (weight 3).
    strong: tuple[str, ...] = ()
    #: Terms that weakly indicate it (weight 1).
    weak: tuple[str, ...] = ()
    #: Terms that make the type unlikely (negative weight).
    counter: tuple[str, ...] = ()
    #: True for cases where the public prosecution and personal liberty are in play.
    criminal: bool = False
    #: Court that normally has jurisdiction.
    default_court: str = "المحكمة الابتدائية"
    description: str = ""


# --------------------------------------------------------------------------- #
# The profiles
# --------------------------------------------------------------------------- #
CIVIL = CaseTypeProfile(
    key="مدني",
    label_ar="مدني",
    source_types=("civil_law", "evidence_law", "civil_procedure"),
    strong=(
        "عقد بيع", "فسخ العقد", "بطلان العقد", "التزام تعاقدي", "المسؤولية التقصيرية",
        "تعويض عن الضرر", "دين", "كفالة", "ضمان", "هبة", "وديعة", "إيجار", "إيجارة",
        "إثراء بلا سبب", "الوفاء بالالتزام", "تنفيذ عقد", "إخلال بالالتزام",
    ),
    weak=(
        "عقد", "التزام", "تعويض", "ضرر", "فسخ", "بطلان", "مبلغ", "دين", "مطالبة",
        "إلزام", "أداء", "مبيع", "ثمن", "مدين", "دائن", "قرض", "رهن", "حيازة",
    ),
    counter=("جناية", "جنحة", "عقوبة", "متهم", "النيابة العامة", "إدانة"),
    description="المعاملات المدنية والالتزامات والعقود المدنية وفق القانون المدني اليمني.",
)

COMMERCIAL = CaseTypeProfile(
    key="تجاري",
    label_ar="تجاري",
    source_types=("commercial_law", "civil_law", "evidence_law", "civil_procedure"),
    strong=(
        "سند لأمر", "كمبيالة", "شيك", "ورقة تجارية", "إفلاس", "شهر إفلاس", "تصفية شركة",
        "تاجر", "الأعمال التجارية", "علامة تجارية", "اسم تجاري", "وكالة تجارية",
        "شركة مساهمة", "شركة تضامن", "شركة ذات مسؤولية محدودة", "السمسرة", "الوساطة التجارية",
        "إيجار تجاري", "الإيجار التجاري", "بنك", "مصرف", "اعتماد مستندي", "تظهير",
        "دفتر تجاري", "بضاعة", "استيراد", "تصدير", "المنافسة غير المشروعة",
    ),
    weak=(
        "شركة", "تجاري", "شريك", "رأس المال", "أرباح", "خسارة", "توزيع", "حصص",
        "مؤسسة", "تجزئة", "جملة", "مورد", "عمولة", "التزام تجاري", "دفع",
    ),
    counter=("جناية", "جنحة", "عقوبة", "متهم", "نفقة", "حضانة", "طلاق"),
    description="المنازعات التجارية والأوراق التجارية وشركات التجارة وفق القانون التجاري اليمني.",
)

CRIMINAL = CaseTypeProfile(
    key="جنائي",
    label_ar="جنائي",
    source_types=("criminal_law", "criminal_procedure", "evidence_law"),
    strong=(
        "جناية", "جنحة", "مخالفة", "عقوبة", "متهم", "النيابة العامة", "إدانة", "براءة",
        "قتل عمد", "قتل خطأ", "شروع في القتل", "اعتداء على النفس", "اعتداء",
        "اعتداء على سلامة الجسم", "إيذاء", "ضرب", "جرح", "عاهة", "عاهة مستديمة",
        "سرقة", "سلب", "نهب", "اختلاس", "احتيال", "رشوة", "تزوير", "استعمال مزور",
        "تقليد", "قصاص", "دية", "حبس", "سجن", "إعدام", "غرامة", "مخدرات", "خمر",
        "زنا", "قذف", "سب", "تحرش", "اغتصاب", "خطف", "تهديد", "إرهاب", "سلاح",
        "جريمة", "فعل جنائي", "القبض", "التوقيف", "التلبس",
    ),
    weak=(
        "نيابة", "بلاغ", "شكوى جزائية", "محضر", "ضبط", "تحقيق", "قاضي الجزاء",
        "الدعوى الجزائية", "المجني عليه", "التحريات", "أمن", "شرطة", "قضية جنائية",
    ),
    counter=("نفقة", "حضانة", "مهر", "طلاق", "شركة", "شيك", "سند لأمر"),
    criminal=True,
    default_court="المحكمة الابتدائية - الجزائية",
    description=(
        "الجرائم والعقوبات وإجراءاتها. تخضع للنيابة العامة ويجب التحقق من توفر أركان "
        "الجريمة وأدلتها قبل أي إدانة."
    ),
)

FAMILY = CaseTypeProfile(
    key="أسري",
    label_ar="أسري",
    source_types=("family_law", "evidence_law", "civil_procedure"),
    strong=(
        "نفقة", "نفقات", "حضانة", "طلاق", "تطليق", "خلع", "فسخ النكاح", "عقد النكاح",
        "عقد قران", "مهر", "صداق", "مؤخر الصداق", "معجل الصداق", "زوجية", "زوج",
        "زوجة", "العدة", "طلاق بائن", "طلاق رجعي", "نسب", "إثبات النسب", "بنوة",
        "ميراث", "إرث", "تركة", "فريضة", "وصية", "رضاعة", "حجر", "ولاية على النفس",
        "قوامة", "رؤية الصغير", "زيارة", "إسقاط الحضانة", "التطليق للضرر",
    ),
    weak=(
        "أسرة", "أحوال شخصية", "قاصر", "صغير", "ولي", "مولود", "ذمة", "شقة الزوجية",
        "بيت الطاعة", "العصبة", "الوارث",
    ),
    counter=("سند لأمر", "كمبيالة", "شيك", "جناية", "جنحة", "شركة", "عقوبة"),
    description="الأحوال الشخصية: النكاح والطلاق والنفقة والحضانة والميراث والوصية.",
)

LABOUR = CaseTypeProfile(
    key="عمالي",
    label_ar="عمالي",
    source_types=("labour_law", "civil_law", "evidence_law", "civil_procedure"),
    strong=(
        "عامل", "عاملين", "صاحب العمل", "عقد عمل", "أجر", "أجور", "مكافأة نهاية الخدمة",
        "فصل تعسفي", "إنهاء خدمة", "إجازة سنوية", "ساعات العمل", "ساعات إضافية",
        "إصابة عمل", "تأمينات اجتماعية", "مستحقات عمالية", "مكتب العمل", "لجنة التحكيم العمالي",
        "الحد الأدنى للأجور", "تسريح", "تعويض عمالي", "بدل", "ترقية العامل",
    ),
    weak=(
        "عمل", "موظف", "وظيفة", "خدمة", "مرتب", "راتب", "شهادة خبرة", "استقالة",
        "مخالصة", "منحة", "بدل سكن", "بدل انتقال",
    ),
    counter=("سند لأمر", "كمبيالة", "طلاق", "نفقة", "حضانة", "جناية", "جنحة"),
    description="علاقات العمل ومستحقات العاملين وفق قانون العمل اليمني.",
)

ADMINISTRATIVE = CaseTypeProfile(
    key="إداري",
    label_ar="إداري",
    source_types=("constitution", "civil_procedure", "civil_law", "executive_regulation"),
    strong=(
        "قرار إداري", "إلغاء قرار", "جهة إدارية", "الإدارة العامة", "موظف عام",
        "المحكمة الإدارية", "دعوى إدارية", "قرار تعيين", "قرار ترقية", "قرار نقل",
        "إحالة إلى المعاش", "فصل إداري", "مخالفة إدارية", "مناقصة", "عقد إداري",
        "التزام إداري", "تجاوز السلطة", "عيب استعمال السلطة", "مرفق عام", "ترخيص",
        "ضبط وظيفي", "لائحة", "تعميم", "جزاء إداري",
    ),
    weak=(
        "وزارة", "مصلحة", "هيئة", "مؤسسة حكومية", "مديرية", "محافظة", "ولاية إدارية",
        "موظف", "وظيفة عامة", "الجهاز المركزي", "الرقابة",
    ),
    counter=("سند لأمر", "كمبيالة", "طلاق", "نفقة", "جناية", "عقوبة", "أرض"),
    description="منازعات القرارات الإدارية والوظيفة العامة وفق الدستور والقوانين النافذة.",
)

REAL_ESTATE = CaseTypeProfile(
    key="عقاري",
    label_ar="عقاري",
    source_types=("civil_law", "evidence_law", "civil_procedure"),
    strong=(
        "عقار", "أرض", "أطيان", "حيازة الأرض", "تسجيل عقاري", "وثيقة عقارية",
        "شفعة", "الشفعة", "إحياء موات", "أملاك الدولة", "وقف", "أوقاف", "حدود الأرض",
        "جوار", "منازعة حدود", "تصرف عقاري", "بيع أرض", "قسمة عقار", "إفراز",
        "شهر عقاري", "سند ملكية", "تصحيح ملكية", "نزع ملكية", "أرض زراعية",
    ),
    weak=(
        "ملكية", "مالك", "عقاري", "بناء", "منزل", "دور", "أرضية", "مساحة", "رسم هندسي",
        "مخطط", "إفراغ", "رهن عقاري",
    ),
    counter=("سند لأمر", "كمبيالة", "طلاق", "نفقة", "جناية", "عقوبة", "عامل"),
    description="المنازعات العقارية والملكية والحيازة وفق القانون المدني اليمني.",
)

EXECUTION = CaseTypeProfile(
    key="تنفيذ",
    label_ar="تنفيذ",
    source_types=("civil_procedure", "civil_law", "evidence_law"),
    strong=(
        "تنفيذ حكم", "سند تنفيذي", "حجز", "حجز أموال", "بيع بالمزاد", "مزاد علني",
        "منع من السفر", "إشكال في التنفيذ", "قاضي التنفيذ", "دائرة التنفيذ",
        "تنفيذ جبري", "إعذار رسمي", "إخلاء", "تسليم مبيع", "تنفيذ التزام",
    ),
    weak=("تنفيذ", "حكم واجب النفاذ", "النيابة في التنفيذ", "إجراءات التنفيذ"),
    counter=("جناية", "جنحة", "طلاق", "حضانة"),
    description="إجراءات التنفيذ الجبري والسندات التنفيذية وفق قانون المرافعات والتنفيذ المدني.",
)

CASE_TYPES: tuple[CaseTypeProfile, ...] = (
    CRIMINAL,
    FAMILY,
    COMMERCIAL,
    LABOUR,
    ADMINISTRATIVE,
    REAL_ESTATE,
    EXECUTION,
    CIVIL,
)

CASE_TYPE_BY_KEY: dict[str, CaseTypeProfile] = {p.key: p for p in CASE_TYPES}
# Common aliases seen in Yemeni filings and in the platform's own API.
CASE_TYPE_ALIASES: dict[str, str] = {
    "جزائي": "جنائي",
    "جنح": "جنائي",
    "جنايات": "جنائي",
    "جزاء": "جنائي",
    "مدني": "مدني",
    "حقوقي": "مدني",
    "تجاري": "تجاري",
    "تجارية": "تجاري",
    "أسري": "أسري",
    "احوال شخصية": "أسري",
    "أحوال شخصية": "أسري",
    "شخصي": "أسري",
    "عمالي": "عمالي",
    "عمل": "عمالي",
    "إداري": "إداري",
    "اداري": "إداري",
    "عقاري": "عقاري",
    "أراضي": "عقاري",
    "اراضي": "عقاري",
    "تنفيذ": "تنفيذ",
}


def resolve_case_type(value: str | None) -> str | None:
    """Map a free-text case type onto a canonical profile key."""
    if not value:
        return None
    cleaned = value.strip()
    if cleaned in CASE_TYPE_BY_KEY:
        return cleaned
    return CASE_TYPE_ALIASES.get(cleaned)


def source_types_for(case_type: str | None) -> list[str] | None:
    """Legal source types a court applies to this kind of case."""
    key = resolve_case_type(case_type)
    if not key:
        return None
    return list(CASE_TYPE_BY_KEY[key].source_types)


def is_criminal(case_type: str | None) -> bool:
    key = resolve_case_type(case_type)
    return bool(key and CASE_TYPE_BY_KEY[key].criminal)


def case_type_label(case_type: str | None) -> str:
    key = resolve_case_type(case_type)
    return CASE_TYPE_BY_KEY[key].label_ar if key else (case_type or "")


def profile_terms(case_type: str | None) -> tuple[str, ...]:
    """Strong then weak lexicon terms for a case type, most specific first."""
    key = resolve_case_type(case_type)
    if not key:
        return ()
    profile = CASE_TYPE_BY_KEY[key]
    # Longest terms are the most specific ("اعتداء على سلامة الجسم" beats "اعتداء"),
    # so they lead the search query and anchor the embedding.
    return tuple(sorted(profile.strong + profile.weak, key=len, reverse=True))


# --------------------------------------------------------------------------- #
# Subject-matter and party extraction patterns
# --------------------------------------------------------------------------- #
#: Patterns that pull the subject matter (موضوع الدعوى) out of a filing.
SUBJECT_PATTERNS: tuple[str, ...] = (
    r"الموضوع\s*[:：]\s*([^\n]{4,200})",
    r"موضوع\s+الدعوى\s*[:：]?\s*([^\n]{4,200})",
    r"بشأن\s+([^\n]{4,200})",
    r"المطلوب\s*[:：]?\s*([^\n]{4,200})",
)

#: Patterns for the plaintiff (المدعي) and defendant (المدعى عليه).
PLAINTIFF_PATTERNS: tuple[str, ...] = (
    r"(?:المدعي|المشتكي|المدّعي)\s*(?:عليه)?\s*[:：]\s*([^\n]{2,120})",
    r"المدعي\s+([^\n:：]{2,80})\s*(?:ضد|مقابل)",
    r"مقدم(?:ة)?\s+الدعوى\s*[:：]\s*([^\n]{2,120})",
)
DEFENDANT_PATTERNS: tuple[str, ...] = (
    r"المدعى\s*عليه\s*[:：]\s*([^\n]{2,120})",
    r"المدعى\s*عليه\s+([^\n:：]{2,80})",
    r"ضد\s*[:：]?\s*([^\n]{2,120})",
    r"المشتكى\s*عليه\s*[:：]\s*([^\n]{2,120})",
    # Criminal filings name the accused المتهم, not المدعى عليه.
    r"المتهم\s*[:：]\s*([^\n]{2,120})",
    r"المتهم\s+([^\n:：]{2,80})",
    r"المتهمون\s*[:：]\s*([^\n]{2,120})",
)

#: Court, case number and amount patterns.
COURT_PATTERNS: tuple[str, ...] = (
    r"((?:المحكمة|محكمة)\s+(?:الابتدائية|الاستئناف|العليا|الإدارية|التجارية|الجزائية)[^\n،,]{0,60})",
    r"((?:المحكمة|محكمة)\s+[^\n،,]{3,60})",
)
CASE_NUMBER_PATTERNS: tuple[str, ...] = (
    r"(?:القضية|الدعوى)\s*(?:رقم|ر\.)\s*[:：]?\s*([0-9\u0660-\u0669]{1,6}\s*/\s*[0-9\u0660-\u0669]{2,4})",
    r"رقم\s+القضية\s*[:：]?\s*([0-9\u0660-\u0669]{1,6}\s*/\s*[0-9\u0660-\u0669]{2,4})",
    r"رقم\s*[:：]\s*([0-9\u0660-\u0669]{1,6}\s*/\s*[0-9\u0660-\u0669]{2,4})",
)

#: Statutory basis phrases the parties rely on ("وفقاً للمادة 146 من القانون المدني").
#: The optional ``[وفبكل]{0,3}`` prefix is essential: Yemeni pleadings write
#: "وفقاً للمادة 146 والمادة 189", so the citation carries the preposition ل
#: ("للمادة"), and a pattern anchored on "المادة" alone would miss it.
LEGAL_BASIS_PATTERNS: tuple[str, ...] = (
    r"(?:^|[\s،,و])(?:[وفبكل]{0,3})?(?:المادة|المواد|مادة|مواد|م\.)\s*\(?\s*"
    r"([0-9\u0660-\u0669]{1,5}(?:\s*(?:و|,|،)\s*(?:المواد?|مادة|م\.)?\s*[0-9\u0660-\u0669]{1,5})*)\s*\)?",
)

#: Phrases that mark the start of the claims (الطلبات) section.
CLAIM_MARKERS: tuple[str, ...] = (
    "الطلبات", "المطلوب", "لذلك يطلب", "وعليه يطلب", "يلتمس", "نلتمس",
    "المطالبة", "أطلب", "نطلب",
)

#: Phrases that mark the start of the facts (الوقائع) section.
FACTS_MARKERS: tuple[str, ...] = (
    "الوقائع", "الواقعة", "حيثيات", "ملخص الوقائع", "تفاصيل الدعوى", "السرد",
)

#: Phrases that mark a defence (الدفوع).
DEFENCE_MARKERS: tuple[str, ...] = (
    "الدفوع", "دفاع", "رد", "ينكر", "يجيب", "الجواب", "الرد على الدعوى",
    "دفع بعدم", "الدفع بالتقادم", "بطلان", "عدم اختصاص",
)


@dataclass(slots=True)
class ClassificationSignal:
    term: str
    weight: int


@dataclass(slots=True)
class CaseTypeScore:
    case_type: str
    label_ar: str
    score: float
    signals: list[ClassificationSignal] = field(default_factory=list)
    criminal: bool = False

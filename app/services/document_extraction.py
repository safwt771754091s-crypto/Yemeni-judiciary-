"""Text extraction from case files, including Arabic OCR for scanned filings.

Yemeni court papers arrive in every shape: typed PDFs, scanned images of a
typewriter page, Word documents, photographs of a hand-carried file. The intake
endpoint must read all of them, because a case cannot be analysed from metadata
alone.

Extraction strategy, per format:

* PDF - the embedded text layer is used when it is substantial. Pages whose text
  layer is empty or tiny (a scanned page) are rendered at ``OCR_DPI`` and passed
  to Tesseract with the Arabic model. This per-page decision matters: mixed
  filings (a typed cover sheet stapled to scanned annexes) are common, and
  OCR-ing a page that already has good text only degrades it.
* Images (png/jpg/tiff/bmp/webp) - OCR directly.
* DOCX - the XML parts are read from the zip with the standard library, so no
  extra dependency is needed; paragraphs and table cells are both kept.
* Plain text / markdown / CSV / JSON - decoded directly.

Every function degrades instead of raising: one unreadable page must not lose
the rest of the file. The caller receives the text plus a list of warnings.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic

logger = get_logger(__name__)

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
DOCX_SUFFIXES = {".docx"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".rtf"}

SUPPORTED_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES | DOCX_SUFFIXES | TEXT_SUFFIXES

# Strips the <w:t> runs out of DOCX XML while keeping paragraph boundaries.
_DOCX_PARAGRAPH = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
_DOCX_TEXT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.DOTALL)
_XML_ENTITIES = (
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&apos;", "'"),
)


class ExtractionError(Exception):
    """Raised only when a file cannot be read at all."""


@dataclass(slots=True)
class ExtractedPage:
    number: int
    text: str
    method: str  # "text" | "ocr" | "native"


@dataclass(slots=True)
class ExtractionResult:
    filename: str
    suffix: str
    text: str
    pages: list[ExtractedPage] = field(default_factory=list)
    page_count: int = 0
    used_ocr: bool = False
    char_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:  # pragma: no cover - depends on the host image
        return False


def ocr_status() -> dict[str, object]:
    """Report OCR readiness so /health/ready can surface a missing engine."""
    if not settings.ocr_enabled:
        return {"enabled": False, "available": False, "languages": []}
    try:
        import pytesseract

        version = str(pytesseract.get_tesseract_version())
        languages = sorted(pytesseract.get_languages(config="") or [])
    except Exception as exc:
        return {"enabled": True, "available": False, "error": str(exc), "languages": []}
    wanted = [lang.strip() for lang in settings.ocr_languages.split("+") if lang.strip()]
    return {
        "enabled": True,
        "available": True,
        "version": version,
        "languages": languages,
        "missing_languages": [lang for lang in wanted if lang not in languages],
    }


def _ocr_image(image: object) -> str:
    """Run Tesseract over a PIL image, returning the raw recognised text."""
    import pytesseract

    config = f"--psm {settings.ocr_psm}"
    return pytesseract.image_to_string(
        image, lang=settings.ocr_languages, config=config
    )


def _clean_page_text(text: str) -> str:
    """Tidy OCR output without altering the words.

    OCR emits ragged line breaks, stray control characters and runs of spaces.
    Normalising whitespace is safe; anything that touched letters or digits
    would corrupt a verbatim quote, so nothing else is changed here.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    # Collapse runs of blank lines to a single separator.
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def extract_from_bytes(
    filename: str, data: bytes, *, content_type: str | None = None
) -> ExtractionResult:
    """Extract text from an uploaded file, choosing the strategy by suffix."""
    suffix = Path(filename or "").suffix.lower()
    result = ExtractionResult(filename=filename, suffix=suffix, text="")
    if not data:
        result.error = "الملف فارغ"
        return result
    try:
        if suffix in PDF_SUFFIXES:
            _extract_pdf(data, result)
        elif suffix in IMAGE_SUFFIXES:
            _extract_image(data, result)
        elif suffix in DOCX_SUFFIXES:
            _extract_docx(data, result)
        elif suffix in TEXT_SUFFIXES or (content_type or "").startswith("text/"):
            _extract_plain(data, result)
        else:
            # Last resort: sniff the magic bytes rather than trusting the name.
            if data[:4] == b"%PDF":
                _extract_pdf(data, result)
            elif data[:4] == b"PK\x03\x04":
                _extract_docx(data, result)
            else:
                result.error = (
                    f"صيغة الملف «{suffix or 'غير معروفة'}» غير مدعومة. "
                    f"الصيغ المدعومة: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
                )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "document extraction failed",
            extra={"filename": filename, "error": str(exc)},
        )
        result.error = f"تعذّر استخراج النص من الملف: {exc}"

    result.text = _clean_page_text(result.text)
    result.char_count = len(result.text)
    result.page_count = result.page_count or len(result.pages)
    result.used_ocr = any(p.method == "ocr" for p in result.pages)
    if not result.error and result.char_count == 0:
        result.warnings.append(
            "لم يُستخرج أي نص من الملف؛ إن كان مستنداً ممسوحاً ضوئياً فتأكد من تفعيل "
            "خدمة التعرّف الضوئي (OCR)."
        )
    return result


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _extract_pdf(data: bytes, result: ExtractionResult) -> None:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - older installs expose ``fitz``
        import fitz as pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        result.page_count = document.page_count
        ocr_pages: list[int] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            text = _clean_page_text(page.get_text("text") or "")
            method = "text"
            if (
                len(text) < settings.pdf_text_min_chars
                and settings.ocr_enabled
                and _tesseract_available()
            ):
                ocr_text = _ocr_pdf_page(page, pymupdf)
                if len(ocr_text) > len(text):
                    text, method = ocr_text, "ocr"
                    ocr_pages.append(index + 1)
            result.pages.append(ExtractedPage(number=index + 1, text=text, method=method))
        result.text = "\n\n".join(p.text for p in result.pages if p.text)
        if ocr_pages:
            result.warnings.append(
                "تم استخدام التعرّف الضوئي (OCR) على الصفحات: "
                + "، ".join(str(n) for n in ocr_pages)
                + ". النص المستخرج قد يحتمل أخطاءً في النقل؛ يُرجى مطابقته مع الأصل."
            )
    finally:
        document.close()


def _ocr_pdf_page(page: object, pymupdf_module: object) -> str:
    try:
        import pymupdf

        pixmap = page.get_pixmap(dpi=settings.ocr_dpi, colorspace=pymupdf.csGRAY)
        image = _pixmap_to_image(pixmap)
        return _clean_page_text(_ocr_image(image))
    except Exception as exc:
        logger.warning("pdf page OCR failed", extra={"error": str(exc)})
        return ""


def _pixmap_to_image(pixmap: object) -> object:
    from PIL import Image

    mode = "L" if getattr(pixmap, "n", 3) == 1 else "RGB"
    return Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def _extract_image(data: bytes, result: ExtractionResult) -> None:
    if not settings.ocr_enabled:
        result.error = "خدمة التعرّف الضوئي (OCR) معطّلة؛ لا يمكن قراءة ملف صورة."
        return
    if not _tesseract_available():
        result.error = (
            "محرّك التعرّف الضوئي (Tesseract) غير مثبّت على الخادم؛ لا يمكن قراءة الصور."
        )
        return
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    text = _clean_page_text(_ocr_image(image))
    result.pages.append(ExtractedPage(number=1, text=text, method="ocr"))
    result.text = text
    result.page_count = 1
    result.warnings.append(
        "نُفِّذ التعرّف الضوئي (OCR) على الصورة. النص المستخرج قد يحتمل أخطاءً في النقل؛ "
        "يُرجى مطابقته مع الأصل قبل الاعتماد عليه."
    )


# --------------------------------------------------------------------------- #
# DOCX (standard library only)
# --------------------------------------------------------------------------- #
def _extract_docx(data: bytes, result: ExtractionResult) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.startswith("word/") and n.endswith(".xml")]
        document_parts = [n for n in names if n == "word/document.xml"]
        if not document_parts:
            document_parts = [n for n in names if n.startswith("word/document")]
        if not document_parts:
            raise ExtractionError("ملف Word لا يحتوي على مستند قابل للقراءة")
        chunks: list[str] = []
        for name in document_parts:
            xml = archive.read(name).decode("utf-8", errors="ignore")
            for paragraph in _DOCX_PARAGRAPH.findall(xml):
                text = "".join(_DOCX_TEXT.findall(paragraph))
                for entity, char in _XML_ENTITIES:
                    text = text.replace(entity, char)
                text = text.strip()
                if text:
                    chunks.append(text)
        # Header/footer text often carries the court and case number.
        for name in names:
            if name.startswith("word/header") or name.startswith("word/footer"):
                xml = archive.read(name).decode("utf-8", errors="ignore")
                for paragraph in _DOCX_PARAGRAPH.findall(xml):
                    text = "".join(_DOCX_TEXT.findall(paragraph)).strip()
                    if text:
                        chunks.append(text)
    result.text = "\n".join(chunks)
    result.pages.append(ExtractedPage(number=1, text=result.text, method="native"))
    result.page_count = 1


# --------------------------------------------------------------------------- #
# Plain text
# --------------------------------------------------------------------------- #
def _extract_plain(data: bytes, result: ExtractionResult) -> None:
    for encoding in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        text = data.decode("utf-8", errors="replace")
    result.text = text
    result.pages.append(ExtractedPage(number=1, text=text, method="native"))
    result.page_count = 1


# --------------------------------------------------------------------------- #
# Helpers used by the intake layer
# --------------------------------------------------------------------------- #
def guess_evidence_type(filename: str) -> str:
    """Map a filename to the platform's evidence-type vocabulary."""
    suffix = Path(filename or "").suffix.lower()
    stem = Path(filename or "").stem
    lowered = f"{stem} {suffix}".lower()
    if suffix in PDF_SUFFIXES or suffix in DOCX_SUFFIXES:
        if any(word in lowered for word in ("عقد", "contract", "اتفاق")):
            return "contract"
        if any(word in lowered for word in ("سند", "ايصال", "إيصال", "receipt", "فاتورة")):
            return "receipt"
        if any(word in lowered for word in ("محضر", "تقرير", "خبرة", "خبير", "report")):
            return "expert"
        return "document"
    if suffix in IMAGE_SUFFIXES:
        return "photo"
    if suffix in {".mp3", ".wav", ".m4a", ".ogg"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".avi", ".mkv"}:
        return "video"
    return "document"


def looks_arabic(text: str) -> bool:
    """True when a sample of the text is predominantly Arabic script."""
    sample = text[:4000]
    if not sample.strip():
        return False
    arabic = len(re.findall(r"[\u0621-\u064A]", sample))
    return arabic >= max(5, len(sample) * 0.15)


def quality_of(text: str, *, used_ocr: bool) -> str:
    """Coarse text-quality grade, mirroring the corpus ``text_quality`` field.

    OCR output is never graded "high": even a clean scan can silently mistype a
    digit, and an article number is exactly the thing a judgment cites.
    """
    normalized = normalize_arabic(text)
    if used_ocr:
        return "medium" if len(normalized) > 200 else "low"
    return "high" if len(normalized) > 80 else "low"

from __future__ import annotations

import html
import math
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from fastapi import HTTPException, status

from core.normalize_input_data import clean_text
from core.security import safe_child_path, safe_slug, unique_child_path

OCR_INPUT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
TEXT_LAYER_MIN_CHARS = 80


@dataclass
class OcrTable:
    sheet_title: str
    rows: list[list[str]]


@dataclass
class OcrPage:
    label: str
    text: str
    rows: list[list[str]]
    method: str


@dataclass
class OcrGeneratedFile:
    kind: str
    path: Path
    rows: int = 0


@dataclass
class OcrProcessResult:
    pages: list[OcrPage]
    generated_files: list[OcrGeneratedFile]


def _ocr_lang() -> str:
    return os.getenv("APP_OCR_LANG", "rus+kaz+eng").strip() or "rus+kaz+eng"


def _tesseract_cmd() -> str:
    return os.getenv("APP_TESSERACT_CMD", os.getenv("TESSERACT_CMD", "")).strip()


def _pdf_zoom() -> float:
    try:
        return max(1.0, min(float(os.getenv("APP_OCR_PDF_ZOOM", "2.0")), 4.0))
    except Exception:
        return 2.0


def _extract_tables_enabled() -> bool:
    return os.getenv("APP_OCR_EXTRACT_TABLES", "1").strip().lower() not in {"0", "false", "no", "off"}


def _require_pil():
    try:
        from PIL import Image
        return Image
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR image support requires Pillow. Install requirements again.",
        ) from exc


def _require_tesseract():
    try:
        import pytesseract
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR requires pytesseract. Install requirements again.",
        ) from exc
    cmd = _tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def _require_fitz():
    try:
        import fitz
        return fitz
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF OCR requires PyMuPDF. Install requirements again.",
        ) from exc


def _xml(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _sheet_name(value: str, fallback: str) -> str:
    text = clean_text(value) or fallback
    text = re.sub(r"[\\/*?:\[\]]+", " ", text).strip() or fallback
    return text[:31]


def _cell_ref(row_index: int, col_index: int) -> str:
    letters = ""
    n = col_index
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row_index}"


def _write_minimal_docx(path: Path, title: str, source_name: str, pages: list[OcrPage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    paragraphs: list[tuple[str, bool]] = [(title, True), (f"Источник: {source_name}", False)]
    for page in pages:
        paragraphs.append((page.label, True))
        for block in re.split(r"\n{2,}", clean_text(page.text)):
            block = clean_text(block)
            if block:
                paragraphs.append((block, False))

    body_parts = []
    for text, bold in paragraphs:
        bold_xml = "<w:b/>" if bold else ""
        body_parts.append(
            "<w:p><w:r><w:rPr>"
            f"{bold_xml}"
            "</w:rPr>"
            f"<w:t xml:space=\"preserve\">{_xml(text)}</w:t>"
            "</w:r></w:p>"
        )
    document_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body>"
        + "".join(body_parts)
        + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )
    content_types = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
        "</Types>"
    )
    rels = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)


def _write_minimal_xlsx(path: Path, title: str, tables: list[OcrTable]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not tables:
        return
    workbook_sheets = []
    workbook_rels = []
    content_overrides = []
    sheet_xml_by_name: dict[str, str] = {}
    for index, table in enumerate(tables, start=1):
        sheet_name = _sheet_name(table.sheet_title, f"Sheet {index}")
        workbook_sheets.append(f"<sheet name=\"{_xml(sheet_name)}\" sheetId=\"{index}\" r:id=\"rId{index}\"/>")
        workbook_rels.append(
            f"<Relationship Id=\"rId{index}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet{index}.xml\"/>"
        )
        content_overrides.append(
            f"<Override PartName=\"/xl/worksheets/sheet{index}.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
        )
        max_columns = max((len(row) for row in table.rows), default=0)
        header = ["Страница"] + [f"Колонка {col}" for col in range(1, max_columns + 1)]
        rows = [header, *[[table.sheet_title, *row] for row in table.rows]]
        row_xml_parts = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(row, start=1):
                cells.append(
                    f"<c r=\"{_cell_ref(row_index, col_index)}\" t=\"inlineStr\"><is><t xml:space=\"preserve\">{_xml(value)}</t></is></c>"
                )
            row_xml_parts.append(f"<row r=\"{row_index}\">{''.join(cells)}</row>")
        sheet_xml_by_name[f"xl/worksheets/sheet{index}.xml"] = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
            "<sheetData>"
            + "".join(row_xml_parts)
            + "</sheetData></worksheet>"
        )

    content_types = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>"
        + "".join(content_overrides)
        + "</Types>"
    )
    rels = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>"
        "</Relationships>"
    )
    workbook_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<sheets>"
        + "".join(workbook_sheets)
        + "</sheets></workbook>"
    )
    workbook_rels_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        + "".join(workbook_rels)
        + "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        for name, xml in sheet_xml_by_name.items():
            archive.writestr(name, xml)


def _nonempty_cells(row: list[str]) -> list[str]:
    return [clean_text(cell) for cell in row if clean_text(cell)]


def _valid_table_group(rows: list[list[str]]) -> bool:
    normalized = [_nonempty_cells(row) for row in rows]
    normalized = [row for row in normalized if len(row) >= 2]
    if len(normalized) < int(os.getenv("APP_OCR_TABLE_MIN_ROWS", "3")):
        return False

    lengths = [len(row) for row in normalized]
    max_cols = max(lengths, default=0)
    if max_cols < 2:
        return False

    most_common = max(lengths.count(length) for length in set(lengths))
    if most_common / max(1, len(lengths)) < 0.60:
        return False

    useful_cells = 0
    weak_cells = 0
    for row in normalized:
        for cell in row:
            if len(cell) >= 2:
                useful_cells += 1
            else:
                weak_cells += 1
    if useful_cells < 6 or weak_cells > useful_cells:
        return False

    total_rows = max(1, len(rows))
    if len(normalized) / total_rows < 0.45 and len(normalized) < 5:
        return False
    return True


def _select_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """Return only rows that form a plausible table.

    Plain OCR text often contains occasional right-column noise or page metadata.
    The old heuristic treated the whole page as a table when only one or two lines
    had a large horizontal gap. This selector keeps only contiguous multi-column
    regions with stable column count.
    """
    if not _extract_tables_enabled():
        return []

    candidates: list[list[list[str]]] = []
    current: list[list[str]] = []
    skipped_single_rows = 0

    def flush() -> None:
        nonlocal current, skipped_single_rows
        if current:
            candidates.append(current)
        current = []
        skipped_single_rows = 0

    for row in rows:
        cells = _nonempty_cells(row)
        if len(cells) >= 2:
            current.append(cells)
            skipped_single_rows = 0
            continue
        if current and skipped_single_rows == 0:
            skipped_single_rows += 1
            continue
        flush()
    flush()

    accepted: list[list[str]] = []
    for group in candidates:
        if not _valid_table_group(group):
            continue
        if accepted:
            accepted.append([])
        accepted.extend(group)
    return accepted


def _extract_fitz_tables(page: Any) -> list[list[str]]:
    if not _extract_tables_enabled() or not hasattr(page, "find_tables"):
        return []
    try:
        found = page.find_tables()
    except Exception:
        return []
    tables = getattr(found, "tables", None) or []
    rows_out: list[list[str]] = []
    for table in tables:
        try:
            raw_rows = table.extract() or []
        except Exception:
            continue
        cleaned_rows: list[list[str]] = []
        for raw_row in raw_rows:
            cells = [clean_text(cell) for cell in (raw_row or [])]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                cleaned_rows.append(cells)
        if _valid_table_group(cleaned_rows):
            if rows_out:
                rows_out.append([])
            rows_out.extend(cleaned_rows)
    return rows_out


def _rows_are_table_like(rows: list[list[str]]) -> bool:
    return bool(_select_table_rows(rows))


def _cluster_words_to_rows(words: list[dict[str, Any]]) -> list[list[str]]:
    if not _extract_tables_enabled():
        return []
    filtered = []
    for word in words:
        text = clean_text(str(word.get("text") or ""))
        if not text:
            continue
        try:
            conf = float(word.get("conf", 0))
        except Exception:
            conf = 0
        if conf < -0.5:
            continue
        left = float(word.get("left", 0))
        top = float(word.get("top", 0))
        width = max(1.0, float(word.get("width", 1)))
        height = max(1.0, float(word.get("height", 1)))
        filtered.append({"text": text, "left": left, "top": top, "right": left + width, "height": height, "center_y": top + height / 2})
    if not filtered:
        return []

    typical_height = median([word["height"] for word in filtered]) if filtered else 12
    row_tolerance = max(6.0, typical_height * 0.70)
    row_groups: list[list[dict[str, Any]]] = []
    for word in sorted(filtered, key=lambda item: (item["center_y"], item["left"])):
        placed = False
        for group in row_groups:
            group_center = median([item["center_y"] for item in group])
            if abs(word["center_y"] - group_center) <= row_tolerance:
                group.append(word)
                placed = True
                break
        if not placed:
            row_groups.append([word])

    rows: list[list[str]] = []
    for group in row_groups:
        ordered = sorted(group, key=lambda item: item["left"])
        widths = [item["right"] - item["left"] for item in ordered]
        typical_width = median(widths) if widths else 20
        gap_threshold = max(38.0, typical_width * 1.75, typical_height * 2.8)
        cells: list[str] = []
        current: list[str] = []
        previous_right: float | None = None
        for item in ordered:
            gap = item["left"] - previous_right if previous_right is not None else 0
            if previous_right is not None and gap > gap_threshold and current:
                cells.append(clean_text(" ".join(current)))
                current = []
            current.append(item["text"])
            previous_right = item["right"]
        if current:
            cells.append(clean_text(" ".join(current)))
        if cells:
            rows.append(cells)
    return _select_table_rows(rows)

def _ocr_image(image: Any, label: str) -> OcrPage:
    pytesseract = _require_tesseract()
    try:
        text = pytesseract.image_to_string(image, lang=_ocr_lang(), config=os.getenv("APP_OCR_TEXT_CONFIG", "--psm 6"))
        data = pytesseract.image_to_data(image, lang=_ocr_lang(), output_type=pytesseract.Output.DICT)
    except Exception as exc:
        name = exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"OCR failed: {name}. Check Tesseract installation and APP_OCR_LANG/APP_TESSERACT_CMD.",
        ) from exc
    words: list[dict[str, Any]] = []
    count = len(data.get("text", []))
    for index in range(count):
        words.append(
            {
                "text": data.get("text", [""])[index],
                "conf": data.get("conf", [0])[index],
                "left": data.get("left", [0])[index],
                "top": data.get("top", [0])[index],
                "width": data.get("width", [0])[index],
                "height": data.get("height", [0])[index],
            }
        )
    return OcrPage(label=label, text=clean_text(text), rows=_cluster_words_to_rows(words), method="tesseract")


def _page_words_from_fitz(page: Any) -> list[dict[str, Any]]:
    words = []
    for raw in page.get_text("words") or []:
        x0, y0, x1, y1, text = raw[:5]
        words.append(
            {
                "text": text,
                "conf": 1,
                "left": x0,
                "top": y0,
                "width": max(1, x1 - x0),
                "height": max(1, y1 - y0),
            }
        )
    return words


def _extract_pdf_pages(path: Path) -> list[OcrPage]:
    fitz = _require_fitz()
    Image = _require_pil()
    pages: list[OcrPage] = []
    force_ocr = os.getenv("APP_OCR_FORCE", "0") == "1"
    min_chars = int(os.getenv("APP_OCR_TEXT_LAYER_MIN_CHARS", str(TEXT_LAYER_MIN_CHARS)))
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PDF cannot be opened for OCR") from exc
    try:
        for index, page in enumerate(document, start=1):
            label = f"Страница {index}"
            text_layer = clean_text(page.get_text("text") or "")
            if text_layer and not force_ocr and len(text_layer) >= min_chars:
                pdf_tables = _extract_fitz_tables(page)
                pages.append(
                    OcrPage(
                        label=label,
                        text=text_layer,
                        rows=pdf_tables or _cluster_words_to_rows(_page_words_from_fitz(page)),
                        method="pdf_text_layer",
                    )
                )
                continue
            matrix = fitz.Matrix(_pdf_zoom(), _pdf_zoom())
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            pages.append(_ocr_image(image, label))
    finally:
        document.close()
    return pages


def _extract_image_pages(path: Path) -> list[OcrPage]:
    Image = _require_pil()
    try:
        with Image.open(path) as image:
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            return [_ocr_image(image, "Изображение 1")]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image cannot be opened for OCR") from exc


def process_ocr_upload(raw_path: Path, output_root: Path, original_name: str | None = None) -> OcrProcessResult:
    suffix = raw_path.suffix.lower()
    if suffix not in OCR_INPUT_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file is not an OCR input")
    if suffix == ".pdf":
        pages = _extract_pdf_pages(raw_path)
    else:
        pages = _extract_image_pages(raw_path)

    pages = [page for page in pages if clean_text(page.text) or page.rows]
    if not pages:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="OCR produced no readable text")

    output_root.mkdir(parents=True, exist_ok=True)
    stem = safe_slug(Path(original_name or raw_path.name).stem, fallback=raw_path.stem, max_len=90)
    title = f"OCR: {Path(original_name or raw_path.name).stem}"
    docx_path = unique_child_path(output_root, f"{stem}_ocr.docx")
    _write_minimal_docx(docx_path, title=title, source_name=original_name or raw_path.name, pages=pages)

    generated = [OcrGeneratedFile(kind="document", path=docx_path)]
    tables = [OcrTable(sheet_title=page.label, rows=page.rows) for page in pages if page.rows]
    if tables:
        xlsx_path = unique_child_path(output_root, f"{stem}_tables.xlsx")
        _write_minimal_xlsx(xlsx_path, title=title, tables=tables)
        generated.append(OcrGeneratedFile(kind="tables", path=xlsx_path, rows=sum(len(table.rows) for table in tables)))
    return OcrProcessResult(pages=pages, generated_files=generated)

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, List, Tuple
from xml.etree import ElementTree as ET


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

DOMAIN_RULES = [
    ("housing", ["общежит", "заселен", "проживан", "hostel"]),
    ("tuition", ["прейскурант", "стоимост", "оплат", "кредит"]),
    ("scores", ["порогов", "проходн", "балл", "ент", "ұбт"]),
    ("timeline", ["хронолог", "календар", "этап", "срок"]),
    ("benefits", ["льгот", "алтын", "дарын", "iqanat", "грант"]),
    ("documents", ["перечень документов", "документ", "заявлен", "справк", "копия"]),
    ("master", ["магистрат", "комплексное тестирование", "кт"]),
    ("phd", ["докторант", "phd"]),
    ("serpin", ["серпін", "серпин"]),
    ("programs", ["образовательн", "программ", "кафедр", "специальност", "оп "]),
    ("rules", ["правила приема", "правила приёма", "положение"]),
    ("contacts", ["контакт", "телефон", "адрес", "email", "e-mail"]),
    ("university_info", ["университет", "сагинов", "караганд", "абылкас"]),
]


def clean_text(text: str) -> str:
    if text is None:
        return ""
    text = text.replace("\xa0", " ")
    text = text.replace("\u200b", "")
    text = restore_missing_separators(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def restore_missing_separators(text: str) -> str:
    if not text:
        return ""
    restored = text
    restored = re.sub(r"(?<=\d)(?=[A-ZА-ЯЁІЇҰҚҒҮӨҺ][a-zа-яёііїңғүұқөһ])", ". ", restored)
    restored = re.sub(r"(?<=[a-zа-яёііїңғүұқөһ])(?=[A-ZА-ЯЁІЇҰҚҒҮӨҺ][a-zа-яёііїңғүұқөһ])", " ", restored)
    restored = re.sub(
        r"([A-ZА-ЯЁІЇҰҚҒҮӨҺ]{2,})([A-ZА-ЯЁІЇҰҚҒҮӨҺ][a-zа-яёііїңғүұқөһ])",
        r"\1 \2",
        restored,
    )
    return restored


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-.а-яА-ЯёЁіІұҰғҒүҮқҚөӨһҺ ]+", "_", name)
    name = re.sub(r"\s+", "_", name).strip("._")
    return name[:180] or "file"


def classify_domain(title: str, source_name: str, body: str = "") -> str:
    hay = f"{title}\n{source_name}\n{body[:4000]}".lower()
    scores = Counter()
    for domain, keywords in DOMAIN_RULES:
        for keyword in keywords:
            if keyword in hay:
                scores[domain] += 1
    return scores.most_common(1)[0][0] if scores else "general"


def docx_block_items(path: Path) -> Tuple[List[str], List[List[List[str]]]]:
    paragraphs: List[str] = []
    tables: List[List[List[str]]] = []

    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(f"{W_NS}body")
    if body is None:
        return paragraphs, tables

    for child in body:
        if child.tag == f"{W_NS}p":
            texts = [node.text for node in child.iter(f"{W_NS}t") if node.text]
            line = clean_text("".join(texts))
            if line:
                paragraphs.append(line)
            continue

        if child.tag != f"{W_NS}tbl":
            continue

        table_rows: List[List[str]] = []
        for row in child.findall(f"{W_NS}tr"):
            values: List[str] = []
            for cell in row.findall(f"{W_NS}tc"):
                parts = [node.text for node in cell.iter(f"{W_NS}t") if node.text]
                value = clean_text("".join(parts))
                values.append(value if value else "∅")
            if values:
                table_rows.append(values)
        if table_rows:
            tables.append(table_rows)

    return paragraphs, tables


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(xml_bytes)
    values: list[str] = []
    for item in root.findall("main:si", XML_NS):
        parts = [node.text for node in item.findall(".//main:t", XML_NS) if node.text]
        values.append(clean_text("".join(parts)))
    return values


def _read_sheet_names(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    rel_targets = {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in rels_root.findall("pkg_rel:Relationship", XML_NS)
    }

    names: dict[str, str] = {}
    for sheet in workbook_root.findall("main:sheets/main:sheet", XML_NS):
        rel_id = sheet.attrib.get(f"{{{XML_NS['rel']}}}id", "")
        target = rel_targets.get(rel_id, "")
        if target:
            target = target.removeprefix("/").removeprefix("xl/")
            names[target] = sheet.attrib.get("name", "Sheet")
    return names


def _column_letter(cell_ref: str) -> str:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    return match.group(1) if match else ""


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    raw = "".join(node.text or "" for node in cell.findall("main:v", XML_NS))
    if cell_type == "s":
        if raw.isdigit() and int(raw) < len(shared_strings):
            return shared_strings[int(raw)]
        return ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", XML_NS))
    return raw


def _read_sheet_rows(archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(f"xl/{sheet_path}"))
    rows: list[list[str]] = []
    for row in root.findall(".//main:sheetData/main:row", XML_NS):
        values_by_col: dict[str, str] = {}
        for cell in row.findall("main:c", XML_NS):
            col = _column_letter(cell.attrib.get("r", ""))
            raw_value = _xlsx_cell_value(cell, shared_strings)
            values_by_col[col] = clean_text(raw_value) if raw_value else "∅"
        ordered = [value for _, value in sorted(values_by_col.items())]
        if any(value != "∅" for value in ordered):
            rows.append(ordered)
    return rows


def xlsx_sheets(path: Path) -> List[Tuple[str, List[List[str]]]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_names = _read_sheet_names(archive)
        result: List[Tuple[str, List[List[str]]]] = []
        for sheet_path, sheet_name in sheet_names.items():
            rows = _read_sheet_rows(archive, sheet_path, shared_strings)
            if rows:
                result.append((sheet_name, rows))
        return result


def render_docx(title: str, source_file: str, domain: str, paragraphs: List[str], tables: List[List[List[str]]]) -> str:
    parts = [f"META_TITLE: {title}", f"META_SOURCE_FILE: {source_file}", f"META_DOMAIN: {domain}", ""]
    if paragraphs:
        parts.append("TEXT:")
        parts.extend(paragraphs)
        parts.append("")
    for index, table in enumerate(tables, start=1):
        parts.append(f"TABLE_{index}:")
        for row in table:
            parts.append("ROW: " + " | ".join(row))
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def render_xlsx(title: str, source_file: str, domain: str, sheet_title: str, rows: List[List[str]]) -> str:
    parts = [
        f"META_TITLE: {title}",
        f"META_SOURCE_FILE: {source_file}",
        f"META_DOMAIN: {domain}",
        f"META_SHEET: {sheet_title}",
        "",
        "TABLE_1:",
    ]
    for row in rows:
        parts.append("ROW: " + " | ".join(row))
    return "\n".join(parts).strip() + "\n"


def normalize_input_tree(input_dir: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue

        ext = path.suffix.lower()
        rel = str(path.relative_to(input_dir))
        stem = path.stem

        if ext == ".docx":
            paragraphs, tables = docx_block_items(path)
            body_preview = "\n".join(paragraphs[:25])
            domain = classify_domain(stem, rel, body_preview)
            content = render_docx(stem, rel, domain, paragraphs, tables)
            out_name = sanitize_filename(stem) + ".txt"
            out_path = output_dir / domain / out_name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
            manifest.append(
                {
                    "source_file": rel,
                    "output_file": str(out_path.relative_to(output_dir)),
                    "domain": domain,
                    "type": "docx",
                    "paragraphs": len(paragraphs),
                    "tables": len(tables),
                }
            )
            continue

        if ext == ".xlsx":
            for sheet_title, rows in xlsx_sheets(path):
                preview = "\n".join(" | ".join(row) for row in rows[:8])
                domain = classify_domain(f"{stem} {sheet_title}", rel, preview)
                content = render_xlsx(stem, rel, domain, sheet_title, rows)
                out_name = sanitize_filename(f"{stem}__{sheet_title}") + ".txt"
                out_path = output_dir / domain / out_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                manifest.append(
                    {
                        "source_file": rel,
                        "output_file": str(out_path.relative_to(output_dir)),
                        "domain": domain,
                        "type": "xlsx",
                        "sheet": sheet_title,
                        "rows": len(rows),
                    }
                )

    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"files": len(manifest), "domains": Counter(item["domain"] for item in manifest)}


def parse_source_content(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext == ".docx":
        paragraphs, tables = docx_block_items(path)
        return {
            "source_type": "docx",
            "title": path.stem,
            "paragraphs": paragraphs,
            "tables": tables,
        }
    if ext == ".xlsx":
        sheets = xlsx_sheets(path)
        return {
            "source_type": "xlsx",
            "title": path.stem,
            "sheets": [{"sheet_title": sheet_title, "rows": rows} for sheet_title, rows in sheets],
        }
    if ext == ".txt":
        return {
            "source_type": "txt",
            "title": path.stem,
            "text": clean_text(path.read_text(encoding="utf-8")),
        }
    return {
        "source_type": ext.lstrip(".") or "unknown",
        "title": path.stem,
        "text": "",
    }

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.config import (
    EMBED_MODEL_DIR,
    GEN_MODEL_DIR,
    GUARD_MODEL_DIR,
    MODELS_DIR,
    RAG_CHUNKS_PATH,
    STORAGE_DIR,
)
from core.generation import generate_from_messages

TRAINING_DIR = Path(STORAGE_DIR) / "training"
DATASETS_DIR = TRAINING_DIR / "datasets"
EXPORTS_DIR = TRAINING_DIR / "exports"
JOBS_PATH = TRAINING_DIR / "jobs.json"
RUNS_DIR = TRAINING_DIR / "runs"
_RUNNING_PROCS: dict[str, subprocess.Popen] = {}
_JOB_LOCK = threading.Lock()

_ALLOWED_STATUSES = {"draft", "approved", "rejected"}
_ALLOWED_SPLITS = {"train", "validation", "test"}


def _now() -> int:
    return int(time.time())


def _ensure_dirs() -> None:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _slug(value: str, fallback: str = "dataset") -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^0-9a-zа-яёәғқңөұүһі_-]+", "_", raw, flags=re.IGNORECASE)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return raw[:80] or fallback


def _dataset_path(dataset_id: str) -> Path:
    _ensure_dirs()
    safe = _slug(dataset_id, "dataset")
    return DATASETS_DIR / f"{safe}.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_jsonl(path: str | Path, limit: int = 2000) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def _normalize_item(raw: dict[str, Any], *, default_lang: str = "ru") -> dict[str, Any]:
    item = dict(raw or {})
    item_id = str(item.get("item_id") or item.get("id") or uuid.uuid4().hex[:12])
    question = str(item.get("question") or item.get("instruction") or item.get("prompt") or "").strip()
    answer = str(item.get("answer") or item.get("output") or item.get("response") or "").strip()
    context = str(item.get("context") or item.get("input") or "").strip()
    status = str(item.get("status") or "draft").strip().lower()
    split = str(item.get("split") or "train").strip().lower()
    if status not in _ALLOWED_STATUSES:
        status = "draft"
    if split not in _ALLOWED_SPLITS:
        split = "train"
    return {
        "item_id": item_id,
        "question": question,
        "answer": answer,
        "context": context,
        "source_id": str(item.get("source_id") or "").strip(),
        "chunk_id": str(item.get("chunk_id") or "").strip(),
        "domain": str(item.get("domain") or "").strip(),
        "schema": str(item.get("schema") or "").strip(),
        "language": str(item.get("language") or default_lang or "ru").strip() or "ru",
        "status": status,
        "split": split,
        "tags": [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()],
        "rating": int(item.get("rating") or 0),
        "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        "created_at": int(item.get("created_at") or _now()),
        "updated_at": int(item.get("updated_at") or _now()),
    }


def _dataset_stats(items: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"total": len(items), "draft": 0, "approved": 0, "rejected": 0, "train": 0, "validation": 0, "test": 0}
    for item in items:
        status = str(item.get("status") or "draft")
        split = str(item.get("split") or "train")
        if status in stats:
            stats[status] += 1
        if split in stats:
            stats[split] += 1
    return stats


def _dataset_summary(dataset: dict[str, Any]) -> dict[str, Any]:
    items = list(dataset.get("items") or [])
    return {k: v for k, v in dataset.items() if k != "items"} | {"stats": _dataset_stats(items)}


def list_datasets() -> list[dict[str, Any]]:
    _ensure_dirs()
    datasets = []
    for path in sorted(DATASETS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        payload = _read_json(path, {})
        if isinstance(payload, dict) and payload.get("dataset_id"):
            datasets.append(_dataset_summary(payload))
    return datasets


def create_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_dirs()
    name = str(payload.get("name") or "Training dataset").strip()
    requested_id = str(payload.get("dataset_id") or "").strip()
    base_id = _slug(requested_id or name, "dataset")
    dataset_id = base_id
    suffix = 2
    while _dataset_path(dataset_id).exists():
        dataset_id = f"{base_id}_{suffix}"
        suffix += 1
    now = _now()
    dataset = {
        "dataset_id": dataset_id,
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "task_type": str(payload.get("task_type") or "chat_qa").strip() or "chat_qa",
        "target_model": str(payload.get("target_model") or "").strip(),
        "dataset_format": str(payload.get("dataset_format") or "chatml_jsonl").strip() or "chatml_jsonl",
        "language": str(payload.get("language") or "ru").strip() or "ru",
        "created_at": now,
        "updated_at": now,
        "items": [],
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
    _write_json(_dataset_path(dataset_id), dataset)
    return _dataset_summary(dataset)


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    path = _dataset_path(dataset_id)
    payload = _read_json(path, None)
    return payload if isinstance(payload, dict) and payload.get("dataset_id") else None


def save_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    dataset["updated_at"] = _now()
    dataset["items"] = [_normalize_item(item, default_lang=dataset.get("language") or "ru") for item in dataset.get("items") or []]
    _write_json(_dataset_path(dataset["dataset_id"]), dataset)
    return dataset


def add_dataset_items(dataset_id: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    existing = list(dataset.get("items") or [])
    seen_keys = {
        (str(item.get("question") or "").strip().lower(), str(item.get("answer") or "").strip().lower())
        for item in existing
    }
    added = []
    for raw in items:
        item = _normalize_item(raw, default_lang=dataset.get("language") or "ru")
        if not item["question"] or not item["answer"]:
            continue
        key = (item["question"].lower(), item["answer"].lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        added.append(item)
    dataset["items"] = existing + added
    save_dataset(dataset)
    return {"dataset": _dataset_summary(dataset), "added": len(added), "items": dataset["items"]}


def update_dataset_item(dataset_id: str, item_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    changed = None
    next_items = []
    for item in dataset.get("items") or []:
        if str(item.get("item_id")) == str(item_id):
            merged = dict(item)
            merged.update(patch or {})
            merged["item_id"] = str(item_id)
            merged["updated_at"] = _now()
            changed = _normalize_item(merged, default_lang=dataset.get("language") or "ru")
            next_items.append(changed)
        else:
            next_items.append(item)
    if changed is None:
        return None
    dataset["items"] = next_items
    save_dataset(dataset)
    return {"dataset": _dataset_summary(dataset), "item": changed, "items": dataset["items"]}


def delete_dataset_item(dataset_id: str, item_id: str) -> dict[str, Any] | None:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    before = len(dataset.get("items") or [])
    dataset["items"] = [item for item in dataset.get("items") or [] if str(item.get("item_id")) != str(item_id)]
    if len(dataset["items"]) == before:
        return None
    save_dataset(dataset)
    return {"dataset": _dataset_summary(dataset), "items": dataset["items"]}


def available_training_models() -> list[dict[str, Any]]:
    configured = [
        {"model_id": "generation", "label": Path(GEN_MODEL_DIR).name or "generation", "path": str(GEN_MODEL_DIR), "role": "generation"},
        {"model_id": "embedding", "label": Path(EMBED_MODEL_DIR).name or "embedding", "path": str(EMBED_MODEL_DIR), "role": "embedding"},
        {"model_id": "guard", "label": Path(GUARD_MODEL_DIR).name or "guard", "path": str(GUARD_MODEL_DIR), "role": "guard"},
    ]
    seen = set()
    models: list[dict[str, Any]] = []
    for item in configured:
        key = item["path"]
        if key in seen:
            continue
        seen.add(key)
        exists = Path(item["path"]).exists()
        models.append({
            **item,
            "exists": exists,
            "trainable": item["role"] in {"generation", "guard"},
            "recommended_method": "QLoRA/LoRA SFT" if item["role"] in {"generation", "guard"} else "embedding fine-tune separately",
        })
    models_root = Path(MODELS_DIR)
    if models_root.exists():
        for child in sorted(models_root.iterdir(), key=lambda x: x.name.lower()):
            if not child.is_dir() and child.suffix.lower() != ".gguf":
                continue
            key = str(child)
            if key in seen:
                continue
            seen.add(key)
            lname = child.name.lower()
            is_embedding = "embed" in lname or "e5" in lname
            models.append({
                "model_id": child.name,
                "label": child.name,
                "path": str(child),
                "role": "embedding" if is_embedding else "generation",
                "exists": True,
                "trainable": not child.suffix.lower() == ".gguf" and not is_embedding,
                "recommended_method": "LoRA/QLoRA SFT" if not is_embedding and child.suffix.lower() != ".gguf" else "export dataset first; GGUF is inference artifact",
            })
    return models


def _chunk_matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source_id = str(filters.get("source_id") or "").strip()
    domain = str(filters.get("domain") or "").strip()
    schema = str(filters.get("schema") or "").strip()
    lang = str(filters.get("language") or "").strip()
    if source_id and metadata.get("source_id") != source_id and row.get("source_file") != source_id:
        return False
    if domain and domain not in {str(row.get("domain") or ""), str(metadata.get("domain") or ""), str(metadata.get("class_name") or "")}:
        return False
    if schema and schema not in {str(metadata.get("schema") or ""), str(metadata.get("entry_type") or "")}:
        return False
    if lang and lang not in {str(metadata.get("language") or ""), str(row.get("language") or "")}:
        return False
    return True


def _extract_qa_pairs_from_text(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    pattern = re.compile(
        r"(?:^|\n)\s*-?\s*(?:Вопрос|Сұрақ|Question)\s*[-:–—]\s*(?P<q>.+?)\s*\n\s*(?:Ответ|Жауап|Answer)\s*[-:–—]\s*(?P<a>.+?)(?=\n\s*-?\s*(?:Вопрос|Сұрақ|Question)\s*[-:–—]|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text or ""):
        q = re.sub(r"\s+", " ", match.group("q")).strip()
        a = re.sub(r"\s+", " ", match.group("a")).strip()
        if q and a:
            if q[-1] != "?":
                q = q.rstrip(".! ") + "?"
            pairs.append((q, a))
    return pairs


def _fallback_question(title: str, domain: str, lang: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    if lang == "kk":
        return f"{title or domain or 'Бұл бөлім'} туралы не айтылған?"
    if lang == "en":
        return f"What is stated about {title or domain or 'this section'}?"
    return f"Что указано в разделе «{title or domain or 'этот материал'}»?"


def _parse_llm_candidates(generated: str) -> list[dict[str, str]]:
    text = (generated or "").strip()
    if not text:
        return []
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, list):
                return [
                    {"question": str(item.get("question") or "").strip(), "answer": str(item.get("answer") or "").strip()}
                    for item in payload
                    if isinstance(item, dict)
                ]
        except Exception:
            pass
    pairs: list[dict[str, str]] = []
    chunks = re.split(r"\n\s*\n+", text)
    for block in chunks:
        q_match = re.search(r"(?:question|вопрос|сұрақ)\s*[:\-–—]\s*(.+)", block, flags=re.IGNORECASE)
        a_match = re.search(r"(?:answer|ответ|жауап)\s*[:\-–—]\s*(.+)", block, flags=re.IGNORECASE | re.DOTALL)
        if q_match and a_match:
            pairs.append({"question": q_match.group(1).strip(), "answer": a_match.group(1).strip()})
    return pairs


def suggest_dataset_items(filters: dict[str, Any]) -> list[dict[str, Any]]:
    count = max(1, min(int(filters.get("count") or 6), 24))
    lang = str(filters.get("language") or "ru").strip() or "ru"
    use_llm = bool(filters.get("use_llm", True))
    rows = [row for row in _read_jsonl(RAG_CHUNKS_PATH, limit=3000) if _chunk_matches(row, filters)]
    candidates: list[dict[str, Any]] = []
    seen = set()

    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        list_items = metadata.get("list_items") if isinstance(metadata.get("list_items"), list) else []
        sources = list_items or [row.get("text") or ""]
        for source_text in sources:
            for question, answer in _extract_qa_pairs_from_text(str(source_text or "")):
                key = (question.lower(), answer.lower())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(_normalize_item({
                    "question": question,
                    "answer": answer,
                    "context": str(source_text or row.get("text") or "")[:4000],
                    "source_id": metadata.get("source_id") or row.get("source_file") or "",
                    "chunk_id": row.get("chunk_id") or "",
                    "domain": row.get("domain") or metadata.get("domain") or metadata.get("class_name") or "",
                    "schema": metadata.get("schema") or metadata.get("entry_type") or "",
                    "language": lang,
                    "metadata": {"suggested_by": "qa_extraction", "title": row.get("title")},
                }, default_lang=lang))
                if len(candidates) >= count:
                    return candidates

    if use_llm and rows:
        selected = rows[: min(4, len(rows))]
        context = "\n\n---\n\n".join(str(row.get("text") or "")[:3000] for row in selected)
        lang_label = {"kk": "Kazakh", "en": "English"}.get(lang, "Russian")
        messages = [
            {"role": "system", "content": "You create supervised fine-tuning dataset items for a grounded university RAG assistant."},
            {
                "role": "user",
                "content": (
                    f"Create exactly {count} diverse applicant-style QA pairs in {lang_label}.\n"
                    "Use only the context. Answers must be factual and short enough for SFT.\n"
                    "Return JSON array only: [{\"question\":\"...\",\"answer\":\"...\"}].\n\n"
                    f"Context:\n{context}"
                ),
            },
        ]
        try:
            generated = generate_from_messages(messages, max_new_tokens=900, ctx_texts=[context])
        except Exception:
            generated = ""
        for pair in _parse_llm_candidates(generated):
            question = re.sub(r"\s+", " ", pair.get("question") or "").strip()
            answer = re.sub(r"\s+", " ", pair.get("answer") or "").strip()
            if not question or not answer:
                continue
            if question[-1] != "?":
                question = question.rstrip(".! ") + "?"
            key = (question.lower(), answer.lower())
            if key in seen:
                continue
            seen.add(key)
            first = selected[0]
            metadata = first.get("metadata") if isinstance(first.get("metadata"), dict) else {}
            candidates.append(_normalize_item({
                "question": question,
                "answer": answer,
                "context": context[:4000],
                "source_id": metadata.get("source_id") or first.get("source_file") or "",
                "chunk_id": first.get("chunk_id") or "",
                "domain": first.get("domain") or metadata.get("domain") or metadata.get("class_name") or "",
                "schema": metadata.get("schema") or metadata.get("entry_type") or "",
                "language": lang,
                "metadata": {"suggested_by": "current_agent", "source_chunks": [row.get("chunk_id") for row in selected]},
            }, default_lang=lang))
            if len(candidates) >= count:
                return candidates

    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
        if not text:
            continue
        answer = text[:700].rstrip()
        question = _fallback_question(str(row.get("title") or ""), str(row.get("domain") or ""), lang)
        key = (question.lower(), answer.lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_normalize_item({
            "question": question,
            "answer": answer,
            "context": text[:4000],
            "source_id": metadata.get("source_id") or row.get("source_file") or "",
            "chunk_id": row.get("chunk_id") or "",
            "domain": row.get("domain") or metadata.get("domain") or metadata.get("class_name") or "",
            "schema": metadata.get("schema") or metadata.get("entry_type") or "",
            "language": lang,
            "metadata": {"suggested_by": "fallback_chunk", "title": row.get("title")},
        }, default_lang=lang))
        if len(candidates) >= count:
            break
    return candidates[:count]


def _format_item(item: dict[str, Any], dataset_format: str) -> dict[str, Any]:
    question = str(item.get("question") or "").strip()
    answer = str(item.get("answer") or "").strip()
    context = str(item.get("context") or "").strip()
    if dataset_format == "alpaca_jsonl":
        return {"instruction": question, "input": context, "output": answer}
    if dataset_format == "plain_pairs_jsonl":
        return {"question": question, "answer": answer, "context": context}
    return {
        "messages": [
            {"role": "system", "content": "Answer strictly using the provided university context. If context is insufficient, say that there is not enough information."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion:\n{question}" if context else question},
            {"role": "assistant", "content": answer},
        ]
    }


def export_dataset(dataset_id: str, *, approved_only: bool = True, dataset_format: str | None = None) -> Path | None:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return None
    fmt = dataset_format or dataset.get("dataset_format") or "chatml_jsonl"
    items = dataset.get("items") or []
    if approved_only:
        items = [item for item in items if item.get("status") == "approved"]
    else:
        items = [item for item in items if item.get("status") != "rejected"]
    export_path = EXPORTS_DIR / f"{dataset_id}_{fmt}.jsonl"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with export_path.open("w", encoding="utf-8") as handle:
        for item in items:
            row = _format_item(item, fmt)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return export_path



def _read_jobs() -> list[dict[str, Any]]:
    _ensure_dirs()
    jobs = _read_json(JOBS_PATH, [])
    return jobs if isinstance(jobs, list) else []


def _write_jobs(jobs: list[dict[str, Any]]) -> None:
    _write_json(JOBS_PATH, jobs[:100])


def _update_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _JOB_LOCK:
        jobs = _read_jobs()
        updated = None
        for job in jobs:
            if str(job.get("job_id")) == str(job_id):
                job.update(patch or {})
                job["updated_at"] = _now()
                updated = job
                break
        _write_jobs(jobs)
        return updated


def _append_job_log(job_id: str, line: str, *, max_lines: int = 120) -> None:
    text = str(line or "").rstrip()
    if not text:
        return
    with _JOB_LOCK:
        jobs = _read_jobs()
        for job in jobs:
            if str(job.get("job_id")) == str(job_id):
                tail = list(job.get("log_tail") or [])
                tail.append(text)
                job["log_tail"] = tail[-max_lines:]
                if text.startswith("TRAINING_PROGRESS "):
                    try:
                        event = json.loads(text.split(" ", 1)[1])
                        job["last_progress_event"] = event
                        if isinstance(event, dict):
                            progress = event.get("progress")
                            if isinstance(progress, (int, float)):
                                job["progress"] = max(0, min(float(progress), 1))
                            if event.get("event") == "train_end":
                                job["progress"] = 1
                    except Exception:
                        pass
                job["updated_at"] = _now()
                break
        _write_jobs(jobs)


def _is_pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=3)
            return str(pid) in (result.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _reader_thread(job_id: str, proc: subprocess.Popen) -> None:
    try:
        if proc.stdout:
            for line in proc.stdout:
                _append_job_log(job_id, line)
        returncode = proc.wait()
        status = "finished" if returncode == 0 else "failed"
        patch = {"status": status, "returncode": returncode, "finished_at": _now()}
        if returncode == 0:
            patch["progress"] = 1
        _update_job(job_id, patch)
    except Exception as exc:
        _append_job_log(job_id, f"reader error: {exc}")
        _update_job(job_id, {"status": "failed", "returncode": -1, "finished_at": _now()})
    finally:
        _RUNNING_PROCS.pop(job_id, None)


def refresh_training_job(job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("status") or "") == "running":
        pid = int(job.get("pid") or 0)
        proc = _RUNNING_PROCS.get(str(job.get("job_id")))
        if proc and proc.poll() is not None:
            job["status"] = "finished" if proc.returncode == 0 else "failed"
            job["returncode"] = proc.returncode
            job["finished_at"] = job.get("finished_at") or _now()
            if proc.returncode == 0:
                job["progress"] = 1
        elif pid and not _is_pid_alive(pid):
            job["status"] = "unknown"
            job["note"] = "Процесс не найден. Сервер мог быть перезапущен или процесс завершился вне панели."
            job["finished_at"] = job.get("finished_at") or _now()
    return job


def start_training_job(job_id: str) -> dict[str, Any] | None:
    jobs = _read_jobs()
    job = next((item for item in jobs if str(item.get("job_id")) == str(job_id)), None)
    if not job:
        return None
    if str(job.get("status")) == "running" and int(job.get("pid") or 0) and _is_pid_alive(int(job.get("pid") or 0)):
        return job
    if str(job.get("model_path") or "").lower().endswith(".gguf"):
        job.update({"status": "failed", "note": "GGUF напрямую не обучается. Выбери HF model directory или HuggingFace model id.", "updated_at": _now()})
        _write_jobs(jobs)
        return job

    export_path = str(job.get("export_path") or "").strip()
    if not export_path or not Path(export_path).exists():
        dataset = get_dataset(str(job.get("dataset_id") or ""))
        export = export_dataset(str(job.get("dataset_id") or ""), approved_only=True, dataset_format=(dataset or {}).get("dataset_format")) if dataset else None
        export_path = str(export) if export else ""
        job["export_path"] = export_path
    if not export_path or not Path(export_path).exists():
        job.update({"status": "failed", "note": "Нет export JSONL. Сначала добавь и approve записи датасета.", "updated_at": _now()})
        _write_jobs(jobs)
        return job

    assistant_dir = Path(__file__).resolve().parents[1]
    output_dir = RUNS_DIR / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    method = str(job.get("method") or "qlora_sft")
    cmd = [
        sys.executable,
        str(assistant_dir / "train_sft.py"),
        "--model", str(job.get("model_path") or job.get("model_id") or ""),
        "--dataset", export_path,
        "--method", method,
        "--output", str(output_dir),
        "--overwrite",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(assistant_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        job.update({"status": "failed", "note": f"Не удалось запустить обучение: {exc}", "updated_at": _now()})
        _write_jobs(jobs)
        return job

    _RUNNING_PROCS[str(job_id)] = proc
    job.update({
        "status": "running",
        "pid": proc.pid,
        "started_at": _now(),
        "updated_at": _now(),
        "returncode": None,
        "progress": 0,
        "output_dir": str(output_dir),
        "command": " ".join(cmd),
        "log_tail": [],
        "note": "Обучение запущено из панели.",
    })
    _write_jobs(jobs)
    threading.Thread(target=_reader_thread, args=(str(job_id), proc), daemon=True).start()
    return job


def stop_training_job(job_id: str) -> dict[str, Any] | None:
    jobs = _read_jobs()
    job = next((item for item in jobs if str(item.get("job_id")) == str(job_id)), None)
    if not job:
        return None
    proc = _RUNNING_PROCS.get(str(job_id))
    pid = int(job.get("pid") or 0)
    try:
        if proc and proc.poll() is None:
            proc.terminate()
        elif pid:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
            else:
                os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        job["note"] = f"Ошибка остановки процесса: {exc}"
    job.update({"status": "stopped", "stopped_at": _now(), "updated_at": _now()})
    _write_jobs(jobs)
    return job

def create_training_job(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_dirs()
    jobs = _read_jobs()
    job_id = uuid.uuid4().hex[:12]
    dataset_id = str(payload.get("dataset_id") or "").strip()
    dataset = get_dataset(dataset_id) if dataset_id else None
    model_id = str(payload.get("model_id") or payload.get("target_model") or (dataset or {}).get("target_model") or "").strip()
    selected_model = next((item for item in available_training_models() if str(item.get("model_id")) == model_id), None)
    model_path = str((selected_model or {}).get("path") or model_id).strip()
    method = str(payload.get("method") or "qlora_sft").strip()
    export_path = export_dataset(dataset_id, approved_only=True, dataset_format=(dataset or {}).get("dataset_format")) if dataset else None
    job = {
        "job_id": job_id,
        "dataset_id": dataset_id,
        "model_id": model_id,
        "model_path": model_path,
        "method": method,
        "status": "planned",
        "created_at": _now(),
        "export_path": str(export_path) if export_path else "",
        "command_hint": (
            f"python train_sft.py --model {model_path or '<model_dir_or_hf_id>'} "
            f"--dataset {export_path or '<dataset.jsonl>'} "
            f"--method {method} --output storage/training/runs/{job_id}"
        ),
        "note": "Панель создает датасет и команду запуска. LoRA/QLoRA запускается через assistant/train_sft.py, чтобы не блокировать FastAPI-панель.",
    }
    jobs.insert(0, job)
    _write_jobs(jobs)
    return job


def list_training_jobs() -> list[dict[str, Any]]:
    jobs = [refresh_training_job(dict(job)) for job in _read_jobs()]
    _write_jobs(jobs)
    return jobs

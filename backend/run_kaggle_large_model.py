import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from blingfire import text_to_sentences
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from core.data_ingest import rebuild_processed_data


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class QdrantIndex:
    client: QdrantClient
    collection_name: str


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML config must contain a mapping: {path}")
    return payload


def get_cfg(cfg: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = cfg
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def resolve_path(raw: str | None, default: Path) -> str:
    if not raw:
        return str(default)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return str(candidate)


def resolve_dtype(name: str | None) -> torch.dtype:
    normalized = str(name or "float16").strip().lower()
    mapping = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    return mapping.get(normalized, torch.float16)


def split_sentences(text: str) -> list[str]:
    return [item.strip() for item in text_to_sentences(text).split("\n") if item.strip()]


def token_len(text: str, tokenizer: Any, token_limit: int) -> int:
    return len(
        tokenizer.encode(
            text,
            add_special_tokens=False,
            truncation=True,
            max_length=token_limit,
        )
    )


def truncate_for_embedding(text: str, tokenizer: Any, token_limit: int) -> str:
    token_ids = tokenizer.encode(
        text,
        add_special_tokens=False,
        truncation=True,
        max_length=token_limit,
    )
    return tokenizer.decode(token_ids, skip_special_tokens=True).strip()


def pack_by_tokens(
    sentences: list[str],
    tokenizer: Any,
    target_tokens: int,
    overlap_tokens: int,
    token_limit: int,
):
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        count = token_len(sentence, tokenizer, token_limit)
        if count > token_limit:
            buffer: list[str] = []
            buffer_tokens = 0
            for word in sentence.split():
                word_len = token_len(word, tokenizer, token_limit)
                if buffer and buffer_tokens + word_len > target_tokens:
                    yield " ".join(buffer)
                    buffer = []
                    buffer_tokens = 0
                buffer.append(word)
                buffer_tokens += word_len
            if buffer:
                yield " ".join(buffer)
            continue

        if current_tokens + count <= target_tokens:
            current.append(sentence)
            current_tokens += count
            continue

        if current:
            yield " ".join(current)

        if overlap_tokens > 0 and current:
            tail: list[str] = []
            tail_tokens = 0
            for prev in reversed(current):
                prev_len = token_len(prev, tokenizer, token_limit)
                if tail_tokens + prev_len > overlap_tokens:
                    break
                tail.insert(0, prev)
                tail_tokens += prev_len
            current = tail
            current_tokens = sum(token_len(item, tokenizer, token_limit) for item in tail)
        else:
            current = []
            current_tokens = 0

        current.append(sentence)
        current_tokens += count

    if current:
        yield " ".join(current)


def chunkify_file(
    path: str,
    tokenizer: Any,
    embed_model: SentenceTransformer,
    target_tokens: int,
    overlap_tokens: int,
    token_limit: int,
    batch_size: int,
) -> list[tuple[str, np.ndarray]]:
    raw_text = Path(path).read_text(encoding="utf-8")
    segments = raw_text.split("```")
    pieces: list[str] = []
    for idx, segment in enumerate(segments):
        source_sentences = [segment] if idx % 2 == 1 else split_sentences(segment)
        for piece in pack_by_tokens(source_sentences, tokenizer, target_tokens, overlap_tokens, token_limit):
            if piece.strip():
                pieces.append(truncate_for_embedding(piece, tokenizer, token_limit))
    if not pieces:
        return []
    vectors = embed_model.encode(
        [f"passage: {piece}" for piece in pieces],
        convert_to_numpy=True,
        normalize_embeddings=True,
        batch_size=batch_size,
        show_progress_bar=False,
    ).astype(np.float32)
    return list(zip(pieces, vectors))


def build_local_index(
    data_dir: str,
    input_dir: str,
    rebuild_data: bool,
    qdrant_path: str,
    collection_name: str,
    tokenizer: Any,
    embed_model: SentenceTransformer,
    target_tokens: int,
    overlap_tokens: int,
    token_limit: int,
    batch_size: int,
) -> QdrantIndex:
    if rebuild_data:
        rebuild_processed_data(input_dir=input_dir, output_dir=data_dir)

    client = QdrantClient(path=qdrant_path)
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)

    dimension = int(embed_model.get_sentence_embedding_dimension())
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
    )

    points: list[PointStruct] = []
    point_id = 0
    for path in sorted(Path(data_dir).rglob("*.txt")):
        for piece, vector in chunkify_file(
            str(path),
            tokenizer=tokenizer,
            embed_model=embed_model,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
            token_limit=token_limit,
            batch_size=batch_size,
        ):
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector.tolist(),
                    payload={"path_to_file": str(path), "text": piece},
                )
            )
            point_id += 1

    if points:
        client.upsert(collection_name=collection_name, points=points)
    return QdrantIndex(client=client, collection_name=collection_name)


def find_local_passage(
    query: str,
    index: QdrantIndex,
    tokenizer: Any,
    embed_model: SentenceTransformer,
    top_k: int,
    threshold: float,
    token_limit: int,
) -> list[str]:
    normalized_query = truncate_for_embedding(query, tokenizer, token_limit)
    vector = embed_model.encode(
        f"query: {normalized_query}",
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)
    results = index.client.search(
        collection_name=index.collection_name,
        query_vector=vector.tolist(),
        limit=top_k,
        with_payload=True,
    )
    texts = []
    for item in results:
        if item.score < threshold:
            continue
        payload = item.payload or {}
        text = payload.get("text")
        if text:
            texts.append(str(text))
    return texts


def load_embedding_stack(cfg: dict[str, Any]) -> tuple[Any, SentenceTransformer]:
    source = str(get_cfg(cfg, "embedding", "source", default="intfloat/multilingual-e5-small"))
    device = str(get_cfg(cfg, "embedding", "device", default="cuda" if torch.cuda.is_available() else "cpu"))
    tokenizer = AutoTokenizer.from_pretrained(source)
    model = SentenceTransformer(source, device=device)
    return tokenizer, model


def load_generation_stack(cfg: dict[str, Any]) -> tuple[Any, Any]:
    token = os.getenv("TOKEN")
    source = str(get_cfg(cfg, "generation", "source", default="Qwen/Qwen2.5-7B-Instruct"))
    tokenizer_source = str(get_cfg(cfg, "generation", "tokenizer_source", default=source))
    device_map = get_cfg(cfg, "generation", "device_map", default="auto")
    dtype = resolve_dtype(get_cfg(cfg, "generation", "dtype", default="float16"))
    load_in_4bit = bool(get_cfg(cfg, "generation", "load_in_4bit", default=False))

    kwargs: dict[str, Any] = {}
    if token:
        kwargs["token"] = token
    if device_map not in {None, "", "none"}:
        kwargs["device_map"] = device_map
    if load_in_4bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype,
        )
    else:
        kwargs["dtype"] = dtype

    model = AutoModelForCausalLM.from_pretrained(source, **kwargs)
    tokenizer_kwargs = {"token": token} if token else {}
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_source, **tokenizer_kwargs)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer, model


def ensure_index(
    cfg: dict[str, Any],
    embed_tokenizer: Any,
    embed_model: SentenceTransformer,
    rebuild_data: bool,
    rebuild_index: bool,
) -> QdrantIndex:
    data_dir = resolve_path(get_cfg(cfg, "paths", "data_dir"), BASE_DIR / "data")
    input_dir = resolve_path(get_cfg(cfg, "paths", "input_data_dir"), BASE_DIR / "input_data")
    qdrant_path = resolve_path(get_cfg(cfg, "rag", "qdrant_path"), BASE_DIR / "storage" / "kaggle" / "qdrant")
    collection_name = str(get_cfg(cfg, "rag", "collection", default="kaggle_large_model_check"))
    token_limit = int(get_cfg(cfg, "rag", "token_limit", default=512))
    target_tokens = int(get_cfg(cfg, "rag", "target_tokens", default=320))
    overlap_tokens = int(get_cfg(cfg, "rag", "overlap_tokens", default=64))
    batch_size = int(get_cfg(cfg, "embedding", "batch_size", default=64))

    client = QdrantClient(path=qdrant_path)
    if not rebuild_index and client.collection_exists(collection_name):
        return QdrantIndex(client=client, collection_name=collection_name)

    return build_local_index(
        data_dir=data_dir,
        input_dir=input_dir,
        rebuild_data=rebuild_data,
        tokenizer=embed_tokenizer,
        embed_model=embed_model,
        qdrant_path=qdrant_path,
        collection_name=collection_name,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        token_limit=token_limit,
        batch_size=batch_size,
    )


def stop_token_ids(tokenizer: Any) -> list[int]:
    ids = [tokenizer.eos_token_id]
    im_end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    if im_end is not None and im_end >= 0:
        ids.append(im_end)
    return ids


def generate_answer(
    question: str,
    context_chunks: list[str],
    lang: str,
    history: list[dict[str, str]],
    cfg: dict[str, Any],
    tokenizer: Any,
    model: Any,
) -> str:
    system_prompt = str(
        get_cfg(
            cfg,
            "prompts",
            "system_prompt",
            default=(
                "You are an AI assistant for Karaganda Technical University named after Abylkas "
                "Saginov in Karaganda, Kazakhstan. Answer only from the supplied context. "
                "If the context is insufficient, say so directly and briefly."
            ),
        )
    )
    history_lines = [f"{item['role']}: {item['content']}" for item in history[-6:] if item.get("content")]
    context_text = "\n\n---\n\n".join(context_chunks) if context_chunks else "(no context found)"
    user_prompt = (
        f"Answer in language: {lang}.\n"
        "Use only the retrieved context.\n"
        "If the context is insufficient, say that the available materials do not contain an exact answer.\n"
        "Do not invent addresses, document lists, scores, prices, or deadlines.\n\n"
        f"Recent history:\n{chr(10).join(history_lines) if history_lines else '(empty)'}\n\n"
        f"Context:\n{context_text}\n\n"
        f"Question: {question}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_tensors="pt",
        return_dict=True,
    ).to(model.device)

    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=int(get_cfg(cfg, "generation", "max_new_tokens", default=768)),
            do_sample=False,
            repetition_penalty=float(get_cfg(cfg, "generation", "repetition_penalty", default=1.1)),
            use_cache=True,
            eos_token_id=stop_token_ids(tokenizer),
            pad_token_id=tokenizer.pad_token_id,
        )

    answer = tokenizer.decode(output[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True).strip()
    return answer or "В доступных материалах нет точного ответа на этот вопрос."


def retrieve_context(
    question: str,
    index: QdrantIndex,
    cfg: dict[str, Any],
    embed_tokenizer: Any,
    embed_model: SentenceTransformer,
) -> list[str]:
    return find_local_passage(
        query=question,
        index=index,
        tokenizer=embed_tokenizer,
        embed_model=embed_model,
        top_k=int(get_cfg(cfg, "rag", "top_k", default=5)),
        threshold=float(get_cfg(cfg, "rag", "threshold", default=0.8)),
        token_limit=int(get_cfg(cfg, "rag", "token_limit", default=512)),
    )


def run_one_question(
    question: str,
    lang: str,
    history: list[dict[str, str]],
    cfg: dict[str, Any],
    index: QdrantIndex,
    embed_tokenizer: Any,
    embed_model: SentenceTransformer,
    gen_tokenizer: Any,
    gen_model: Any,
    show_context: bool,
) -> None:
    ctx_list = retrieve_context(question, index, cfg, embed_tokenizer, embed_model)
    if show_context:
        print("\n=== CONTEXT ===")
        if not ctx_list:
            print("(empty)")
        else:
            for idx, chunk in enumerate(ctx_list, start=1):
                print(f"[{idx}] {chunk}\n")

    answer = generate_answer(question, ctx_list, lang, history, cfg, gen_tokenizer, gen_model)
    print("\n=== ANSWER ===")
    print(answer)

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})


def main() -> None:
    load_dotenv(BASE_DIR / ".env.local")

    parser = argparse.ArgumentParser(description="Standalone Kaggle/large-model RAG checker.")
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "configs" / "kaggle-large-model.yaml"),
        help="Path to YAML config for the large-model run.",
    )
    parser.add_argument("--question", default="", help="Single question to run. If omitted, interactive mode starts.")
    parser.add_argument("--lang", default="", help="Override response language, e.g. ru / kk / en.")
    parser.add_argument("--rebuild-data", action="store_true", help="Rebuild processed .txt data from input_data before indexing.")
    parser.add_argument("--rebuild-index", action="store_true", help="Force rebuilding the Qdrant index.")
    parser.add_argument("--hide-context", action="store_true", help="Do not print retrieved context chunks.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = BASE_DIR / config_path
    cfg = load_yaml(config_path)

    embed_tokenizer, embed_model = load_embedding_stack(cfg)
    gen_tokenizer, gen_model = load_generation_stack(cfg)

    rebuild_data = bool(args.rebuild_data or get_cfg(cfg, "runtime", "rebuild_data", default=False))
    rebuild_index = bool(args.rebuild_index or get_cfg(cfg, "runtime", "rebuild_index", default=False))
    index = ensure_index(
        cfg,
        embed_tokenizer,
        embed_model,
        rebuild_data=rebuild_data,
        rebuild_index=rebuild_index,
    )

    lang = args.lang or str(get_cfg(cfg, "runtime", "lang", default="ru"))
    show_context = not args.hide_context and bool(get_cfg(cfg, "runtime", "show_context", default=True))
    history: list[dict[str, str]] = []

    if args.question.strip():
        run_one_question(
            question=args.question.strip(),
            lang=lang,
            history=history,
            cfg=cfg,
            index=index,
            embed_tokenizer=embed_tokenizer,
            embed_model=embed_model,
            gen_tokenizer=gen_tokenizer,
            gen_model=gen_model,
            show_context=show_context,
        )
        return

    if not bool(get_cfg(cfg, "runtime", "interactive", default=True)):
        raise SystemExit("Interactive mode is disabled in config and no --question was provided.")

    print("Interactive large-model check. Empty line or Ctrl+C to exit.")
    while True:
        try:
            question = input("\nquestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            break
        run_one_question(
            question=question,
            lang=lang,
            history=history,
            cfg=cfg,
            index=index,
            embed_tokenizer=embed_tokenizer,
            embed_model=embed_model,
            gen_tokenizer=gen_tokenizer,
            gen_model=gen_model,
            show_context=show_context,
        )


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


CHAT_FALLBACK_SYSTEM = "Answer strictly using the provided university context. If context is insufficient, say that there is not enough information."


def emit_progress(event: dict[str, Any]) -> None:
    try:
        print("TRAINING_PROGRESS " + json.dumps(event, ensure_ascii=False), flush=True)
    except Exception:
        pass


class JsonProgressCallback:
    def __init__(self, total_steps: int | None = None) -> None:
        self.total_steps = int(total_steps or 0)

    def on_train_begin(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
        total = int(getattr(state, "max_steps", 0) or self.total_steps or 0)
        emit_progress({"event": "train_begin", "step": 0, "total_steps": total, "progress": 0})

    def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, Any] | None = None, **kwargs: Any) -> None:
        total = int(getattr(state, "max_steps", 0) or self.total_steps or 0)
        step = int(getattr(state, "global_step", 0) or 0)
        progress = (step / total) if total > 0 else 0
        emit_progress({"event": "log", "step": step, "total_steps": total, "progress": max(0, min(progress, 1)), "logs": logs or {}})

    def on_save(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
        total = int(getattr(state, "max_steps", 0) or self.total_steps or 0)
        step = int(getattr(state, "global_step", 0) or 0)
        progress = (step / total) if total > 0 else 0
        emit_progress({"event": "save", "step": step, "total_steps": total, "progress": max(0, min(progress, 1))})

    def on_train_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
        total = int(getattr(state, "max_steps", 0) or self.total_steps or 0)
        step = int(getattr(state, "global_step", 0) or total or 0)
        emit_progress({"event": "train_end", "step": step, "total_steps": total, "progress": 1})


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    if not rows:
        raise ValueError(f"Dataset is empty: {source}")
    return rows


def as_messages(row: dict[str, Any]) -> list[dict[str, str]]:
    messages = row.get("messages")
    if isinstance(messages, list) and messages:
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip()
            content = str(message.get("content") or "").strip()
            if role and content:
                normalized.append({"role": role, "content": content})
        if normalized:
            return normalized

    instruction = str(row.get("instruction") or row.get("question") or row.get("prompt") or "").strip()
    context = str(row.get("input") or row.get("context") or "").strip()
    output = str(row.get("output") or row.get("answer") or row.get("response") or "").strip()
    user_text = f"Context:\n{context}\n\nQuestion:\n{instruction}" if context else instruction
    return [
        {"role": "system", "content": CHAT_FALLBACK_SYSTEM},
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": output},
    ]


def format_example(row: dict[str, Any], tokenizer: Any) -> str:
    messages = as_messages(row)
    if hasattr(tokenizer, "apply_chat_template") and tokenizer.chat_template:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    parts = []
    for message in messages:
        role = message["role"].strip().lower()
        content = message["content"].strip()
        parts.append(f"<{role}>\n{content}\n</{role}>")
    eos = tokenizer.eos_token or ""
    return "\n".join(parts) + eos


class JsonlSftDataset:
    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, max_seq_len: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_seq_len = int(max_seq_len)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        text = format_example(self.rows[index], self.tokenizer)
        encoded = self.tokenizer(
            text,
            truncation=True,
            max_length=self.max_seq_len,
            padding=False,
            return_attention_mask=True,
        )
        encoded["labels"] = list(encoded["input_ids"])
        return encoded


def split_rows(rows: list[dict[str, Any]], validation_split: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    if validation_split <= 0 or len(rows) < 20:
        return rows, None
    val_size = max(1, int(math.ceil(len(rows) * validation_split)))
    val_size = min(val_size, max(1, len(rows) - 1))
    return rows[val_size:], rows[:val_size]


def parse_target_modules(value: str) -> list[str] | str:
    value = (value or "auto").strip()
    if value in {"auto", "all-linear"}:
        return value
    return [part.strip() for part in value.split(",") if part.strip()]


def resolve_torch_dtype(torch: Any, value: str) -> Any:
    value = (value or "auto").lower()
    if value == "bf16":
        return torch.bfloat16
    if value == "fp16":
        return torch.float16
    if value == "fp32":
        return torch.float32
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    if torch.cuda.is_available():
        return torch.float16
    return torch.float32


def train(args: argparse.Namespace) -> None:
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainerCallback, TrainingArguments
        from transformers import DataCollatorForLanguageModeling
        from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training

        class _JsonProgressCallback(TrainerCallback):
            def on_train_begin(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
                total = int(getattr(state, "max_steps", 0) or 0)
                emit_progress({"event": "train_begin", "step": 0, "total_steps": total, "progress": 0})

            def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, Any] | None = None, **kwargs: Any) -> None:
                total = int(getattr(state, "max_steps", 0) or 0)
                step = int(getattr(state, "global_step", 0) or 0)
                progress = (step / total) if total > 0 else 0
                emit_progress({"event": "log", "step": step, "total_steps": total, "progress": max(0, min(progress, 1)), "logs": logs or {}})

            def on_save(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
                total = int(getattr(state, "max_steps", 0) or 0)
                step = int(getattr(state, "global_step", 0) or 0)
                progress = (step / total) if total > 0 else 0
                emit_progress({"event": "save", "step": step, "total_steps": total, "progress": max(0, min(progress, 1))})

            def on_train_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
                total = int(getattr(state, "max_steps", 0) or 0)
                step = int(getattr(state, "global_step", 0) or total or 0)
                emit_progress({"event": "train_end", "step": step, "total_steps": total, "progress": 1})
    except ImportError as exc:
        raise SystemExit(
            "Training dependencies are missing. Install them with:\n"
            "pip install -r requirements-training.txt\n\n"
            f"Original import error: {exc}"
        ) from exc

    method = args.method.lower().strip()
    use_lora = method in {"lora", "lora_sft", "qlora", "qlora_sft"}
    use_qlora = method in {"qlora", "qlora_sft"}
    dtype = resolve_torch_dtype(torch, args.dtype)

    rows = read_jsonl(args.dataset)
    train_rows, val_rows = split_rows(rows, args.validation_split)

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    quantization_config = None
    device_map = None
    if use_qlora:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype if dtype in {torch.float16, torch.bfloat16} else torch.float16,
        )
        device_map = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=True,
        torch_dtype=None if use_qlora else dtype,
        quantization_config=quantization_config,
        device_map=device_map,
    )
    model.config.use_cache = False

    if use_lora:
        if use_qlora:
            model = prepare_model_for_kbit_training(model)
        target_modules = parse_target_modules(args.target_modules)
        if target_modules == "auto":
            target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            bias="none",
            target_modules=target_modules,
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

    train_dataset = JsonlSftDataset(train_rows, tokenizer, args.max_seq_len)
    eval_dataset = JsonlSftDataset(val_rows, tokenizer, args.max_seq_len) if val_rows else None
    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    bf16 = dtype == torch.bfloat16 and torch.cuda.is_available()
    fp16 = dtype == torch.float16 and torch.cuda.is_available()
    optim = args.optim or ("paged_adamw_8bit" if use_qlora else "adamw_torch")

    training_args = TrainingArguments(
        output_dir=args.output,
        overwrite_output_dir=args.overwrite,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        eval_strategy="steps" if eval_dataset else "no",
        eval_steps=args.eval_steps,
        bf16=bf16,
        fp16=fp16,
        optim=optim,
        gradient_checkpointing=args.gradient_checkpointing,
        report_to=[] if args.report_to == "none" else [args.report_to],
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
        callbacks=[_JsonProgressCallback()],
    )
    emit_progress({"event": "dataset_ready", "train_rows": len(train_rows), "validation_rows": len(val_rows or [])})
    trainer.train(resume_from_checkpoint=args.resume_from_checkpoint or None)
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)

    if args.merge and use_lora:
        merged_dir = Path(args.output) / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)
        merged = model.merge_and_unload()
        merged.save_pretrained(merged_dir, safe_serialization=True)
        tokenizer.save_pretrained(merged_dir)
        print(f"Merged model saved to: {merged_dir}")

    print(f"Training finished. Output: {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoRA/QLoRA SFT training for exported AI-Talapker JSONL datasets.")
    parser.add_argument("--model", required=True, help="HF model id or local model directory. GGUF files are not trainable here.")
    parser.add_argument("--dataset", required=True, help="Exported JSONL dataset path.")
    parser.add_argument("--output", default="storage/training/runs/sft-run", help="Output adapter/model directory.")
    parser.add_argument("--method", default="qlora_sft", choices=["qlora_sft", "lora_sft", "full_sft", "qlora", "lora", "full"])
    parser.add_argument("--max-seq-len", type=int, default=1024)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--validation-split", type=float, default=0.05)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--target-modules", default="auto", help="auto, all-linear, or comma list: q_proj,k_proj,v_proj,o_proj,...")
    parser.add_argument("--dtype", default="auto", choices=["auto", "bf16", "fp16", "fp32"])
    parser.add_argument("--optim", default="", help="Override optimizer, e.g. adamw_torch or paged_adamw_8bit.")
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--save-total-limit", type=int, default=2)
    parser.add_argument("--resume-from-checkpoint", default="")
    parser.add_argument("--report-to", default="none")
    parser.add_argument("--gradient-checkpointing", action="store_true", default=True)
    parser.add_argument("--no-gradient-checkpointing", action="store_false", dest="gradient_checkpointing")
    parser.add_argument("--merge", action="store_true", help="Merge LoRA adapter into base model after training. Needs enough VRAM/RAM.")
    parser.add_argument("--overwrite", action="store_true")
    return parser


if __name__ == "__main__":
    train(build_parser().parse_args())

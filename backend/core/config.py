import os
import secrets
import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "configs" / "app.yaml"

load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.local", override=True)


def _resolve_config_path() -> Path:
    raw = os.getenv("APP_CONFIG_FILE", "").strip()
    if not raw:
        return DEFAULT_CONFIG_PATH
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = BASE_DIR / raw
    return candidate


def _load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {path}")
    return payload


def _get(section: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = section
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return int(default)
    return int(str(raw).strip())


def _resolve_path(raw_path: str | None, default: Path) -> str:
    if not raw_path:
        return str(default)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return str(candidate)


def _model_section(name: str) -> dict[str, Any]:
    value = _get(APP_CONFIG, "models", name, default={}) or {}
    return value if isinstance(value, dict) else {}


def _normalize_backend(raw: Any, default: str) -> str:
    value = str(raw or default).strip().lower().replace("-", "_")
    aliases = {
        "hf": "transformers",
        "torch": "transformers",
        "causal_lm": "transformers",
        "sentence_transformer": "sentence_transformers",
        "sentence_transformer_torch": "sentence_transformers",
        "llama_cpp": "gguf",
        "llamacpp": "gguf",
        "llama_server": "llama_server",
        "llama.cpp_server": "llama_server",
        "llamacpp_server": "llama_server",
        "gguf_server": "llama_server",
        "api": "openrouter",
        "cloud": "openrouter",
        "open_router": "openrouter",
        "fast_embed": "fastembed",
        "qdrant_fastembed": "fastembed",
    }
    return aliases.get(value, value)


def _section_has_gguf_config(section: dict[str, Any]) -> bool:
    if any(section.get(key) for key in ("gguf_path", "gguf_file", "gguf_filename")):
        return True
    gguf = section.get("gguf")
    if isinstance(gguf, str):
        return bool(gguf.strip())
    if isinstance(gguf, dict):
        return any(gguf.get(key) for key in ("path", "file", "filename"))
    dirname = str(section.get("dirname") or "")
    return dirname.lower().endswith(".gguf")


def _model_backend(name: str, default: str) -> str:
    env_names = [f"APP_{name.upper()}_BACKEND"]
    if name == "generation":
        env_names.extend(["LLM_PROVIDER", "CHAT_PROVIDER"])
    elif name == "embedding":
        env_names.extend(["EMBEDDING_PROVIDER", "EMBED_PROVIDER"])
    raw = next((os.getenv(env_name) for env_name in env_names if os.getenv(env_name) is not None), None)
    section = _model_section(name)
    if raw is None:
        raw = section.get("backend")
    if raw is None and _section_has_gguf_config(section):
        raw = "gguf"
    return _normalize_backend(raw, default)


APP_CONFIG_PATH = _resolve_config_path()
APP_CONFIG = _load_yaml_config(APP_CONFIG_PATH)

STORAGE_DIR = Path(_resolve_path(_get(APP_CONFIG, "paths", "storage_dir"), BASE_DIR / "storage"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MODELS_DIR = _resolve_path(_get(APP_CONFIG, "paths", "models_dir"), BASE_DIR / "models")
INPUT_DATA_DIR = _resolve_path(_get(APP_CONFIG, "paths", "input_data_dir"), BASE_DIR / "input_data")
DATA_DIR = _resolve_path(_get(APP_CONFIG, "paths", "data_dir"), BASE_DIR / "data")
KNOWLEDGE_REGISTRY_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "knowledge_registry_path"),
    STORAGE_DIR / "knowledge_registry.json",
)
KNOWLEDGE_CATALOG_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "knowledge_catalog_path"),
    STORAGE_DIR / "knowledge_catalog.json",
)
KNOWLEDGE_ENTRIES_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "knowledge_entries_path"),
    STORAGE_DIR / "knowledge_entries.jsonl",
)
CURATED_ENTRIES_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "curated_entries_path"),
    STORAGE_DIR / "curated_entries.json",
)
RAG_DOCUMENTS_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "rag_documents_path"),
    STORAGE_DIR / "rag_documents.jsonl",
)
RAG_CHUNKS_PATH = _resolve_path(
    _get(APP_CONFIG, "paths", "rag_chunks_path"),
    STORAGE_DIR / "rag_chunks.jsonl",
)

EMBED_BACKEND = _model_backend("embedding", "sentence_transformers")
GEN_BACKEND = _model_backend("generation", "transformers")
GUARD_BACKEND = _model_backend("guard", "transformers")

EMBED_MODEL_DIRNAME = str(_get(APP_CONFIG, "models", "embedding", "dirname", default="multilingual-e5-small"))
GEN_MODEL_DIRNAME = str(_get(APP_CONFIG, "models", "generation", "dirname", default="Qwen2.5-3B-Instruct"))
GUARD_MODEL_DIRNAME = str(_get(APP_CONFIG, "models", "guard", "dirname", default="Qwen3Guard-Gen-0.6B"))
EMBED_MODEL_REPO = str(_get(APP_CONFIG, "models", "embedding", "repo_id", default="intfloat/multilingual-e5-small"))
GEN_MODEL_REPO = str(_get(APP_CONFIG, "models", "generation", "repo_id", default="Qwen/Qwen2.5-3B-Instruct"))
GUARD_MODEL_REPO = str(_get(APP_CONFIG, "models", "guard", "repo_id", default="Qwen/Qwen3Guard-Gen-0.6B"))
EMBED_MODEL_DIR = os.path.join(MODELS_DIR, EMBED_MODEL_DIRNAME)
GEN_MODEL_DIR = os.path.join(MODELS_DIR, GEN_MODEL_DIRNAME)
GUARD_MODEL_DIR = os.path.join(MODELS_DIR, GUARD_MODEL_DIRNAME)
ANSWER_GUARD_ENABLED = _get_bool_env(
    "ANSWER_GUARD_ENABLED",
    bool(_get(APP_CONFIG, "models", "guard", "enabled", default=True)),
)


def _gguf_raw_path(name: str) -> str | None:
    section = _model_section(name)
    env_path = os.getenv(f"APP_{name.upper()}_GGUF_PATH")
    if env_path:
        return env_path
    direct = section.get("gguf_path") or section.get("gguf_file") or section.get("gguf_filename")
    if direct:
        return str(direct)
    gguf = section.get("gguf")
    if isinstance(gguf, str):
        return gguf
    if isinstance(gguf, dict):
        return str(gguf.get("path") or gguf.get("file") or gguf.get("filename") or "") or None
    dirname = str(section.get("dirname") or "")
    if dirname.lower().endswith(".gguf"):
        return dirname
    return None


def _resolve_gguf_path(name: str, default_filename: str | None = None) -> str:
    raw = _gguf_raw_path(name) or default_filename
    if not raw:
        return ""
    candidate = Path(str(raw))
    if not candidate.is_absolute():
        if len(candidate.parts) == 1:
            candidate = Path(MODELS_DIR) / candidate
        else:
            candidate = BASE_DIR / candidate
    return str(candidate)


def _gguf_cfg(name: str, key: str, default: Any) -> Any:
    env = os.getenv(f"APP_{name.upper()}_GGUF_{key.upper()}")
    if env is not None:
        return env
    section = _model_section(name)
    gguf = section.get("gguf")
    if isinstance(gguf, dict) and key in gguf:
        return gguf.get(key)
    return section.get(f"gguf_{key}", default)


GEN_GGUF_PATH = _resolve_gguf_path("generation")
GUARD_GGUF_PATH = _resolve_gguf_path("guard")
EMBED_GGUF_PATH = _resolve_gguf_path("embedding")
GEN_GGUF_N_CTX = int(_gguf_cfg("generation", "n_ctx", 8192))
GUARD_GGUF_N_CTX = int(_gguf_cfg("guard", "n_ctx", 4096))
EMBED_GGUF_N_CTX = int(_gguf_cfg("embedding", "n_ctx", 512))
GEN_GGUF_N_GPU_LAYERS = int(_gguf_cfg("generation", "n_gpu_layers", -1))
GUARD_GGUF_N_GPU_LAYERS = int(_gguf_cfg("guard", "n_gpu_layers", -1))
EMBED_GGUF_N_GPU_LAYERS = int(_gguf_cfg("embedding", "n_gpu_layers", -1))
GEN_GGUF_MAIN_GPU = int(_gguf_cfg("generation", "main_gpu", 0))
GUARD_GGUF_MAIN_GPU = int(_gguf_cfg("guard", "main_gpu", 0))
EMBED_GGUF_MAIN_GPU = int(_gguf_cfg("embedding", "main_gpu", 0))
GEN_GGUF_DEVICE = str(_gguf_cfg("generation", "device", _get(APP_CONFIG, "runtime", "device", default="auto")) or "auto").strip().lower()
GUARD_GGUF_DEVICE = str(_gguf_cfg("guard", "device", _get(APP_CONFIG, "runtime", "device", default="auto")) or "auto").strip().lower()
EMBED_GGUF_DEVICE = str(_gguf_cfg("embedding", "device", _get(APP_CONFIG, "runtime", "embedding_device", default="auto")) or "auto").strip().lower()
GEN_GGUF_CHAT_FORMAT = str(_gguf_cfg("generation", "chat_format", "") or "").strip() or None
GUARD_GGUF_CHAT_FORMAT = str(_gguf_cfg("guard", "chat_format", "") or "").strip() or None
GGUF_VERBOSE = _get_bool_env("APP_GGUF_VERBOSE", bool(_get(APP_CONFIG, "runtime", "gguf_verbose", default=False)))
GGUF_RUNTIME = str(os.getenv("APP_GGUF_RUNTIME", _get(APP_CONFIG, "runtime", "gguf_runtime", default="python")) or "python").strip().lower().replace("-", "_")
if GGUF_RUNTIME in {"server", "llama", "llama_cpp_server", "llamacpp_server", "gguf_server"}:
    GGUF_RUNTIME = "llama_server"

TOKEN_LIMIT = int(_get(APP_CONFIG, "rag", "token_limit", default=512))
TARGET_TOKENS = int(_get(APP_CONFIG, "rag", "target_tokens", default=320))
OVERLAP_TOKENS = int(_get(APP_CONFIG, "rag", "overlap_tokens", default=64))
SIM_THRESHOLD = float(_get(APP_CONFIG, "rag", "sim_threshold", default=0.8))
MAX_CTX_CHUNKS = int(_get(APP_CONFIG, "rag", "max_context_chunks", default=5))
LOOKUP_MAX_ITERATIONS = int(_get(APP_CONFIG, "rag", "lookup_max_iterations", default=4))
REBUILD_CONFIG = dict(_get(APP_CONFIG, "rebuild", default={}) or {})
RETRIEVAL_CONFIG = dict(_get(APP_CONFIG, "retrieval", default={}) or {})
DEFAULT_RETRIEVAL_TOP_K = int(RETRIEVAL_CONFIG.get("top_k", MAX_CTX_CHUNKS))
DEFAULT_RETRIEVAL_DOMAINS = list(RETRIEVAL_CONFIG.get("default_domains", []))
DEFAULT_RETRIEVAL_SCHEMAS = list(RETRIEVAL_CONFIG.get("default_schemas", []))
EMBED_BATCH_SIZE = int(_get(APP_CONFIG, "models", "embedding", "batch_size", default=8))
EMBED_MAX_SEQ_LENGTH = int(_get(APP_CONFIG, "models", "embedding", "max_seq_length", default=TOKEN_LIMIT))
MAX_NEW_TOKENS = int(_get(APP_CONFIG, "generation", "max_new_tokens", default=640))

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", str(_get(APP_CONFIG, "openrouter", "api_key", default="") or "")).strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL",
    str(_get(APP_CONFIG, "openrouter", "base_url", default="https://openrouter.ai/api/v1") or "https://openrouter.ai/api/v1"),
).rstrip("/")
OPENROUTER_CHAT_MODEL = os.getenv(
    "OPENROUTER_CHAT_MODEL",
    str(_get(APP_CONFIG, "openrouter", "chat_model", default="openrouter/free") or "openrouter/free"),
).strip()
OPENROUTER_GUARD_MODEL = os.getenv(
    "OPENROUTER_GUARD_MODEL",
    str(_get(APP_CONFIG, "openrouter", "guard_model", default="") or ""),
).strip()
OPENROUTER_EMBEDDING_MODEL = os.getenv(
    "OPENROUTER_EMBEDDING_MODEL",
    str(_get(APP_CONFIG, "openrouter", "embedding_model", default="openai/text-embedding-3-small") or "openai/text-embedding-3-small"),
).strip()
OPENROUTER_EMBEDDING_DIMENSIONS = _get_int_env(
    "OPENROUTER_EMBEDDING_DIMENSIONS",
    int(_get(APP_CONFIG, "openrouter", "embedding_dimensions", default=1536)),
)
OPENROUTER_HTTP_REFERER = os.getenv(
    "OPENROUTER_HTTP_REFERER",
    str(_get(APP_CONFIG, "openrouter", "http_referer", default="") or ""),
).strip()
OPENROUTER_APP_TITLE = os.getenv(
    "OPENROUTER_APP_TITLE",
    str(_get(APP_CONFIG, "openrouter", "app_title", default="AI-Talapker") or "AI-Talapker"),
).strip()
OPENROUTER_TIMEOUT = _get_int_env("OPENROUTER_TIMEOUT", int(_get(APP_CONFIG, "openrouter", "timeout", default=60)))

FASTEMBED_MODEL = os.getenv(
    "FASTEMBED_MODEL",
    str(_get(APP_CONFIG, "fastembed", "model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
).strip()
FASTEMBED_CACHE_DIR = os.getenv(
    "FASTEMBED_CACHE_DIR",
    str(_get(APP_CONFIG, "fastembed", "cache_dir", default=str(BASE_DIR / "models" / "fastembed")) or str(BASE_DIR / "models" / "fastembed")),
).strip()
FASTEMBED_THREADS = _get_int_env("FASTEMBED_THREADS", int(_get(APP_CONFIG, "fastembed", "threads", default=1)))
FASTEMBED_BATCH_SIZE = _get_int_env("FASTEMBED_BATCH_SIZE", int(_get(APP_CONFIG, "fastembed", "batch_size", default=16)))
FASTEMBED_DIMENSIONS = _get_int_env("FASTEMBED_DIMENSIONS", int(_get(APP_CONFIG, "fastembed", "dimensions", default=384)))
ANSWER_FILTER_MODE = str(_get(APP_CONFIG, "answer_filter", "mode", default="balanced")).strip().lower()
ANSWER_FILTER_MIN_SUPPORTED_TERMS = int(_get(APP_CONFIG, "answer_filter", "min_supported_terms", default=1))
ANSWER_FILTER_GENERIC_OVERLAP_THRESHOLD = float(
    _get(APP_CONFIG, "answer_filter", "generic_overlap_threshold", default=0.18)
)
ANSWER_FILTER_ALLOW_PARTIAL = bool(_get(APP_CONFIG, "answer_filter", "allow_partial_answers", default=True))

TOKEN = os.getenv("TOKEN")


def _backend_needs_torch(backend: str) -> bool:
    return backend in {"transformers", "sentence_transformers"}


TORCH_REQUIRED = (
    _backend_needs_torch(EMBED_BACKEND)
    or _backend_needs_torch(GEN_BACKEND)
    or (ANSWER_GUARD_ENABLED and _backend_needs_torch(GUARD_BACKEND))
)
GGUF_BACKENDS = {"gguf", "llama_server"}
ALL_MODELS_GGUF = (
    EMBED_BACKEND in GGUF_BACKENDS
    and GEN_BACKEND in GGUF_BACKENDS
    and (not ANSWER_GUARD_ENABLED or GUARD_BACKEND in GGUF_BACKENDS)
)


def _torch_cuda_available() -> bool:
    if not TORCH_REQUIRED:
        return False
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


yaml_device = str(_get(APP_CONFIG, "runtime", "device", default="auto")).strip().lower()
if yaml_device in {"", "auto"}:
    default_device = "cuda" if _torch_cuda_available() else "cpu"
else:
    default_device = yaml_device
APP_DEVICE = os.getenv("APP_DEVICE", default_device)
USE_CUDA = APP_DEVICE.startswith("cuda") and _torch_cuda_available()

yaml_embedding_device = str(_get(APP_CONFIG, "runtime", "embedding_device", default="cpu")).strip().lower()
if yaml_embedding_device in {"", "auto"}:
    default_embedding_device = APP_DEVICE
else:
    default_embedding_device = yaml_embedding_device
EMBED_DEVICE = os.getenv("EMBED_DEVICE", default_embedding_device)

LOAD_4BIT = _get_bool_env(
    "APP_LOAD_4BIT",
    bool(_get(APP_CONFIG, "runtime", "load_4bit", default=False)) and USE_CUDA and GEN_BACKEND == "transformers",
)

QDRANT_PATH = os.getenv(
    "QDRANT_PATH",
    _resolve_path(_get(APP_CONFIG, "qdrant", "path"), STORAGE_DIR / "qdrant"),
)
QDRANT_URL = os.getenv("QDRANT_URL", str(_get(APP_CONFIG, "qdrant", "url", default="") or "")).strip().rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", str(_get(APP_CONFIG, "qdrant", "api_key", default="") or "")).strip()
QDRANT_COLLECTION = os.getenv(
    "QDRANT_COLLECTION",
    str(_get(APP_CONFIG, "qdrant", "collection", default="university_knowledge") or "university_knowledge"),
).strip()
QDRANT_VECTOR_NAME = os.getenv("QDRANT_VECTOR_NAME", str(_get(APP_CONFIG, "qdrant", "vector_name", default="") or "")).strip()
QDRANT_TIMEOUT = _get_int_env("QDRANT_TIMEOUT", int(_get(APP_CONFIG, "qdrant", "timeout", default=30)))
QDRANT_PREFER_GRPC = _get_bool_env("QDRANT_PREFER_GRPC", bool(_get(APP_CONFIG, "qdrant", "prefer_grpc", default=False)))
REDIS_URL = os.getenv("REDIS_URL", str(_get(APP_CONFIG, "redis", "url", default="redis://127.0.0.1:6379/0")))
REDIS_SESSION_TTL = int(os.getenv("REDIS_SESSION_TTL", str(_get(APP_CONFIG, "redis", "session_ttl", default=86400))))

ALLOW_WEB_SEARCH_DEFAULT = _get_bool_env(
    "ALLOW_WEB_SEARCH_DEFAULT",
    bool(_get(APP_CONFIG, "web_search", "enabled_by_default", default=False)),
)
WEB_SEARCH_WHITELIST = [
    item.strip().lower()
    for item in (
        os.getenv("WEB_SEARCH_WHITELIST")
        or ",".join(_get(APP_CONFIG, "web_search", "whitelist", default=["kstu.kz", "www.kstu.kz"]))
    ).split(",")
    if item.strip()
]
WEB_SEARCH_MAX_RESULTS = int(
    os.getenv("WEB_SEARCH_MAX_RESULTS", str(_get(APP_CONFIG, "web_search", "max_results", default=3)))
)

ADMIN_USERNAME = str(os.getenv("ADMIN_USERNAME", _get(APP_CONFIG, "admin", "username", default="")) or "")
ADMIN_PASSWORD = str(os.getenv("ADMIN_PASSWORD", _get(APP_CONFIG, "admin", "password", default="")) or "")
_raw_admin_jwt_secret = str(os.getenv("ADMIN_JWT_SECRET", _get(APP_CONFIG, "admin", "jwt_secret", default="")) or "")
ADMIN_JWT_SECRET_EPHEMERAL = False
if not _raw_admin_jwt_secret or _raw_admin_jwt_secret in {"change-me", "changeme", "secret", "admin"} or len(_raw_admin_jwt_secret) < 32:
    ADMIN_JWT_SECRET = secrets.token_urlsafe(48)
    ADMIN_JWT_SECRET_EPHEMERAL = True
    warnings.warn(
        "ADMIN_JWT_SECRET is missing or unsafe; using an ephemeral per-process secret. "
        "Set ADMIN_JWT_SECRET in .env.local for stable sessions and API keys.",
        RuntimeWarning,
        stacklevel=2,
    )
else:
    ADMIN_JWT_SECRET = _raw_admin_jwt_secret
ADMIN_ALLOW_CONFIG_BOOTSTRAP = _get_bool_env(
    "ADMIN_ALLOW_CONFIG_BOOTSTRAP",
    bool(_get(APP_CONFIG, "admin", "allow_config_bootstrap", default=False)),
)
ADMIN_JWT_TTL_SECONDS = int(
    os.getenv("ADMIN_JWT_TTL_SECONDS", str(_get(APP_CONFIG, "admin", "jwt_ttl_seconds", default=43200)))
)

ANSWER_NOT_FOUND = str(
    _get(
        APP_CONFIG,
        "prompts",
        "answer_not_found",
        default="Информации в базе недостаточно для ответа на этот вопрос.",
    )
)
SYSTEM_PROMPT = str(
    _get(
        APP_CONFIG,
        "prompts",
        "system_prompt",
        default=(
            "You are an AI assistant for Karaganda Technical University named after Abylkas Saginov. "
            "The university is located in Karaganda, Kazakhstan. "
            "You help applicants and students with university-related questions. "
            "Answer clearly, completely, and naturally. "
            "When the context contains a list, enumerate the items in a readable way and add a short introductory sentence. "
            "Prefer a fuller answer over a one-line reply, but do not invent facts. "
            "Use a neutral tone and refer to the institution as 'the university', not 'your university' or 'our university'. "
            "Do not add praise, marketing language, or general statements that are not explicitly supported by the context. "
            "Answer strictly based on the provided context; if the context is irrelevant or missing, "
            "reply exactly: There is not enough information in the database to answer this question. "
            "Never include angle-bracket placeholders like <...> in the final answer; "
            "replace any such placeholder with the actual answer from context, or, if missing, with a short helpful hint."
        ),
    )
)

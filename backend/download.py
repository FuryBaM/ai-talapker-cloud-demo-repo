import argparse
from pathlib import Path

import certifi
import requests
import yaml
from huggingface_hub import snapshot_download


BASE_DIR = Path(__file__).resolve().parent


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML config must contain a mapping: {path}")
    return payload


def get_cfg(cfg: dict, *keys: str, default=None):
    current = cfg
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Download local models described in YAML config.")
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "configs" / "app.yaml"),
        help="YAML config with model repo ids and local directories.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = BASE_DIR / config_path
    cfg = load_yaml(config_path)

    models_dir = Path(resolve_path(get_cfg(cfg, "paths", "models_dir"), BASE_DIR / "models"))
    embed_dir = models_dir / str(get_cfg(cfg, "models", "embedding", "dirname", default="multilingual-e5-small"))
    gen_dir = models_dir / str(get_cfg(cfg, "models", "generation", "dirname", default="Qwen2.5-3B-Instruct"))
    guard_dir = models_dir / str(get_cfg(cfg, "models", "guard", "dirname", default="Qwen3Guard-Gen-0.6B"))
    embed_repo = str(get_cfg(cfg, "models", "embedding", "repo_id", default="intfloat/multilingual-e5-small"))
    gen_repo = str(get_cfg(cfg, "models", "generation", "repo_id", default="Qwen/Qwen2.5-3B-Instruct"))
    guard_repo = str(get_cfg(cfg, "models", "guard", "repo_id", default="Qwen/Qwen3Guard-Gen-0.6B"))
    guard_enabled = bool(get_cfg(cfg, "models", "guard", "enabled", default=True))

    requests.get("https://huggingface.co", verify=certifi.where())

    snapshot_download(
        repo_id=embed_repo,
        local_dir=str(embed_dir),
        local_dir_use_symlinks=False,
    )
    snapshot_download(
        repo_id=gen_repo,
        local_dir=str(gen_dir),
        local_dir_use_symlinks=False,
    )
    if guard_enabled:
        snapshot_download(
            repo_id=guard_repo,
            local_dir=str(guard_dir),
            local_dir_use_symlinks=False,
        )


if __name__ == "__main__":
    main()

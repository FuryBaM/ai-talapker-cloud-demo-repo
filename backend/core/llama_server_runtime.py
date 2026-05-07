from __future__ import annotations

import atexit
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from core.config import APP_CONFIG, BASE_DIR, STORAGE_DIR, _get, _resolve_path


DEFAULT_PORTS = {
    "generation": 8001,
    "embedding": 8002,
    "guard": 8003,
}

_RUNNING: dict[str, "ManagedLlamaServer"] = {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_project_path(raw: str | None, default: Path) -> Path:
    return Path(_resolve_path(raw, default)).resolve()


def _runtime_cfg(key: str, default: Any = None) -> Any:
    return _get(APP_CONFIG, "runtime", "llama_server", key, default=default)


def _model_cfg(model_name: str, key: str, default: Any = None) -> Any:
    section = _get(APP_CONFIG, "models", model_name, default={}) or {}
    if not isinstance(section, dict):
        return default
    server = section.get("llama_server")
    if isinstance(server, dict) and key in server:
        return server.get(key)
    gguf = section.get("gguf")
    if isinstance(gguf, dict):
        nested = gguf.get("server")
        if isinstance(nested, dict) and key in nested:
            return nested.get(key)
        if f"server_{key}" in gguf:
            return gguf.get(f"server_{key}")
    return default


def _first_config_value(model_name: str, key: str, default: Any = None) -> Any:
    value = _model_cfg(model_name, key, None)
    if value is not None:
        return value
    return _runtime_cfg(key, default)


def _windows_vcvars64_candidates() -> list[Path]:
    """Return existing vcvars64.bat candidates, newest/most explicit first.

    Do not assume a fixed Visual Studio directory name. Some machines report paths like
    Microsoft Visual Studio/18/Community, while VS 2022 usually lives under 2022.
    """
    if platform.system().lower() != "windows":
        return []

    candidates: list[Path] = []

    vsinstalldir = os.environ.get("VSINSTALLDIR")
    if vsinstalldir:
        candidates.append(Path(vsinstalldir) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")

    vswhere = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        try:
            output = subprocess.check_output([
                str(vswhere),
                "-all",
                "-products", "*",
                "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property", "installationPath",
            ], text=True, errors="replace").strip()
            for line in output.splitlines():
                line = line.strip()
                if line:
                    candidates.append(Path(line) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")
        except Exception:
            pass

    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    versions = ["2026", "2022", "2019", "18", "17", "16"]
    editions = ["BuildTools", "Community", "Professional", "Enterprise", "Preview"]
    for raw_root in roots:
        if not raw_root:
            continue
        for version in versions:
            vs_root = Path(raw_root) / "Microsoft Visual Studio" / version
            for edition in editions:
                candidates.append(vs_root / edition / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")

    seen: set[str] = set()
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            result.append(resolved)
    return result


def _run_build_command(cmd: list[str]) -> None:
    """Run a CMake/build command with useful Windows fallbacks and visible logs.

    Windows quoting around vcvars64.bat is fragile when nested inside `cmd /s /c`.
    A temporary .cmd wrapper is more reliable and prints the real CMake output.
    """
    if platform.system().lower() != "windows":
        subprocess.check_call(cmd)
        return

    errors: list[tuple[str, int, str]] = []
    command_line = subprocess.list2cmdline(cmd)

    def run_and_capture(name: str, command: list[str]) -> bool:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if output.strip():
            print(output)
        if proc.returncode == 0:
            return True
        errors.append((name, proc.returncode, output[-8000:]))
        return False

    use_vcvars = str(_runtime_cfg("use_vcvars", "auto") or "auto").strip().lower()
    candidates = _windows_vcvars64_candidates() if use_vcvars != "false" else []

    for vcvars in candidates:
        print(f"[llama-server] build env: {vcvars}")
        with tempfile.NamedTemporaryFile("w", suffix=".cmd", delete=False, encoding="utf-8") as handle:
            wrapper_path = Path(handle.name)
            handle.write("@echo off\n")
            handle.write(f'call "{vcvars}"\n')
            handle.write("if errorlevel 1 exit /b %errorlevel%\n")
            handle.write(command_line + "\n")
            handle.write("exit /b %errorlevel%\n")
        try:
            if run_and_capture(str(vcvars), [str(wrapper_path)]):
                return
        finally:
            try:
                wrapper_path.unlink(missing_ok=True)
            except Exception:
                pass

    if run_and_capture("direct", cmd):
        return

    summary = "\n\n".join(
        f"--- build attempt: {name} exited {code} ---\n{tail}"
        for name, code, tail in errors[-4:]
    )
    raise RuntimeError(f"Build command failed: {command_line}\n\n{summary}")


@dataclass(frozen=True)
class LlamaServerSettings:
    name: str
    purpose: str
    model_path: Path
    host: str
    port: int
    n_ctx: int
    n_gpu_layers: int
    main_gpu: int
    device: str
    embedding: bool
    pooling: str | None
    parallel: int
    extra_args: tuple[str, ...]
    auto_setup: bool
    prefer_cuda: bool
    fallback_cpu: bool
    reuse_existing: bool
    repo_url: str
    git_ref: str
    runtime_dir: Path
    build_dir: Path
    executable_windows: Path | None
    executable_linux: Path | None
    log_dir: Path

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def build_server_settings(
    *,
    name: str,
    model_path: str,
    purpose: str,
    n_ctx: int,
    n_gpu_layers: int,
    main_gpu: int = 0,
    device: str = "auto",
    embedding: bool = False,
) -> LlamaServerSettings:
    runtime_dir = _resolve_project_path(
        str(_runtime_cfg("runtime_dir", "runtime/llama.cpp")),
        BASE_DIR / "runtime" / "llama.cpp",
    )
    build_dir = _resolve_project_path(
        str(_runtime_cfg("build_dir", str(runtime_dir / "build"))),
        runtime_dir / "build",
    )
    exe_win_raw = _runtime_cfg("executable_windows", None)
    exe_linux_raw = _runtime_cfg("executable_linux", None)
    executable_windows = _resolve_project_path(str(exe_win_raw), BASE_DIR / str(exe_win_raw)) if exe_win_raw else None
    executable_linux = _resolve_project_path(str(exe_linux_raw), BASE_DIR / str(exe_linux_raw)) if exe_linux_raw else None

    extra_args_raw = _first_config_value(name, "extra_args", [])
    if isinstance(extra_args_raw, str):
        extra_args = tuple(part for part in extra_args_raw.split() if part)
    elif isinstance(extra_args_raw, list):
        extra_args = tuple(str(item) for item in extra_args_raw)
    else:
        extra_args = ()

    return LlamaServerSettings(
        name=name,
        purpose=purpose,
        model_path=Path(model_path).resolve(),
        host=str(_first_config_value(name, "host", "127.0.0.1")),
        port=int(_first_config_value(name, "port", DEFAULT_PORTS.get(name, 8001))),
        n_ctx=int(_first_config_value(name, "n_ctx", n_ctx)),
        n_gpu_layers=int(_first_config_value(name, "n_gpu_layers", n_gpu_layers)),
        main_gpu=int(_first_config_value(name, "main_gpu", main_gpu)),
        device=str(_first_config_value(name, "device", device) or "auto").strip().lower(),
        embedding=bool(embedding),
        pooling=(str(_first_config_value(name, "pooling", "") or "").strip() or None),
        parallel=int(_first_config_value(name, "parallel", 1 if embedding else 4)),
        extra_args=extra_args,
        auto_setup=_as_bool(_runtime_cfg("auto_setup", False)),
        prefer_cuda=_as_bool(_runtime_cfg("prefer_cuda", True), True),
        fallback_cpu=_as_bool(_runtime_cfg("fallback_cpu", True), True),
        reuse_existing=_as_bool(_runtime_cfg("reuse_existing", False), False),
        repo_url=str(_runtime_cfg("repo_url", "https://github.com/ggml-org/llama.cpp.git")),
        git_ref=str(_runtime_cfg("git_ref", "master")),
        runtime_dir=runtime_dir,
        build_dir=build_dir,
        executable_windows=executable_windows,
        executable_linux=executable_linux,
        log_dir=_resolve_project_path(str(_runtime_cfg("log_dir", "storage/logs")), STORAGE_DIR / "logs"),
    )


class ManagedLlamaServer:
    def __init__(self, settings: LlamaServerSettings) -> None:
        self.settings = settings
        self.process: subprocess.Popen | None = None
        self.executable: Path | None = None
        self._log_handle = None

    def ensure_runtime(self) -> Path:
        executable = self._configured_executable()
        wants_cuda = self._wants_cuda()
        explicit_cuda = self.settings.device.startswith("cuda")

        # Do not silently reuse an old CPU-only build when CUDA is requested.
        # Previous fallback builds could leave build/bin/llama-server.exe without CUDA;
        # that binary accepts -ngl but prints "compiled without support for GPU offload".
        existing = executable if executable and executable.exists() else self._find_built_executable()
        if existing and (not wants_cuda or self._cuda_build_marker_exists()):
            self.executable = existing
            return existing

        if existing and wants_cuda and not self.settings.auto_setup:
            raise RuntimeError(
                "CUDA was requested, but the existing llama-server build is not marked as CUDA-enabled. "
                f"Existing binary: {existing}. Remove the build directory and rebuild with GGML_CUDA=ON, "
                "or enable runtime.llama_server.auto_setup."
            )

        if not self.settings.auto_setup:
            expected = executable or self._default_executable_path()
            raise RuntimeError(
                "llama-server binary was not found. "
                f"Expected: {expected}. Enable runtime.llama_server.auto_setup or build llama.cpp manually."
            )

        self._clone_runtime()
        try:
            self._build_runtime(cuda=wants_cuda, clean=True)
        except Exception:
            # Explicit device: cuda must fail loudly. CPU fallback is only safe for auto-mode.
            if not (wants_cuda and self.settings.fallback_cpu and not explicit_cuda):
                raise
            print("[llama-server] CUDA build failed; retrying CPU build.")
            self._build_runtime(cuda=False, clean=True)

        found = self._find_built_executable()
        if not found:
            raise RuntimeError(f"llama-server build finished, but executable was not found under {self.settings.build_dir}")
        self.executable = found
        return found

    def start(self) -> None:
        if self.is_alive():
            return
        if self._endpoint_alive():
            if self.settings.reuse_existing:
                print(f"[llama-server:{self.settings.name}] existing server detected at {self.settings.base_url}")
                return
            raise RuntimeError(
                f"Port {self.settings.port} already has a server at {self.settings.base_url}. "
                "Stop old llama-server.exe processes before starting this project, or set "
                "runtime.llama_server.reuse_existing: true only if you are sure the existing server "
                "uses the same model and context size."
            )

        if not self.settings.model_path.exists():
            raise FileNotFoundError(f"{self.settings.name} GGUF model not found: {self.settings.model_path}")

        executable = self.ensure_runtime()
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path()
        self._log_handle = log_path.open("w", encoding="utf-8", errors="replace")

        n_gpu_layers = self._effective_gpu_layers()
        cmd = [
            str(executable),
            "-m",
            str(self.settings.model_path),
            "-c",
            str(self.settings.n_ctx),
            "-ngl",
            str(n_gpu_layers),
            "--host",
            self.settings.host,
            "--port",
            str(self.settings.port),
        ]
        if self.settings.main_gpu >= 0:
            cmd += ["--main-gpu", str(self.settings.main_gpu)]
        if self.settings.parallel > 0:
            cmd += ["--parallel", str(self.settings.parallel)]
        if self.settings.embedding:
            cmd.append("--embedding")
        if self.settings.pooling:
            cmd += ["--pooling", self.settings.pooling]
        cmd += list(self.settings.extra_args)

        command_line = " ".join(cmd)
        print(f"[llama-server:{self.settings.name}] starting: {command_line}")
        if self._log_handle:
            self._log_handle.write(f"[llama-server:{self.settings.name}] command: {command_line}\n")
            self._log_handle.write(f"[llama-server:{self.settings.name}] cwd: {BASE_DIR}\n")
            self._log_handle.write(f"[llama-server:{self.settings.name}] CUDA_PATH: {os.environ.get('CUDA_PATH', '')}\n")
            self._log_handle.write(f"[llama-server:{self.settings.name}] PATH head: {os.environ.get('PATH', '')[:1000]}\n")
            self._log_handle.flush()
        self.process = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=self._runtime_env(),
        )

        self._wait_until_ready()
        self._validate_started_runtime(log_path, n_gpu_layers)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._close_log_handle()

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _close_log_handle(self) -> None:
        if self._log_handle:
            try:
                self._log_handle.flush()
            except Exception:
                pass
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def _log_path(self) -> Path:
        return self.settings.log_dir / f"llama-server-{self.settings.name}-{self.settings.port}.log"

    def _read_log_tail(self, limit: int = 12000) -> str:
        path = self._log_path()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"<failed to read log {path}: {exc}>"
        text = text[-limit:]
        return text if text.strip() else "<log is empty>"

    def _runtime_env(self) -> dict[str, str]:
        env = os.environ.copy()
        cuda_path = env.get("CUDA_PATH")
        if cuda_path:
            current_path = env.get("PATH", "")
            preferred_paths = [
                str(Path(cuda_path) / "bin" / "x64"),
                str(Path(cuda_path) / "bin"),
                str(Path(cuda_path) / "libnvvp"),
            ]
            prepend: list[str] = []
            current_lower = current_path.lower()
            for candidate in preferred_paths:
                if candidate.lower() not in current_lower:
                    prepend.append(candidate)
            if prepend:
                env["PATH"] = os.pathsep.join(prepend + [current_path])
        return env

    def _wait_until_ready(self) -> None:
        deadline = time.time() + int(_runtime_cfg("startup_timeout_seconds", 180))
        last_error = ""
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                return_code = self.process.returncode
                self._close_log_handle()
                raise RuntimeError(
                    f"llama-server for {self.settings.name} exited early with code {return_code}. "
                    f"Log: {self._log_path()}\n"
                    f"--- log tail ---\n{self._read_log_tail()}"
                )
            try:
                response = requests.get(f"{self.settings.base_url}/health", timeout=2)
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = str(exc)
            try:
                response = requests.get(f"{self.settings.base_url}/v1/models", timeout=2)
                if response.status_code < 500:
                    return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1)
        raise RuntimeError(
            f"llama-server for {self.settings.name} did not become ready: {last_error}. "
            f"Log: {self._log_path()}\n--- log tail ---\n{self._read_log_tail()}"
        )

    def _endpoint_alive(self) -> bool:
        try:
            response = requests.get(f"{self.settings.base_url}/health", timeout=1)
            return response.status_code < 500
        except Exception:
            return False

    def _cuda_build_marker(self) -> Path:
        return self.settings.build_dir / ".llama-server-cuda-build"

    def _cuda_build_marker_exists(self) -> bool:
        marker = self._cuda_build_marker()
        if not marker.exists():
            return False
        try:
            return marker.read_text(encoding="utf-8", errors="replace").strip().lower() == "cuda=on"
        except Exception:
            return False

    def _write_build_marker(self, *, cuda: bool) -> None:
        try:
            marker = self._cuda_build_marker()
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("cuda=on" if cuda else "cuda=off", encoding="utf-8")
        except Exception:
            pass

    def _validate_started_runtime(self, log_path: Path, n_gpu_layers: int) -> None:
        if not self._wants_cuda() or n_gpu_layers <= 0:
            return
        time.sleep(0.5)
        try:
            if self._log_handle:
                self._log_handle.flush()
        except Exception:
            pass
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")[-12000:].lower()
        except Exception:
            return
        cpu_only_markers = (
            "compiled without support for gpu offload",
            "no usable gpu found",
            "--gpu-layers option will be ignored",
        )
        if any(item in text for item in cpu_only_markers):
            self.stop()
            raise RuntimeError(
                f"llama-server for {self.settings.name} started as CPU-only, but CUDA was requested. "
                f"Remove {self.settings.build_dir} and rebuild with CUDA, or set device: cpu. Log: {log_path}"
            )
        if "offloaded 0/" in text and "offloaded 0/0" not in text:
            self.stop()
            raise RuntimeError(
                f"llama-server for {self.settings.name} did not offload layers to GPU although -ngl {n_gpu_layers} was used. "
                f"Log: {log_path}"
            )

    def _configured_executable(self) -> Path | None:
        if platform.system().lower() == "windows":
            return self.settings.executable_windows
        return self.settings.executable_linux

    def _default_executable_path(self) -> Path:
        if platform.system().lower() == "windows":
            return self.settings.build_dir / "bin" / "Release" / "llama-server.exe"
        return self.settings.build_dir / "bin" / "llama-server"

    def _find_built_executable(self) -> Path | None:
        names = ["llama-server.exe"] if platform.system().lower() == "windows" else ["llama-server"]
        candidates: list[Path] = []
        configured = self._configured_executable()
        if configured:
            candidates.append(configured)
        candidates.append(self._default_executable_path())
        candidates += [path for name in names for path in self.settings.build_dir.rglob(name)] if self.settings.build_dir.exists() else []
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return candidate.resolve()
        return None

    def _clone_runtime(self) -> None:
        if self.settings.runtime_dir.exists() and (self.settings.runtime_dir / "CMakeLists.txt").exists():
            return
        if not shutil.which("git"):
            raise RuntimeError("git is required to auto-clone llama.cpp")
        self.settings.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1"]
        if self.settings.git_ref and self.settings.git_ref != "master":
            cmd += ["--branch", self.settings.git_ref]
        cmd += [self.settings.repo_url, str(self.settings.runtime_dir)]
        subprocess.check_call(cmd)

    def _build_runtime(self, *, cuda: bool, clean: bool = False) -> None:
        if not shutil.which("cmake"):
            raise RuntimeError("cmake is required to build llama.cpp")
        if clean and self.settings.build_dir.exists():
            shutil.rmtree(self.settings.build_dir)
        self.settings.build_dir.mkdir(parents=True, exist_ok=True)

        generator = self._select_cmake_generator(cuda=cuda)

        configure = [
            "cmake",
            "-S",
            str(self.settings.runtime_dir),
            "-B",
            str(self.settings.build_dir),
            f"-DGGML_CUDA={'ON' if cuda else 'OFF'}",
        ]
        if generator:
            configure += ["-G", generator]
        if generator.lower() == "ninja":
            configure += ["-DCMAKE_BUILD_TYPE=Release"]

        print(f"[llama-server] configuring llama.cpp: cuda={cuda}, generator={generator or 'default'}")
        _run_build_command(configure)

        build = ["cmake", "--build", str(self.settings.build_dir), "--config", "Release", "--parallel"]
        print("[llama-server] building llama.cpp")
        _run_build_command(build)
        self._write_build_marker(cuda=cuda)

    def _select_cmake_generator(self, *, cuda: bool) -> str:
        if cuda:
            generator = str(
                _runtime_cfg("cmake_generator_cuda", _runtime_cfg("cmake_generator", "")) or ""
            ).strip()
            if platform.system().lower() == "windows":
                if generator.lower() == "ninja" and not shutil.which("ninja"):
                    raise RuntimeError(
                        "CUDA build requests CMake generator Ninja, but ninja.exe was not found. "
                        "Run `pip install ninja` in the active environment, or set "
                        "runtime.llama_server.cmake_generator_cuda to an installed generator. "
                        "CPU fallback works without Ninja when fallback_cpu is true."
                    )
                if not generator and shutil.which("ninja"):
                    # Visual Studio generators can fail with CUDA using "No CUDA toolset found"
                    # when CUDA's MSBuild integration is absent. Ninja + vcvars64 uses nvcc/cl directly.
                    generator = "Ninja"
            return generator

        # CPU fallback must not inherit the CUDA generator. This lets CMake choose
        # the installed Visual Studio generator when Ninja is unavailable.
        return str(_runtime_cfg("cmake_generator_cpu", "") or "").strip()

    def _wants_cuda(self) -> bool:
        if self.settings.device == "cpu":
            return False
        if self.settings.device.startswith("cuda"):
            return True
        return self.settings.prefer_cuda

    def _effective_gpu_layers(self) -> int:
        if not self._wants_cuda():
            return 0
        if self.settings.n_gpu_layers < 0:
            return 999
        return self.settings.n_gpu_layers


def get_or_start_server(settings: LlamaServerSettings) -> ManagedLlamaServer:
    key = f"{settings.name}:{settings.host}:{settings.port}:{settings.model_path}"
    server = _RUNNING.get(key)
    if server is None:
        server = ManagedLlamaServer(settings)
        _RUNNING[key] = server
    server.start()
    return server


def stop_all_servers() -> None:
    for server in list(_RUNNING.values()):
        try:
            server.stop()
        except Exception:
            pass


atexit.register(stop_all_servers)

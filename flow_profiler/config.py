from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable, Sequence


class ConfigError(ValueError):
    """Raised when profiler configuration is unsafe or invalid."""


@dataclass(frozen=True)
class PromptInput:
    prompt_id: str
    text: str


@dataclass(frozen=True)
class FlowProfilerConfig:
    mode: str
    prompts: tuple[PromptInput, ...]
    output_dir: Path
    requested_outputs: int
    aspect_ratio: str
    max_generations: int
    delay_sec: float
    project_root: Path
    user_data_dir: Path | None = None
    approve_external_action: bool = False
    timeout_sec: int = 120
    stop_on_first_warning: bool = True


def build_config(
    *,
    mode: str,
    prompt: str | None = None,
    prompt_file: str | Path | None = None,
    prompt_id: str | None = None,
    output_dir: str | Path = "flow_profiler_runs",
    requested_outputs: int = 1,
    aspect_ratio: str = "landscape",
    max_generations: int | None = None,
    delay_sec: float = 0,
    project_root: str | Path | None = None,
    user_data_dir: str | Path | Sequence[str | Path] | None = None,
    approve_external_action: bool = False,
    timeout_sec: int = 120,
) -> FlowProfilerConfig:
    root = Path(project_root or Path.cwd()).resolve()
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode not in {"dry-run", "single", "browser-check"}:
        raise ConfigError("unsupported mode")

    if normalized_mode == "single":
        _validate_single_prompt_source(prompt=prompt, prompt_file=prompt_file)
    elif normalized_mode == "browser-check":
        _validate_browser_check_prompt_source(
            prompt=prompt,
            prompt_file=prompt_file,
            prompt_id=prompt_id,
        )
    else:
        if prompt and prompt_file:
            raise ConfigError("provide either prompt or prompt_file")
        if not prompt and not prompt_file:
            raise ConfigError("a prompt source is required")

    out_dir = _safe_output_dir(output_dir, root)
    prompt_items = (
        []
        if normalized_mode == "browser-check"
        else _load_prompts(prompt, prompt_file, prompt_id, root)
    )

    if requested_outputs < 1:
        raise ConfigError("requested_outputs must be at least 1")
    if normalized_mode == "browser-check":
        if max_generations not in (None, 1):
            raise ConfigError("browser-check requires max_generations to be absent or 1")
        max_generations = 0
    elif max_generations is None:
        max_generations = len(prompt_items)
    if max_generations < 1:
        if normalized_mode != "browser-check":
            raise ConfigError("max_generations must be at least 1")
    if delay_sec < 0:
        raise ConfigError("delay_sec must not be negative")

    resolved_user_data_dir: Path | None = None
    if normalized_mode == "single":
        _validate_single_invariants(
            prompt=prompt,
            prompt_file=prompt_file,
            requested_outputs=requested_outputs,
            max_generations=max_generations,
            delay_sec=delay_sec,
            approve_external_action=approve_external_action,
            timeout_sec=timeout_sec,
        )
        resolved_user_data_dir = _safe_user_data_dir(user_data_dir, root)
    elif normalized_mode == "browser-check":
        _validate_browser_check_invariants(
            requested_outputs=requested_outputs,
            delay_sec=delay_sec,
            approve_external_action=approve_external_action,
            timeout_sec=timeout_sec,
        )
        resolved_user_data_dir = _safe_optional_user_data_dir(user_data_dir, root)
    elif user_data_dir is not None:
        raise ConfigError("user_data_dir is only supported in single mode")
    elif approve_external_action:
        raise ConfigError("approve_external_action is only supported in single mode")

    return FlowProfilerConfig(
        mode=normalized_mode,
        prompts=tuple(prompt_items),
        output_dir=out_dir,
        requested_outputs=requested_outputs,
        aspect_ratio=aspect_ratio or "landscape",
        max_generations=max_generations,
        delay_sec=float(delay_sec),
        project_root=root,
        user_data_dir=resolved_user_data_dir,
        approve_external_action=bool(approve_external_action),
        timeout_sec=int(timeout_sec),
        stop_on_first_warning=True,
    )


def _validate_single_invariants(
    *,
    prompt: str | None,
    prompt_file: str | Path | None,
    requested_outputs: int,
    max_generations: int,
    delay_sec: float,
    approve_external_action: bool,
    timeout_sec: int,
) -> None:
    if not approve_external_action:
        raise ConfigError("single mode requires --approve-external-action")
    _validate_single_prompt_source(prompt=prompt, prompt_file=prompt_file)
    if requested_outputs != 1:
        raise ConfigError("single mode requires requested_outputs=1")
    if max_generations != 1:
        raise ConfigError("single mode requires max_generations=1")
    if delay_sec != 0:
        raise ConfigError("single mode requires delay_sec=0")
    if timeout_sec < 30 or timeout_sec > 300:
        raise ConfigError("timeout_sec must be between 30 and 300")


def _validate_browser_check_invariants(
    *,
    requested_outputs: int,
    delay_sec: float,
    approve_external_action: bool,
    timeout_sec: int,
) -> None:
    if not approve_external_action:
        raise ConfigError("browser-check requires --approve-external-action")
    if requested_outputs != 1:
        raise ConfigError("browser-check requires requested_outputs=1")
    if delay_sec != 0:
        raise ConfigError("browser-check requires delay_sec=0")
    if timeout_sec < 5 or timeout_sec > 300:
        raise ConfigError("timeout_sec must be between 5 and 300")


def _validate_single_prompt_source(
    *,
    prompt: str | None,
    prompt_file: str | Path | None,
) -> None:
    if prompt_file is not None:
        raise ConfigError("single mode rejects --prompt-file")
    if prompt is None or not prompt.strip():
        raise ConfigError("single mode requires --prompt")


def _validate_browser_check_prompt_source(
    *,
    prompt: str | None,
    prompt_file: str | Path | None,
    prompt_id: str | None,
) -> None:
    if prompt is not None:
        raise ConfigError("browser-check rejects --prompt")
    if prompt_file is not None:
        raise ConfigError("browser-check rejects --prompt-file")
    if prompt_id is not None:
        raise ConfigError("browser-check rejects --prompt-id")


def _load_prompts(
    prompt: str | None,
    prompt_file: str | Path | None,
    prompt_id: str | None,
    project_root: Path,
) -> list[PromptInput]:
    if prompt is not None:
        text = prompt.strip()
        if not text:
            raise ConfigError("prompt must not be empty")
        return [PromptInput(prompt_id or "prompt-001", text)]

    file_path = _safe_prompt_file(prompt_file, project_root)
    try:
        raw_lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError("prompt_file could not be read") from exc

    prompts = [line.strip() for line in raw_lines if line.strip()]
    if not prompts:
        raise ConfigError("prompt_file did not contain prompts")
    if prompt_id and len(prompts) == 1:
        ids = [prompt_id]
    else:
        ids = _stable_ids(len(prompts))
    return [PromptInput(item_id, text) for item_id, text in zip(ids, prompts)]


def _stable_ids(count: int) -> list[str]:
    width = max(3, len(str(count)))
    return [f"prompt-{index:0{width}d}" for index in range(1, count + 1)]


def _safe_output_dir(value: str | Path, project_root: Path) -> Path:
    raw = Path(value)
    if _has_parent_reference(raw) or str(value).strip() in {"", "."}:
        raise ConfigError("unsafe output_dir")
    resolved = (project_root / raw if not raw.is_absolute() else raw).resolve()
    _ensure_inside_project(resolved, project_root, "unsafe output_dir")
    if resolved == project_root or _has_unsafe_part(resolved):
        raise ConfigError("unsafe output_dir")
    return resolved


def _safe_prompt_file(value: str | Path | None, project_root: Path) -> Path:
    if value is None:
        raise ConfigError("a prompt source is required")
    raw = Path(value)
    if _has_parent_reference(raw) or str(value).strip() in {"", "."}:
        raise ConfigError("unsafe prompt_file")
    resolved = (project_root / raw if not raw.is_absolute() else raw).resolve()
    _ensure_inside_project(resolved, project_root, "unsafe prompt_file")
    if resolved == project_root or _has_unsafe_part(resolved) or _has_unsafe_suffix(resolved):
        raise ConfigError("unsafe prompt_file")
    if not resolved.is_file():
        raise ConfigError("prompt_file could not be read")
    return resolved


def _safe_user_data_dir(
    value: str | Path | Sequence[str | Path] | None,
    project_root: Path,
) -> Path:
    if value is None:
        raise ConfigError("single mode requires --user-data-dir")
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ConfigError("single mode requires exactly one --user-data-dir")
        value = value[0]
    raw_text = str(value).strip()
    if not raw_text:
        raise ConfigError("unsafe user_data_dir")
    if os.pathsep and os.pathsep in raw_text:
        raise ConfigError("unsafe user_data_dir")
    raw = Path(raw_text)
    if _has_parent_reference(raw):
        raise ConfigError("unsafe user_data_dir")
    resolved = (project_root / raw if not raw.is_absolute() else raw).resolve()
    if not resolved.exists():
        raise ConfigError("user_data_dir does not exist")
    if not resolved.is_dir():
        raise ConfigError("user_data_dir must be an existing directory")
    return resolved


def _safe_optional_user_data_dir(
    value: str | Path | Sequence[str | Path] | None,
    project_root: Path,
) -> Path | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return None
        if len(value) != 1:
            raise ConfigError("browser-check accepts zero or one --user-data-dir")
        value = value[0]
    return _safe_user_data_dir(value, project_root)


def _ensure_inside_project(path: Path, project_root: Path, message: str) -> None:
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ConfigError(message) from exc


def _has_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _has_unsafe_suffix(path: Path) -> bool:
    return path.suffix.lower() == "." + "har"


def _has_unsafe_part(path: Path) -> bool:
    unsafe = _unsafe_names()
    return any(part.lower() in unsafe for part in path.parts)


def _unsafe_names() -> set[str]:
    return {
        ".git",
        "." + "env",
        "." + "env_flow",
        "google" + "_profile",
        "api" + "_config.json",
    }


def rejected_modes() -> Sequence[str]:
    return ("batch", "ramp")


def validate_no_dangerous_flags(flag_names: Iterable[str]) -> None:
    blocked = {
        "account-switching",
        "adapter",
        "batch",
        "browser",
        "captcha",
        "continue-warning",
        "csv",
        "dangerous-mode",
        "ignore-warning",
        "proxy",
        "ramp",
        "retry",
        "telegram",
    }
    for flag in flag_names:
        normalized = flag.lstrip("-").replace("_", "-").lower()
        if normalized in blocked:
            raise ConfigError("unsupported flag")

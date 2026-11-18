"""Where tkn-innerloop keeps its plans and caches."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


def cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def cache_dir(root: Path) -> Path:
    """Per-checkout state, kept out of the working tree so repository-wide tools never see it."""
    base = cache_home()
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return base / "tkn-innerloop" / f"{root.name}-{key}"


def home_relative(path: Path) -> str:
    return f"~/{path.relative_to(Path.home())}" if path.is_relative_to(Path.home()) else str(path)


def source_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError(f"Input must be repository-relative: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Input must be a file inside the repository: {relative}")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_name(root: Path, pipeline: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", str(pipeline.relative_to(root))).lstrip(".")


def cache_path(root: Path, pipeline: Path, explicit: list[str] | None = None) -> Path:
    """One plan per pipeline for the automatic scope, one per chosen --steps set otherwise."""
    name = cache_name(root, pipeline)
    if explicit:
        name += "." + hashlib.sha256(",".join(explicit).encode()).hexdigest()[:8]
    return cache_dir(root) / f"{name}.plan.json"


def write_cache(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

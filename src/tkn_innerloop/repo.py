"""Git repository helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL
    ).strip()


def repo_root(start: Path) -> Path:
    try:
        return Path(git(start.resolve(), "rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Not a Git working tree: {start}") from exc


def default_base(root: Path) -> str:
    """The PR target ref: origin's default branch, else main, else master."""
    try:
        return git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except subprocess.CalledProcessError:
        pass
    for candidate in ("main", "master"):
        try:
            git(root, "rev-parse", "--verify", "--quiet", candidate)
            return candidate
        except subprocess.CalledProcessError:
            continue
    raise ValueError("Cannot tell the PR target branch; pass --base REF")


def guess_root(path: Path) -> Path:
    try:
        return repo_root(path.parent)
    except ValueError:
        return path.parent.resolve()


def relative_name(path: Path, root: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def repo_yaml_files(root: Path) -> list[str]:
    """Tracked and untracked, non-ignored YAML files; every file when this is not a Git tree."""
    try:
        listed = git(
            root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            "--", "*.yaml", "*.yml",
        ).split("\0")
    except (subprocess.CalledProcessError, OSError):
        listed = [
            str(p.relative_to(root))
            for p in root.rglob("*.y*ml")
            if ".git" not in p.relative_to(root).parts
        ]
    return sorted(
        name
        for name in listed
        if name.endswith((".yaml", ".yml"))
        and (root / name).is_file()
    )

"""Collect local, sanitized source for read-only PipelineRun assessment."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import yaml

from .repo import repo_yaml_files
from .tekton import DEFINITION_KIND

YAML_SUFFIXES = {".yaml", ".yml"}
DOCUMENT_START = re.compile(r"^---(?=[ \t\r\n]|$)", re.MULTILINE)
SENSITIVE = re.compile(
    r"secret|credential|password|(?:^|[-_.])(?:[^-_.]+)?token(?:$|[-_.])|api[-_]?key|"
    r"private[-_]?key|authorization|kubeconfig|dockerconfig|ssh[-_]?key|bearer|access[-_]?key|"
    r"last-applied-configuration",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"://[^/\s]+@|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[a-zA-Z0-9_]+|github_pat_[a-zA-Z0-9_]+|AKIA[A-Z0-9]{16}|sk-[\w-]{16,})\b|"
    r"(?:password|token|secret|api[-_]?key|authorization|credential)[\w-]*[ \t]*[:=]|"
    r"--(?:password|token|secret|api[-_]?key)\b|authorization[ \t]*:|bearer[ \t]+\S",
    re.IGNORECASE,
)
REDACTED = "[redacted credential data]"
PRIVATE_PATH = re.compile(r"(?:^|[._-])(?:secrets?|credentials?|kubeconfig|dockerconfig|id_rsa)(?:[.]|$)", re.I)
CHECKOUT_PART = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_. -]{0,127}")


def checkout_path_observation(root: Path, path: Path, *, output: bool) -> str:
    """Inspect only directory entries under the checkout, without following symlinks."""
    if (path.is_absolute() or not path.parts or len(path.parts) > 12
        or any(not CHECKOUT_PART.fullmatch(part) or SENSITIVE.search(part) or PRIVATE_PATH.search(part)
               for part in path.parts)):
        return "path not eligible for checkout inspection"
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with ExitStack() as opened:
            directory = os.open(root, flags)
            opened.callback(os.close, directory)
            for part in path.parts[:-1]:
                directory = os.open(part, flags, dir_fd=directory)
                opened.callback(os.close, directory)
            try:
                entry = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                if not output:
                    return "input file is missing"
            else:
                if not stat.S_ISREG(entry.st_mode):
                    return "target is not a regular file"
                if not entry.st_mode & (0o222 if output else 0o444):
                    return "target file has no write permission bits" if output else "input file has no read permission bits"
            if not output:
                return "regular input file present"
            if os.access(".", os.W_OK | os.X_OK, dir_fd=directory):
                return "output parent exists and is writable on the host"
            return "output parent is not writable on the host"
    except FileNotFoundError:
        return "output parent is missing" if output else "input file or parent is missing"
    except OSError:
        return "cannot inspect path without crossing a symlink or inaccessible directory"


@dataclass(frozen=True, slots=True)
class Workflow:
    path: Path
    number: int
    identity: str
    document: dict
    siblings: list[tuple[int, object]]


def diagnostic(identity: str, problem: str) -> None:
    # Keep untrusted names and errors from emitting terminal control characters.
    safe = "".join(char if char.isprintable() else " " for char in f"{identity}: {problem}")
    print(f"  {safe}", file=sys.stderr)


def _sanitize(value: object, depth: int = 40) -> object:
    if depth == 0:
        raise ValueError("Manifest has recursive or excessively nested values")
    if isinstance(value, dict):
        named_secret = SENSITIVE.search(str(value.get("name", "")))
        return {
            str(key): REDACTED
            if SENSITIVE.search(str(key)) or (named_secret and key in ("value", "default"))
            # Preserve the presence of references but never dereference them.
            else _sanitize(item, depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item, depth - 1) for item in value]
    if isinstance(value, str) and SENSITIVE_TEXT.search(value):
        return REDACTED
    return value


def _documents(text: str) -> Iterator[tuple[int, object, str]]:
    """Recover subsequent explicit YAML documents after an invalid one."""
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        starts = [match.start() for match in DOCUMENT_START.finditer(text)]
        if not starts or any(
            line.strip() and not line.lstrip().startswith(("#", "%"))
            for line in text[:starts[0]].splitlines()
        ):
            starts.insert(0, 0)
        for number, (start, end) in enumerate(zip(starts, [*starts[1:], len(text)], strict=True), 1):
            try:
                yield number, yaml.safe_load(text[start:end]), ""
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                line = text.count("\n", 0, start) + mark.line + 1 if mark else None
                yield number, None, "invalid YAML" + (f" near line {line}" if line else "")
    else:
        for number, document in enumerate(documents, 1):
            yield number, document, ""


def manifest_path(path: Path, root: Path) -> bool:
    """Only regular checkout files reached without symlinks."""
    return (path.is_relative_to(root)
            and not any(part.is_symlink() for part in (path, *path.parents)
                        if part != root and part.is_relative_to(root))
            and path.is_file())


def _manifest(
    path: Path, root: Path, hashes: dict[str, str], *, report: bool, issues: list[str] | None = None,
) -> list[tuple[int, object]]:
    name = path.relative_to(root).as_posix()
    try:
        # Skip credential-named files and all symlinks, including internal aliases.
        if any(PRIVATE_PATH.search(part) for part in path.relative_to(root).parts):
            return []
        if not manifest_path(path, root):
            if report:
                diagnostic(name, "skipping non-regular file or symbolic link")
            return []
        data = path.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError, RecursionError):
        if report:
            diagnostic(name, "could not read YAML")
        if issues is not None:
            issues.append(name)
        return []
    if not report and not DEFINITION_KIND.search(text):
        return []
    hashes[name] = hashlib.sha256(data).hexdigest()
    parsed = []
    for number, raw, error in _documents(text):
        if not error and raw is not None:
            if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str) or not raw["kind"]:
                error = "expected a Kubernetes resource with a kind"
            elif raw["kind"] in ("PipelineRun", "Pipeline", "Task", "StepAction"):
                if raw.get("metadata") is not None and not isinstance(raw["metadata"], dict):
                    error = "metadata must be a mapping"
                else:
                    try:
                        parsed.append((number, _sanitize({key: raw[key] for key in
                                      ("apiVersion", "kind", "metadata", "spec") if key in raw})))
                    except ValueError as exc:
                        error = str(exc)
        if error and report:
            diagnostic(f"{name}#{number}", error)
            if issues is not None:
                issues.append(f"{name}#{number}")
    return parsed


def discover(
    root: Path, hashes: dict[str, str], issues: list[str],
) -> tuple[list[Workflow], dict[Path, list[tuple[int, object]]]]:
    """Find PipelineRuns in path and document order; keep same-file definitions."""
    tekton = root / ".tekton"
    if tekton.is_symlink():
        diagnostic(".tekton", "skipping symbolic link")
        return [], {}
    if not tekton.is_dir():
        return [], {}
    manifests = {}
    runs = []
    def walk_error(err: OSError) -> None:
        issues.append(".tekton")
        diagnostic(".tekton", err.strerror or "could not read directory")

    for directory, dirs, files in os.walk(tekton, onerror=walk_error):
        dirs[:] = sorted(part for part in dirs if not PRIVATE_PATH.search(part))
        for part in list(dirs):
            if (Path(directory) / part).is_symlink():
                diagnostic(str((Path(directory) / part).relative_to(root)), "skipping symbolic link")
                dirs.remove(part)
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix not in YAML_SUFFIXES:
                continue
            documents = _manifest(path, root, hashes, report=True, issues=issues)
            manifests[path] = documents
            for number, raw in documents:
                if raw["kind"] != "PipelineRun":
                    continue
                metadata = raw.get("metadata") or {}
                title = metadata.get("name") or metadata.get("generateName")
                identity = f"{path.relative_to(root).as_posix()}#{number}"
                if isinstance(title, str) and title:
                    identity += f" ({title})"
                runs.append(Workflow(path, number, identity, raw, documents))
    return sorted(runs, key=lambda run: (run.path, run.number)), manifests


def definition_documents(
    root: Path, manifests: dict[Path, list[tuple[int, object]]], hashes: dict[str, str],
) -> Iterable[tuple[str, dict]]:
    """Offer local definitions to the existing inventory lookup, never remote ones."""
    paths = {*manifests, *(root / name for name in repo_yaml_files(root))}
    for path in sorted(paths):
        documents = manifests[path] if path in manifests else _manifest(path, root, hashes, report=False)
        for _, raw in documents:
            if raw["kind"] in ("Pipeline", "Task", "StepAction"):
                yield path.relative_to(root).as_posix(), raw

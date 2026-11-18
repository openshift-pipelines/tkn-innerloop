"""Pipelines-as-Code annotations, placeholders and tkn pac resolve."""

from __future__ import annotations

import re
import subprocess

PAC_UNBOUND = re.compile(r"\{\{")
PAC_ANNOTATION = "pipelinesascode.tekton.dev/"
# PaC placeholders with a local equivalent; the runner supplies these shell variables.
PAC_BINDINGS = {
    "revision": "$HEAD_SHA",
    "source_branch": "$HEAD_REF",
    "target_branch": "$BASE_REF",
    "event_type": "pull_request",
}
PAC_BOUND = re.compile(r"\{\{\s*(" + "|".join(PAC_BINDINGS) + r")\s*\}\}")


def annotations(raw: object) -> dict:
    metadata = raw.get("metadata") if isinstance(raw, dict) else None
    found = metadata.get("annotations") if isinstance(metadata, dict) else None
    return found if isinstance(found, dict) else {}


def pac_remote(documents: list) -> bool:
    """Whether PaC annotations name pipelines or tasks it fetches before running."""
    return any(
        str(key).startswith((f"{PAC_ANNOTATION}task", f"{PAC_ANNOTATION}pipeline"))
        for raw in documents
        for key in annotations(raw)
    )


def pac_profile(documents: list) -> bool:
    """Pipelines-as-Code files carry its annotations; only those get PaC placeholder handling."""
    return any(
        str(key).startswith(PAC_ANNOTATION)
        for document in documents
        for key in annotations(document)
    )


def bind_pac(value: object) -> object:
    """Replace known PaC placeholders with runner shell variables.

    Images are left alone: the runtime receives them literally, never through a shell.
    """
    if isinstance(value, str):
        return PAC_BOUND.sub(lambda match: PAC_BINDINGS[match.group(1)], value)
    if isinstance(value, dict):
        return {key: item if key == "image" else bind_pac(item) for key, item in value.items()}
    if isinstance(value, list):
        return [bind_pac(item) for item in value]
    return value


RESOLVE_FORMAT = 2
_tkn_pac_versions: dict[str, str] = {}


def tkn_pac_version(tool: str) -> str:
    if tool not in _tkn_pac_versions:
        try:
            result = subprocess.run(
                [tool, "version"], capture_output=True, text=True, timeout=30, check=False
            )
            _tkn_pac_versions[tool] = result.stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            _tkn_pac_versions[tool] = "unknown"
    return _tkn_pac_versions[tool]

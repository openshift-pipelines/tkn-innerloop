"""Konflux checkout and trusted-artifact steps, which the local checkout replaces."""

from __future__ import annotations

import re

# Konflux restores the checkout from an OCI artifact with these steps; locally the worktree is mounted.
TRUSTED_ARTIFACTS = "quay.io/konflux-ci/build-trusted-artifacts"
GIT_CLONE_BUNDLE = re.compile(r"/task-git-clone[^/@]*@")
PROVIDED = "provided by the local checkout"


def restored_paths(steps: list) -> list[str]:
    """Directories Konflux trusted-artifact steps restore the source checkout into, longest first."""
    paths = set()
    for step in steps:
        args = step.get("args") if isinstance(step, dict) else None
        if trusted_artifact(step) and isinstance(args, list) and args[:1] == ["use"]:
            for arg in args[1:]:
                artifact, _, path = str(arg).partition("=")
                if "SOURCE_ARTIFACT" in artifact and path.startswith("/"):
                    paths.add(path.rstrip("/"))
    return sorted(paths, key=len, reverse=True)


def bind_restored(value: object, paths: list[str]) -> object:
    """Point paths of the restored checkout at $WORKSPACE; images are left alone."""
    if not paths:
        return value
    if isinstance(value, str):
        for path in paths:
            value = re.sub(re.escape(path) + r"(?![\w.-])", "$WORKSPACE", value)
        return value
    if isinstance(value, dict):
        return {key: item if key == "image" else bind_restored(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [bind_restored(item, paths) for item in value]
    return value


def trusted_artifact(step: object) -> bool:
    return isinstance(step, dict) and str(step.get("image", "")).startswith(TRUSTED_ARTIFACTS)


def provided_by_checkout(source: dict) -> bool:
    """Konflux clone and trusted-artifact steps only recreate the checkout that is mounted locally."""
    return trusted_artifact(source["step"]) or any(
        GIT_CLONE_BUNDLE.search(name) for name in source.get("defined_in", [])
    )

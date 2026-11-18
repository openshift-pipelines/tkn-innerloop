"""Reading Tekton YAML: documents, references, parameters and steps."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml

from .repo import repo_yaml_files

SUBSTITUTION = re.compile(
    r"\{\{.*?\}\}|\$\((?:workspaces|params|context|steps|tasks|results)\.[^)]*\)"
)
TEKTON_UNBOUND = re.compile(r"\$\((?:workspaces|params|context|steps|tasks|results)\.")
DEFINITION_KIND = re.compile(r"^kind:\s*['\"]?(?:Pipeline|Task|StepAction)['\"]?\s*$", re.MULTILINE)
ENTRY_KIND = re.compile(r"^kind:\s*['\"]?(?:PipelineRun|Pipeline)['\"]?\s*$", re.MULTILINE)
# Bare names are looked up on the image PATH (the bash image keeps it in /usr/local/bin).
SHELLS = ("sh", "bash", "/bin/sh", "/bin/bash")
UNRESOLVED_IMAGE = re.compile(r"\$|\{\{")


def adapted_script(source: dict, edits: list) -> str:
    script = source.get("script")
    if script is None:
        if not source.get("command"):
            raise ValueError(
                "Cannot resolve an image entrypoint or remote reference to a script"
            )
        script = shlex.join([*source["command"], *source.get("args", [])]) + "\n"
    if not isinstance(edits, list):
        raise TypeError("Runnable steps need a list of exact script edits")
    for edit in edits:
        if (
            not isinstance(edit, dict)
            or not all(
                isinstance(edit.get(key), str) for key in ("old", "new", "reason")
            )
            or not edit["old"]
            or not edit["reason"].strip()
        ):
            raise ValueError("Each script edit needs old, new and reason strings")
        if script.count(edit["old"]) != 1:
            raise ValueError(
                "Each script edit must match exactly once in the source script"
            )
        script = script.replace(edit["old"], edit["new"], 1)
    return script


def yaml_documents(path: Path) -> list[tuple[int, object]]:
    return [
        (number, document)
        for number, document in enumerate(yaml.safe_load_all(path.read_text()), 1)
        if document is not None
    ]


class Definitions:
    """Pipeline, Task and StepAction documents a pipeline can reference by name.

    The repository is only scanned when a reference is not defined in the pipeline file.
    """

    def __init__(self, root: Path):
        self.root = root
        self._index: dict[tuple[str, str], list[tuple[str, dict]]] | None = None

    def index(self) -> dict[tuple[str, str], list[tuple[str, dict]]]:
        if self._index is None:
            self._index = {}
            for name in repo_yaml_files(self.root):
                try:
                    text = (self.root / name).read_text()
                    if not DEFINITION_KIND.search(text):
                        continue
                    documents = list(yaml.safe_load_all(text))
                except (OSError, UnicodeDecodeError, yaml.YAMLError):
                    continue
                for document in documents:
                    if not isinstance(document, dict) or document.get("kind") not in ("Pipeline", "Task", "StepAction"):
                        continue
                    title = (document.get("metadata") or {}).get("name")
                    if isinstance(title, str) and title:
                        self._index.setdefault((document["kind"], title), []).append((name, document))
        return self._index

    def find(self, kind: str, name: str, own: str, own_documents: list) -> tuple[dict, str]:
        local = [
            document
            for document in own_documents
            if document.get("kind") == kind and (document.get("metadata") or {}).get("name") == name
        ]
        if len(local) > 1:
            raise LookupError(f"{kind} {name} is defined more than once in {own}")
        if local:
            return local[0], own
        matches = [m for m in self.index().get((kind, name), []) if m[0] != own]
        if not matches:
            raise LookupError(f"no {kind} named {name} in the repository")
        if len(matches) > 1:
            files = ", ".join(sorted({m[0] for m in matches}))
            raise LookupError(f"{kind} {name} is defined more than once: {files}")
        return matches[0][1], matches[0][0]


def local_ref(ref: object, kind: str) -> str | None:
    """The name of a reference Tekton would look up in the namespace, else None (remote or other kind)."""
    if not isinstance(ref, dict) or ref.get("resolver") or ref.get("bundle") or ref.get("params"):
        return None
    if (ref.get("kind") or kind) != kind:
        return None
    api = ref.get("apiVersion")
    if api and not str(api).startswith("tekton.dev/"):
        return None
    name = ref.get("name")
    return name if isinstance(name, str) and name else None


def ref_note(ref: object, kind: str = "Task") -> str:
    ref = ref if isinstance(ref, dict) else {}
    if ref.get("resolver"):
        return f"{ref['resolver']} resolver reference, contents not read"
    if ref.get("bundle"):
        return "bundle reference, contents not read"
    return f"{ref.get('kind') or kind} reference, contents not read"


def mapping(value, what: str) -> dict:
    value = value or {}
    if isinstance(value, dict):
        return value
    raise ValueError(f"{what} must be a mapping")


IMAGE_PARAM = re.compile(r"\$\(params(?:\.([\w.-]+)|\[['\"]([\w.-]+)['\"]\])\)")


def param_layer(values: dict, params: object, field: str) -> dict:
    """Overlay params on values; a param whose value is not a plain string hides any default."""
    values = dict(values)
    for p in params if isinstance(params, list) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str) and field in p:
            if isinstance(p[field], str):
                values[p["name"]] = p[field]
            else:
                values.pop(p["name"], None)
    return values


def fill_params(text: str, values: dict) -> str:
    return IMAGE_PARAM.sub(lambda m: values.get(m.group(1) or m.group(2), m.group(0)), text)


def bound_values(declared: object, bindings: object, outer: dict) -> dict:
    """Param values inside a Task or StepAction: its defaults, overridden by the caller's bindings.

    A binding that cannot be worked out (a task result, an array) hides the default
    rather than falling back to it.
    """
    values = param_layer({}, declared, "default")
    for p in bindings if isinstance(bindings, list) else []:
        if isinstance(p, dict) and isinstance(p.get("name"), str) and "value" in p:
            value = fill_params(p["value"], outer) if isinstance(p["value"], str) else None
            if value is None or "$(" in value:
                values.pop(p["name"], None)
            else:
                values[p["name"]] = value
    return values


def task_values(task: dict, task_spec: dict, run_spec: dict, spec: dict) -> dict:
    pipeline = param_layer(param_layer({}, spec.get("params"), "default"), run_spec.get("params"), "value")
    return bound_values(task_spec.get("params"), task.get("params"), pipeline)


def bind_image(step: dict, values: dict) -> dict:
    """Fill $(params.x) in a step image, since a plan must keep the image unchanged."""
    image = step.get("image")
    if not isinstance(image, str) or not IMAGE_PARAM.search(image):
        return step
    return {**step, "image": fill_params(image, values)}


def select_steps(steps: list[dict], selection: str | None) -> list[dict]:
    if selection is None:
        return steps
    requested = [name.strip() for name in selection.split(",") if name.strip()]
    if not requested:
        raise ValueError("Step selection is empty")
    selected = []
    for name in requested:
        matches = [
            s for s in steps if s["id"] == name or s["id"].split("/")[-1] == name
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Unknown or ambiguous step: {name}; use a full step ID from inspect"
            )
        if matches[0] not in selected:
            selected.append(matches[0])
    return [s for s in steps if s in selected]

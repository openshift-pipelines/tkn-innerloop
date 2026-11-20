"""Source-only plan inputs for repository-wide runs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .assess_source import REDACTED, manifest_path
from .inventory import CONFIG_FILE
from .paths import digest
from .tekton import SHELLS, SUBSTITUTION

WORKSPACE_PATH = re.compile(r"\$\(workspaces\.([\w-]+)\.path\)")
RUNNER_VARS = ("WORKSPACE", "BASE_SHA", "HEAD_SHA", "HEAD_REF", "BASE_REF", "RESULTS", "CACHES")


@dataclass(frozen=True, slots=True)
class NativeScope:
    pipeline: str
    document: int
    sources: dict[str, dict]
    inputs: dict[str, str]
    config_hash: str | None
    dependencies: dict[str, tuple[str, ...]]

    def verify(self, root: Path) -> None:
        for name, expected in self.inputs.items():
            path = root / name
            if not manifest_path(path, root) or digest(path) != expected:
                raise ValueError(f"Stale assessment: {name} changed; rerun repository inspection")
        if config_digest(root) != self.config_hash:
            raise ValueError(f"Stale assessment: {CONFIG_FILE} changed; rerun repository inspection")


def config_digest(root: Path) -> str | None:
    path = root / CONFIG_FILE
    if not path.exists() and not path.is_symlink():
        return None
    if not manifest_path(path, root):
        raise ValueError(f"{CONFIG_FILE} must be a regular checkout file, not a symbolic link")
    return digest(path)


def native_host_env() -> dict[str, str]:
    # Do not pass credentials or shell startup hooks from the caller to a bare run.
    return {"PATH": os.getenv("PATH", os.defpath)}


def _declared(entries: list, name: str) -> dict:
    return next((entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name), {})


def _checkout_workspace(task: dict, defaults: dict, pipeline: dict, name: str) -> bool:
    declaration = _declared(defaults.get("workspaces", []), name)
    binding = _declared(task.get("workspaces", []), name)
    workspace = binding.get("workspace")
    return (
        bool(declaration) and declaration.get("mountPath") in (None, "/workspace")
        and isinstance(workspace, str) and set(binding) == {"name", "workspace"}
        and bool(_declared(pipeline["pipeline_defaults"].get("workspaces", []), workspace))
        and _declared(pipeline["spec"].get("workspaces", []), workspace) == {"name": workspace, "emptyDir": {}}
    )


def native_step(source: dict) -> dict:
    """Translate only execution context, without rewriting a source script or argv."""
    step, task, defaults, pipeline = (source[name] for name in
                                      ("step", "task_context", "task_defaults", "pipeline_context"))
    run = pipeline["spec"]
    if REDACTED in json.dumps([step, task, defaults, run], default=str):
        raise ValueError("needs redacted or credential inputs")
    if any(run.get(key) for key in ("podTemplate", "taskRunSpecs", "taskRunTemplate")):
        raise ValueError("needs PipelineRun pod or task overrides")
    if defaults.get("stepTemplate") or defaults.get("volumes"):
        raise ValueError("needs a task step template or volumes")
    if set(step) - {"name", "description", "displayName", "image", "script", "command", "args",
                    "workingDir", "env", "onError", "ref", "params"}:
        raise ValueError("needs step options not supported by a native local run")
    script = step.get("script")
    if script is not None:
        if not isinstance(script, str) or not script.strip():
            raise ValueError("needs a nonempty shell script")
        if step.get("command") or step.get("args"):
            raise ValueError("combines a script with an entrypoint or arguments")
    else:
        command, args = step.get("command"), step.get("args", [])
        if (not isinstance(command, list) or not command or not command[0]
            or not isinstance(args, list) or not all(isinstance(item, str) for item in command + args)):
            raise ValueError("needs an explicit source command and literal argv")
    shell = "/bin/sh"
    if script and script.startswith("#!"):
        interpreter = script.splitlines()[0][2:].split()
        if interpreter[:1] in (["/usr/bin/env"], ["/bin/env"]):
            interpreter = interpreter[1:]
        if len(interpreter) != 1 or interpreter[0] not in SHELLS:
            raise ValueError("script interpreter is not supported by the local runner (sh/bash only)")
        shell = interpreter[0]
    env = {}
    for entry in step.get("env", []):
        if not isinstance(entry, dict) or set(entry) != {"name", "value"} or not isinstance(entry["value"], str):
            raise ValueError("needs non-literal environment inputs")
        name = entry["name"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or name in env or name in RUNNER_VARS or name in {"PATH", "ENV", "BASH_ENV", "SHELLOPTS", "HOME"}
            or name.startswith(("LD_", "DYLD_")) or "$" in entry["value"] or "`" in entry["value"]):
            raise ValueError("needs unsupported environment settings")
        env[name] = entry["value"]
    workdir = step.get("workingDir", "")
    if not isinstance(workdir, str):
        raise ValueError("needs a literal working directory")
    if match := WORKSPACE_PATH.fullmatch(workdir):
        if not _checkout_workspace(task, defaults, pipeline, match[1]):
            raise ValueError("needs a workspace other than the local checkout")
        workdir = ""
    elif workdir == "/workspace":
        names = [entry["name"] for entry in defaults.get("workspaces", []) if isinstance(entry, dict)
                 and isinstance(entry.get("name"), str) and entry.get("mountPath") == "/workspace"]
        if len(names) != 1 or not _checkout_workspace(task, defaults, pipeline, names[0]):
            raise ValueError("'/workspace' needs a task workspace mounted there and bound to the checkout")
        workdir = ""
    text = [script or "", *step.get("command", []), *step.get("args", [])]
    if any(re.search(SUBSTITUTION, item) for item in text):
        raise ValueError("needs Tekton or event substitutions in the source command")
    if workdir and (not re.fullmatch(r"[\w./ -]+", workdir) or Path(workdir).is_absolute() or ".." in Path(workdir).parts):
        raise ValueError("needs a working directory outside the checkout or a substitution")
    return {"shell": shell, "working_dir": "$WORKSPACE" + (f"/{workdir}" if workdir else ""), "env": env}

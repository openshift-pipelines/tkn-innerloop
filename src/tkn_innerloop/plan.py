"""Execution plans: inputs, validation, display and assembly."""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

from .bundles import bundle_path
from .inventory import inventory
from .pac import PAC_BINDINGS, PAC_UNBOUND, RESOLVE_FORMAT, pac_profile, pac_remote, tkn_pac_version
from .paths import cache_dir, cache_name, digest, home_relative, source_path, write_cache
from .tekton import SHELLS, SUBSTITUTION, TEKTON_UNBOUND, UNRESOLVED_IMAGE, adapted_script, yaml_documents
from .ui import stage, step_row, style

ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
RUNNER_VARS = ("WORKSPACE", "BASE_SHA", "HEAD_SHA", "HEAD_REF", "BASE_REF", "RESULTS", "CACHES")
PLAN_VERSION = 8


def plan_input(root: Path, name: str, resolved: str | None) -> Path:
    """A plan input: a repository file, a fetched bundle object, or the plan's tkn pac resolve output."""
    if name.startswith("bundle:"):
        return bundle_path(name)
    if name != resolved:
        return source_path(root, name)
    path = cache_dir(root) / name
    if "/" in name or not path.is_file():
        raise ValueError(f"Stale plan: the tkn pac resolve output {name} is gone; regenerate the plan")
    return path


def definition_files(sources: dict[str, dict]) -> set[str]:
    return {name for source in sources.values() for name in source["defined_in"]}


def plan_sources(plan: dict, root: Path) -> dict[str, dict]:
    """The steps a plan was built from: its tkn pac resolve output, or the pipeline itself."""
    resolved = plan.get("resolved")
    return inventory(
        plan_input(root, resolved or plan["pipeline"], resolved), root, pipeline=plan["pipeline"]
    )


def validate_plan(plan: dict, root: Path) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("version"), int):
        raise ValueError(f"Expected an execution plan with version {PLAN_VERSION}")
    if plan["version"] != PLAN_VERSION:
        raise ValueError(
            "Stale plan: made by another tkn-innerloop version; regenerate the plan"
        )
    pipeline = plan.get("pipeline")
    if not isinstance(pipeline, str):
        raise TypeError("Plan needs a repository-relative pipeline path")
    resolved = plan.get("resolved")
    if resolved is not None and not isinstance(resolved, str):
        raise TypeError("Plan resolved source must be a cache file name")
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict) or pipeline not in inputs or (resolved and resolved not in inputs):
        raise ValueError("Plan inputs must include the pipeline and its SHA-256")
    for name, expected in inputs.items():
        if digest(plan_input(root, name, resolved)) != expected:
            raise ValueError(f"Stale plan: {name} changed; regenerate the plan")
    sources = plan_sources(plan, root)
    missing = definition_files(sources) - set(inputs)
    if missing:
        raise ValueError(
            f"Stale plan: the pipeline now reads {', '.join(sorted(missing))}; regenerate the plan"
        )
    pac = pac_profile([source["pipeline_context"] for source in sources.values()])
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Plan must account for every source step")
    ids = [step.get("id") for step in steps if isinstance(step, dict)]
    if (
        len(ids) != len(steps)
        or not all(isinstance(key, str) for key in ids)
        or len(set(ids)) != len(ids)
        or set(ids) != set(sources)
    ):
        raise ValueError("Plan must account for every source step exactly once")
    for step in steps:
        key = step["id"]
        if step.get("decision") not in ("run", "skip", "unresolved"):
            raise ValueError(f"{key}: decision must be run, skip or unresolved")
        if not isinstance(step.get("reason"), str) or not step["reason"].strip():
            raise ValueError(f"{key}: explain the decision")
        if step["decision"] != "run":
            continue
        if "cache" in sources[key]:
            raise ValueError(f"{key}: tekton-caches actions cannot run locally")
        dependencies = step.get("depends_on")
        earlier = {
            s["id"] for s in steps[: steps.index(step)] if s["decision"] == "run"
        }
        if not isinstance(dependencies, list) or not all(
            isinstance(name, str) and name in earlier for name in dependencies
        ):
            raise ValueError(f"{key}: depends_on must name earlier runnable steps")
        original = sources[key]["step"]
        hints = " ".join(
            str(owner.get(field, ""))
            for owner in (original, sources[key]["task_context"])
            for field in ("description", "displayName")
        )
        if "(skip-local)" in hints.lower():
            raise ValueError(f"{key}: source explicitly requires skip-local")
        if not original.get("image") or step.get("image") != original["image"]:
            raise ValueError(
                f"{key}: runnable steps must retain their source image; resolve refs first"
            )
        if UNRESOLVED_IMAGE.search(step["image"]):
            raise ValueError(
                f"{key}: image {step['image']} has an unresolved substitution; mark the step unresolved"
            )
        if step.get("script") != adapted_script(original, step.get("edits")):
            raise ValueError(
                f"{key}: script must match the source with its declared edits applied"
            )
        for field in ("script", "working_dir", "shell"):
            if not isinstance(step.get(field), str) or not step[field].strip():
                raise ValueError(f"{key}: runnable step needs {field}")
        if step["shell"] not in SHELLS:
            raise ValueError(f"{key}: unsupported shell; mark the step unresolved")
        if not isinstance(step.get("adaptations"), list) or not all(
            isinstance(item, str) for item in step["adaptations"]
        ):
            raise ValueError(f"{key}: list adaptations explicitly")
        env = step.get("env")
        required = step.get("required_env")
        optional = step.setdefault("optional_env", [])
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and ENV_NAME.fullmatch(k) and isinstance(v, str)
            for k, v in env.items()
        ):
            raise ValueError(f"{key}: env must map variable names to string values")
        if not isinstance(required, list) or not all(
            isinstance(name, str) and ENV_NAME.fullmatch(name) for name in required
        ):
            raise ValueError(f"{key}: required_env must list variable names")
        if not isinstance(optional, list) or not all(
            isinstance(name, str) and ENV_NAME.fullmatch(name) for name in optional
        ):
            raise ValueError(f"{key}: optional_env must list variable names")
        if set(optional) & set(required):
            raise ValueError(f"{key}: a variable cannot be both required and optional")
        required = [*required, *optional]
        if set(env) & (set(required) | set(RUNNER_VARS)):
            raise ValueError(
                f"{key}: env conflicts with required or reserved variables"
            )
        if set(RUNNER_VARS) & set(required):
            raise ValueError(
                f"{key}: {', '.join(RUNNER_VARS)} are supplied by the runner"
            )
        required = step["required_env"]
        if any(
            "$(" in value or "`" in value
            for value in [step["working_dir"], *env.values()]
        ):
            raise ValueError(
                f"{key}: env and working_dir are literal values, not shell commands; put substitutions in script edits"
            )
        texts = "\n".join([step["script"], step["working_dir"], *env.values()])
        if TEKTON_UNBOUND.search(texts) or (pac and PAC_UNBOUND.search(texts)):
            found = [
                match
                for match in dict.fromkeys(SUBSTITUTION.findall(texts))
                if pac or not match.startswith("{{")
            ]
            kind = "Tekton or PaC" if pac else "Tekton"
            raise ValueError(
                f"{key}: unresolved {kind} substitution: {', '.join(found[:3])}"
            )
        syntax = subprocess.run(
            [step["shell"], "-n"],
            input=step["script"],
            text=True,
            capture_output=True,
            check=False,
        )
        if syntax.returncode:
            raise ValueError(f"{key}: invalid shell syntax: {syntax.stderr.strip()}")
    return plan


def load_plan(path: Path, root: Path) -> dict:
    return validate_plan(json.loads(path.read_text()), root)


def print_plan(plan: dict, detailed: bool = False) -> None:
    badges = {
        "run": ("✓ RUN LOCAL", "32"),
        "skip": ("↷ SKIP", "2"),
        "unresolved": ("? UNRESOLVED", "33"),
    }
    for step in plan["steps"]:
        if step["decision"] == "skip" and not detailed:
            continue
        label, color = badges[step["decision"]]
        step_row(label, step["id"], step["reason"], color)
        if detailed:
            for adaptation in step.get("adaptations", []):
                for line in textwrap.wrap(
                    f"↳ {adaptation}",
                    max(30, min(shutil.get_terminal_size().columns - 17, 100)),
                ):
                    print(style(f"                 {line}", "36"))
    counts = {
        decision: sum(s["decision"] == decision for s in plan["steps"])
        for decision in ("run", "skip", "unresolved")
    }
    print(
        f"\n  {counts['run']} ready · {counts['skip']} skipped · {counts['unresolved']} unresolved"
    )
    print(
        style(
            "  Source hashes, exact edits and shell syntax checked. Cluster CI is still required.",
            "2",
        )
    )


def print_diff(plan: dict, root: Path) -> None:
    sources = plan_sources(plan, root)
    for step in plan["steps"]:
        if step["decision"] != "run":
            continue
        print(
            f"\n{step['id']}: {step['image']} ({step['shell']}, cwd={step['working_dir']})"
        )
        print(
            f"env={step['env']}, required_env={step['required_env']}, depends_on={step['depends_on']}"
        )
        original = adapted_script(sources[step["id"]]["step"], [])
        diff = list(
            difflib.unified_diff(
                original.splitlines(),
                step["script"].splitlines(),
                fromfile="Tekton source",
                tofile="Local plan",
                lineterm="",
            )
        )
        for line in diff or ["Script unchanged."]:
            print(
                style(
                    line,
                    "32"
                    if line.startswith("+")
                    else "31"
                    if line.startswith("-")
                    else "2",
                )
            )


class PlanRejected(ValueError):
    """The agent's answer was malformed or failed validation, so one corrected retry is worthwhile."""


def assemble_plan(
    proposed: object,
    root: Path,
    relative: str,
    sources: dict,
    selected_ids: list[str],
    resolved: str | None = None,
    extra_inputs: tuple[str, ...] | list[str] = (),
) -> dict:
    """Turn the agent's JSON into a validated plan; any structural problem is PlanRejected."""
    try:
        if not isinstance(proposed, dict):
            raise TypeError("Agent must return one JSON object")
        names = proposed.get("inputs")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("Agent must return a list of inspected input paths")
        names = [
            str(Path(name).relative_to(root)) if Path(name).is_absolute() else name
            for name in names
            if not Path(name).is_relative_to(cache_dir(root))
        ]
        known = {relative, *extra_inputs, *definition_files(sources), *names}
        if resolved:
            known.add(resolved)
        plan = {
            "version": PLAN_VERSION,
            "pipeline": relative,
            **({"resolved": resolved} if resolved else {}),
            "inputs": {
                name: digest(plan_input(root, name, resolved)) for name in sorted(known)
            },
            "steps": proposed.get("steps"),
        }
        if not isinstance(plan["steps"], list):
            raise TypeError("Agent must return a list of steps")
        if any(
            not isinstance(s, dict) or s.get("id") not in selected_ids
            for s in plan["steps"]
        ):
            raise ValueError("Agent returned steps outside the requested scope")
        plan["steps"].extend(
            {
                "id": key,
                "decision": "skip",
                "reason": "Remote cache action" if "cache" in source else "Outside requested scope",
            }
            for key, source in sources.items()
            if key not in selected_ids
        )
        for step in plan["steps"]:
            if step.get("decision") == "run" and step.get("id") in sources:
                step["script"] = adapted_script(
                    sources[step["id"]]["step"], step.get("edits")
                )
        validate_plan(plan, root)
        if any(
            s["decision"] == "run" and s["id"] not in selected_ids
            for s in plan["steps"]
        ):
            raise ValueError("Agent selected steps outside the requested scope")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PlanRejected(str(exc)) from exc
    plan["scope"] = list(selected_ids)
    return plan


def plan_source(root: Path, pipeline: Path, refresh: bool = False) -> tuple[Path, list[str]]:
    """The file to build a plan from, and the extra files it was resolved from.

    PaC pipelines go through `tkn pac resolve` when tkn-pac is installed, so tasks from
    pipelinesascode.tekton.dev/task annotations are fetched. The output is cached and
    reused until the pipeline or a local definition it reads changes.
    """
    documents = [raw for _, raw in yaml_documents(pipeline)]
    if not pac_profile(documents):
        return pipeline, []
    remote = pac_remote(documents)
    tool = shutil.which("tkn-pac")
    if not tool:
        if remote:
            stage("Resolve", "install tkn-pac to fetch annotation tasks; they stay unresolved", "33", "!")
        return pipeline, []
    relative = str(pipeline.relative_to(root))
    try:
        definitions = sorted(
            name for name in definition_files(inventory(pipeline, root)) if not name.startswith("bundle:")
        )
    except (ValueError, KeyError, TypeError):
        definitions = []
    inputs = {name: digest(source_path(root, name)) for name in [relative, *definitions]}
    cmd = ["tkn-pac", "resolve", "--no-secret", "--no-generate-name", "-f", relative]
    for name in definitions:
        cmd += ["-f", name]
    for name, value in PAC_BINDINGS.items():
        cmd += ["-p", f"{name}={value}"]
    key = {"format": RESOLVE_FORMAT, "inputs": inputs, "command": cmd, "tkn-pac": tkn_pac_version(tool)}
    header = f"# tkn-innerloop resolved from: {json.dumps(key, sort_keys=True)}\n"
    out = cache_dir(root) / f"{cache_name(root, pipeline)}.resolved.yaml"
    if out.is_file() and not refresh and out.read_text().startswith(header):
        return out, definitions
    try:
        result = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=120, check=False
        )
        failure = result.returncode or not result.stdout.strip()
        reason = (result.stderr.strip().splitlines() or ["no output"])[-1]
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure, reason = True, str(exc)
    if failure:
        stage("Resolve", f"tkn pac resolve failed ({reason}); using local resolution", "33", "!")
        return pipeline, []
    write_cache(out, header + result.stdout)
    stage("Resolve", f"tkn pac resolve · {home_relative(out)}")
    return out, definitions

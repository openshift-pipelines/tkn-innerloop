"""Listing the steps of a pipeline and deciding which ones can run locally."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .bundles import BundleError, bundle_definition
from .caches import CACHED, local_caches, tekton_cache
from .konflux import PROVIDED, bind_restored, provided_by_checkout, restored_paths, trusted_artifact
from .pac import PAC_ANNOTATION, bind_pac, pac_profile, pac_remote
from .repo import guess_root, relative_name, repo_yaml_files
from .tekton import (
    ENTRY_KIND,
    UNRESOLVED_IMAGE,
    Definitions,
    bind_image,
    bound_values,
    local_ref,
    mapping,
    ref_note,
    select_steps,
    task_values,
    yaml_documents,
)

CONFIG_FILE = ".tekton-innerloop.yaml"


def inventory(
    path: Path, root: Path | None = None, definitions: Definitions | None = None,
    *, pipeline: str | None = None, documents: list[tuple[int, object]] | None = None,
    document_number: int | None = None, resolve_bundles: bool = True,
) -> dict[str, dict]:
    """Every step of the file's PipelineRuns (or, without any, its Pipelines).

    Local pipelineRef/taskRef names are inlined from the same file or the repository;
    anything else stays a reference step that cannot run.
    """
    root = root or guess_root(path)
    definitions = definitions or Definitions(root)
    own = relative_name(path, root) if documents is None else path.relative_to(root).as_posix()
    documents = yaml_documents(path) if documents is None else documents
    for number, raw in documents:
        if not isinstance(raw, dict) or not isinstance(raw.get("kind"), str) or not raw["kind"]:
            raise ValueError(f"Document {number}: expected a Kubernetes resource with a kind")
    # ConfigMaps, Secrets, volume claims and the like often ship next to the pipeline.
    documents = [(n, raw) for n, raw in documents if raw["kind"] in ("PipelineRun", "Pipeline", "Task", "StepAction")]
    own_documents = [raw for _, raw in documents]
    entries = [(n, raw) for n, raw in documents if raw["kind"] == "PipelineRun"] or [
        (n, raw) for n, raw in documents if raw["kind"] == "Pipeline"
    ]
    if document_number is not None:
        entries = [(n, raw) for n, raw in entries if n == document_number]
    if not entries:
        raise ValueError("Expected a PipelineRun or a Pipeline")
    multiple = len(entries) > 1
    hint = (
        "; a Pipelines-as-Code annotation may fetch it, install tkn-pac so tkn pac resolve can"
        if resolve_bundles and pac_remote([raw for _, raw in entries])
        else ""
    )
    run_names = set()
    found = {}
    for number, raw in entries:
        run_sources = {}
        metadata = mapping(raw.get("metadata"), f"Document {number}: metadata")
        run_name = metadata.get("name") or metadata.get("generateName")
        if multiple:
            if not isinstance(run_name, str) or not run_name:
                raise ValueError(
                    f"Document {number}: multi-document {raw['kind']}s need a name or generateName"
                )
            if run_name in run_names:
                raise ValueError(f"Duplicate {raw['kind']} name: {run_name}")
            run_names.add(run_name)
        defined = set()
        if raw["kind"] == "Pipeline":
            run_spec, spec = {}, mapping(raw.get("spec"), f"Document {number}: spec")
        else:
            run_spec = mapping(raw.get("spec"), f"Document {number}: spec")
            spec = run_spec.get("pipelineSpec")
            if spec is None and run_spec.get("pipelineRef"):
                ref = run_spec["pipelineRef"]
                name = local_ref(ref, "Pipeline")
                if name is None:
                    if not resolve_bundles:
                        raise LookupError(ref_note(ref, "Pipeline"))
                    try:
                        fetched = bundle_definition(ref, "Pipeline")
                    except BundleError as exc:
                        raise ValueError(f"Document {number}: bundle pipelineRef: {exc}") from None
                    if fetched is None:
                        raise ValueError(
                            f"Document {number}: remote pipelineRef is not supported ({ref_note(ref)}); "
                            "keep the Pipeline in the repository"
                        )
                    pipeline_doc, where = fetched
                else:
                    try:
                        pipeline_doc, where = definitions.find("Pipeline", name, own, own_documents)
                    except LookupError as exc:
                        if not resolve_bundles:
                            raise
                        raise ValueError(f"Document {number}: pipelineRef {name}: {exc}{hint}") from None
                spec = mapping(pipeline_doc.get("spec"), f"Pipeline {name or where} spec")
                if where != own:
                    defined.add(where)
        if not isinstance(spec, dict) or not spec.get("tasks"):
            raise ValueError(
                f"Document {number}: expected tasks in an embedded pipelineSpec or a local pipelineRef"
            )
        not_lists = [f for f in ("tasks", "finally") if not isinstance(spec.get(f, []), list)]
        if not_lists:
            raise ValueError(f"Document {number}: pipeline {not_lists[0]} must be a list")
        pipeline_context = {
            "metadata": metadata,
            "spec": {k: v for k, v in run_spec.items() if k != "pipelineSpec"},
            "pipeline_defaults": {
                k: v for k, v in spec.items() if k not in ("tasks", "finally")
            },
        }
        for task in [*spec.get("tasks", []), *spec.get("finally", [])]:
            if not isinstance(task, dict) or not isinstance(task.get("name"), str) or not task["name"]:
                raise ValueError(f"Document {number}: every pipeline task needs a name")
            task_name = task["name"]
            task_spec = mapping(task.get("taskSpec"), f"Document {number}: task {task_name} taskSpec")
            task_defined = set(defined)
            note = None
            if not task_spec and task.get("taskRef"):
                name = local_ref(task["taskRef"], "Task")
                if name is None and not resolve_bundles:
                    note = ref_note(task["taskRef"])
                elif name is None:
                    try:
                        fetched = bundle_definition(task["taskRef"], "Task")
                    except BundleError as exc:
                        note = f"bundle reference not fetched: {exc}"
                    else:
                        if fetched is None:
                            note = ref_note(task["taskRef"])
                        else:
                            task_spec = mapping(fetched[0].get("spec"), f"Task {fetched[1]} spec")
                            task_defined.add(fetched[1])
                else:
                    try:
                        task_doc, where = definitions.find("Task", name, own, own_documents)
                        task_spec = mapping(task_doc.get("spec"), f"Task {name} spec")
                        if where != own:
                            task_defined.add(where)
                    except LookupError as exc:
                        note = f"{exc}{hint}"
            steps = task_spec.get("steps") or []
            if not isinstance(steps, list):
                raise ValueError(f"Document {number}: task {task_name} steps must be a list")
            whole_task = not steps
            if whole_task:
                steps = [{"name": "task", "ref": task.get("taskRef", {})}]
            restored = restored_paths(steps)
            for index, step in enumerate(steps):
                if not isinstance(step, dict) or not step:
                    raise ValueError(f"Document {number}: task {task_name} has a step that is not a mapping")
                key = f"{task_name}/{step.get('name') or f'unnamed-{index}'}"
                if multiple:
                    key = f"{run_name}/{key}"
                if key in run_sources:
                    raise ValueError(f"Duplicate source step: {key}")
                values = task_values(task, task_spec, run_spec, spec)
                step_defined = set(task_defined)
                step_note = note
                action = None
                if not whole_task and step.get("ref") and not step.get("image"):
                    name = local_ref(step["ref"], "StepAction")
                    action_doc = None
                    if name is None and not resolve_bundles:
                        step_note = ref_note(step["ref"], "StepAction")
                    elif name is None:
                        try:
                            fetched = bundle_definition(step["ref"], "StepAction")
                        except BundleError as exc:
                            step_note = step_note or f"bundle reference not fetched: {exc}"
                        else:
                            if fetched is None:
                                step_note = step_note or ref_note(step["ref"], "StepAction")
                            else:
                                action_doc, where = fetched
                                name = (action_doc.get("metadata") or {}).get("name") or where
                    else:
                        try:
                            action_doc, where = definitions.find("StepAction", name, own, own_documents)
                        except LookupError as exc:
                            step_note = f"{exc}{hint}"
                    if action_doc is not None:
                        action_spec = mapping(action_doc.get("spec"), f"StepAction {name} spec")
                        action = {"name": name, **{k: action_spec[k] for k in ("params", "results") if k in action_spec}}
                        values = bound_values(action_spec.get("params"), step.get("params"), values)
                        step = {
                            **{k: v for k, v in action_spec.items() if k not in ("params", "results")},
                            **{k: v for k, v in step.items() if k != "ref"},
                        }
                        if where != own:
                            step_defined.add(where)
                if document_number is None and not trusted_artifact(step):
                    step = bind_restored(step, restored)
                step = bind_image(step, values)
                cache = None if whole_task else tekton_cache(step, action["name"] if action else None)
                run_sources[key] = {
                    "step": step,
                    "pipeline_context": pipeline_context,
                    "task_context": {k: v for k, v in task.items() if k != "taskSpec"},
                    "task_defaults": {
                        k: v for k, v in task_spec.items() if k != "steps"
                    },
                    "finally": task in spec.get("finally", []),
                    "defined_in": sorted(step_defined),
                }
                if action:
                    run_sources[key]["step_action"] = action
                if cache is not None:
                    run_sources[key]["cache"] = cache
                if step_note:
                    run_sources[key]["reference_note"] = step_note
        if document_number is None:
            local_caches(run_sources, pipeline or own, run_name if isinstance(run_name, str) else "")
        found.update(run_sources)
    if document_number is None and pac_profile([raw for _, raw in entries]):
        found = bind_pac(found)
    return found


def plan_scope(sources: dict[str, dict], selection: str | None) -> list[str]:
    selected = select_steps([{"id": key} for key in sources], selection)
    ids = []
    for step in selected:
        key = step["id"]
        if "cache" not in sources[key]:
            ids.append(key)
        elif selection is not None:
            raise ValueError(f"{key}: tekton-caches actions cannot run locally")
    return ids


PUBLISH_OR_DEPLOY = "publishes or deploys"
SKIP_LOCAL = "marked skip-local"
CLUSTER_ONLY = re.compile(
    r"\b(?:git\s+push|(?:docker|podman|buildah|skopeo)\s+push|cosign|"
    r"kubectl\s+(?:apply|create|delete|patch)|oc\s+(?:apply|create|delete|patch)|"
    r"helm\s+(?:install|upgrade)|gh\s+release|goreleaser\s+release)\b"
    r"|\bcurl\b(?:[^\n]|\\\n)*?(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b"
)


def cluster_patterns(root: Path) -> list[re.Pattern]:
    """Built-in publish/deploy patterns plus the repository's own from .tekton-innerloop.yaml."""
    path = root / CONFIG_FILE
    if not path.is_file():
        return [CLUSTER_ONLY]
    try:
        config = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{CONFIG_FILE}: {exc}") from exc
    if not isinstance(config, dict) or set(config) - {"cluster_only"}:
        raise ValueError(f"{CONFIG_FILE}: only a cluster_only list is supported")
    extra = config.get("cluster_only", [])
    if not isinstance(extra, list) or not all(isinstance(p, str) and p for p in extra):
        raise ValueError(f"{CONFIG_FILE}: cluster_only must be a list of regular expressions")
    try:
        return [CLUSTER_ONLY, *(re.compile(pattern) for pattern in extra)]
    except re.error as exc:
        raise ValueError(f"{CONFIG_FILE}: invalid cluster_only pattern: {exc}") from exc


def auto_scope(
    sources: dict[str, dict], patterns: list[re.Pattern] | None = None
) -> tuple[list[str], dict[str, str]]:
    """Steps worth running locally, and why each other step is left to the cluster.

    Decided from the YAML alone, so the agent is never asked about the obvious cases.
    """
    patterns = patterns or [CLUSTER_ONLY]
    keep, left = [], {}
    for key, source in sources.items():
        step = source["step"]
        hints = " ".join(
            str(owner.get(field, ""))
            for owner in (step, source["task_context"])
            for field in ("description", "displayName")
        ).lower()
        text = " ".join(
            [str(step.get("script", "")), *map(str, step.get("command", []) or []),
             *map(str, step.get("args", []) or [])]
        )
        if "cache" in source:
            left[key] = CACHED
        elif source["finally"]:
            left[key] = "runs after the pipeline"
        elif provided_by_checkout(source):
            left[key] = PROVIDED
        elif not step.get("image"):
            left[key] = "external reference"
        elif not isinstance(step["image"], str) or UNRESOLVED_IMAGE.search(step["image"]):
            left[key] = "image has an unresolved substitution"
        elif step.get("script") is None and not step.get("command"):
            left[key] = "runs the image entrypoint"
        elif "(skip-local)" in hints:
            left[key] = SKIP_LOCAL
        elif any(pattern.search(text) for pattern in patterns):
            left[key] = PUBLISH_OR_DEPLOY
        else:
            keep.append(key)
    return keep, left


def runnable_pipelines(
    root: Path, paths: list[Path], definitions: Definitions
) -> tuple[list[tuple[Path, str, bool]], list[tuple[Path, str]]]:
    """Files with a runnable PipelineRun or Pipeline, and entry files that cannot be used, with why."""
    found, unusable = [], []
    for path in paths:
        try:
            if not ENTRY_KIND.search(path.read_text()):
                continue
        except (OSError, UnicodeDecodeError):
            continue
        try:
            inventory(path, root, definitions)
            documents = [raw for _, raw in yaml_documents(path)]
        except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
            unusable.append((path, str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
            continue
        is_run = any(raw.get("kind") == "PipelineRun" for raw in documents)
        first = next(d for d in documents if d.get("kind") == ("PipelineRun" if is_run else "Pipeline"))
        notes = (first.get("metadata") or {}).get("annotations") or {}
        events = str(notes.get(f"{PAC_ANNOTATION}on-event", "")).strip("[] ")
        if not events:
            cel = str(notes.get(f"{PAC_ANNOTATION}on-cel-expression", ""))
            events = ", ".join(dict.fromkeys(re.findall(r'event == "(\w+)"', cel)))
        found.append((path, events.replace(",", ", ").replace("  ", " "), is_run))
    return found, unusable


def find_pipelines(root: Path) -> list[tuple[Path, str]]:
    """Runnable PipelineRuns (else Pipelines) in .tekton/, or anywhere in the repository
    when .tekton/ has no pipeline at all, each with a short note on when it triggers.

    Pipelines in .tekton/ that cannot be used are reported rather than replaced by
    whatever else the repository holds (examples, test fixtures).
    """
    definitions = Definitions(root)
    tekton = root / ".tekton"
    found, unusable = runnable_pipelines(
        root, sorted([*tekton.glob("*.yaml"), *tekton.glob("*.yml")]), definitions
    )
    if not found and unusable:
        listing = "\n".join(f"    {path.relative_to(root)}: {why}" for path, why in unusable)
        raise ValueError(
            f"No pipeline in .tekton/ can run locally:\n{listing}\nName another pipeline file explicitly."
        )
    if not found:
        found, _ = runnable_pipelines(
            root,
            [root / name for name in repo_yaml_files(root) if Path(name).parent != Path(".tekton")],
            definitions,
        )
    if any(is_run for *_, is_run in found):
        found = [entry for entry in found if entry[2]]
    return [(path, note) for path, note, _ in found]


def choose_pipeline(root: Path, target: str | None) -> Path:
    if target:
        for candidate in (Path(target), root / target):
            if candidate.is_file():
                return candidate.resolve()
        raise ValueError(f"Pipeline not found: {target}")
    candidates = find_pipelines(root)
    if len(candidates) == 1:
        return candidates[0][0]
    if not candidates:
        raise ValueError("No runnable PipelineRun or Pipeline found in the repository")
    listing = "\n".join(
        f"    tkn-innerloop run {path.relative_to(root)}" + (f"   # {trigger}" if trigger else "")
        for path, trigger in candidates
    )
    raise ValueError(f"Several pipelines found; pick one:\n{listing}")

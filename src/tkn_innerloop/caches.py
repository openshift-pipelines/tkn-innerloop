"""tekton-caches steps replaced by persistent local cache directories."""

from __future__ import annotations

import argparse
import hashlib
import os
import posixpath
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .paths import cache_dir, home_relative
from .repo import repo_root
from .runtime import cache_image_id, cache_image_uid
from .tekton import SUBSTITUTION, adapted_script, local_ref
from .ui import stage

# openshift-pipelines/tekton-caches StepActions, by reference name, URL or path, or by their tool.
CACHE_STEP = re.compile(r"(?:^|[/:])cache-(?:fetch|upload)(?:\.ya?ml)?$")
CACHE_TOOL = re.compile(r"(?:exec[ \t]+)?/ko-app/cache[ \t]+(?:fetch|upload)(?:[ \t]+[^;|&<>()\n\x60]*)?")
CACHE_IMAGES = {
    "quay.io/openshift-pipeline/pipelines-cache-rhel9",
    "ghcr.io/openshift-pipelines/tekton-caches/cache",
    "ghcr.io/chmouel/tekton-caches/cache",
}
COMPLEX_SHELL = re.compile(r"(?<!<)<<(?!<)|\$\(|\x60")
WORKSPACE_PATH = re.compile(r"^\$\(workspaces\.[\w.-]+\.path\)")
CACHED = "remote cache action"
SHARED_CACHE_ENV = {
    "GOCACHE": "go", "GOMODCACHE": "go", "GOLANGCILINT_CACHE": "go",
    "npm_config_cache": "npm", "NPM_CONFIG_CACHE": "npm",
}
SHARED_CACHE_PATH = re.compile(r"^\$(?:CACHES|\{CACHES\})/((go|npm)-shared/[\w.-]+)(?=/|$)")


def cache_only_script(script: str) -> bool:
    lines = [
        line for line in map(str.strip, script.splitlines())
        if line and not line.startswith("#")
    ]
    while lines and re.fullmatch(r"set -[eu]+", lines[0]):
        lines.pop(0)
    return len(lines) == 1 and CACHE_TOOL.fullmatch(SUBSTITUTION.sub("placeholder", lines[0])) is not None


def cache_reference(ref: object) -> bool:
    if not isinstance(ref, dict):
        return False
    resolver = ref.get("resolver")
    if not resolver:
        name = local_ref(ref, "StepAction")
        return bool(name and CACHE_STEP.fullmatch(name))
    if not isinstance(resolver, str):
        return False
    field = {
        "http": "url", "git": "pathInRepo", "bundles": "name", "hub": "name", "cluster": "name",
    }.get(resolver)
    params = ref.get("params") if isinstance(ref.get("params"), list) else []
    return field is not None and any(
        isinstance(param, dict) and param.get("name") == field
        and isinstance(param.get("value"), str) and CACHE_STEP.search(param["value"])
        for param in params
    )


def upstream_cache_script(name: str | None, image: object, script: str) -> bool:
    if name not in ("cache-fetch", "cache-upload") or not isinstance(image, str):
        return False
    repository = re.split(r"[:@]", image, maxsplit=1)[0]
    verb = name.removeprefix("cache-")
    return repository in CACHE_IMAGES and bool(re.search(
        rf"(?m)^[ \t]*(?:exec[ \t]+)?/ko-app/cache[ \t]+{verb}(?=[ \t]|$)",
        script.replace("\\\n", " "),
    ))


def tekton_cache(step: dict, action_name: str | None = None) -> dict | None:
    script = step.get("script")
    command = step.get("command") or []
    argv = [*command, *(step.get("args") or [])] if command else []
    if not (
        (
            isinstance(script, str)
            and (upstream_cache_script(action_name, step.get("image"), script) or cache_only_script(script))
        )
        or (script is None and argv[:2] in (["/ko-app/cache", "fetch"], ["/ko-app/cache", "upload"]))
        or (script is None and not command and not step.get("image") and cache_reference(step.get("ref")))
    ):
        return None
    given = step.get("params") if isinstance(step.get("params"), list) else []
    # Upstream spells them CACHE_PATH and WORKING_DIR, copies cachePath and workingdir.
    values = {
        re.sub(r"[^a-z]", "", str(p.get("name", "")).lower()): p.get("value")
        for p in given
        if isinstance(p, dict)
    }
    path, workdir = values.get("cachepath"), values.get("workingdir")
    return {
        "path": path.rstrip("/") if isinstance(path, str) else "",
        "working_dir": workdir if isinstance(workdir, str) else "",
    }


def cache_slot(tail: str, identity: tuple) -> str:
    name = re.sub(r"[^\w.-]+", "-", tail.rsplit("/", 1)[-1]).strip("-.") or "cache"
    suffix = hashlib.sha256(repr(identity).encode()).hexdigest()[:12]
    return f"{name}-{suffix}"


def workspace_paths(source: dict, task: str) -> dict[str, tuple]:
    mounts = {
        item["name"]: item.get("mountPath")
        for item in source["task_defaults"].get("workspaces") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    bindings = {name: ("task", task, f"$(workspaces.{name}.path)") for name in mounts}
    for binding in source["task_context"].get("workspaces") or []:
        if not (
            isinstance(binding, dict)
            and all(isinstance(binding.get(field), str) for field in ("name", "workspace"))
            and isinstance(binding.get("subPath", ""), str)
        ):
            continue
        bindings[binding["name"]] = ("workspace", binding["workspace"], binding.get("subPath", ""))
    paths = {}
    for name, location in bindings.items():
        paths[f"$(workspaces.{name}.path)"] = location
        mount = mounts.get(name) or f"/workspace/{name}"
        if isinstance(mount, str) and mount.startswith("/") and "$" not in mount:
            paths[posixpath.normpath(mount)] = location
    return paths


def cache_location(path: str, task: str, workspaces: dict[str, tuple]) -> tuple[tuple, str]:
    match = WORKSPACE_PATH.match(path)
    prefix = match[0] if match else ""
    if not prefix and path.startswith("/"):
        prefix = max(
            (mount for mount in workspaces if path == mount or path.startswith(mount.rstrip("/") + "/")),
            key=len, default="",
        )
    return workspaces.get(prefix, ("task", task, prefix)), path[len(prefix.rstrip("/")):]


def workspace_location(scope: tuple, tail: str) -> tuple[tuple, str]:
    if scope[0] == "workspace":
        return scope[:2], posixpath.normpath(posixpath.join("/", scope[2].strip("/"), tail.lstrip("/")))
    return scope, posixpath.normpath(tail)


def cache_path_pattern(paths: Iterable[str], *, path_list: bool = False) -> str:
    alternatives = "|".join(map(re.escape, sorted(paths, key=len, reverse=True)))
    colon = ":" if path_list else ""
    return rf"""(?<![^\s'"=;|&<>({colon}])(?:{alternatives})(?=$|[/\s'";|&<>(){colon}])"""


def bind_caches(source: dict, script: str, slots: dict[str, str]) -> None:
    path_pattern = cache_path_pattern(slots)
    literal = re.compile(path_pattern)
    env_paths = re.compile(cache_path_pattern(slots, path_list=True))
    shell = re.compile(rf'''\\.|"[^"\\]*(?:\\.[^"\\]*)*"|'[^']*'|(?<![^\s;|&()])\#[^\n]*|{path_pattern}''')

    def bind_shell(match: re.Match) -> str:
        word = match[0]
        if word.startswith("'"):
            return literal.sub(lambda m: f"""'"$CACHES"'/{slots[m[0]]}""", word)
        if word.startswith('"'):
            return literal.sub(lambda m: f"$CACHES/{slots[m[0]]}", word)
        if word in slots:
            return f'"$CACHES"/{slots[word]}'
        return word

    step = source["step"].copy()
    bound = shell.sub(bind_shell, script)
    if bound != script:
        step["script"] = bound

    def bind_env(owner: dict) -> dict:
        if not owner.get("env"):
            return owner
        return {
            **owner,
            "env": [
                {**env, "value": env_paths.sub(lambda m: f"$CACHES/{slots[m[0]]}", env["value"])}
                if isinstance(env, dict) and isinstance(env.get("value"), str) else env
                for env in owner["env"]
            ],
        }

    source["step"] = bind_env(step)
    defaults = source["task_defaults"]
    if defaults.get("stepTemplate"):
        defaults["stepTemplate"] = bind_env(defaults["stepTemplate"])


def cache_paths(location: tuple, directory: str, task: str, workspaces: dict[str, tuple]) -> list[str]:
    if location[0] == "task":
        _, owner, prefix = location
        if owner != task:
            return []
        aliases = [alias for alias, binding in workspaces.items() if binding == location]
        return [alias.rstrip("/") + directory for alias in aliases or [prefix]]
    paths = []
    for prefix, binding in workspaces.items():
        owner, mount = workspace_location(binding, "")
        if owner == location and directory.startswith(mount.rstrip("/") + "/"):
            paths.append(prefix.rstrip("/") + "/" + posixpath.relpath(directory, mount))
    return paths


def local_caches(sources: dict[str, dict], pipeline: str, run: str) -> None:
    cache_workdirs = {
        key: source["cache"].pop("working_dir") for key, source in sources.items() if "cache" in source
    }
    if not any(source.get("cache", {}).get("path") for source in sources.values()):
        return
    tasks = {key: key.rsplit("/", 2)[-2] for key in sources}
    workspaces = {key: workspace_paths(source, tasks[key]) for key, source in sources.items()}
    workdirs = []
    consumers = {}
    for key, source in sources.items():
        if "cache" not in source:
            template = source["task_defaults"].get("stepTemplate") or {}
            workdir = source["step"].get("workingDir") or template.get("workingDir") or ""
            if workdir.startswith("/"):
                workdir = posixpath.normpath(workdir)
            owner, tail = cache_location(workdir, tasks[key], workspaces[key])
            workdirs.append(workspace_location(owner, tail))
            workdirs.extend(
                workspace_location(binding, "") for binding in workspaces[key].values()
                if binding[0] == "workspace"
            )
            step = source["step"]
            script = step.get("script")
            if script is None and step.get("command"):
                script = adapted_script(step, [])
            script = script if isinstance(script, str) else ""
            env = [
                (item.get("name"), item["value"])
                for owner in (step, template)
                for item in owner.get("env") or []
                if isinstance(item, dict) and isinstance(item.get("value"), str)
            ]
            image = step.get("image")
            complex_shell = bool(COMPLEX_SHELL.search(SUBSTITUTION.sub("", script)))
            consumers[key] = (image if isinstance(image, str) else None, script, env, complex_shell)

    declared = {}
    for key, source in sources.items():
        if path := source.get("cache", {}).get("path"):
            scope, tail = cache_location(path, tasks[key], workspaces[key])
            declared[key] = (tail, *workspace_location(scope, tail))
    locations = {(location, directory) for _, location, directory in declared.values()}

    replacements: dict[str, dict[str, str]] = {key: {} for key in consumers}
    for key, (tail, location, directory) in declared.items():
        cache = sources[key]["cache"]
        working_dir = workspace_location(*cache_location(cache_workdirs[key], tasks[key], workspaces[key]))
        # npm resolves node_modules from the project directory even without an explicit path
        if (
            cache["path"] == "/workspace"
            or not re.fullmatch(r"/[\w./ -]+", tail)
            or any(part in ("", ".", "..") for part in tail[1:].split("/"))
            or "node_modules" in directory.split("/")
            or any(
                owner == location and (wd == directory or wd.startswith(directory + "/"))
                for owner, wd in (*workdirs, working_dir)
            )
            or any(
                owner == location and other != directory and (
                    other.startswith(directory.rstrip("/") + "/")
                    or directory.startswith(other.rstrip("/") + "/")
                )
                for owner, other in locations
            )
        ):
            cache["path"] = ""
            continue
        users = {}
        images = set()
        shareable = location[0] == "workspace"
        tools = set()
        for other, (image, script, env, complex_shell) in consumers.items():
            paths = cache_paths(location, directory, tasks[other], workspaces[other])
            if not paths:
                continue
            in_script = re.search(cache_path_pattern(paths), script)
            if in_script and complex_shell:
                break
            env_paths = re.compile(cache_path_pattern(paths, path_list=True))
            env_users = [(name, value) for name, value in env if env_paths.search(value)]
            if in_script or env_users:
                users[other] = paths
                images.add(image)
                tools.update(SHARED_CACHE_ENV.get(name) for name, _ in env_users if isinstance(name, str))
                shareable = shareable and not in_script and all(
                    isinstance(name, str) and name in SHARED_CACHE_ENV
                    and any(value == path or value.startswith(path + "/") for path in paths)
                    and ":" not in value and ".." not in value.split("/")
                    for name, value in env_users
                )
            if len(images) > 1 and (not shareable or len(tools) != 1):
                break
        else:
            slot = cache_slot(directory, (pipeline, run, location, directory))
            if len(images) > 1:
                slot = f"{next(iter(tools))}-shared/{slot}"
                cache["workspace_dir"] = directory
            cache["dir"] = f"$CACHES/{slot}"
            for other, paths in users.items():
                replacements[other].update(dict.fromkeys(paths, slot))
            continue
        # Keep all consumers at the original path if any one cannot be relocated safely
        cache["path"] = ""

    for key, paths in replacements.items():
        if paths:
            bind_caches(sources[key], consumers[key][1], paths)


def caches_dir(root: Path) -> Path:
    """The persistent directory standing in for a checkout's tekton-caches caches."""
    return cache_dir(root) / "caches"


def prepare_caches(root: Path, runtime: str, container_key: str) -> Path:
    private = caches_dir(root)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    data = private / "data"
    data.mkdir(exist_ok=True)
    data.chmod(0o700)
    owner = f"{os.getuid()}:{os.getgid()}" if runtime == "host" else container_key
    suffix = hashlib.sha256(owner.encode()).hexdigest()[:16]
    shared = data / f"{runtime}-{suffix}"
    shared.mkdir(exist_ok=True)
    shared.chmod(0o777)
    return shared


def shared_tool_caches(
    runtime: str, steps: list[dict], sources: dict, timeout: float
) -> tuple[list[dict], dict[str, str], dict[str, dict[str, int]]]:
    image_ids: dict[str, str] = {}
    image_uids: dict[str, int] = {}
    cache_users: dict[str, set[int]] = {}
    uses = {}
    for step in steps:
        refs = {
            name: match for name, value in step["env"].items()
            if (match := SHARED_CACHE_PATH.match(value)) and SHARED_CACHE_ENV.get(name) == match[2]
        }
        if not refs:
            continue
        image = step["image"]
        if image not in image_ids:
            image_ids[image] = cache_image_id(runtime, image, timeout)
            image_uids[image] = cache_image_uid(runtime, image_ids[image], timeout)
        uid = image_uids[image]
        uses[step["id"]] = refs
        for ref in refs.values():
            cache_users.setdefault(ref[1], set()).add(uid)

    if not uses:
        return steps, {}, {}

    incompatible = {slot for slot, users in cache_users.items() if len(users) > 1}
    workspace_paths = {
        cache["dir"].removeprefix("$CACHES/"): cache["workspace_dir"]
        for source in sources.values()
        if "workspace_dir" in (cache := source.get("cache", {}))
    }
    if incompatible - workspace_paths.keys():
        raise ValueError("Shared caches used by different container UIDs need a workspace path")
    if incompatible:
        stage("Caches", "using the workspace for caches shared by different container UIDs", "33", "!")

    adapted = []
    shared_uids: dict[str, dict[str, int]] = {}
    for step in steps:
        env = dict(step["env"])
        for name, ref in uses.get(step["id"], {}).items():
            if ref[1] in incompatible:
                env[name] = "$WORKSPACE" + workspace_paths[ref[1]] + env[name][ref.end():]
            else:
                shared_uids.setdefault(step["id"], {})[ref[2]] = image_uids[step["image"]]
        adapted.append({**step, "env": env})
    return adapted, image_ids, shared_uids


def clean_caches(args: argparse.Namespace) -> int:
    """Remove the checkout's local caches; the next run starts them cold."""
    path = caches_dir(repo_root(Path(args.repo)))
    if not path.exists():
        print(f"No local caches in {home_relative(path)}")
        return 0
    try:
        shutil.rmtree(path)
    except PermissionError:
        if shutil.which("podman") and os.getuid() != 0:
            subprocess.run(
                ["podman", "unshare", "rm", "-rf", str(path)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        if path.exists():
            if not shutil.which("docker"):
                raise
            subprocess.run(
                [
                    "docker", "run", "--rm", "--user", "0", "--network", "none",
                    "-v", f"{path}:/cache:Z", "--entrypoint", "/bin/rm",
                    "docker.io/library/alpine:3", "-rf", "/cache/data",
                ],
                check=True,
            )
            shutil.rmtree(path)
    print(f"Removed local caches in {home_relative(path)}")
    return 0

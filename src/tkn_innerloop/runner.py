"""Running a plan's steps in containers or on the host."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .caches import prepare_caches, shared_tool_caches
from .paths import cache_dir
from .plan import RUNNER_VARS, load_plan, plan_sources
from .repo import default_base, git, repo_root
from .runtime import cache_image_id, hand_back_ownership, local_image_id, resolve_runtime
from .tekton import select_steps
from .ui import heading, step_row, style, verdict

STARTED = ".tkn-innerloop-started"


def expand(value: str, bindings: dict[str, str]) -> str:
    names = "|".join(RUNNER_VARS)
    return re.sub(
        rf"\$\{{({names})\}}|\$({names})\b",
        lambda match: bindings[match.group(1) or match.group(2)],
        value,
    )


def uses_var(steps: list[dict], name: str) -> bool:
    used = json.dumps([[s["script"], s["working_dir"], s["env"]] for s in steps])
    return re.search(rf"\$(?:{name}\b|\{{{name}\b)", used) is not None


def runner_bindings(
    root: Path, steps: list[dict], workspace: str, base: str | None, results: str = "",
) -> dict[str, str]:
    """Values for the runner variables, computed only when a selected step uses them."""

    def wanted(name: str) -> bool:
        return uses_var(steps, name)

    bindings = dict.fromkeys(RUNNER_VARS, "")
    bindings["WORKSPACE"] = workspace
    bindings["RESULTS"] = results
    if wanted("BASE_SHA") or wanted("BASE_REF"):
        base = base or default_base(root)
        if wanted("BASE_SHA"):
            bindings["BASE_SHA"] = git(root, "merge-base", "HEAD", base)
        if wanted("BASE_REF"):
            remote, _, branch = base.partition("/")
            bindings["BASE_REF"] = branch if branch and remote in git(root, "remote").split() else base
    if wanted("HEAD_SHA"):
        bindings["HEAD_SHA"] = git(root, "rev-parse", "HEAD")
    if wanted("HEAD_REF"):
        bindings["HEAD_REF"] = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return bindings


def report_blocked(plan: dict, unresolved: list[dict], args: argparse.Namespace) -> int:
    """Say which steps block the run, why, and what to do about it. Nothing has run."""
    err = sys.stderr
    count = len(unresolved)
    print(file=err)
    verdict(
        "✗ BLOCKED",
        f"{count} unresolved step{'s' if count != 1 else ''} in scope; nothing was run",
        "31",
        err,
    )
    print(file=err)
    for step in unresolved:
        step_row("? UNRESOLVED", step["id"], step["reason"], "33", err)
    runtime = f" --runtime {args.runtime}" if args.runtime not in ("host", "auto") else ""
    decided = [s for s in plan["steps"] if s["decision"] == "run"]
    needed = sorted({name for s in decided for name in s["required_env"]})
    print(style("\n  What now", "1", err), file=err)
    if decided:
        print(style("  Run only the steps that are decided:", "2", err), file=err)
        forwarded = "".join(f" --env {name}" for name in needed)
        ids = ",".join(s["id"] for s in decided)
        command = f"tkn-innerloop run {shlex.quote(args.plan)} --steps {ids}{forwarded}{runtime}"
        print(style(f"    {command}", "36", err), file=err)
        if needed:
            names = ", ".join(needed)
            print(style(f"    (needs {names} set in your environment)", "2", err), file=err)
    print(style("  Or regenerate the plan and review it again:", "2", err), file=err)
    command = f"tkn-innerloop generate {shlex.quote(plan['pipeline'])} -o {shlex.quote(args.plan)}"
    print(style(f"    {command}", "36", err), file=err)
    print(file=err)
    return 1


def note_image_drift(runtime: str, root: Path, image: str, current: str = "") -> None:
    """Tell the user when a tag now points at a different image than on the last run."""
    current = current or local_image_id(runtime, image)
    if not current:
        return
    state = cache_dir(root) / "images.json"
    try:
        known = json.loads(state.read_text())
    except (OSError, json.JSONDecodeError):
        known = {}
    if known.get(image) and known[image] != current:
        print(
            style(f"    ! {image} now resolves to a different image than last time", "33"),
            flush=True,
        )
    if known.get(image) != current:
        known[image] = current
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(known, indent=2) + "\n")


def continues_on_error(source: dict) -> str | None:
    """Where Tekton's onError: continue applies.

    "step": the failure is ignored and the Task goes on. "task": the Task stops at the
    failing step, but the pipeline carries on as if it had succeeded.
    """
    if source["step"].get("onError") == "continue":
        return "step"
    if source["task_context"].get("onError") == "continue":
        return "task"
    return None


def run_plan(args: argparse.Namespace) -> int:
    """Run a plan's checks with a fresh $RESULTS directory, removed afterwards."""
    cache = cache_dir(repo_root(Path(args.repo)))
    cache.mkdir(parents=True, exist_ok=True)
    # mkdtemp's 0700 keeps the world-writable directory inside it private.
    with tempfile.TemporaryDirectory(prefix="run-", dir=cache, ignore_cleanup_errors=True) as private:
        results = Path(private) / "results"
        results.mkdir()
        # Images may run as any user, which rootless podman maps to a uid other than ours.
        results.chmod(0o777)
        try:
            return run_checks(args, results)
        finally:
            if args.runtime == "podman" and os.getuid() != 0:
                # A non-root image user maps to a subuid the host user cannot delete as.
                subprocess.run(
                    ["podman", "unshare", "rm", "-rf", str(results)],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )


def run_checks(args: argparse.Namespace, results: Path) -> int:
    root = repo_root(Path(args.repo))
    plan = load_plan(Path(args.plan), root)
    scope = select_steps(plan["steps"], args.steps)
    unresolved = [s for s in scope if s["decision"] == "unresolved"]
    if unresolved:
        return report_blocked(plan, unresolved, args)
    if args.steps and any(s["decision"] != "run" for s in scope):
        raise ValueError("Requested steps include skipped steps; review the plan")
    steps = [s for s in scope if s["decision"] == "run"]
    if not steps:
        raise ValueError("No runnable steps selected")
    selected_ids = {s["id"] for s in steps}
    for step in steps:
        if set(step["depends_on"]) - selected_ids:
            raise ValueError(f"{step['id']}: selection omits required setup steps")
    args.runtime = resolve_runtime(args.runtime)
    if args.runtime != "host" and not shutil.which(args.runtime):
        others = [r for r in ("docker", "podman") if r != args.runtime and shutil.which(r)]
        advice = (
            f"{others[0]} is installed: use --runtime {others[0]}"
            if others
            else "install docker or podman, or use --runtime host to run the scripts directly"
        )
        raise ValueError(f"Container runtime not found: {args.runtime} ({advice})")
    forwarded = set(args.env)
    required = {name for step in steps for name in step["required_env"]}
    optional = {name for step in steps for name in step.get("optional_env", [])}
    if required - forwarded or any(name not in os.environ for name in required):
        raise ValueError(
            "Required environment must be set and explicitly forwarded with --env: "
            + ", ".join(sorted(required))
        )
    if forwarded - required - optional:
        raise ValueError("Only required_env or optional_env variables may be forwarded")
    sources = plan_sources(plan, root)
    host = args.runtime == "host"
    pinned_images, shared_uids = {}, {}
    if not host:
        steps, pinned_images, shared_uids = shared_tool_caches(args.runtime, steps, sources, args.timeout)
    bindings = runner_bindings(
        root, steps, str(root) if host else "/workspace", args.base,
        str(results) if host else "/results",
    )
    if args.runtime == "host":
        print(
            "Host mode uses local tool versions and has access to your host environment.",
            flush=True,
        )
    start = time.monotonic()
    heading(
        "Run local checks", f"{len(steps)} checks · {args.runtime} · {plan['pipeline']}"
    )
    passed = 0
    failed: list[str] = []
    ignored: list[str] = []
    not_run: list[str] = []
    stopped: dict[str, str] = {}
    after_ignored: list[str] = []
    for index, step in enumerate(steps, 1):
        task = step["id"].rsplit("/", 1)[0]
        if task in stopped:
            print(
                style(f"\n  ↷ {step['id']} skipped: {stopped[task]} stopped its task", "33"),
                flush=True,
            )
            after_ignored.append(step["id"])
            continue
        broken = [name for name in step["depends_on"] if name in failed or name in not_run]
        if broken:
            print(
                style(
                    f"\n  ↷ {step['id']} skipped: needs {', '.join(broken)}, which failed",
                    "33",
                ),
                flush=True,
            )
            not_run.append(step["id"])
            continue
        image = step["image"]
        image_id = pinned_images.get(image, "")
        caches = None
        shared_mounts = []
        if uses_var([step], "CACHES"):
            if not host and not image_id:
                try:
                    image_id = cache_image_id(args.runtime, image, args.timeout)
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    print(f"  ✗ {step['id']}: {exc}", file=sys.stderr)
                    failed.append(step["id"])
                    continue
            caches = prepare_caches(root, args.runtime, image_id)
            for tool, uid in shared_uids.get(step["id"], {}).items():
                directory = f"{tool}-shared"
                (caches / directory).mkdir(exist_ok=True)
                shared = prepare_caches(root, args.runtime, f"{tool}:{uid}")
                shared_mounts.extend(["-v", f"{shared}:/caches/{directory}:z"])
        bindings["CACHES"] = (str(caches) if host else "/caches") if caches else ""
        env = dict(bindings)
        env.update(
            {
                name: expand(value, bindings)
                for name, value in step["env"].items()
            }
        )
        env.update({name: os.environ[name] for name in step["required_env"]})
        passed_optional = [
            name
            for name in step.get("optional_env", [])
            if name in forwarded and name in os.environ
        ]
        env.update({name: os.environ[name] for name in passed_optional})
        working_dir = expand(step["working_dir"], bindings)
        container_name = f"tkn-innerloop-{uuid.uuid4().hex}"
        original = sources[step["id"]]["step"]
        shell_args = (
            ["-s"] if original.get("script", "").startswith("#!") else ["-e", "-s"]
        )
        if args.runtime == "host":
            cmd = [step["shell"], *shell_args]
            process_env = {**os.environ, **env}
            cwd = working_dir
        else:
            cmd = [
                args.runtime,
                "run",
                "--rm",
                "--name",
                container_name,
                "-i",
                "-v",
                f"{root}:/workspace:Z",
                "-v",
                f"{results}:/results:Z",
                *(["-v", f"{caches}:/caches:Z"] if caches else []),
                *shared_mounts,
                "-w",
                working_dir,
            ]
            if (root / ".git").is_file():
                common = Path(
                    git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
                )
                if not common.is_relative_to(root):
                    cmd.extend(["-v", f"{common}:{common}:ro"])
            for name, value in env.items():
                cmd.extend(
                    ["-e", name if name in step["required_env"] or name in passed_optional else f"{name}={value}"]
                )
            cmd.extend(["--entrypoint", step["shell"], image_id or image, *shell_args])
            process_env = os.environ.copy()
            cwd = str(root)
        print(style(f"\n  ▶ [{index}/{len(steps)}] {step['id']}", "1;36"), flush=True)
        print(style(f"    {step['image']}", "2"), flush=True)
        step_start = time.monotonic()
        started = results / STARTED
        started.unlink(missing_ok=True)
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=process_env,
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            proc.communicate(
                f': > {shlex.quote(str(started) if host else "/results/" + STARTED)} || :\n{step["script"]}\n',
                timeout=args.timeout,
            )
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as interrupted:
            if args.runtime != "host":
                try:
                    subprocess.run(
                        [args.runtime, "rm", "-f", container_name],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    print(
                        f"Could not remove timed-out container: {container_name}",
                        file=sys.stderr,
                    )
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
            hand_back_ownership(args.runtime, root, step, results, image_id or image)
            if isinstance(interrupted, KeyboardInterrupt):
                print(
                    style(f"  ✗ {step['id']}: interrupted", "1;31", sys.stderr),
                    file=sys.stderr,
                )
                return 1
            print(
                style(f"\n  ✗ {step['id']} timed out after {args.timeout}s", "1;31"),
                flush=True,
            )
            failed.append(step["id"])
            continue
        hand_back_ownership(args.runtime, root, step, results, image_id or image)
        if args.runtime != "host":
            note_image_drift(args.runtime, root, image, image_id)
        # Without the marker the shell never ran (pull error, missing shell), so onError does not apply.
        continuation = (
            continues_on_error(sources[step["id"]])
            if proc.returncode and started.exists()
            else None
        )
        if continuation:
            if continuation == "task":
                stopped[task] = step["id"]
            print(
                style(
                    f"\n  ! {step['id']} failed (exit {proc.returncode}), ignored: onError is continue",
                    "33",
                ),
                flush=True,
            )
            ignored.append(step["id"])
            continue
        if proc.returncode:
            print(
                style(
                    f"\n  ✗ {step['id']} failed (exit {proc.returncode}) · {time.monotonic() - step_start:.2f}s",
                    "1;31",
                ),
                flush=True,
            )
            failed.append(step["id"])
            continue
        passed += 1
        print(
            style(
                f"  ✓ {step['id']} passed · {time.monotonic() - step_start:.2f}s", "32"
            ),
            flush=True,
        )
    skipped = sum(s["decision"] == "skip" for s in plan["steps"])
    unknown = sum(s["decision"] == "unresolved" for s in plan["steps"])
    if failed:
        print(
            style(
                f"\n  ✗ {len(failed)} of {len(steps)} failed: {', '.join(failed)}"
                + (f" · {len(not_run)} not run (needed a failed step)" if not_run else "")
                + f" · {passed} passed · {time.monotonic() - start:.2f}s\n",
                "1;31",
            )
        )
        return 1
    print(
        style(
            f"\n  ✓ {passed}/{len(steps)} local checks passed · {time.monotonic() - start:.2f}s",
            "1;32",
        )
    )
    print(
        style(
            (f"  {len(ignored)} failed with onError: continue · " if ignored else "  ")
            + (f"{len(after_ignored)} not run after them · " if after_ignored else "")
            + f"{skipped} skipped · {unknown} unresolved · cluster CI still required\n",
            "2",
        )
    )
    return 0

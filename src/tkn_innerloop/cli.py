"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .agent import agent_model, build_plan
from .assess import assess, assess_repository, render_assessment
from .bundles import input_label
from .caches import CACHED, clean_caches
from .inventory import auto_scope, choose_pipeline, cluster_patterns, inventory, plan_scope
from .konflux import PROVIDED
from .paths import cache_path, home_relative, write_cache
from .plan import load_plan, plan_source, print_diff, print_plan
from .repo import guess_root, repo_root
from .runner import report_blocked, run_plan
from .ui import heading, stage, step_row, style, verdict

# Installed packages carry the skill next to this file; a checkout keeps it under skills/.
SKILL_PATH = Path(__file__).with_name("SKILL.md")
if not SKILL_PATH.exists():
    SKILL_PATH = Path(__file__).resolve().parents[2] / "skills/tekton-innerloop/SKILL.md"


def inspect_pipeline(path: Path) -> None:
    """List the steps without judging them: nothing has been converted or validated yet."""
    root = guess_root(path)
    sources = inventory(path, root)
    heading("Tekton inner-loop", str(path))
    references = 0
    for key, source in sources.items():
        image = source["step"].get("image")
        origin = f" · from {', '.join(map(input_label, source['defined_in']))}" if source["defined_in"] else ""
        if source["task_context"].get("matrix"):
            origin += " · matrix"
        if source.get("cache", {}).get("dir"):
            origin += f" · local cache {source['cache']['dir']}"
        if image:
            step_row("○ PENDING", key, image + origin, "2")
        else:
            references += 1
            note = source.get("reference_note", "External reference, contents not read")
            step_row("? REFERENCE", key, note[:1].upper() + note[1:] + origin, "33")
    pending = len(sources) - references
    print(
        f"\n  {len(sources)} steps · {pending} pending · {references} references"
        " · not converted yet"
    )
    print(style("  Next: generate a plan to choose your local checks.\n", "36"))


def generate(args: argparse.Namespace) -> int:
    path = Path(args.file).resolve()
    root = repo_root(path.parent)
    relative = str(path.relative_to(root))
    source, extra = plan_source(root, path)
    sources = inventory(source, root, pipeline=relative)
    selected_ids = plan_scope(sources, args.steps)
    heading(
        "Build your local plan",
        f"{relative} · {len(selected_ids)} steps · {agent_model(args.model)}",
    )
    sys.stdout.flush()
    start = time.monotonic()
    plan = build_plan(
        path, root, selected_ids, args.model, args.timeout, source=source, extra_inputs=extra
    )
    out = Path(args.output)
    out.write_text(json.dumps(plan, indent=2) + "\n")
    print_plan(plan)
    print(style(f"\n  ✓ Plan ready in {time.monotonic() - start:.1f}s → {out}", "1;32"))
    print(
        style(f"  Next: tkn-innerloop validate {shlex.quote(str(out))} --diff\n", "36")
    )
    return 0


def ensure_plan(
    args: argparse.Namespace,
    root: Path,
    pipeline: Path,
    scope: list[str],
    explicit: bool = False,
    source: Path | None = None,
    extra_inputs: list[str] | tuple[str, ...] = (),
) -> tuple[Path, dict]:
    """The cached plan when it still matches, else a freshly generated one."""
    out = cache_path(root, pipeline, scope if explicit else None)
    relative = home_relative(out)
    resolved = source.name if source and source != pipeline else None
    reason = "no plan yet"
    if out.exists() and not getattr(args, "refresh", False):
        try:
            plan = load_plan(out, root)
            if plan.get("resolved") != resolved:
                reason = "resolution changed"
            elif sorted(plan.get("scope", [])) == sorted(scope):
                stage("Plan", f"reused · {relative}")
                return out, plan
            else:
                reason = "scope changed"
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            reason = str(exc).split(";")[0]
    elif getattr(args, "refresh", False):
        reason = "--refresh"
    stage("Plan", f"{reason}, asking the agent", "36", "…")
    start = time.monotonic()
    plan = build_plan(
        pipeline, root, scope, getattr(args, "model", None),
        getattr(args, "convert_timeout", 300),
        source=source, extra_inputs=extra_inputs,
    )
    write_cache(out, json.dumps(plan, indent=2) + "\n")
    stage("Plan", f"generated in {time.monotonic() - start:.1f}s · {relative}")
    return out, plan


def run_command(args: argparse.Namespace) -> int:
    """Run a saved plan, convert an explicit file, or assess the repository first.

    Exit 0: every runnable check passed. 1: a check failed. 2: all passed but some
    steps stayed unresolved, so the result is incomplete.
    """
    if args.plan and args.plan.endswith(".json"):
        return run_plan(args)
    root = repo_root(Path(args.repo))
    if not args.plan:
        return run_repository(args, root.resolve())
    pipeline = choose_pipeline(root, args.plan)
    relative = str(pipeline.relative_to(root))
    heading("Tekton inner-loop", relative)
    source, extra = plan_source(root, pipeline, getattr(args, "refresh", False))
    sources = inventory(source, root, pipeline=relative)
    explicit = bool(args.steps)
    auto, left = auto_scope(sources, cluster_patterns(root))
    scope = plan_scope(sources, args.steps) if explicit else auto
    if not scope:
        raise ValueError("Nothing in this pipeline can run locally; every step is cluster-only")
    outside = [key for key in left if key not in scope and left[key] not in (PROVIDED, CACHED)]
    provided = sum(left[key] == PROVIDED and key not in scope for key in left)
    cached = sum(left[key] == CACHED and key not in scope for key in left)
    shown = ", ".join(outside[:3]) + (f" +{len(outside) - 3}" if len(outside) > 3 else "")
    stage(
        "Scope",
        f"{len(scope)} of {len(sources)} steps run locally"
        + (f" · {provided} provided by the checkout" if provided else "")
        + (f" · {cached} remote cache actions skipped" if cached else "")
        + (f" · cluster-only: {shown}" if outside else ""),
    )
    plan_file, plan = ensure_plan(
        args, root, pipeline, scope, explicit=explicit, source=source, extra_inputs=extra
    )
    args.plan = str(plan_file)
    unresolved = [s for s in plan["steps"] if s["decision"] == "unresolved" and s["id"] in scope]
    decided = [s["id"] for s in plan["steps"] if s["decision"] == "run"]
    if unresolved and not explicit:
        if not decided:
            return report_blocked(plan, unresolved, args)
        args.steps = ",".join(decided)
        stage(
            "Unresolved",
            f"{len(unresolved)} not checked: " + ", ".join(s["id"] for s in unresolved),
            "33",
            "!",
        )
    code = run_plan(args)
    if code == 0 and unresolved and not explicit:
        print(
            style(
                f"  ! Passed, but {len(unresolved)} step(s) were not checked (unresolved). "
                "See `tkn-innerloop validate` for why.\n",
                "33",
            )
        )
        return 2
    return code


def run_repository(args: argparse.Namespace, root: Path) -> int:
    """Run independently validated documents without reusing explicit plan caches."""
    if args.steps is not None or args.env:
        raise ValueError("--steps and --env require an explicit pipeline file or saved plan")
    reports, issues = assess_repository(root, args.model, args.convert_timeout)
    if not reports:
        print("No PipelineRuns found under .tekton/; nothing ran")
        return 1 if issues else 2
    failed, incomplete, completed = bool(issues), False, False
    for report in reports:
        render_assessment(report)
        if report.error:
            failed = True
            print("  Run: assessment failed; nothing ran", flush=True)
            continue
        incomplete |= report.unresolved
        scope = report.native
        if scope is None:
            print("  Run: no eligible local steps", flush=True)
            continue
        local_args = argparse.Namespace(**{**vars(args), "repo": str(root)})
        try:
            plan = build_plan(root / scope.pipeline, root, list(scope.dependencies), args.model,
                              args.convert_timeout, native=scope)
            code = run_plan(local_args, native=(scope, plan))
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            print(f"  Run: failed ({str(exc)!r})", flush=True)
            failed = True
            continue
        completed |= code == 0
        failed |= code == 1
        incomplete |= code == 2
        label = {0: "completed eligible work", 1: "failed", 2: "incomplete"}[code]
        print(f"  Run: {label}", flush=True)
    return 1 if failed else 2 if incomplete or not completed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tkn-innerloop",
        description="Agent-led Tekton conversion with repeatable local execution"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    inspect = sub.add_parser(
        "inspect", help="Assess a repository's PipelineRuns or list the source steps in a file"
    )
    inspect.add_argument("path", nargs="?", default=".", type=Path, help="Repository directory (default: .) or pipeline file")
    inspect.add_argument("--model", metavar="MODEL", help="Pi model for repository inspection")
    gen = sub.add_parser(
        "generate",
        help="Ask an agent to inspect repository files and produce a JSON plan",
    )
    gen.add_argument("file")
    gen.add_argument("-o", "--output", required=True)
    gen.add_argument(
        "--steps",
        help="Comma-separated step names or full IDs from inspect to include in local scope",
    )
    gen.add_argument(
        "--model", help="Pi model (default: TKN_INNERLOOP_AI_MODEL or haiku)"
    )
    gen.add_argument("--timeout", type=int, default=300)
    validate = sub.add_parser(
        "validate", help="Check source hashes, plan structure and shell syntax"
    )
    validate.add_argument("plan")
    validate.add_argument("--repo", default=".")
    validate.add_argument(
        "--diff", action="store_true", help="Show source-to-plan script changes"
    )
    run = sub.add_parser(
        "run",
        help="Run eligible repository work, an explicit pipeline, or a saved plan",
    )
    run.add_argument(
        "plan",
        nargs="?",
        help="pipeline YAML or saved plan JSON (default: assess PipelineRuns under .tekton/ and run eligible work)",
    )
    run.add_argument("--repo", default=".")
    run.add_argument(
        "--runtime",
        choices=("auto", "docker", "podman", "host"),
        default="auto",
        help="auto picks podman, then docker",
    )
    run.add_argument(
        "--base",
        default=None,
        help="PR target ref for git merge-base (default: origin's default branch, else main or master)",
    )
    run.add_argument("--steps", help="Run an explicit subset of the plan")
    run.add_argument(
        "--env",
        action="append",
        default=[],
        help="Explicitly forward a required or optional environment variable",
    )
    run.add_argument("--timeout", type=int, default=180, help="Seconds allowed per check")
    run.add_argument("--model", help="Pi model for repository assessment or explicit plan generation")
    run.add_argument(
        "--convert-timeout", type=int, default=300, help="Seconds allowed for each assessment or explicit conversion"
    )
    run.add_argument(
        "--refresh", action="store_true", help="Regenerate the cached plan for an explicit pipeline file"
    )
    cache = sub.add_parser("cache", help="Manage the local caches that replace tekton-caches steps")
    cache.add_argument("action", choices=("clean",))
    cache.add_argument("--repo", default=".")
    skills = sub.add_parser("skills", help="Show or install the agent skill")
    skills.add_argument(
        "action", choices=("show", "install"), nargs="?", default="show"
    )
    skills.add_argument("--target", default=".agents/skills")
    args = parser.parse_args()
    try:
        if args.cmd == "inspect":
            if args.path.is_dir():
                return assess(args.path, args.model)
            inspect_pipeline(args.path)
        elif args.cmd == "generate":
            return generate(args)
        elif args.cmd == "validate":
            root = repo_root(Path(args.repo))
            try:
                plan = load_plan(Path(args.plan), root)
            except ValueError as exc:
                print(style("\n╭─ Validate local plan " + "─" * 36, "1;31", sys.stderr), file=sys.stderr)
                print(f"│ {args.plan}", file=sys.stderr)
                print(style("╰" + "─" * 60, "31", sys.stderr), file=sys.stderr)
                verdict("✗ INVALID", str(exc), "31", sys.stderr)
                print(file=sys.stderr)
                return 1
            heading("Validate local plan", str(args.plan))
            verdict("✓ VALID", "plan checks passed", "32")
            unresolved = sum(s["decision"] == "unresolved" for s in plan["steps"])
            if unresolved:
                verdict(
                    "! BLOCKED",
                    f"{unresolved} unresolved step(s) stop any run that selects them",
                    "33",
                )
            print()
            print_plan(plan, detailed=args.diff)
            if args.diff:
                print_diff(plan, root)
        elif args.cmd == "run":
            return run_command(args)
        elif args.cmd == "cache":
            return clean_caches(args)
        elif args.action == "show":
            print(SKILL_PATH.read_text())
        else:
            target = Path(args.target) / "tekton-innerloop"
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILL_PATH, target / "SKILL.md")
            print(f"Installed {target / 'SKILL.md'}")
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        yaml.YAMLError,
        subprocess.SubprocessError,
    ) as exc:
        print(style(f"\n  ✗ {exc}\n", "1;31", sys.stderr), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n  Cancelled.\n", file=sys.stderr)
        return 130

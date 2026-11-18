"""The inspect, validate and run commands, pipeline discovery and saved plans."""

import argparse
import contextlib
import copy
import io
import json
import os
import sys
from unittest.mock import Mock, patch

import yaml

from tkn_innerloop.cli import ensure_plan, main, run_command
from tkn_innerloop.inventory import choose_pipeline
from tkn_innerloop.paths import digest
from tkn_innerloop.ui import style

from .helpers import CLI, InnerloopCase, patch_names


class CommandTests(InnerloopCase):
    def run_validate(self):
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps(self.plan))
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(
                sys, "argv", [str(CLI), "validate", str(plan_file), "--repo", str(self.root)]
            ),
            patch_names(repo_root=lambda path: self.root),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = main()
        return code, out.getvalue(), err.getvalue()

    def cached(self, scope=("checks/check",), **kwargs):
        args = argparse.Namespace(model=None, convert_timeout=5, refresh=False)
        vars(args).update(kwargs)
        scope = list(scope)
        plan = dict(copy.deepcopy(self.plan), scope=scope)
        built = Mock(return_value=plan)
        with (
            patch_names(build_plan=built),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = ensure_plan(args, self.root, self.pipeline, scope)
        return result, built

    def run_target(self, target, plan, run_code=0, **kwargs):
        args = argparse.Namespace(
            plan=target, repo=str(self.root), runtime="host", base=None,
            steps=None, env=[], timeout=2, model=None, convert_timeout=5, refresh=False,
        )
        vars(args).update(kwargs)
        ran = Mock(return_value=run_code)
        with (
            patch_names(repo_root=lambda path: self.root,
                ensure_plan=Mock(return_value=(self.plan_file, plan)),
                run_plan=ran,
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = run_command(args)
        return code, ran, args

    def test_inspect_never_labels_unknown_deployment_runnable(self):
        self.write_source(
            [
                {
                    "name": "deploy",
                    "image": "kubectl",
                    "script": "kubectl apply -f prod.yaml",
                }
            ]
        )
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(CLI), "inspect", str(self.pipeline)]),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertIn("○ PENDING", output.getvalue())
        self.assertIn("checks/deploy", output.getvalue())
        self.assertNotIn("RUN LOCAL", output.getvalue())
        self.assertNotIn("VALID", output.getvalue())

    def test_inspect_marks_references_as_unread(self):
        self.write_source([{"name": "fetch", "ref": {"name": "git-clone"}}])
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(CLI), "inspect", str(self.pipeline)]),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertIn("? REFERENCE", output.getvalue())
        self.assertIn("1 references", output.getvalue())

    def test_validate_reports_valid_plan(self):
        code, out, _ = self.run_validate()
        self.assertEqual(code, 0)
        self.assertIn("✓ VALID", out)
        self.assertNotIn("INVALID", out)

    def test_validate_reports_invalid_plan_on_stderr(self):
        self.plan["steps"] = []
        code, out, err = self.run_validate()
        self.assertEqual(code, 1)
        self.assertIn("✗ INVALID", err)
        self.assertNotIn("✓ VALID", out)

    def test_pipeline_is_found_or_the_choice_is_listed(self):
        choose = choose_pipeline
        step = [{"name": "a", "image": "example.invalid/x:1", "script": "true"}]
        self.assertEqual(choose(self.root, None), self.pipeline.resolve())
        bundle = self.root / ".tekton" / "bundle.yaml"
        bundle.parent.mkdir()
        bundle.write_text(
            yaml.safe_dump({"kind": "PipelineRun", "spec": {"pipelineRef": {"resolver": "git"}}})
        )
        with self.assertRaisesRegex(
            ValueError, r"No pipeline in \.tekton/ can run locally:\n    \.tekton/bundle\.yaml: .*remote pipelineRef"
        ):
            choose(self.root, None)
        bundle.unlink()
        self.pipeline.unlink()
        with self.assertRaisesRegex(ValueError, "No runnable PipelineRun"):
            choose(self.root, None)
        one = self.write_pipeline("one.yaml", step, annotations={"pipelinesascode.tekton.dev/on-event": "[pull_request]"})
        self.assertEqual(choose(self.root, None), one)
        self.write_pipeline("two.yaml", step, annotations={"pipelinesascode.tekton.dev/on-event": "[push]"})
        with self.assertRaisesRegex(ValueError, r"run \.tekton/one\.yaml   # pull_request"):
            choose(self.root, None)
        self.assertEqual(choose(self.root, ".tekton/two.yaml").name, "two.yaml")
        with self.assertRaisesRegex(ValueError, "not found"):
            choose(self.root, "missing.yaml")

    def test_plan_is_cached_outside_the_working_tree_and_reused(self):
        (path, _), built = self.cached()
        self.assertEqual(built.call_count, 1)
        self.assertEqual(path.parent, self.cache)
        self.assertFalse(path.is_relative_to(self.root))
        _, built = self.cached()
        self.assertEqual(built.call_count, 0)

    def test_cached_plan_is_rebuilt_when_stale_refreshed_or_scope_changes(self):
        self.cached()
        _, built = self.cached(refresh=True)
        self.assertEqual(built.call_count, 1)
        _, built = self.cached(scope=("checks/check", "checks/other"))
        self.assertEqual(built.call_count, 1)
        self.write_source(
            [{"name": "check", "image": "example.invalid/check:1", "script": "exit 1"}]
        )
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        _, built = self.cached()
        self.assertEqual(built.call_count, 1)

    def test_run_with_a_json_plan_skips_conversion(self):
        code, ran, _ = self.run_target("saved.plan.json", self.plan)
        self.assertEqual(code, 0)
        ran.assert_called_once()

    def test_run_with_a_pipeline_converts_then_runs(self):
        code, _, args = self.run_target(str(self.pipeline), dict(self.plan, scope=["checks/check"]))
        self.assertEqual(code, 0)
        self.assertEqual(args.plan, str(self.plan_file))

    def test_unresolved_steps_are_reported_and_exit_with_two(self):
        plan = copy.deepcopy(self.plan)
        plan["steps"].append(
            {"id": "checks/other", "decision": "unresolved", "reason": "needs a service"}
        )
        self.write_source(
            [
                {"name": "check", "image": "example.invalid/check:1", "script": "exit 0"},
                {"name": "other", "image": "example.invalid/other:1", "script": "exit 0"},
            ]
        )
        code, _, args = self.run_target(str(self.pipeline), plan)
        self.assertEqual(code, 2)
        self.assertEqual(args.steps, "checks/check")

    def test_explicit_steps_are_never_widened_by_unresolved_ones(self):
        plan = copy.deepcopy(self.plan)
        plan["steps"].append(
            {"id": "checks/other", "decision": "unresolved", "reason": "needs a service"}
        )
        self.write_source(
            [
                {"name": "check", "image": "example.invalid/check:1", "script": "exit 0"},
                {"name": "other", "image": "example.invalid/other:1", "script": "exit 0"},
            ]
        )
        code, _, args = self.run_target(str(self.pipeline), plan, steps="check")
        self.assertEqual(code, 0)
        self.assertEqual(args.steps, "check")

    def test_only_unresolved_steps_blocks_instead_of_running(self):
        plan = copy.deepcopy(self.plan)
        plan["steps"][0] = {"id": "checks/check", "decision": "unresolved", "reason": "why"}
        code, ran, _ = self.run_target(str(self.pipeline), plan)
        self.assertEqual(code, 1)
        ran.assert_not_called()

    def test_terminal_colors_respect_redirection_and_no_color(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            env = os.environ.copy()
            env.pop("NO_COLOR", None)
            with patch.dict(os.environ, env, clear=True):
                self.assertIn("\033[32m", style("ready", "32", terminal))
            with patch.dict(os.environ, {"NO_COLOR": ""}):
                self.assertEqual(style("ready", "32", terminal), "ready")
        self.assertEqual(style("ready", "32", io.StringIO()), "ready")

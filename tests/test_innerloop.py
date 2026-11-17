import argparse
import contextlib
import copy
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

CLI = Path(__file__).resolve().parents[1] / "bin/tkn-innerloop"
MODULE = runpy.run_path(str(CLI))
GLOBALS = MODULE["run_plan"].__globals__


class InnerloopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="innerloop-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pipeline = self.root / "pipeline.yaml"
        self.write_source(
            [{"name": "check", "image": "example.invalid/check:1", "script": "exit 0"}]
        )
        self.plan = {
            "version": 1,
            "pipeline": "pipeline.yaml",
            "inputs": {"pipeline.yaml": MODULE["digest"](self.pipeline)},
            "steps": [
                {
                    "id": "checks/check",
                    "decision": "run",
                    "reason": "Local check",
                    "image": "example.invalid/check:1",
                    "shell": "/bin/sh",
                    "working_dir": "$WORKSPACE",
                    "env": {},
                    "required_env": [],
                    "depends_on": [],
                    "script": "exit 0",
                    "edits": [],
                    "adaptations": [],
                }
            ],
        }
        self.plan_file = self.root / "plan.json"

    def write_source(self, steps):
        self.pipeline.write_text(
            yaml.safe_dump(
                {
                    "kind": "PipelineRun",
                    "spec": {
                        "pipelineSpec": {
                            "tasks": [{"name": "checks", "taskSpec": {"steps": steps}}]
                        }
                    },
                }
            )
        )

    def validate(self):
        return MODULE["validate_plan"](self.plan, self.root)

    def set_script(self, script):
        self.plan["steps"][0].update(
            script=script,
            edits=[{"old": "exit 0", "new": script, "reason": "Fixture adaptation"}],
        )

    def run_plan(self, capture=None, **kwargs):
        self.plan_file.write_text(json.dumps(self.plan))
        args = argparse.Namespace(
            plan=str(self.plan_file),
            repo=str(self.root),
            runtime="host",
            base="origin/target",
            steps=None,
            env=[],
            timeout=2,
        )
        vars(args).update(kwargs)
        with (
            patch.dict(GLOBALS, repo_root=lambda path: self.root),
            contextlib.redirect_stdout(capture or io.StringIO()),
        ):
            return MODULE["run_plan"](args)

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
            self.assertEqual(MODULE["main"](), 0)
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
            self.assertEqual(MODULE["main"](), 0)
        self.assertIn("? REFERENCE", output.getvalue())
        self.assertIn("1 references", output.getvalue())

    def run_validate(self):
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps(self.plan))
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(
                sys, "argv", [str(CLI), "validate", str(plan_file), "--repo", str(self.root)]
            ),
            patch.dict(GLOBALS, repo_root=lambda path: self.root),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = MODULE["main"]()
        return code, out.getvalue(), err.getvalue()

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

    def test_incomplete_or_duplicate_coverage_rejected(self):
        self.plan["steps"].append(copy.deepcopy(self.plan["steps"][0]))
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.validate()
        self.plan["steps"] = []
        with self.assertRaisesRegex(ValueError, "every source step"):
            self.validate()

    def test_unresolved_reference_cannot_be_run(self):
        self.write_source([{"name": "check", "ref": {"name": "remote"}}])
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        with self.assertRaisesRegex(ValueError, "retain their source image"):
            self.validate()

    def test_changed_image_and_unbound_templates_rejected(self):
        self.plan["steps"][0]["image"] = "different:1"
        with self.assertRaisesRegex(ValueError, "source image"):
            self.validate()
        self.plan["steps"][0]["image"] = "example.invalid/check:1"
        self.set_script("cd $(workspaces.source.path)")
        with self.assertRaisesRegex(ValueError, "unresolved Tekton"):
            self.validate()

    def test_skip_hint_is_honored_even_with_display_name(self):
        self.write_source(
            [
                {
                    "name": "check",
                    "image": "example.invalid/check:1",
                    "displayName": "Check",
                    "description": "(skip-local)",
                    "script": "exit 0",
                }
            ]
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        with self.assertRaisesRegex(ValueError, "skip-local"):
            self.validate()

    def test_changed_helper_invalidates_plan(self):
        helper = self.root / "helper.sh"
        helper.write_text("exit 0\n")
        self.plan["inputs"]["helper.sh"] = MODULE["digest"](helper)
        self.validate()
        helper.write_text("exit 1\n")
        with self.assertRaisesRegex(ValueError, "Stale plan: helper.sh"):
            self.validate()

    def test_source_edits_allow_reuse_but_pipeline_changes_do_not(self):
        (self.root / "test.go").write_text("changed test\n")
        self.validate()
        self.pipeline.write_text(self.pipeline.read_text() + "\n# changed\n")
        with self.assertRaisesRegex(ValueError, "Stale plan"):
            self.validate()

    def test_inputs_cannot_escape_repository(self):
        self.plan["inputs"][str(self.pipeline)] = MODULE["digest"](self.pipeline)
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            self.validate()

    def test_empty_script_and_invalid_shell_rejected(self):
        self.set_script("")
        with self.assertRaisesRegex(ValueError, "needs script"):
            self.validate()
        self.set_script("if; then")
        with self.assertRaisesRegex(ValueError, "invalid shell syntax"):
            self.validate()

    def test_environment_cannot_pretend_to_evaluate_result_files(self):
        self.plan["steps"][0]["env"] = {"ARTIFACT": "$(cat result.txt)"}
        with self.assertRaisesRegex(ValueError, "literal values"):
            self.validate()

    def test_command_only_source_can_fail_locally(self):
        self.write_source(
            [
                {
                    "name": "check",
                    "image": "example.invalid/check:1",
                    "command": ["/bin/sh", "-c"],
                    "args": ["exit 17"],
                }
            ]
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][0]["script"] = "/bin/sh -c 'exit 17'\n"
        self.assertEqual(self.run_plan(), 1)

    def test_host_working_directory_environment_and_merge_base(self):
        (self.root / "subdir").mkdir()
        self.plan["steps"][0].update(
            working_dir="$WORKSPACE/subdir", env={"EXPECTED": "$BASE_SHA"}
        )
        self.set_script(
            'test "$EXPECTED" = merge-base-sha && test "$PWD" = "$WORKSPACE/subdir"'
        )
        git = Mock(return_value="merge-base-sha")
        with patch.dict(GLOBALS, git=git):
            self.assertEqual(self.run_plan(), 0)
        git.assert_called_once_with(self.root, "merge-base", "HEAD", "origin/target")

    def test_unknown_empty_and_unresolved_selections_fail(self):
        for selection in ("missing", " , "):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.run_plan(steps=selection)
        self.plan["steps"][0]["decision"] = "unresolved"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.run_plan(), 1)
        self.assertIn("✗ BLOCKED", err.getvalue())
        self.assertIn("nothing was run", err.getvalue())
        self.plan["steps"][0]["decision"] = "skip"
        with self.assertRaisesRegex(ValueError, "No runnable steps"):
            self.run_plan()

    def test_blocked_run_suggests_decided_steps_with_their_environment(self):
        plan = {
            "pipeline": ".tekton/go.yaml",
            "steps": [
                {"id": "go/cache-fetch", "decision": "unresolved", "reason": "remote task"},
                {"id": "go/unittest", "decision": "run", "required_env": ["GH_TOKEN"]},
                {"id": "go/lint", "decision": "run", "required_env": []},
                {"id": "go/cache-upload", "decision": "skip", "reason": "push"},
            ],
        }
        args = argparse.Namespace(plan="/tmp/foo", runtime="podman")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = MODULE["report_blocked"](plan, [plan["steps"][0]], args)
        text = err.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("? UNRESOLVED go/cache-fetch", text)
        self.assertIn(
            "run /tmp/foo --steps go/unittest,go/lint --env GH_TOKEN --runtime podman", text
        )
        self.assertIn("generate .tekton/go.yaml -o /tmp/foo", text)

    def test_missing_runtime_names_an_installed_alternative(self):
        installed = {"docker": "/usr/bin/docker"}
        with patch("shutil.which", side_effect=installed.get):
            with self.assertRaisesRegex(ValueError, "podman.*docker is installed.*--runtime docker"):
                self.run_plan(runtime="podman")
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "--runtime host"):
                self.run_plan(runtime="docker")

    def test_selection_cannot_omit_declared_setup(self):
        self.write_source(
            [
                {
                    "name": "check",
                    "image": "example.invalid/check:1",
                    "script": "exit 0",
                },
                {
                    "name": "second",
                    "image": "example.invalid/check:1",
                    "script": "exit 0",
                },
            ]
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        second = copy.deepcopy(self.plan["steps"][0])
        second.update(id="checks/second", depends_on=["checks/check"])
        self.plan["steps"].append(second)
        with self.assertRaisesRegex(ValueError, "omits required setup"):
            self.run_plan(steps="second")

    def test_secrets_require_explicit_forwarding(self):
        self.plan["steps"][0]["required_env"] = ["INNERLOOP_TEST_SECRET"]
        self.set_script('test "$INNERLOOP_TEST_SECRET" = fixture')
        with patch.dict(os.environ, INNERLOOP_TEST_SECRET="fixture"):
            with self.assertRaisesRegex(ValueError, "explicitly forwarded"):
                self.run_plan()
            self.assertEqual(self.run_plan(env=["INNERLOOP_TEST_SECRET"]), 0)

    def test_optional_env_is_forwarded_only_when_asked_and_never_blocks(self):
        self.plan["steps"][0]["optional_env"] = ["INNERLOOP_TEST_OPTIONAL"]

        def container_cmd(**kwargs):
            process = Mock(returncode=0)
            with (
                patch.object(subprocess, "Popen", return_value=process) as popen,
                patch.object(subprocess, "run", return_value=Mock(returncode=0)),
                patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
            ):
                self.assertEqual(self.run_plan(runtime="podman", **kwargs), 0)
            return popen.call_args.args[0]

        with patch.dict(os.environ, INNERLOOP_TEST_OPTIONAL="fixture"):
            self.assertNotIn("INNERLOOP_TEST_OPTIONAL", container_cmd())
            self.assertIn(
                "INNERLOOP_TEST_OPTIONAL",
                container_cmd(env=["INNERLOOP_TEST_OPTIONAL"]),
            )
        os.environ.pop("INNERLOOP_TEST_OPTIONAL", None)
        self.assertNotIn(
            "INNERLOOP_TEST_OPTIONAL", container_cmd(env=["INNERLOOP_TEST_OPTIONAL"])
        )

    def test_optional_env_cannot_overlap_required(self):
        self.plan["steps"][0]["required_env"] = ["SAME"]
        self.plan["steps"][0]["optional_env"] = ["SAME"]
        with self.assertRaisesRegex(ValueError, "both required and optional"):
            self.validate()

    def test_runtime_auto_prefers_podman_then_docker(self):
        resolve = MODULE["resolve_runtime"]
        both = {"podman": "/usr/bin/podman", "docker": "/usr/bin/docker"}
        with patch("shutil.which", side_effect=both.get):
            self.assertEqual(resolve("auto"), "podman")
            self.assertEqual(resolve("docker"), "docker")
        with patch("shutil.which", side_effect={"docker": "/usr/bin/docker"}.get):
            self.assertEqual(resolve("auto"), "docker")
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "No container runtime"):
                resolve("auto")

    def test_default_base_prefers_origin_head_then_main_then_master(self):
        def sh(*cmd):
            subprocess.run(cmd, cwd=self.root, check=True, capture_output=True)

        sh("git", "init", "-q", "-b", "trunk")
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x")
        with self.assertRaisesRegex(ValueError, "--base"):
            MODULE["default_base"](self.root)
        sh("git", "branch", "master")
        self.assertEqual(MODULE["default_base"](self.root), "master")
        sh("git", "branch", "main")
        self.assertEqual(MODULE["default_base"](self.root), "main")

    def test_container_runtime_receives_stdin_context_and_entrypoint(self):
        process = Mock(returncode=0)
        self.plan["steps"][0]["env"] = {"CACHE": "$WORKSPACE/cache"}
        with (
            patch.object(subprocess, "Popen", return_value=process) as popen,
            patch.object(subprocess, "run", return_value=Mock(returncode=0)),
            patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
        ):
            self.assertEqual(self.run_plan(runtime="podman"), 0)
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:3], ["podman", "run", "--rm"])
        self.assertIn("-i", cmd)
        self.assertIn("WORKSPACE=/workspace", cmd)
        self.assertIn("CACHE=/workspace/cache", cmd)
        self.assertIn(f"{self.root}:/workspace:Z", cmd)
        self.assertEqual(
            cmd[-5:], ["--entrypoint", "/bin/sh", "example.invalid/check:1", "-e", "-s"]
        )
        process.communicate.assert_called_once_with("exit 0\n", timeout=2)

    def test_agent_generation_scopes_work_and_preserves_complete_coverage(self):
        self.write_source(
            [
                {
                    "name": "check",
                    "image": "example.invalid/check:1",
                    "script": "exit 0",
                },
                {
                    "name": "other",
                    "image": "example.invalid/other:1",
                    "script": "echo OMITTED_SCRIPT",
                },
            ]
        )
        proposed = {"inputs": [], "steps": copy.deepcopy(self.plan["steps"])}
        proposed["steps"][0].pop("script")
        agent = Mock(return_value=proposed)
        args = argparse.Namespace(
            file=str(self.pipeline),
            steps="check",
            model=None,
            output=str(self.plan_file),
            timeout=10,
        )
        with (
            patch.dict(GLOBALS, repo_root=lambda path: self.root, agent_plan=agent),
            patch.dict(os.environ, {"TKN_INNERLOOP_AI_MODEL": ""}),
            patch.object(MODULE["shutil"], "which", return_value="pi"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MODULE["generate"](args), 0)
        cmd = agent.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "read,grep,find,ls")
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        self.assertIn("--offline", cmd)
        self.assertNotIn("OMITTED_SCRIPT", cmd[-1])
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual(
            plan["inputs"]["pipeline.yaml"], MODULE["digest"](self.pipeline)
        )
        self.assertEqual(plan["steps"][0]["script"], "exit 0")
        self.assertEqual(plan["steps"][1]["decision"], "skip")

    def build(self, agent):
        with (
            patch.dict(GLOBALS, agent_plan=agent),
            patch.dict(os.environ, {"TKN_INNERLOOP_AI_MODEL": ""}),
            patch.object(MODULE["shutil"], "which", return_value="pi"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return MODULE["build_plan"](self.pipeline, self.root, ["checks/check"], None, 10)

    def good_proposal(self):
        proposed = {"inputs": [], "steps": copy.deepcopy(self.plan["steps"])}
        proposed["steps"][0].pop("script")
        return proposed

    def test_malformed_answer_is_retried_with_the_error(self):
        rejected = MODULE["PlanRejected"]("Agent returned invalid plan JSON")
        agent = Mock(side_effect=[rejected, self.good_proposal()])
        plan = self.build(agent)
        self.assertEqual(agent.call_count, 2)
        self.assertIn("previous answer was rejected", agent.call_args.args[0][-1])
        self.assertIn("invalid plan JSON", agent.call_args.args[0][-1])
        self.assertEqual(plan["scope"], ["checks/check"])

    def test_invalid_plan_is_retried_then_gives_up_after_three_attempts(self):
        bad = self.good_proposal()
        bad["steps"][0]["image"] = "example.invalid/other:1"
        agent = Mock(return_value=bad)
        with self.assertRaisesRegex(ValueError, "source image"):
            self.build(agent)
        self.assertEqual(agent.call_count, 3)

    def test_second_retry_can_still_recover(self):
        rejected = MODULE["PlanRejected"]("Agent returned invalid plan JSON")
        agent = Mock(side_effect=[rejected, rejected, self.good_proposal()])
        self.assertEqual(self.build(agent)["scope"], ["checks/check"])
        self.assertEqual(agent.call_count, 3)

    def test_unbound_placeholder_is_named_and_hinted_on_retry(self):
        bad = self.good_proposal()
        bad["steps"][0]["edits"] = [
            {"old": "exit 0", "new": "echo {{ body.pull_request.base.sha }}", "reason": "x"}
        ]
        agent = Mock(side_effect=[bad, self.good_proposal()])
        self.build(agent)
        sent = agent.call_args.args[0][-1]
        self.assertIn("{{ body.pull_request.base.sha }}", sent)
        self.assertIn("$BASE_SHA", sent)

    def test_unresolved_judgment_is_never_retried(self):
        proposed = {
            "inputs": [],
            "steps": [{"id": "checks/check", "decision": "unresolved", "reason": "needs a service"}],
        }
        agent = Mock(return_value=proposed)
        plan = self.build(agent)
        self.assertEqual(agent.call_count, 1)
        self.assertEqual(plan["steps"][0]["decision"], "unresolved")

    def test_timeouts_and_agent_failures_are_not_retried(self):
        agent = Mock(side_effect=ValueError("Conversion timed out after 10s"))
        with self.assertRaisesRegex(ValueError, "timed out"):
            self.build(agent)
        self.assertEqual(agent.call_count, 1)

    def write_pipeline(self, name, steps, finally_steps=None, annotations=None):
        path = self.root / ".tekton" / name
        path.parent.mkdir(exist_ok=True)
        spec = {"tasks": [{"name": "checks", "taskSpec": {"steps": steps}}]}
        if finally_steps:
            spec["finally"] = [{"name": "report", "taskSpec": {"steps": finally_steps}}]
        path.write_text(
            yaml.safe_dump(
                {
                    "kind": "PipelineRun",
                    "metadata": {"name": name, "annotations": annotations or {}},
                    "spec": {"pipelineSpec": spec},
                }
            )
        )
        return path

    def test_auto_scope_leaves_obvious_cluster_work_out(self):
        image = "example.invalid/x:1"
        path = self.write_pipeline(
            "p.yaml",
            [
                {"name": "lint", "image": image, "script": "make lint"},
                {"name": "clone", "ref": {"name": "git-clone"}},
                {"name": "hidden", "image": image, "script": "x", "displayName": "Sign (skip-local)"},
                {"name": "ship", "image": image, "script": "podman push img"},
                {
                    "name": "notify",
                    "image": image,
                    "script": 'curl -s -H "A: b" \\\n  -X POST "https://example.invalid"',
                },
            ],
            finally_steps=[{"name": "tail", "image": image, "script": "echo done"}],
        )
        keep, left = MODULE["auto_scope"](MODULE["inventory"](path))
        self.assertEqual(keep, ["checks/lint"])
        self.assertEqual(left["checks/clone"], "external reference")
        self.assertEqual(left["checks/hidden"], "marked skip-local")
        self.assertEqual(left["checks/ship"], "publishes or deploys")
        self.assertEqual(left["checks/notify"], "publishes or deploys")
        self.assertEqual(left["report/tail"], "runs after the pipeline")

    def test_pipeline_is_found_or_the_choice_is_listed(self):
        choose = MODULE["choose_pipeline"]
        step = [{"name": "a", "image": "example.invalid/x:1", "script": "true"}]
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

    def cached(self, scope=("checks/check",), **kwargs):
        args = argparse.Namespace(model=None, convert_timeout=5, refresh=False)
        vars(args).update(kwargs)
        scope = list(scope)
        plan = dict(copy.deepcopy(self.plan), scope=scope)
        built = Mock(return_value=plan)
        with (
            patch.dict(GLOBALS, build_plan=built),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = MODULE["ensure_plan"](args, self.root, self.pipeline, scope)
        return result, built

    def test_plan_is_cached_in_a_self_ignoring_folder_and_reused(self):
        (path, _), built = self.cached()
        self.assertEqual(built.call_count, 1)
        self.assertEqual(path.parent.name, ".tekton-innerloop")
        self.assertEqual((path.parent / ".gitignore").read_text(), "*\n")
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
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        _, built = self.cached()
        self.assertEqual(built.call_count, 1)

    def run_target(self, target, plan, run_code=0, **kwargs):
        args = argparse.Namespace(
            plan=target, repo=str(self.root), runtime="host", base=None,
            steps=None, env=[], timeout=2, model=None, convert_timeout=5, refresh=False,
        )
        vars(args).update(kwargs)
        ran = Mock(return_value=run_code)
        with (
            patch.dict(
                GLOBALS,
                repo_root=lambda path: self.root,
                ensure_plan=Mock(return_value=(self.plan_file, plan)),
                run_plan=ran,
            ),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = MODULE["run_command"](args)
        return code, ran, args

    def test_run_with_a_json_plan_skips_conversion(self):
        code, ran, _ = self.run_target("saved.plan.json", self.plan)
        self.assertEqual(code, 0)
        ran.assert_called_once()

    def test_run_with_a_pipeline_converts_then_runs(self):
        code, ran, args = self.run_target(str(self.pipeline), dict(self.plan, scope=["checks/check"]))
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
        code, ran, args = self.run_target(str(self.pipeline), plan)
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
        code, ran, args = self.run_target(str(self.pipeline), plan, steps="check")
        self.assertEqual(code, 0)
        self.assertEqual(args.steps, "check")

    def test_only_unresolved_steps_blocks_instead_of_running(self):
        plan = copy.deepcopy(self.plan)
        plan["steps"][0] = {"id": "checks/check", "decision": "unresolved", "reason": "why"}
        code, ran, _ = self.run_target(str(self.pipeline), plan)
        self.assertEqual(code, 1)
        ran.assert_not_called()

    def multi_step_plan(self, specs):
        """specs: list of (name, script, depends_on). Steps run on the host in this order."""
        image = "example.invalid/check:1"
        self.write_source(
            [{"name": n, "image": image, "script": sc} for n, sc, _ in specs]
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        base = self.plan["steps"][0]
        self.plan["steps"] = [
            dict(copy.deepcopy(base), id=f"checks/{n}", script=sc, depends_on=[f"checks/{d}" for d in deps])
            for n, sc, deps in specs
        ]

    def test_independent_steps_keep_running_after_a_failure(self):
        self.multi_step_plan(
            [
                ("a", 'touch "$WORKSPACE/ran-a"', []),
                ("b", "exit 1", []),
                ("c", 'touch "$WORKSPACE/ran-c"', ["b"]),
                ("d", 'touch "$WORKSPACE/ran-d"', []),
                ("e", 'touch "$WORKSPACE/ran-e"', ["c"]),
            ]
        )
        out = io.StringIO()
        code = self.run_plan(capture=out)
        self.assertEqual(code, 1)
        for ran in ("ran-a", "ran-d"):
            self.assertTrue((self.root / ran).exists(), ran)
        for skipped in ("ran-c", "ran-e"):
            self.assertFalse((self.root / skipped).exists(), skipped)
        text = out.getvalue()
        self.assertIn("1 of 5 failed: checks/b", text)
        self.assertIn("2 not run", text)
        self.assertIn("checks/c skipped: needs checks/b, which failed", text)
        self.assertIn("checks/e skipped: needs checks/c, which failed", text)

    def run_container(self, runtime, uid=1000, **extra):
        process = Mock(returncode=0)
        run = Mock(return_value=Mock(returncode=0, stdout="sha256:aaa"))
        with (
            patch.object(subprocess, "Popen", return_value=process),
            patch.object(subprocess, "run", run),
            patch.object(MODULE["shutil"], "which", return_value=f"/usr/bin/{runtime}"),
            patch.object(os, "getuid", return_value=uid),
            patch.object(os, "getgid", return_value=uid),
        ):
            self.assertEqual(self.run_plan(runtime=runtime, **extra), 0)
        return run

    def test_docker_hands_root_owned_files_back_but_podman_and_root_do_not(self):
        chowns = lambda run: [
            c for c in run.call_args_list if "chown" in " ".join(c.args[0])
        ]
        docker = chowns(self.run_container("docker"))
        self.assertEqual(len(docker), 1)
        self.assertIn("find /workspace -user 0 -exec chown 1000:1000 {} +", docker[0].args[0])
        self.assertIn("example.invalid/check:1", docker[0].args[0])
        self.assertEqual(chowns(self.run_container("podman")), [])
        self.assertEqual(chowns(self.run_container("docker", uid=0)), [])

    def test_a_moved_image_tag_is_flagged_on_the_next_run(self):
        def run_with(image_id):
            process = Mock(returncode=0)
            run = Mock(return_value=Mock(returncode=0, stdout=image_id))
            out = io.StringIO()
            with (
                patch.object(subprocess, "Popen", return_value=process),
                patch.object(subprocess, "run", run),
                patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
                patch.dict(GLOBALS, repo_root=lambda path: self.root),
                contextlib.redirect_stdout(out),
            ):
                args = argparse.Namespace(
                    plan=str(self.plan_file), repo=str(self.root), runtime="podman",
                    base="main", steps=None, env=[], timeout=2,
                )
                self.plan_file.write_text(json.dumps(self.plan))
                MODULE["run_plan"](args)
            return out.getvalue()

        self.assertNotIn("different image", run_with("sha256:aaa"))
        self.assertNotIn("different image", run_with("sha256:aaa"))
        self.assertIn("different image than last time", run_with("sha256:bbb"))
        self.assertEqual((self.root / ".tekton-innerloop" / ".gitignore").read_text(), "*\n")

    def test_agent_stream_uses_final_message_and_shows_tool_progress(self):
        events = [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "toolUse",
                    "content": [{"type": "text", "text": "Looking around"}],
                },
            },
            {"type": "tool_execution_start", "toolName": "read"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [
                        {
                            "type": "text",
                            "text": 'Plan follows:\n```json\n{"inputs":[],"steps":[]}\n```',
                        }
                    ],
                },
            },
        ]
        code = (
            "import sys; sys.stdout.write("
            + repr("\n".join(json.dumps(e) for e in events) + "\n")
            + ")"
        )
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = MODULE["agent_plan"]([sys.executable, "-c", code], self.root, 5)
        self.assertEqual(result, {"inputs": [], "steps": []})
        self.assertIn("Inspecting repository", output.getvalue())
        self.assertNotIn("\033[", output.getvalue())

    def test_agent_timeout_stops_process(self):
        pid_file = self.root / "agent.pid"
        code = (
            "import os,time; from pathlib import Path; Path("
            + repr(str(pid_file))
            + ").write_text(str(os.getpid())); time.sleep(10)"
        )
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(ValueError, "timed out"),
        ):
            MODULE["agent_plan"]([sys.executable, "-c", code], self.root, 0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_agent_errors_and_truncated_responses_are_rejected(self):
        for code, error in [
            (
                "import sys; print('provider failed', file=sys.stderr); sys.exit(1)",
                "provider failed",
            ),
            ("print('{}')", "completed plan"),
            (
                "print("
                + repr(
                    json.dumps(
                        {
                            "type": "message_end",
                            "message": {"role": "assistant", "stopReason": "length"},
                        }
                    )
                )
                + ")",
                "completed plan",
            ),
        ]:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, error):
                MODULE["agent_plan"]([sys.executable, "-c", code], self.root, 5)

    def test_multi_document_inventory_preserves_followup_context(self):
        source = yaml.safe_load(self.pipeline.read_text())
        documents = []
        for name in ("build", "tests", "deploy"):
            run = copy.deepcopy(source)
            run["metadata"] = {"name": name}
            documents.append(run)
        documents[-1]["metadata"]["annotations"] = {
            "pipelinesascode.tekton.dev/on-pipelinerun": '["build", "tests"]'
        }
        documents[-1]["spec"]["params"] = [
            {"name": "artifact", "value": "{{ runs.build.results.artifact }}"}
        ]
        self.pipeline.write_text(yaml.safe_dump_all([None, *documents, None]))
        sources = MODULE["inventory"](self.pipeline)
        self.assertEqual(
            list(sources),
            [f"{name}/checks/check" for name in ("build", "tests", "deploy")],
        )
        context = sources["deploy/checks/check"]["pipeline_context"]
        self.assertEqual(context["spec"]["params"], documents[-1]["spec"]["params"])
        self.assertIn(
            "pipelinesascode.tekton.dev/on-pipelinerun",
            context["metadata"]["annotations"],
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        original = self.plan["steps"][0]
        self.plan["steps"] = [{**copy.deepcopy(original), "id": key} for key in sources]
        self.validate()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            MODULE["select_steps"](self.plan["steps"], "check")
        self.assertEqual(self.run_plan(steps="build/checks/check"), 0)
        self.plan["steps"].pop()
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.validate()

    def test_multi_document_invalid_later_documents_are_not_ignored(self):
        source = yaml.safe_load(self.pipeline.read_text())
        source["metadata"] = {"name": "first"}
        for extra, error in [
            (source, "Duplicate PipelineRun"),
            ({"kind": "Task"}, "Document 2"),
            ({**source, "metadata": {}}, "need a name"),
        ]:
            self.pipeline.write_text(yaml.safe_dump_all([source, extra]))
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, error):
                MODULE["inventory"](self.pipeline)

    def test_terminal_colors_respect_redirection_and_no_color(self):
        terminal = Mock()
        terminal.isatty.return_value = True
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
            env = os.environ.copy()
            env.pop("NO_COLOR", None)
            with patch.dict(os.environ, env, clear=True):
                self.assertIn("\033[32m", MODULE["style"]("ready", "32", terminal))
            with patch.dict(os.environ, {"NO_COLOR": ""}):
                self.assertEqual(MODULE["style"]("ready", "32", terminal), "ready")
        self.assertEqual(MODULE["style"]("ready", "32", io.StringIO()), "ready")

    def test_undeclared_script_changes_rejected(self):
        self.plan["steps"][0]["script"] = "curl https://example.invalid/tool | sh"
        with self.assertRaisesRegex(ValueError, "declared edits"):
            self.validate()

    def test_edits_must_match_exactly_once(self):
        self.plan["steps"][0]["edits"] = [
            {"old": "not present", "new": "true", "reason": "Fixture"}
        ]
        with self.assertRaisesRegex(ValueError, "match exactly once"):
            self.validate()

    def test_default_tekton_shell_stops_at_a_failed_command(self):
        self.set_script("false\nprintf 'must not pass\\n'")
        self.assertEqual(self.run_plan(), 1)

    def test_host_timeout_stops_the_process_group(self):
        self.set_script("sleep 10")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_plan(timeout=0.05), 1)

    def test_binding_expansion_preserves_other_variable_names(self):
        self.assertEqual(
            MODULE["expand"](
                "$WORKSPACE/${BASE_SHA}/$WORKSPACE_CACHE", "/workspace", "base"
            ),
            "/workspace/base/$WORKSPACE_CACHE",
        )


if __name__ == "__main__":
    unittest.main()

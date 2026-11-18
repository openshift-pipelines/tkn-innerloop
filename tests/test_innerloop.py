import argparse
import contextlib
import copy
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

CLI = Path(__file__).resolve().parents[1] / "bin/tkn-innerloop"
MODULE = runpy.run_path(str(CLI))
GLOBALS = MODULE["run_plan"].__globals__
CORPUS = Path(__file__).resolve().parent / "corpus"


def clear_bundle_memos(test):
    for name in ("_bundle_digests", "_bundle_failures"):
        memo = patch.dict(GLOBALS[name], clear=True)
        memo.start()
        test.addCleanup(memo.stop)


def offline_skopeo(*args, **kwargs):
    raise GLOBALS["BundleError"]("offline: skopeo " + " ".join(args))


class InnerloopTests(unittest.TestCase):
    def setUp(self):
        clear_bundle_memos(self)
        self.temp = tempfile.TemporaryDirectory(prefix="innerloop-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cache = tempfile.TemporaryDirectory(prefix="innerloop-cache-")
        self.addCleanup(cache.cleanup)
        environ = patch.dict(os.environ, XDG_CACHE_HOME=cache.name)
        environ.start()
        self.addCleanup(environ.stop)
        self.cache = MODULE["cache_dir"](self.root)
        self.pipeline = self.root / "pipeline.yaml"
        self.write_source(
            [{"name": "check", "image": "example.invalid/check:1", "script": "exit 0"}]
        )
        self.plan = {
            "version": MODULE["PLAN_VERSION"],
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
        process.communicate.assert_called_once_with(
            ': > /results/.tkn-innerloop-started || :\nexit 0\n', timeout=2
        )

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
        self.assertNotIn("OMITTED_SCRIPT", agent.call_args.args[3])
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

    def test_model_thinking_suffix_replaces_thinking_off(self):
        tests = [
            ("haiku", ["--thinking", "off"]),
            ("github-copilot/claude-sonnet-5.5:minimal", []),
        ]
        for model, thinking in tests:
            with self.subTest(model=model):
                cmd = MODULE["pi_command"](model)
                index = cmd.index("--model")
                self.assertEqual(cmd[index - len(thinking):index], thinking)
                self.assertEqual(cmd.count("--thinking"), len(thinking) // 2)
                self.assertEqual(cmd[index + 1], model)

    def test_malformed_answer_is_retried_with_the_error(self):
        rejected = MODULE["PlanRejected"]("Agent returned invalid plan JSON")
        agent = Mock(side_effect=[rejected, self.good_proposal()])
        plan = self.build(agent)
        self.assertEqual(agent.call_count, 2)
        self.assertIn("previous answer was rejected", agent.call_args.args[3])
        self.assertIn("invalid plan JSON", agent.call_args.args[3])
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
        source = yaml.safe_load(self.pipeline.read_text())
        source["metadata"] = {"annotations": {"pipelinesascode.tekton.dev/on-event": "[pull_request]"}}
        self.pipeline.write_text(yaml.safe_dump(source))
        bad = self.good_proposal()
        bad["steps"][0]["edits"] = [
            {"old": "exit 0", "new": "echo {{ body.pull_request.base.sha }}", "reason": "x"}
        ]
        agent = Mock(side_effect=[bad, self.good_proposal()])
        self.build(agent)
        sent = agent.call_args.args[3]
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
                {"name": "dynamic", "image": "example.invalid/x:{{ source_branch }}", "script": "make"},
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
        self.assertEqual(left["checks/dynamic"], "image has an unresolved substitution")
        self.assertEqual(left["checks/notify"], "publishes or deploys")
        self.assertEqual(left["report/tail"], "runs after the pipeline")

    def test_tekton_caches_steps_become_a_local_cache_directory(self):
        image = "example.invalid/go:1"
        actions = self.root / "stepactions.yaml"
        actions.write_text(
            yaml.safe_dump_all(
                {
                    "apiVersion": "tekton.dev/v1beta1",
                    "kind": "StepAction",
                    "metadata": {"name": name},
                    "spec": {
                        "image": "quay.io/openshift-pipeline/pipelines-cache-rhel9:next",
                        "params": [{"name": "CACHE_PATH"}, {"name": "WORKING_DIR"}],
                        "script": f"/ko-app/cache {name.split('-')[1]} --folder $(params.CACHE_PATH)",
                    },
                }
                for name in ("cache-fetch", "cache-upload")
            )
        )
        cache_params = [
            {"name": "CACHE_PATH", "value": "$(workspaces.source.path)/go-cache"},
            {"name": "WORKING_DIR", "value": "$(workspaces.source.path)"},
        ]
        path = self.write_pipeline(
            "p.yaml",
            [
                {"name": "fetch", "ref": {"name": "cache-fetch"}, "params": cache_params},
                {
                    "name": "remote",
                    "ref": {"resolver": "http", "params": [{"name": "url", "value": "https://x.invalid/tekton/cache-upload.yaml"}]},
                    "params": [{"name": "cachePath", "value": "$(workspaces.source.path)/lint-cache"}],
                },
                {
                    "name": "test",
                    "image": image,
                    "workingDir": "$(workspaces.source.path)",
                    "env": [{"name": "GOCACHE", "value": "$(workspaces.source.path)/go-cache/build"}],
                    "script": "ls $(workspaces.source.path)/lint-cache $(workspaces.source.path)/go-cache-other",
                },
                {"name": "save", "ref": {"name": "cache-upload"}, "params": cache_params},
            ],
        )
        sources = MODULE["inventory"](path, self.root)
        go = sources["checks/fetch"]["cache"]["dir"]
        lint = sources["checks/remote"]["cache"]["dir"]
        self.assertRegex(go, r"^\$CACHES/go-cache-[0-9a-f]{12}$")
        self.assertRegex(lint, r"^\$CACHES/lint-cache-[0-9a-f]{12}$")
        self.assertEqual(sources["checks/save"]["cache"]["dir"], go)
        test = sources["checks/test"]["step"]
        self.assertEqual(test["env"][0]["value"], f"{go}/build")
        self.assertEqual(test["workingDir"], "$(workspaces.source.path)")
        self.assertEqual(
            test["script"], f'''ls "$CACHES"/{lint.split("/")[-1]} $(workspaces.source.path)/go-cache-other'''
        )
        keep, left = MODULE["auto_scope"](sources)
        self.assertEqual(keep, ["checks/test"])
        for key in ("checks/fetch", "checks/remote", "checks/save"):
            self.assertEqual(left[key], MODULE["CACHED"])
        self.assertIn("$CACHES", MODULE["plan_prompt"]("p.yaml", sources, ["checks/test"]))

    def test_cache_paths_expand_in_scripts_and_command_arguments(self):
        cache = "$(workspaces.source.path)/go-cache"
        script = f"""printf '%s\n' '{cache}/one file' "{cache}/two files" {cache}/third"""
        path = self.write_pipeline("p.yaml", [
            {"name": "fetch", "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": cache}]},
            {"name": "script", "image": "example.invalid/x:1", "script": script},
            {"name": "argv", "image": "example.invalid/x:1", "command": ["printf"],
             "args": ["%s\n", f"{cache}/one file", f"{cache}/two files", f"{cache}/third"]},
        ])
        sources = MODULE["inventory"](path, self.root)
        target = str(self.root / "local caches")
        slot = sources["checks/fetch"]["cache"]["dir"].split("/")[-1]
        for key in ("checks/script", "checks/argv"):
            with self.subTest(key=key):
                script = MODULE["adapted_script"](sources[key]["step"], [])
                result = subprocess.run(
                    ["/bin/sh", "-e", "-s"], input=script, text=True, capture_output=True,
                    env={**os.environ, "CACHES": target}, check=True,
                )
                self.assertEqual(result.stdout.splitlines(), [
                    f"{target}/{slot}/one file", f"{target}/{slot}/two files",
                    f"{target}/{slot}/third",
                ])

    def test_relative_and_implicit_caches_keep_their_paths(self):
        modules = "$(workspaces.source.path)/node_modules"
        script = f"npm cache verify && ls /opt/mycache && npm install && du -sh '{modules}'"
        path = self.write_pipeline("p.yaml", [
            {"name": name, "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": cache}]}
            for name, cache in (("relative", "cache"), ("modules", modules))
        ] + [{"name": "check", "image": "example.invalid/x:1", "script": script}])
        sources = MODULE["inventory"](path, self.root)
        self.assertEqual(sources["checks/check"]["step"]["script"], script)
        for name in ("relative", "modules"):
            self.assertEqual(sources[f"checks/{name}"]["cache"], {"path": ""})

    def test_cache_rewrites_follow_workspace_bindings_within_each_pipelinerun(self):
        def task(name, workspace, alias="source", fetch=False, subpath=""):
            cache = f"$(workspaces.{alias}.path)/go-cache"
            steps = [{"name": "check", "image": "example.invalid/x:1", "script": f"ls {cache}"}]
            if fetch:
                steps.insert(0, {"name": "fetch", "ref": {"name": "cache-fetch"},
                                 "params": [{"name": "CACHE_PATH", "value": cache}]})
            return {"name": name, "taskSpec": {"steps": steps},
                    "workspaces": [{"name": alias, "workspace": workspace, "subPath": subpath}]}

        self.pipeline.write_text(yaml.safe_dump_all([
            {"kind": "PipelineRun", "metadata": {"name": name},
             "spec": {"pipelineSpec": {"tasks": tasks}}}
            for name, tasks in [
                ("first", [task("owner", "shared", fetch=True), task("consumer", "shared", "repo"),
                           task("other", "different"), task("subdir", "shared", subpath="elsewhere")]),
                ("second", [task("other", "shared")]),
            ]
        ]))
        sources = MODULE["inventory"](self.pipeline, self.root)
        slot = sources["first/owner/fetch"]["cache"]["dir"].split("/")[-1]
        for name in ("first/owner", "first/consumer"):
            self.assertEqual(sources[f"{name}/check"]["step"]["script"], f'ls "$CACHES"/{slot}')
        for name in ("first/other", "first/subdir", "second/other"):
            self.assertEqual(sources[f"{name}/check"]["step"]["script"], "ls $(workspaces.source.path)/go-cache")

    def test_shared_task_steps_use_each_pipelineruns_own_cache(self):
        cache = "$(workspaces.source.path)/go-cache"
        task = {
            "kind": "Task", "metadata": {"name": "shared"},
            "spec": {"steps": [
                {"name": "fetch", "ref": {"name": "cache-fetch"},
                 "params": [{"name": "CACHE_PATH", "value": cache}]},
                {"name": "check", "image": "example.invalid/x:1", "script": f"ls {cache}"},
            ]},
        }
        runs = [
            {"kind": "PipelineRun", "metadata": {"name": name},
             "spec": {"pipelineSpec": {"tasks": [{"name": "owner", "taskRef": {"name": "shared"}}]}}}
            for name in ("one", "two")
        ]
        self.pipeline.write_text(yaml.safe_dump_all([*runs, task]))
        sources = MODULE["inventory"](self.pipeline, self.root)
        slots = [sources[f"{name}/owner/fetch"]["cache"]["dir"].split("/")[-1] for name in ("one", "two")]
        self.assertNotEqual(*slots)
        for name, slot in zip(("one", "two"), slots, strict=True):
            self.assertEqual(sources[f"{name}/owner/check"]["step"]["script"], f'ls "$CACHES"/{slot}')

    def test_cache_slots_are_stable_when_reordered_and_distinct_in_other_pipelines(self):
        steps = [
            {"name": name, "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": f"/tool-{name}/cache"}]}
            for name in ("a", "b")
        ]

        def slots(name, steps):
            path = self.write_pipeline(name, steps)
            sources = MODULE["inventory"](path, self.root)
            return {key: src["cache"]["dir"] for key, src in sources.items()}

        original = slots("p.yaml", steps)
        self.assertEqual(original, slots("p.yaml", steps[::-1]))
        self.assertEqual(len(set(original.values())), 2)
        self.assertFalse(set(original.values()) & set(slots("other.yaml", steps).values()))

    def test_here_documents_and_other_consumers_keep_the_original_cache_path(self):
        cache = "$(workspaces.source.path)/go-cache"
        for delimiter in ("EOF", "'EOF'"):
            with self.subTest(delimiter=delimiter):
                script = f"cat <<{delimiter}\npath={cache}\nEOF\n"
                path = self.write_pipeline("p.yaml", [
                    {"name": "fetch", "ref": {"name": "cache-fetch"},
                     "params": [{"name": "CACHE_PATH", "value": cache}]},
                    {"name": "config", "image": "example.invalid/x:1", "script": script},
                    {"name": "check", "image": "example.invalid/x:1", "script": f"ls {cache}"},
                ])
                sources = MODULE["inventory"](path, self.root)
                self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
                self.assertEqual(sources["checks/config"]["step"]["script"], script)
                self.assertEqual(sources["checks/check"]["step"]["script"], f"ls {cache}")

    def test_a_cache_used_inside_command_substitution_remains_shared(self):
        cache = self.root / "go-cache"
        producer = f"mkdir -p {cache}\nprintf ok > {cache}/item"
        consumer = f'value="$(cat {cache}/item)"\ntest "$value" = ok'
        self.write_source([
            {"name": "fetch", "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": str(cache)}]},
            {"name": "write", "image": "example.invalid/x:1", "script": producer},
            {"name": "read", "image": "example.invalid/x:1", "script": consumer},
        ])
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["checks/write"]["step"]["script"], producer)
        self.assertEqual(sources["checks/read"]["step"]["script"], consumer)
        env = {**os.environ, "CACHES": str(self.root / "local caches")}
        for name in ("write", "read"):
            subprocess.run(["/bin/sh", "-ec", sources[f"checks/{name}"]["step"]["script"]], env=env, check=True)

    def test_physical_cache_paths_are_shared_across_workspace_aliases_and_images(self):
        producer_path = "$(workspaces.source.path)/tool/cache"
        consumer_path = "$(workspaces.repo.path)/cache"
        producer = {"name": "write", "image": "example.invalid/build:1", "script": f"mkdir -p {producer_path}"}
        consumer = {"name": "read", "image": producer["image"],
                    "env": [{"name": "MAVEN_REPO", "value": consumer_path}], "script": 'ls "$MAVEN_REPO"'}
        tasks = [
            {"name": "build", "workspaces": [{"name": "source", "workspace": "shared"}],
             "taskSpec": {"steps": [
                 {"name": "fetch", "ref": {"name": "cache-fetch"},
                  "params": [{"name": "CACHE_PATH", "value": producer_path}]},
                 producer,
             ]}},
            {"name": "test", "workspaces": [{"name": "repo", "workspace": "shared", "subPath": "tool"}],
             "taskSpec": {"steps": [consumer]}},
        ]
        source = {"kind": "Pipeline", "spec": {"tasks": tasks}}
        self.pipeline.write_text(yaml.safe_dump(source))
        sources = MODULE["inventory"](self.pipeline, self.root)
        local_dir = sources["build/fetch"]["cache"]["dir"]
        self.assertEqual(sources["build/write"]["step"]["script"], f'''mkdir -p "$CACHES"/{local_dir.split("/")[-1]}''')
        self.assertEqual(sources["test/read"]["step"]["env"][0]["value"], local_dir)

        consumer["image"] = "example.invalid/test:1"
        self.pipeline.write_text(yaml.safe_dump(source))
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(sources["build/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["build/write"]["step"]["script"], producer["script"])
        self.assertEqual(sources["test/read"]["step"]["env"][0]["value"], consumer_path)

    def test_cache_containing_a_working_directory_in_another_subpath_is_preserved(self):
        cache = "$(workspaces.source.path)/go-cache"
        tasks = [
            {"name": "owner", "workspaces": [{"name": "source", "workspace": "shared"}],
             "taskSpec": {"steps": [
                 {"name": "fetch", "ref": {"name": "cache-fetch"},
                  "params": [{"name": "CACHE_PATH", "value": cache}]},
                 {"name": "setup", "image": "example.invalid/x:1", "script": f"mkdir -p {cache}/nested"},
             ]}},
            {"name": "consumer", "workspaces": [{"name": "repo", "workspace": "shared", "subPath": "go-cache"}],
             "taskSpec": {"stepTemplate": {"workingDir": "$(workspaces.repo.path)/nested"},
                          "steps": [{"name": "check", "image": "example.invalid/x:1", "script": "pwd"}]}},
        ]
        self.pipeline.write_text(yaml.safe_dump({"kind": "Pipeline", "spec": {"tasks": tasks}}))
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(sources["owner/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["owner/setup"]["step"]["script"], f"mkdir -p {cache}/nested")

    def test_a_cache_of_the_inherited_working_directory_is_not_moved(self):
        path = self.write_pipeline(
            "p.yaml",
            [
                {
                    "name": "fetch",
                    "ref": {"name": "cache-fetch"},
                    "params": [{"name": "CACHE_PATH", "value": "/src"}],
                },
                {"name": "test", "image": "example.invalid/x:1", "script": "make -C /src"},
            ],
        )
        pipeline = yaml.safe_load(path.read_text())
        pipeline["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"]["stepTemplate"] = {"workingDir": "/src"}
        path.write_text(yaml.safe_dump(pipeline))
        sources = MODULE["inventory"](path, self.root)
        self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["checks/test"]["step"]["script"], "make -C /src")
        self.assertEqual(MODULE["auto_scope"](sources)[1]["checks/fetch"], MODULE["CACHED"])

    def test_resolved_upstream_multiline_cache_actions_are_skipped(self):
        self.write_source([
            {"name": "fetch", "ref": {"name": "cache-fetch"}},
            {"name": "upload", "ref": {"name": "cache-upload"}},
        ])
        image = "quay.io/openshift-pipeline/pipelines-cache-rhel9:next"
        definitions = [
            {
                "kind": "StepAction", "metadata": {"name": f"cache-{verb}"},
                "spec": {"image": image, "script": "\n".join([
                    "#!/bin/sh", "set -x", "FLAGS=", "for p in \"$@\"; do",
                    "  FLAGS=\"$FLAGS --pattern $p\"", "done",
                    f"/ko-app/cache {verb} $FLAGS \\", "    --folder \"$PARAM_CACHE_PATH\"",
                    "result=$?", "exit \"$result\"",
                ])},
            }
            for verb in ("fetch", "upload")
        ]
        actions = self.root / "actions.yaml"
        actions.write_text(yaml.safe_dump_all(definitions))
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(MODULE["auto_scope"](sources), (
            [], {"checks/fetch": MODULE["CACHED"], "checks/upload": MODULE["CACHED"]},
        ))
        plan = copy.deepcopy(self.plan)
        plan["inputs"] = {path.name: MODULE["digest"](path) for path in (self.pipeline, actions)}
        plan["steps"] = [
            {**plan["steps"][0], "id": "checks/upload"},
            {"id": "checks/fetch", "decision": "skip", "reason": "remote cache action"},
        ]
        with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
            MODULE["validate_plan"](plan, self.root)

        definitions[0]["spec"]["image"] = "example.invalid/check:1"
        definitions[1]["spec"]["script"] = "make setup"
        actions.write_text(yaml.safe_dump_all(definitions))
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(MODULE["auto_scope"](sources)[0], ["checks/fetch", "checks/upload"])

    def test_overlapping_caches_keep_their_directory_hierarchy(self):
        image = "example.invalid/check:1"
        scripts = ["touch /cache/nested/item", "ls -R /cache"]
        self.write_source([
            {"name": name, "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": path}]}
            for name, path in (("parent", "/cache"), ("child", "/cache/nested"))
        ] + [
            {"name": name, "image": image, "script": script}
            for name, script in zip(("write", "read"), scripts, strict=True)
        ])
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual([sources[f"checks/{name}"]["step"]["script"] for name in ("write", "read")], scripts)
        for name in ("parent", "child"):
            self.assertEqual(sources[f"checks/{name}"]["cache"], {"path": ""})

    def test_literal_workspace_mount_is_used_for_the_working_directory_guard(self):
        cache = "$(workspaces.source.path)/cache"
        script = f"mkdir -p {cache}/nested"
        self.write_source([
            {"name": "fetch", "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": cache}]},
            {"name": "setup", "image": "example.invalid/check:1", "script": script},
            {"name": "check", "image": "example.invalid/check:1", "script": "pwd",
             "workingDir": "/workspace/source/cache/nested"},
        ])
        pipeline = yaml.safe_load(self.pipeline.read_text())
        task = pipeline["spec"]["pipelineSpec"]["tasks"][0]
        task["workspaces"] = [{"name": "source", "workspace": "shared"}]
        self.pipeline.write_text(yaml.safe_dump(pipeline))
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["checks/setup"]["step"]["script"], script)

    def test_cache_paths_in_colon_delimited_environment_values_are_rewritten(self):
        cache = "$(workspaces.source.path)/cache"
        image = "example.invalid/check:1"
        self.write_source([
            {"name": "fetch", "ref": {"name": "cache-fetch"},
             "params": [{"name": "CACHE_PATH", "value": cache}]},
            {"name": "build", "image": image, "script": f"mkdir -p {cache}"},
            {"name": "check", "image": image, "script": "make check",
             "env": [{"name": "CPATH", "value": f"{cache}:/usr/include:{cache}/other:{cache}-old"}]},
        ])
        sources = MODULE["inventory"](self.pipeline, self.root)
        local = sources["checks/fetch"]["cache"]["dir"]
        self.assertEqual(
            sources["checks/check"]["step"]["env"][0]["value"],
            f"{local}:/usr/include:{local}/other:{cache}-old",
        )

    def test_cache_actions_are_excluded_from_generation_and_rejected_in_runnable_plans(self):
        self.write_source([
            {"name": "check", "image": "example.invalid/check:1", "script": "exit 0"},
            {"name": "save", "image": "example.invalid/check:1",
             "command": ["/ko-app/cache"], "args": ["upload", "remote-cache"]},
        ])
        agent = Mock(return_value=self.good_proposal())
        args = argparse.Namespace(
            file=str(self.pipeline), steps=None, model=None, output=str(self.plan_file),
            timeout=10, repo=str(self.root), plan=str(self.pipeline),
        )
        with (
            patch.dict(GLOBALS, repo_root=lambda path: self.root, agent_plan=agent),
            patch.object(MODULE["shutil"], "which", return_value="pi"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MODULE["generate"](args), 0)
            self.assertNotIn("remote-cache", agent.call_args.args[3])
            args.steps = "save"
            with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
                MODULE["generate"](args)
            with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
                MODULE["run_command"](args)
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual(plan["scope"], ["checks/check"])
        self.assertEqual(plan["steps"][1]["decision"], "skip")
        plan["steps"][1] = {
            **plan["steps"][0], "id": "checks/save", "script": "/ko-app/cache upload remote-cache\n",
        }
        with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
            MODULE["validate_plan"](plan, self.root)

    def test_cache_tool_in_a_script_with_other_commands_keeps_the_step_in_scope(self):
        image = "example.invalid/check:1"
        self.write_source([
            {"name": "dedicated", "image": image, "script": "#!/bin/sh\nset -e\nexec /ko-app/cache fetch data\n"},
            {"name": "mixed", "image": image, "script": "/ko-app/cache fetch data\nmake test\n"},
            {"name": "chained", "image": image, "script": "/ko-app/cache fetch data && make test"},
        ])
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(MODULE["auto_scope"](sources)[0], ["checks/mixed", "checks/chained"])
        self.assertEqual(MODULE["plan_scope"](sources, "mixed"), ["checks/mixed"])

    def test_resolved_cache_names_and_unrelated_resolver_parameters_do_not_exclude_a_step(self):
        self.write_source([
            {"name": "setup", "ref": {"name": "cache-fetch"}},
            {"name": "external", "ref": {"resolver": "git", "params": [
                {"name": "revision", "value": "cache-fetch"},
                {"name": "pathInRepo", "value": "steps/setup.yaml"},
            ]}},
            {"name": "real-cache", "ref": {"resolver": "git", "params": [
                {"name": "pathInRepo", "value": "steps/cache-upload.yaml"},
            ]}},
        ])
        (self.root / "action.yaml").write_text(yaml.safe_dump({
            "kind": "StepAction", "metadata": {"name": "cache-fetch"},
            "spec": {"image": "example.invalid/x:1", "script": "make setup"},
        }))
        sources = MODULE["inventory"](self.pipeline, self.root)
        keep, left = MODULE["auto_scope"](sources)
        self.assertEqual(keep, ["checks/setup"])
        self.assertEqual(left["checks/external"], "external reference")
        self.assertEqual(left["checks/real-cache"], MODULE["CACHED"])

    def test_pipeline_is_found_or_the_choice_is_listed(self):
        choose = MODULE["choose_pipeline"]
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

    def test_results_pass_between_steps_outside_the_repository(self):
        self.multi_step_plan(
            [
                ("a", 'mkdir -p "$RESULTS/a" && printf hello > "$RESULTS/a/out"', []),
                ("b", 'echo "$RESULTS" > "$WORKSPACE/dir"; test "$(cat "$RESULTS/a/out")" = hello', ["a"]),
            ]
        )
        self.assertEqual(self.run_plan(), 0)
        results = Path((self.root / "dir").read_text().strip())
        self.assertFalse(results.is_relative_to(self.root))
        self.assertFalse(results.exists())

    def test_on_error_continue_failures_are_ignored(self):
        self.multi_step_plan(
            [("a", "exit 1", []), ("b", 'touch "$WORKSPACE/ran-b"', ["a"])]
        )
        image = "example.invalid/check:1"
        self.write_source(
            [
                {"name": "a", "image": image, "script": "exit 1", "onError": "continue"},
                {"name": "b", "image": image, "script": 'touch "$WORKSPACE/ran-b"'},
            ]
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][1]["shell"] = "sh"
        out = io.StringIO()
        self.assertEqual(self.run_plan(capture=out), 0)
        self.assertTrue((self.root / "ran-b").exists())
        self.assertIn("checks/a failed (exit 1), ignored: onError is continue", out.getvalue())
        self.assertIn("1 failed with onError: continue", out.getvalue())

    def test_task_on_error_continue_stops_the_task_but_not_the_pipeline(self):
        self.multi_step_plan(
            [
                ("a", "exit 1", []),
                ("b", 'touch "$WORKSPACE/ran-b"', []),
                ("c", 'touch "$WORKSPACE/ran-c"', []),
            ]
        )
        image = "example.invalid/check:1"
        self.pipeline.write_text(
            yaml.safe_dump(
                {
                    "kind": "PipelineRun",
                    "spec": {
                        "pipelineSpec": {
                            "tasks": [
                                {
                                    "name": "checks",
                                    "onError": "continue",
                                    "taskSpec": {
                                        "steps": [
                                            {"name": "a", "image": image, "script": "exit 1"},
                                            {"name": "b", "image": image, "script": 'touch "$WORKSPACE/ran-b"'},
                                        ]
                                    },
                                },
                                {
                                    "name": "later",
                                    "runAfter": ["checks"],
                                    "taskSpec": {
                                        "steps": [
                                            {"name": "c", "image": image, "script": 'touch "$WORKSPACE/ran-c"'}
                                        ]
                                    },
                                },
                            ]
                        }
                    },
                }
            )
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][2].update(id="later/c", depends_on=["checks/b"])
        out = io.StringIO()
        self.assertEqual(self.run_plan(capture=out), 0)
        self.assertFalse((self.root / "ran-b").exists())
        self.assertTrue((self.root / "ran-c").exists())
        self.assertIn("checks/b skipped: checks/a stopped its task", out.getvalue())
        self.assertIn("1 not run after them", out.getvalue())

    def test_on_error_continue_applies_only_once_the_script_started(self):
        image = "example.invalid/check:1"
        self.write_source([{"name": "check", "image": image, "script": "exit 127", "onError": "continue"}])
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][0]["script"] = "exit 127"
        self.assertEqual(self.run_plan(), 0)
        odd = Path(os.environ["XDG_CACHE_HOME"]) / 'a $HOME `false` "q'
        with patch.dict(os.environ, XDG_CACHE_HOME=str(odd)):
            self.assertEqual(self.run_plan(), 0)
        # The container runtime fails the same way when the image has no such shell.
        with (
            patch.object(subprocess, "Popen", return_value=Mock(returncode=127)),
            patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout="sha256:aaa")),
            patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
        ):
            self.assertEqual(self.run_plan(runtime="podman"), 1)

    def test_independent_steps_run_after_a_timeout(self):
        self.multi_step_plan([("a", "sleep 5", []), ("b", 'touch "$WORKSPACE/ran-b"', [])])
        self.assertEqual(self.run_plan(timeout=0.2), 1)
        self.assertTrue((self.root / "ran-b").exists())

    def test_caches_persist_between_runs_outside_the_repository_until_cleaned(self):
        self.multi_step_plan(
            [("a", 'mkdir -p "$CACHES/go" && echo x >> "$CACHES/go/hits" && echo "$CACHES" > "$WORKSPACE/dir"', [])]
        )
        self.assertEqual(self.run_plan(), 0)
        self.assertEqual(self.run_plan(), 0)
        caches = Path((self.root / "dir").read_text().strip())
        self.assertFalse(caches.is_relative_to(self.root))
        self.assertEqual((caches / "go" / "hits").read_text(), "x\nx\n")
        self.assertEqual(caches.stat().st_mode & 0o777, 0o777)
        self.assertEqual(caches.parent.stat().st_mode & 0o777, 0o700)
        args = argparse.Namespace(repo=str(self.root))
        with (
            patch.dict(GLOBALS, repo_root=lambda path: self.root),
            patch.object(MODULE["shutil"], "which", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MODULE["clean_caches"](args), 0)
        self.assertFalse(caches.exists())

    def test_containers_mount_caches_only_when_a_step_uses_them(self):
        self.multi_step_plan([("cached", "exit 0", []), ("uncached", "exit 0", [])])
        self.plan["steps"][0]["env"] = {"GOCACHE": "$CACHES/go/build"}
        with (
            patch.object(subprocess, "Popen", return_value=Mock(returncode=0)) as popen,
            patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout="sha256:aaa")),
            patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
        ):
            self.assertEqual(self.run_plan(runtime="podman"), 0)
        cached, uncached = [call.args[0] for call in popen.call_args_list]
        self.assertTrue(any(arg.endswith(":/caches:Z") for arg in cached))
        self.assertIn("GOCACHE=/caches/go/build", cached)
        self.assertNotIn("/caches", " ".join(uncached))

    def test_cache_directory_is_reused_only_for_the_same_runtime_and_image(self):
        prepare = MODULE["prepare_caches"]
        docker = prepare(self.root, "docker", "sha256:aaa")
        self.assertEqual(docker, prepare(self.root, "docker", "sha256:aaa"))
        self.assertEqual(len({
            docker, prepare(self.root, "host", ""), prepare(self.root, "podman", "sha256:aaa"),
            prepare(self.root, "docker", "sha256:bbb"),
        }), 4)

    def test_tool_cache_mounts_are_shared_across_images_with_the_same_uid(self):
        for tool, variables in [
            ("go", ("GOCACHE", "GOCACHE")),
            ("npm", ("npm_config_cache", "NPM_CONFIG_CACHE")),
        ]:
            with self.subTest(tool=tool):
                self.check_tool_cache_mounts(tool, variables)

    def check_tool_cache_mounts(self, tool, variables):
        self.multi_step_plan([("unit", "exit 0", []), ("lint", "exit 0", [])])
        document = yaml.safe_load(self.pipeline.read_text())
        task = document["spec"]["pipelineSpec"]["tasks"][0]
        task["workspaces"] = [{"name": "source", "workspace": "checkout"}]
        path = f"$(workspaces.source.path)/{tool}-cache"
        source_steps = task["taskSpec"]["steps"]
        source_steps.insert(0, {"name": "fetch", "ref": {"name": "cache-fetch"},
                                "params": [{"name": "CACHE_PATH", "value": path}]})
        for index, (step, var) in enumerate(zip(source_steps[1:], variables, strict=True)):
            step["image"] = f"example.invalid/{tool}:{index}"
            step["env"] = [{"name": var, "value": f"{path}/contents"}]
        self.pipeline.write_text(yaml.safe_dump(document))
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        sources = MODULE["inventory"](self.pipeline, self.root)
        slot = sources["checks/fetch"]["cache"]["dir"]
        self.assertTrue(slot.startswith(f"$CACHES/{tool}-shared/"))
        for step, var in zip(self.plan["steps"], variables, strict=True):
            original = sources[step["id"]]["step"]
            step["image"] = original["image"]
            step["env"] = {env["name"]: env["value"] for env in original["env"]}
            self.assertEqual(step["env"][var], f"{slot}/contents")
        self.plan["steps"].append({"id": "checks/fetch", "decision": "skip", "reason": "Remote cache"})

        for lint_user in ("0", "1001"):
            with self.subTest(lint_user=lint_user):
                def run(cmd, lint_user=lint_user, **kwargs):
                    output = ""
                    if "{{.Id}}" in cmd:
                        output = "sha256:" + cmd[-1].rsplit(":", 1)[-1]
                    if "{{.Config.User}}" in cmd and cmd[-1] == "sha256:1":
                        output = lint_user
                    return Mock(returncode=0, stdout=output)

                with (
                    patch.object(subprocess, "Popen", return_value=Mock(returncode=0)) as popen,
                    patch.object(subprocess, "run", side_effect=run),
                    patch.object(MODULE["shutil"], "which", return_value="/usr/bin/podman"),
                ):
                    self.assertEqual(self.run_plan(runtime="podman"), 0)
                commands = [call.args[0] for call in popen.call_args_list]
                mounts = [arg for cmd in commands for arg in cmd if arg.endswith(f":/caches/{tool}-shared:z")]
                if lint_user == "0":
                    self.assertEqual(len(mounts), 2)
                    self.assertEqual(mounts[0], mounts[1])
                    self.assertTrue(all(
                        f"{var}={slot.replace('$CACHES', '/caches')}/contents" in cmd
                        for cmd, var in zip(commands, variables, strict=True)
                    ))
                else:
                    self.assertEqual(mounts, [])
                    self.assertTrue(all(
                        f"{var}=/workspace/{tool}-cache/contents" in cmd
                        for cmd, var in zip(commands, variables, strict=True)
                    ))

    def test_npm_installed_dependencies_and_mixed_use_cache_paths_stay_in_the_workspace(self):
        for directory, other_var in [("node_modules", "npm_config_cache"), ("npm-cache", "CACHE_DIR")]:
            with self.subTest(directory=directory):
                path = f"$(workspaces.source.path)/{directory}"
                self.write_source([
                    {"name": "fetch", "ref": {"name": "cache-fetch"},
                     "params": [{"name": "CACHE_PATH", "value": path}]},
                    {"name": "seed", "image": "node:22", "script": "npm cache add is-number",
                     "env": [{"name": "npm_config_cache", "value": path}]},
                    {"name": "install", "image": "node:24", "script": "npm ci",
                     "env": [{"name": other_var, "value": path}]},
                ])
                document = yaml.safe_load(self.pipeline.read_text())
                document["spec"]["pipelineSpec"]["tasks"][0]["workspaces"] = [
                    {"name": "source", "workspace": "checkout"},
                ]
                self.pipeline.write_text(yaml.safe_dump(document))
                sources = MODULE["inventory"](self.pipeline, self.root)
                self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
                for name in ("seed", "install"):
                    self.assertEqual(sources[f"checks/{name}"]["step"]["env"][0]["value"], path)

    def test_results_directory_is_writable_by_any_container_user(self):
        self.cache.mkdir(parents=True)
        self.cache.chmod(0o755)
        self.multi_step_plan(
            [("a", 'stat -c %a "$RESULTS" > "$WORKSPACE/mode"; stat -c %a "$RESULTS/.." > "$WORKSPACE/parent"', [])]
        )
        self.assertEqual(self.run_plan(), 0)
        self.assertEqual((self.root / "mode").read_text().strip(), "777")
        self.assertEqual((self.root / "parent").read_text().strip(), "700")

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
        self.plan["steps"][0]["env"] = {"GOCACHE": "$CACHES/go"}
        docker = chowns(self.run_container("docker"))
        self.assertEqual(len(docker), 1)
        self.assertIn("find /workspace /results ! -user 1000 -exec chown 1000:1000 {} +", docker[0].args[0])
        self.assertNotIn("/caches", " ".join(docker[0].args[0]))
        self.assertEqual(docker[0].args[0][3:5], ["--user", "0"])
        self.assertIn("sha256:aaa", docker[0].args[0])
        self.assertEqual(chowns(self.run_container("podman")), [])
        self.assertEqual(chowns(self.run_container("docker", uid=0)), [])

    def test_cache_clean_uses_docker_when_host_cannot_delete_cache_data(self):
        path = MODULE["caches_dir"](self.root)
        path.mkdir(parents=True)
        args = argparse.Namespace(repo=str(self.root))
        docker = Mock()
        with (
            patch.dict(GLOBALS, repo_root=lambda path: self.root),
            patch.object(shutil, "rmtree", side_effect=[PermissionError(), None]) as rmtree,
            patch.object(shutil, "which", side_effect=lambda name: name if name == "docker" else None),
            patch.object(subprocess, "run", docker),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(MODULE["clean_caches"](args), 0)
        self.assertEqual(rmtree.call_count, 2)
        command = docker.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "run", "--rm", "--user", "0"])
        self.assertIn(f"{path}:/cache:Z", command)
        self.assertEqual(command[-2:], ["-rf", "/cache/data"])

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
        self.assertTrue((self.cache / "images.json").is_file())

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

    def test_agent_prompt_is_sent_on_stdin(self):
        prompt = "x" * 300_000
        code = (
            "import json, sys; size = len(sys.stdin.read()); print(json.dumps({'type': 'message_end', "
            "'message': {'role': 'assistant', 'stopReason': 'stop', 'content': "
            "[{'type': 'text', 'text': json.dumps({'size': size})}]}}))"
        )
        with contextlib.redirect_stderr(io.StringIO()):
            result = MODULE["agent_plan"]([sys.executable, "-c", code], self.root, 5, prompt)
        self.assertEqual(result, {"size": len(prompt)})

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
            ("just text", "Document 2: expected a Kubernetes resource"),
            ({**source, "metadata": {}}, "need a name"),
        ]:
            self.pipeline.write_text(yaml.safe_dump_all([source, extra]))
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, error):
                MODULE["inventory"](self.pipeline)

    def test_unnamed_steps_and_unrelated_resources_are_handled(self):
        source = yaml.safe_load(self.pipeline.read_text())
        del source["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"]["steps"][0]["name"]
        self.pipeline.write_text(
            yaml.safe_dump_all([{"kind": "PersistentVolumeClaim", "metadata": {"name": "x"}}, source])
        )
        self.assertEqual(list(MODULE["inventory"](self.pipeline)), ["checks/unnamed-0"])
        source["spec"]["pipelineSpec"]["tasks"] = source["spec"]["pipelineSpec"]["tasks"][0]
        self.pipeline.write_text(yaml.safe_dump(source))
        with self.assertRaisesRegex(ValueError, "tasks must be a list"):
            MODULE["inventory"](self.pipeline)
        source["metadata"] = "bad"
        self.pipeline.write_text(yaml.safe_dump(source))
        with self.assertRaisesRegex(ValueError, "metadata must be a mapping"):
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
        bindings = dict.fromkeys(MODULE["RUNNER_VARS"], "")
        bindings.update(WORKSPACE="/workspace", BASE_SHA="base")
        self.assertEqual(
            MODULE["expand"]("$WORKSPACE/${BASE_SHA}/$WORKSPACE_CACHE", bindings),
            "/workspace/base/$WORKSPACE_CACHE",
        )

    def write_yaml(self, name, *documents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump_all(documents))
        return path

    def task(self, name, script="exit 0"):
        return {
            "kind": "Task",
            "metadata": {"name": name},
            "spec": {
                "params": [{"name": "go-version", "default": "1.22"}],
                "steps": [{"name": "check", "image": "example.invalid/check:1", "script": script}],
            },
        }

    def pipeline_doc(self, name="ci", task_ref=None):
        return {
            "kind": "Pipeline",
            "metadata": {"name": name},
            "spec": {"tasks": [{"name": "checks", "taskRef": task_ref or {"name": "lint"}}]},
        }

    def test_local_pipeline_and_task_references_are_inlined(self):
        self.write_yaml("tekton/task.yaml", self.task("lint"))
        self.write_yaml("tekton/pipeline.yaml", self.pipeline_doc())
        self.pipeline.write_text(
            yaml.safe_dump({"kind": "PipelineRun", "spec": {"pipelineRef": {"name": "ci"}}})
        )
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(list(sources), ["checks/check"])
        source = sources["checks/check"]
        self.assertEqual(source["step"]["image"], "example.invalid/check:1")
        self.assertEqual(source["defined_in"], ["tekton/pipeline.yaml", "tekton/task.yaml"])
        self.assertEqual(source["task_defaults"]["params"][0]["default"], "1.22")

    def test_step_image_parameters_are_filled_from_literal_values(self):
        task = self.task("lint")
        task["spec"]["steps"][0]["image"] = "example.invalid/go:$(params.go-version)"
        pipeline = self.pipeline_doc()
        pipeline["spec"]["params"] = [{"name": "go", "default": "1.23"}]
        run = {"kind": "PipelineRun", "spec": {"pipelineRef": {"name": "ci"}}}
        self.write_yaml("task.yaml", task)
        for task_params, run_params, image in [
            (None, None, "example.invalid/go:1.22"),
            ([{"name": "go-version", "value": "$(params.go)"}], None, "example.invalid/go:1.23"),
            (
                [{"name": "go-version", "value": "$(params.go)"}],
                [{"name": "go", "value": "1.24"}],
                "example.invalid/go:1.24",
            ),
            (
                [{"name": "go-version", "value": "$(tasks.a.results.v)"}],
                None,
                "example.invalid/go:$(params.go-version)",
            ),
            ([{"name": "go-version", "value": ["1.21"]}], None, "example.invalid/go:$(params.go-version)"),
        ]:
            pipeline["spec"]["tasks"][0]["params"] = task_params
            run["spec"]["params"] = run_params
            self.pipeline.write_text(yaml.safe_dump_all([run, pipeline]))
            with self.subTest(image=image):
                step = MODULE["inventory"](self.pipeline, self.root)["checks/check"]["step"]
                self.assertEqual(step["image"], image)
        self.write_source([{"name": "check", "image": "x:$(params.v)", "script": "exit 0"}])
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][0]["image"] = "x:$(params.v)"
        with self.assertRaisesRegex(ValueError, "unresolved substitution"):
            self.validate()

    def test_bracket_image_parameters_and_pac_images_are_not_shell_bound(self):
        self.write_source(
            [{"name": "check", "image": "x:$(params['v'])", "script": "echo {{revision}}"}]
        )
        source = yaml.safe_load(self.pipeline.read_text())
        task = source["spec"]["pipelineSpec"]["tasks"][0]
        task["params"] = [{"name": "v", "value": "{{revision}}"}]
        source["metadata"] = {"annotations": {"pipelinesascode.tekton.dev/on-event": "[pull_request]"}}
        self.pipeline.write_text(yaml.safe_dump(source))
        step = MODULE["inventory"](self.pipeline)["checks/check"]["step"]
        self.assertEqual(step["image"], "x:{{revision}}")
        self.assertEqual(step["script"], "echo $HEAD_SHA")
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][0].update(image=step["image"], script=step["script"], edits=[])
        with self.assertRaisesRegex(ValueError, "image x:\\{\\{revision\\}\\} has an unresolved"):
            self.validate()

    def test_plain_pipeline_file_with_its_tasks_is_an_entry_point(self):
        self.pipeline.write_text(
            yaml.safe_dump_all([self.pipeline_doc(), self.task("lint")])
        )
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(sources["checks/check"]["defined_in"], [])
        self.validate()

    def test_missing_ambiguous_and_remote_task_references_stay_references(self):
        self.write_yaml("a.yaml", self.task("twice"))
        self.write_yaml("b.yaml", self.task("twice"))
        for ref, note in [
            ({"name": "absent"}, "no Task named absent"),
            ({"name": "twice"}, "defined more than once: a.yaml, b.yaml"),
            ({"name": "same"}, "defined more than once in pipeline.yaml"),
            ({"resolver": "git", "params": []}, "git resolver"),
            ({"name": "lint", "kind": "ClusterTask"}, "ClusterTask reference"),
        ]:
            self.pipeline.write_text(
                yaml.safe_dump_all([self.pipeline_doc(task_ref=ref), self.task("same"), self.task("same")])
            )
            with self.subTest(ref=ref):
                source = MODULE["inventory"](self.pipeline, self.root)["checks/task"]
                self.assertIn(note, source["reference_note"])
                self.assertNotIn("image", source["step"])

    def test_local_step_actions_are_inlined(self):
        self.write_yaml(
            "stepactions/build.yaml",
            {
                "kind": "StepAction",
                "metadata": {"name": "build"},
                "spec": {
                    "params": [{"name": "version", "default": "1.21"}],
                    "image": "example.invalid/go:$(params.version)",
                    "script": "go build $(params.version)",
                },
            },
        )
        lint = self.task("lint")
        lint["spec"]["steps"] = [
            {"name": "build", "ref": {"name": "build"}, "params": [{"name": "version", "value": "$(params.go-version)"}]},
            {"name": "clone", "ref": {"resolver": "hub", "params": []}},
        ]
        self.pipeline.write_text(yaml.safe_dump_all([self.pipeline_doc(), lint]))
        sources = MODULE["inventory"](self.pipeline, self.root)
        build = sources["checks/build"]
        self.assertEqual(build["step"]["image"], "example.invalid/go:1.22")
        self.assertEqual(build["step"]["script"], "go build $(params.version)")
        self.assertNotIn("ref", build["step"])
        self.assertEqual(build["step_action"]["name"], "build")
        self.assertEqual(build["defined_in"], ["stepactions/build.yaml"])
        self.assertIn("hub resolver", sources["checks/clone"]["reference_note"])

    def test_missing_or_remote_pipeline_reference_is_an_error(self):
        pac = {"annotations": {"pipelinesascode.tekton.dev/pipeline": "https://example.invalid/p.yaml"}}
        for ref, metadata, error in [
            ({"name": "absent"}, {}, "no Pipeline named absent in the repository$"),
            ({"name": "absent"}, pac, "no Pipeline named absent .*install tkn-pac"),
            ({"resolver": "hub", "params": []}, {}, "remote pipelineRef"),
        ]:
            self.pipeline.write_text(
                yaml.safe_dump({"kind": "PipelineRun", "metadata": metadata, "spec": {"pipelineRef": ref}})
            )
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, error):
                MODULE["inventory"](self.pipeline, self.root)

    def test_referenced_definitions_are_plan_inputs_and_invalidate_it(self):
        task = self.write_yaml("tasks/lint.yaml", self.task("lint"))
        self.pipeline.write_text(yaml.safe_dump(self.pipeline_doc()))
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        with self.assertRaisesRegex(ValueError, "now reads tasks/lint.yaml"):
            self.validate()
        sources = MODULE["inventory"](self.pipeline, self.root)
        plan = MODULE["assemble_plan"](
            self.good_proposal(), self.root, "pipeline.yaml", sources, ["checks/check"]
        )
        self.assertIn("tasks/lint.yaml", plan["inputs"])
        self.plan = plan
        self.validate()
        task.write_text(yaml.safe_dump(self.task("lint", "exit 1")))
        with self.assertRaisesRegex(ValueError, "Stale plan: tasks/lint.yaml"):
            self.validate()

    def test_pac_placeholders_are_only_checked_and_bound_for_pac_files(self):
        self.set_script('echo "{{ .Version }}"')
        self.validate()
        source = yaml.safe_load(self.pipeline.read_text())
        source["metadata"] = {"annotations": {"pipelinesascode.tekton.dev/on-event": "[pull_request]"}}
        source["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"]["steps"][0]["script"] = (
            "git diff {{ revision }} {{target_branch}} {{ hash }}"
        )
        self.pipeline.write_text(yaml.safe_dump(source))
        step = MODULE["inventory"](self.pipeline)["checks/check"]["step"]
        self.assertEqual(step["script"], "git diff $HEAD_SHA $BASE_REF {{ hash }}")
        self.plan["inputs"]["pipeline.yaml"] = MODULE["digest"](self.pipeline)
        self.plan["steps"][0].update(script=step["script"], edits=[])
        with self.assertRaisesRegex(ValueError, r"unresolved Tekton or PaC substitution: \{\{ hash \}\}"):
            self.validate()

    def test_runner_supplies_head_and_base_branch_variables(self):
        self.set_script('test "$HEAD_SHA:$HEAD_REF:$BASE_REF" = "sha:topic:main"')
        answers = {
            ("rev-parse", "HEAD"): "sha",
            ("rev-parse", "--abbrev-ref", "HEAD"): "topic",
            ("remote",): "origin\nupstream",
        }
        with patch.dict(GLOBALS, git=lambda root, *args: answers[args]):
            self.assertEqual(self.run_plan(base="origin/main"), 0)
        self.plan["steps"][0]["required_env"] = ["HEAD_SHA"]
        with self.assertRaisesRegex(ValueError, "supplied by the runner"):
            self.validate()

    def test_repository_config_adds_cluster_only_patterns(self):
        sources = {
            "checks/docs": {
                "step": {"image": "x", "script": "npx wrangler deploy"},
                "task_context": {},
                "finally": False,
            }
        }
        patterns = MODULE["cluster_patterns"](self.root)
        self.assertEqual(MODULE["auto_scope"](sources, patterns)[0], ["checks/docs"])
        (self.root / ".tekton-innerloop.yaml").write_text("cluster_only: ['\\bwrangler\\s+deploy']\n")
        patterns = MODULE["cluster_patterns"](self.root)
        self.assertEqual(
            MODULE["auto_scope"](sources, patterns)[1], {"checks/docs": "publishes or deploys"}
        )
        for text in ("cluster_only: ['(']\n", "cluster_only: x\n", "other: []\n"):
            (self.root / ".tekton-innerloop.yaml").write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, ".tekton-innerloop.yaml"):
                MODULE["cluster_patterns"](self.root)

    PINNED = "sha256:" + "a" * 64
    TAGGED = "sha256:" + "b" * 64

    def fake_bundle(self, *objects, error=None):
        """A skopeo on PATH that serves one bundle image holding objects, or fails with error."""
        registry = self.root / "registry"
        blobs = registry / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        layers = []
        for index, obj in enumerate(objects, 1):
            data = json.dumps(obj).encode()
            blob = blobs / f"{index:064x}"
            with tarfile.open(blob, "w:gz") as tar:
                info = tarfile.TarInfo(obj["metadata"]["name"])
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            annotations = {"dev.tekton.image.kind": obj["kind"].lower(), "dev.tekton.image.name": obj["metadata"]["name"]}
            layers.append({"digest": f"sha256:{blob.name}", "annotations": annotations})
        (blobs / "manifest.json").write_text(json.dumps({"layers": layers}))
        failure = f'echo "{error}" >&2; exit 1' if error else ""
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        tool = bindir / "skopeo"
        tool.write_text(
            f'#!/bin/sh\necho "$@" >> {registry}/calls\n{failure}\n'
            f'case "$1" in\ninspect) echo {self.TAGGED} ;;\ncopy) cp {blobs}/* "${{4#dir:}}/" ;;\nesac\n'
        )
        tool.chmod(0o755)
        return patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"})

    def skopeo_calls(self):
        calls = self.root / "registry" / "calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def bundle_ref(self, image, name, kind="task"):
        params = {"bundle": image, "name": name, "kind": kind}
        return {"resolver": "bundles", "params": [{"name": k, "value": v} for k, v in params.items()]}

    def test_bundle_references_are_fetched_or_left_with_a_reason(self):
        repo = "example.invalid/catalog/task-lint"
        action = {"kind": "StepAction", "metadata": {"name": "lint"}, "spec": {"image": "example.invalid/act:1", "script": "act"}}
        task = self.task("lint")
        task["spec"]["steps"].append({"name": "act", "ref": self.bundle_ref(f"{repo}:0.1@{self.PINNED}", "lint", "stepaction")})
        tests = [
            {"name": "tag and digest", "image": f"{repo}:0.1@{self.PINNED}", "digest": self.PINNED, "inspect": False},
            {"name": "tag only", "image": f"{repo}:0.1", "digest": self.TAGGED, "inspect": True},
            {"name": "registry error", "image": f"{repo}@{self.PINNED}", "error": "FATA unauthorized",
             "note": "bundle reference not fetched: skopeo failed: FATA unauthorized"},
            {"name": "no skopeo", "image": f"{repo}@{self.PINNED}", "which": None,
             "note": "bundle reference not fetched: install skopeo to fetch Tekton bundles"},
        ]
        for tt in tests:
            with self.subTest(tt["name"]):
                shutil.rmtree(self.root / "registry", ignore_errors=True)
                shutil.rmtree(Path(os.environ["XDG_CACHE_HOME"]) / "tkn-innerloop" / "bundles", ignore_errors=True)
                GLOBALS["_bundle_digests"].clear()
                GLOBALS["_bundle_failures"].clear()
                self.pipeline.write_text(yaml.safe_dump(
                    {"kind": "PipelineRun", "spec": {"pipelineSpec": {"tasks": [
                        {"name": "checks", "taskRef": self.bundle_ref(tt["image"], "lint")}]}}}
                ))
                which = patch.object(GLOBALS["shutil"], "which", return_value=None) if "which" in tt else contextlib.nullcontext()
                with self.fake_bundle(action, task, error=tt.get("error")), which:
                    sources = MODULE["inventory"](self.pipeline, self.root)
                if "note" in tt:
                    self.assertEqual(list(sources), ["checks/task"])
                    self.assertIn(tt["note"], sources["checks/task"]["reference_note"])
                    continue
                self.assertEqual(list(sources), ["checks/check", "checks/act"])
                task_input = f"bundle:{repo}@{tt['digest']}#task/lint"
                self.assertEqual(sources["checks/check"]["defined_in"], [task_input])
                self.assertEqual(sources["checks/act"]["step"]["script"], "act")
                self.assertEqual(sources["checks/act"]["step_action"]["name"], "lint")
                self.assertIn(f"bundle:{repo}@{self.PINNED}#stepaction/lint", sources["checks/act"]["defined_in"])
                calls = self.skopeo_calls()
                self.assertIn(f"copy -q docker://{repo}@{tt['digest']} dir:", calls[-2 if tt["inspect"] else 0])
                self.assertEqual(any(call.startswith("inspect") for call in calls), tt["inspect"])

    def test_bundle_pipeline_reference_is_inlined_or_an_error(self):
        image = f"example.invalid/catalog/pipeline-ci@{self.PINNED}"
        pipeline = self.pipeline_doc(task_ref=self.bundle_ref(image, "lint"))
        self.pipeline.write_text(yaml.safe_dump(
            {"kind": "PipelineRun", "spec": {"pipelineRef": self.bundle_ref(image, "ci", "pipeline")}}
        ))
        with self.fake_bundle(pipeline, self.task("lint")):
            sources = MODULE["inventory"](self.pipeline, self.root)
        self.assertEqual(list(sources), ["checks/check"])
        self.assertIn(f"bundle:example.invalid/catalog/pipeline-ci@{self.PINNED}#pipeline/ci", sources["checks/check"]["defined_in"])
        shutil.rmtree(Path(os.environ["XDG_CACHE_HOME"]) / "tkn-innerloop" / "bundles")
        GLOBALS["_bundle_failures"].clear()
        with self.fake_bundle(error="manifest unknown"), self.assertRaisesRegex(
            ValueError, "bundle pipelineRef: skopeo failed: manifest unknown"
        ):
            MODULE["inventory"](self.pipeline, self.root)

    def test_bundle_objects_are_cached_and_invalidate_the_plan(self):
        image = f"example.invalid/catalog/task-lint:0.1@{self.PINNED}"
        self.pipeline.write_text(yaml.safe_dump(self.pipeline_doc(task_ref=self.bundle_ref(image, "lint"))))
        with self.fake_bundle(self.task("lint")):
            sources = MODULE["inventory"](self.pipeline, self.root)
            GLOBALS["_bundle_failures"].clear()
            self.assertEqual(MODULE["inventory"](self.pipeline, self.root), sources)
        self.assertEqual(len(self.skopeo_calls()), 1)
        plan = MODULE["assemble_plan"](self.good_proposal(), self.root, "pipeline.yaml", sources, ["checks/check"])
        name = f"bundle:example.invalid/catalog/task-lint@{self.PINNED}#task/lint"
        self.assertIn(name, plan["inputs"])
        self.plan = plan
        self.validate()
        cached = GLOBALS["bundle_path"](name)
        cached.write_text(cached.read_text().replace("exit 0", "exit 1"))
        with self.assertRaisesRegex(ValueError, f"Stale plan: {name} changed"):
            self.validate()

    def test_konflux_checkout_steps_are_provided_and_source_is_the_worktree(self):
        self.write_source([
            {"name": "use-trusted-artifact", "image": "quay.io/konflux-ci/build-trusted-artifacts:latest",
             "args": ["use", "$(params.SOURCE_ARTIFACT)=/var/workdir/source"]},
            {"name": "check", "image": "example.invalid/check:1", "workingDir": "/var/workdir/source",
             "script": "ls /var/workdir/source/x /var/workdir/sourced"},
        ])
        sources = MODULE["inventory"](self.pipeline, self.root)
        step = sources["checks/check"]["step"]
        self.assertEqual(step["workingDir"], "$WORKSPACE")
        self.assertEqual(step["script"], "ls $WORKSPACE/x /var/workdir/sourced")
        keep, left = MODULE["auto_scope"](sources)
        self.assertEqual(keep, ["checks/check"])
        self.assertEqual(left, {"checks/use-trusted-artifact": "provided by the local checkout"})

    def fake_tkn_pac(self, body, version="0.40.0"):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        tool = bindir / "tkn-pac"
        tool.write_text(f'#!/bin/sh\n[ "$1" = version ] && {{ echo "{version}"; exit 0; }}\n{body}\n')
        tool.chmod(0o755)
        return patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"})

    def test_tkn_pac_resolution_is_cached_and_falls_back_on_failure(self):
        source = yaml.safe_load(self.pipeline.read_text())
        source["metadata"] = {"annotations": {"pipelinesascode.tekton.dev/task": "git-clone"}}
        self.pipeline.write_text(yaml.safe_dump(source))
        resolved = yaml.safe_dump({**source, "metadata": {"name": "resolved", **source["metadata"]}})
        calls = self.root / "calls"
        out = io.StringIO()
        resolver = f"echo \"$@\" >> {calls}\ncat <<'EOF'\n{resolved}EOF"
        for version, runs in [("0.40.0", 1), ("0.40.0", 1), ("0.41.0", 2)]:
            GLOBALS["_tkn_pac_versions"].clear()
            with self.fake_tkn_pac(resolver, version), contextlib.redirect_stdout(out):
                path, extra = MODULE["plan_source"](self.root, self.pipeline)
            self.assertEqual(len(calls.read_text().splitlines()), runs, version)
        self.assertEqual(path.parent, self.cache)
        self.assertEqual(extra, [])
        self.assertIn("-p revision=$HEAD_SHA", calls.read_text())
        sources = MODULE["inventory"](path, self.root)
        plan = MODULE["assemble_plan"](
            self.good_proposal(), self.root, "pipeline.yaml", sources, ["checks/check"], path.name,
        )
        self.assertIn(plan["resolved"], plan["inputs"])
        self.plan = plan
        self.validate()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "resolve output .* is gone"):
            self.validate()
        with self.fake_tkn_pac("echo boom >&2; exit 1"), contextlib.redirect_stdout(out):
            self.assertEqual(
                MODULE["plan_source"](self.root, self.pipeline, refresh=True)[0], self.pipeline
            )
        self.assertIn("tkn pac resolve failed (boom)", out.getvalue())


class CorpusTests(unittest.TestCase):
    def test_shipped_demo_plan_validates_against_the_pinned_source(self):
        plan = CLI.parent.parent / "demo" / "linters.plan.json"
        MODULE["load_plan"](plan, CORPUS / "pac-linter" / "repo")

    """Real pipelines from other repositories; see tests/corpus/SOURCES.md."""

    def check_step(self, source, expected):
        step = source["step"]
        for field, value in expected.items():
            if field == "reference":
                self.assertEqual(not step.get("image"), value)
            elif field == "note":
                self.assertIn(value, source.get("reference_note", ""))
            elif field == "image":
                self.assertEqual(step.get("image"), value)
            elif field == "step_action":
                self.assertEqual(source.get("step_action", {}).get("name"), value)
            elif field == "matrix":
                self.assertEqual(bool(source["task_context"].get("matrix")), value)
            elif field == "script_contains":
                for text in value:
                    self.assertIn(text, step.get("script", ""))
            elif field == "env_contains":
                for text in value:
                    self.assertIn(text, json.dumps(step.get("env", [])))
            else:
                self.assertEqual(source[field], value, field)

    def test_corpus_matches_expected(self):
        cases = sorted(path.parent for path in CORPUS.glob("*/expected.yaml"))
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            expected = yaml.safe_load((case / "expected.yaml").read_text())
            bundles = case / "bundles"
            tags = bundles / "tags.yaml"
            with (
                self.subTest(case=case.name),
                tempfile.TemporaryDirectory() as tmp,
                tempfile.TemporaryDirectory() as cache,
                patch.dict(GLOBALS, skopeo=offline_skopeo),
                patch.dict(GLOBALS["_bundle_failures"], clear=True),
                patch.dict(
                    GLOBALS["_bundle_digests"],
                    yaml.safe_load(tags.read_text()) if tags.exists() else {},
                    clear=True,
                ),
                patch.dict(
                    os.environ,
                    {"XDG_CACHE_HOME": cache, GLOBALS["BUNDLE_DIR_ENV"]: str(bundles)},
                ),
            ):
                root = Path(tmp)
                shutil.copytree(case / "repo", root, dirs_exist_ok=True)
                entry = root / expected["source"]
                if expected.get("error"):
                    with self.assertRaisesRegex(ValueError, expected["error"]):
                        MODULE["inventory"](entry, root)
                    continue
                sources = MODULE["inventory"](entry, root)
                documents = list(yaml.safe_load_all(entry.read_text()))
                self.assertEqual(MODULE["pac_profile"](documents), expected["pac"])
                self.assertEqual(list(sources), list(expected["steps"]))
                for key, step in expected["steps"].items():
                    with self.subTest(step=key):
                        self.check_step(sources[key], step)
                keep, left = MODULE["auto_scope"](sources, MODULE["cluster_patterns"](root))
                self.assertEqual(keep, expected["scope"]["local"])
                self.assertEqual(left, expected["scope"]["cluster"])

if __name__ == "__main__":
    unittest.main()

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

    def run_plan(self, **kwargs):
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
            contextlib.redirect_stdout(io.StringIO()),
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
        self.assertIn("? REVIEW", output.getvalue())
        self.assertIn("checks/deploy", output.getvalue())
        self.assertNotIn("RUN LOCAL", output.getvalue())

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
        with self.assertRaisesRegex(ValueError, "Unresolved steps"):
            self.run_plan()
        self.plan["steps"][0]["decision"] = "skip"
        with self.assertRaisesRegex(ValueError, "No runnable steps"):
            self.run_plan()

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

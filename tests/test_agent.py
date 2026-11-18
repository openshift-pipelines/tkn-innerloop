"""Asking the agent for a plan: prompts, retries, streaming and failures."""

import argparse
import contextlib
import copy
import io
import json
import os
import shutil
import sys
from unittest.mock import Mock, patch

import yaml

from tkn_innerloop.agent import agent_plan, build_plan, pi_command
from tkn_innerloop.cli import generate
from tkn_innerloop.paths import digest
from tkn_innerloop.plan import PlanRejected

from .helpers import InnerloopCase, patch_names


class AgentTests(InnerloopCase):
    def build(self, agent):
        with (
            patch_names(agent_plan=agent),
            patch.dict(os.environ, {"TKN_INNERLOOP_AI_MODEL": ""}),
            patch.object(shutil, "which", return_value="pi"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return build_plan(self.pipeline, self.root, ["checks/check"], None, 10)

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
            patch_names(repo_root=lambda path: self.root, agent_plan=agent),
            patch.dict(os.environ, {"TKN_INNERLOOP_AI_MODEL": ""}),
            patch.object(shutil, "which", return_value="pi"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(generate(args), 0)
        cmd = agent.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--tools") + 1], "read,grep,find,ls")
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        self.assertIn("--offline", cmd)
        self.assertNotIn("OMITTED_SCRIPT", agent.call_args.args[3])
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual(
            plan["inputs"]["pipeline.yaml"], digest(self.pipeline)
        )
        self.assertEqual(plan["steps"][0]["script"], "exit 0")
        self.assertEqual(plan["steps"][1]["decision"], "skip")

    def test_model_thinking_suffix_replaces_thinking_off(self):
        tests = [
            ("haiku", ["--thinking", "off"]),
            ("github-copilot/claude-sonnet-5.5:minimal", []),
        ]
        for model, thinking in tests:
            with self.subTest(model=model):
                cmd = pi_command(model)
                index = cmd.index("--model")
                self.assertEqual(cmd[index - len(thinking):index], thinking)
                self.assertEqual(cmd.count("--thinking"), len(thinking) // 2)
                self.assertEqual(cmd[index + 1], model)

    def test_malformed_answer_is_retried_with_the_error(self):
        rejected = PlanRejected("Agent returned invalid plan JSON")
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
        rejected = PlanRejected("Agent returned invalid plan JSON")
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
            result = agent_plan([sys.executable, "-c", code], self.root, 5)
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
            result = agent_plan([sys.executable, "-c", code], self.root, 5, prompt)
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
            agent_plan([sys.executable, "-c", code], self.root, 0.5)
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
                agent_plan([sys.executable, "-c", code], self.root, 5)

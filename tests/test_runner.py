"""Running plan steps on the host or in containers."""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tkn_innerloop.paths import digest
from tkn_innerloop.plan import RUNNER_VARS
from tkn_innerloop.repo import default_base
from tkn_innerloop.runner import expand, report_blocked, run_plan
from tkn_innerloop.runtime import resolve_runtime

from .helpers import InnerloopCase, patch_names


class RunnerTests(InnerloopCase):
    def run_container(self, runtime, uid=1000, **extra):
        process = Mock(returncode=0)
        run = Mock(return_value=Mock(returncode=0, stdout="sha256:aaa"))
        with (
            patch.object(subprocess, "Popen", return_value=process),
            patch.object(subprocess, "run", run),
            patch.object(shutil, "which", return_value=f"/usr/bin/{runtime}"),
            patch.object(os, "getuid", return_value=uid),
            patch.object(os, "getgid", return_value=uid),
        ):
            self.assertEqual(self.run_plan(runtime=runtime, **extra), 0)
        return run

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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
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
        with patch_names(git=git):
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
            code = report_blocked(plan, [plan["steps"][0]], args)
        text = err.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("? UNRESOLVED go/cache-fetch", text)
        self.assertIn(
            "run /tmp/foo --steps go/unittest,go/lint --env GH_TOKEN --runtime podman", text
        )
        self.assertIn("generate .tekton/go.yaml -o /tmp/foo", text)

    def test_missing_runtime_names_an_installed_alternative(self):
        installed = {"docker": "/usr/bin/docker"}
        with (
            patch("shutil.which", side_effect=installed.get),
            self.assertRaisesRegex(ValueError, "podman.*docker is installed.*--runtime docker"),
        ):
            self.run_plan(runtime="podman")
        with patch("shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "--runtime host"):
            self.run_plan(runtime="docker")

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
                patch.object(shutil, "which", return_value="/usr/bin/podman"),
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

    def test_runtime_auto_prefers_podman_then_docker(self):
        resolve = resolve_runtime
        both = {"podman": "/usr/bin/podman", "docker": "/usr/bin/docker"}
        with patch("shutil.which", side_effect=both.get):
            self.assertEqual(resolve("auto"), "podman")
            self.assertEqual(resolve("docker"), "docker")
        with patch("shutil.which", side_effect={"docker": "/usr/bin/docker"}.get):
            self.assertEqual(resolve("auto"), "docker")
        with patch("shutil.which", return_value=None), self.assertRaisesRegex(ValueError, "No container runtime"):
            resolve("auto")

    def test_default_base_prefers_origin_head_then_main_then_master(self):
        def sh(*cmd):
            subprocess.run(cmd, cwd=self.root, check=True, capture_output=True)

        sh("git", "init", "-q", "-b", "trunk")
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "x")
        with self.assertRaisesRegex(ValueError, "--base"):
            default_base(self.root)
        sh("git", "branch", "master")
        self.assertEqual(default_base(self.root), "master")
        sh("git", "branch", "main")
        self.assertEqual(default_base(self.root), "main")

    def test_container_runtime_receives_stdin_context_and_entrypoint(self):
        process = Mock(returncode=0)
        self.plan["steps"][0]["env"] = {"CACHE": "$WORKSPACE/cache"}
        with (
            patch.object(subprocess, "Popen", return_value=process) as popen,
            patch.object(subprocess, "run", return_value=Mock(returncode=0)),
            patch.object(shutil, "which", return_value="/usr/bin/podman"),
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        self.plan["steps"][0]["script"] = "exit 127"
        self.assertEqual(self.run_plan(), 0)
        odd = Path(os.environ["XDG_CACHE_HOME"]) / 'a $HOME `false` "q'
        with patch.dict(os.environ, XDG_CACHE_HOME=str(odd)):
            self.assertEqual(self.run_plan(), 0)
        # The container runtime fails the same way when the image has no such shell.
        with (
            patch.object(subprocess, "Popen", return_value=Mock(returncode=127)),
            patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout="sha256:aaa")),
            patch.object(shutil, "which", return_value="/usr/bin/podman"),
        ):
            self.assertEqual(self.run_plan(runtime="podman"), 1)

    def test_independent_steps_run_after_a_timeout(self):
        self.multi_step_plan([("a", "sleep 5", []), ("b", 'touch "$WORKSPACE/ran-b"', [])])
        self.assertEqual(self.run_plan(timeout=0.2), 1)
        self.assertTrue((self.root / "ran-b").exists())

    def test_results_directory_is_writable_by_any_container_user(self):
        self.cache.mkdir(parents=True)
        self.cache.chmod(0o755)
        self.multi_step_plan(
            [("a", 'stat -c %a "$RESULTS" > "$WORKSPACE/mode"; stat -c %a "$RESULTS/.." > "$WORKSPACE/parent"', [])]
        )
        self.assertEqual(self.run_plan(), 0)
        self.assertEqual((self.root / "mode").read_text().strip(), "777")
        self.assertEqual((self.root / "parent").read_text().strip(), "700")

    def test_docker_hands_root_owned_files_back_but_podman_and_root_do_not(self):
        def chowns(run):
            return [c for c in run.call_args_list if "chown" in " ".join(c.args[0])]

        self.plan["steps"][0]["env"] = {"GOCACHE": "$CACHES/go"}
        docker = chowns(self.run_container("docker"))
        self.assertEqual(len(docker), 1)
        self.assertIn("find /workspace /results ! -user 1000 -exec chown 1000:1000 {} +", docker[0].args[0])
        self.assertNotIn("/caches", " ".join(docker[0].args[0]))
        self.assertEqual(docker[0].args[0][3:5], ["--user", "0"])
        self.assertIn("sha256:aaa", docker[0].args[0])
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
                patch.object(shutil, "which", return_value="/usr/bin/podman"),
                patch_names(repo_root=lambda path: self.root),
                contextlib.redirect_stdout(out),
            ):
                args = argparse.Namespace(
                    plan=str(self.plan_file), repo=str(self.root), runtime="podman",
                    base="main", steps=None, env=[], timeout=2,
                )
                self.plan_file.write_text(json.dumps(self.plan))
                run_plan(args)
            return out.getvalue()

        self.assertNotIn("different image", run_with("sha256:aaa"))
        self.assertNotIn("different image", run_with("sha256:aaa"))
        self.assertIn("different image than last time", run_with("sha256:bbb"))
        self.assertTrue((self.cache / "images.json").is_file())

    def test_default_tekton_shell_stops_at_a_failed_command(self):
        self.set_script("false\nprintf 'must not pass\\n'")
        self.assertEqual(self.run_plan(), 1)

    def test_host_timeout_stops_the_process_group(self):
        self.set_script("sleep 10")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_plan(timeout=0.05), 1)

    def test_binding_expansion_preserves_other_variable_names(self):
        bindings = dict.fromkeys(RUNNER_VARS, "")
        bindings.update(WORKSPACE="/workspace", BASE_SHA="base")
        self.assertEqual(
            expand("$WORKSPACE/${BASE_SHA}/$WORKSPACE_CACHE", bindings),
            "/workspace/base/$WORKSPACE_CACHE",
        )

    def test_runner_supplies_head_and_base_branch_variables(self):
        self.set_script('test "$HEAD_SHA:$HEAD_REF:$BASE_REF" = "sha:topic:main"')
        answers = {
            ("rev-parse", "HEAD"): "sha",
            ("rev-parse", "--abbrev-ref", "HEAD"): "topic",
            ("remote",): "origin\nupstream",
        }
        with patch_names(git=lambda root, *args: answers[args]):
            self.assertEqual(self.run_plan(base="origin/main"), 0)
        self.plan["steps"][0]["required_env"] = ["HEAD_SHA"]
        with self.assertRaisesRegex(ValueError, "supplied by the runner"):
            self.validate()

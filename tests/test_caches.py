"""tekton-caches steps and the local cache directories that replace them."""

import argparse
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tkn_innerloop.agent import plan_prompt
from tkn_innerloop.caches import CACHED, caches_dir, clean_caches, prepare_caches
from tkn_innerloop.cli import generate, run_command
from tkn_innerloop.inventory import auto_scope, inventory, plan_scope
from tkn_innerloop.paths import digest
from tkn_innerloop.plan import validate_plan
from tkn_innerloop.tekton import adapted_script

from .helpers import InnerloopCase, patch_names


class CacheTests(InnerloopCase):
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        sources = inventory(self.pipeline, self.root)
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
                    patch.object(shutil, "which", return_value="/usr/bin/podman"),
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
        sources = inventory(path, self.root)
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
        keep, left = auto_scope(sources)
        self.assertEqual(keep, ["checks/test"])
        for key in ("checks/fetch", "checks/remote", "checks/save"):
            self.assertEqual(left[key], CACHED)
        self.assertIn("$CACHES", plan_prompt("p.yaml", sources, ["checks/test"]))

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
        sources = inventory(path, self.root)
        target = str(self.root / "local caches")
        slot = sources["checks/fetch"]["cache"]["dir"].split("/")[-1]
        for key in ("checks/script", "checks/argv"):
            with self.subTest(key=key):
                script = adapted_script(sources[key]["step"], [])
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
        sources = inventory(path, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
            sources = inventory(path, self.root)
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
                sources = inventory(path, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(self.pipeline, self.root)
        local_dir = sources["build/fetch"]["cache"]["dir"]
        self.assertEqual(sources["build/write"]["step"]["script"], f'''mkdir -p "$CACHES"/{local_dir.split("/")[-1]}''')
        self.assertEqual(sources["test/read"]["step"]["env"][0]["value"], local_dir)

        consumer["image"] = "example.invalid/test:1"
        self.pipeline.write_text(yaml.safe_dump(source))
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(path, self.root)
        self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
        self.assertEqual(sources["checks/test"]["step"]["script"], "make -C /src")
        self.assertEqual(auto_scope(sources)[1]["checks/fetch"], CACHED)

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
        sources = inventory(self.pipeline, self.root)
        self.assertEqual(auto_scope(sources), (
            [], {"checks/fetch": CACHED, "checks/upload": CACHED},
        ))
        plan = copy.deepcopy(self.plan)
        plan["inputs"] = {path.name: digest(path) for path in (self.pipeline, actions)}
        plan["steps"] = [
            {**plan["steps"][0], "id": "checks/upload"},
            {"id": "checks/fetch", "decision": "skip", "reason": "remote cache action"},
        ]
        with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
            validate_plan(plan, self.root)

        definitions[0]["spec"]["image"] = "example.invalid/check:1"
        definitions[1]["spec"]["script"] = "make setup"
        actions.write_text(yaml.safe_dump_all(definitions))
        sources = inventory(self.pipeline, self.root)
        self.assertEqual(auto_scope(sources)[0], ["checks/fetch", "checks/upload"])

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
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
        sources = inventory(self.pipeline, self.root)
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
            patch_names(repo_root=lambda path: self.root, agent_plan=agent),
            patch.object(shutil, "which", return_value="pi"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(generate(args), 0)
            self.assertNotIn("remote-cache", agent.call_args.args[3])
            args.steps = "save"
            with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
                generate(args)
            with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
                run_command(args)
        plan = json.loads(self.plan_file.read_text())
        self.assertEqual(plan["scope"], ["checks/check"])
        self.assertEqual(plan["steps"][1]["decision"], "skip")
        plan["steps"][1] = {
            **plan["steps"][0], "id": "checks/save", "script": "/ko-app/cache upload remote-cache\n",
        }
        with self.assertRaisesRegex(ValueError, "tekton-caches actions cannot run locally"):
            validate_plan(plan, self.root)

    def test_cache_tool_in_a_script_with_other_commands_keeps_the_step_in_scope(self):
        image = "example.invalid/check:1"
        self.write_source([
            {"name": "dedicated", "image": image, "script": "#!/bin/sh\nset -e\nexec /ko-app/cache fetch data\n"},
            {"name": "mixed", "image": image, "script": "/ko-app/cache fetch data\nmake test\n"},
            {"name": "chained", "image": image, "script": "/ko-app/cache fetch data && make test"},
        ])
        sources = inventory(self.pipeline, self.root)
        self.assertEqual(auto_scope(sources)[0], ["checks/mixed", "checks/chained"])
        self.assertEqual(plan_scope(sources, "mixed"), ["checks/mixed"])

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
        sources = inventory(self.pipeline, self.root)
        keep, left = auto_scope(sources)
        self.assertEqual(keep, ["checks/setup"])
        self.assertEqual(left["checks/external"], "external reference")
        self.assertEqual(left["checks/real-cache"], CACHED)

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
            patch_names(repo_root=lambda path: self.root),
            patch.object(shutil, "which", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(clean_caches(args), 0)
        self.assertFalse(caches.exists())

    def test_containers_mount_caches_only_when_a_step_uses_them(self):
        self.multi_step_plan([("cached", "exit 0", []), ("uncached", "exit 0", [])])
        self.plan["steps"][0]["env"] = {"GOCACHE": "$CACHES/go/build"}
        with (
            patch.object(subprocess, "Popen", return_value=Mock(returncode=0)) as popen,
            patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout="sha256:aaa")),
            patch.object(shutil, "which", return_value="/usr/bin/podman"),
        ):
            self.assertEqual(self.run_plan(runtime="podman"), 0)
        cached, uncached = [call.args[0] for call in popen.call_args_list]
        self.assertTrue(any(arg.endswith(":/caches:Z") for arg in cached))
        self.assertIn("GOCACHE=/caches/go/build", cached)
        self.assertNotIn("/caches", " ".join(uncached))

    def test_cache_directory_is_reused_only_for_the_same_runtime_and_image(self):
        prepare = prepare_caches
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
                sources = inventory(self.pipeline, self.root)
                self.assertEqual(sources["checks/fetch"]["cache"], {"path": ""})
                for name in ("seed", "install"):
                    self.assertEqual(sources[f"checks/{name}"]["step"]["env"][0]["value"], path)

    def test_cache_clean_uses_docker_when_host_cannot_delete_cache_data(self):
        path = caches_dir(self.root)
        path.mkdir(parents=True)
        args = argparse.Namespace(repo=str(self.root))
        docker = Mock()
        with (
            patch_names(repo_root=lambda path: self.root),
            patch.object(shutil, "rmtree", side_effect=[PermissionError(), None]) as rmtree,
            patch.object(shutil, "which", side_effect=lambda name: name if name == "docker" else None),
            patch.object(subprocess, "run", docker),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(clean_caches(args), 0)
        self.assertEqual(rmtree.call_count, 2)
        command = docker.call_args.args[0]
        self.assertEqual(command[:5], ["docker", "run", "--rm", "--user", "0"])
        self.assertIn(f"{path}:/cache:Z", command)
        self.assertEqual(command[-2:], ["-rf", "/cache/data"])

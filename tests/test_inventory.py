"""Reading pipelines: references, parameters, scope and Pipelines-as-Code."""

import contextlib
import copy
import io
import os
from unittest.mock import patch

import yaml

from tkn_innerloop.inventory import auto_scope, cluster_patterns, inventory
from tkn_innerloop.pac import _tkn_pac_versions
from tkn_innerloop.paths import digest
from tkn_innerloop.plan import assemble_plan, plan_source
from tkn_innerloop.tekton import select_steps

from .helpers import InnerloopCase


class InventoryTests(InnerloopCase):
    def write_yaml(self, name, *documents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump_all(documents))
        return path

    def fake_tkn_pac(self, body, version="0.40.0"):
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        tool = bindir / "tkn-pac"
        tool.write_text(f'#!/bin/sh\n[ "$1" = version ] && {{ echo "{version}"; exit 0; }}\n{body}\n')
        tool.chmod(0o755)
        return patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"})

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
        keep, left = auto_scope(inventory(path))
        self.assertEqual(keep, ["checks/lint"])
        self.assertEqual(left["checks/clone"], "external reference")
        self.assertEqual(left["checks/hidden"], "marked skip-local")
        self.assertEqual(left["checks/ship"], "publishes or deploys")
        self.assertEqual(left["checks/dynamic"], "image has an unresolved substitution")
        self.assertEqual(left["checks/notify"], "publishes or deploys")
        self.assertEqual(left["report/tail"], "runs after the pipeline")

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
        sources = inventory(self.pipeline)
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        original = self.plan["steps"][0]
        self.plan["steps"] = [{**copy.deepcopy(original), "id": key} for key in sources]
        self.validate()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            select_steps(self.plan["steps"], "check")
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
                inventory(self.pipeline)

    def test_unnamed_steps_and_unrelated_resources_are_handled(self):
        source = yaml.safe_load(self.pipeline.read_text())
        del source["spec"]["pipelineSpec"]["tasks"][0]["taskSpec"]["steps"][0]["name"]
        self.pipeline.write_text(
            yaml.safe_dump_all([{"kind": "PersistentVolumeClaim", "metadata": {"name": "x"}}, source])
        )
        self.assertEqual(list(inventory(self.pipeline)), ["checks/unnamed-0"])
        source["spec"]["pipelineSpec"]["tasks"] = source["spec"]["pipelineSpec"]["tasks"][0]
        self.pipeline.write_text(yaml.safe_dump(source))
        with self.assertRaisesRegex(ValueError, "tasks must be a list"):
            inventory(self.pipeline)
        source["metadata"] = "bad"
        self.pipeline.write_text(yaml.safe_dump(source))
        with self.assertRaisesRegex(ValueError, "metadata must be a mapping"):
            inventory(self.pipeline)

    def test_local_pipeline_and_task_references_are_inlined(self):
        self.write_yaml("tekton/task.yaml", self.task("lint"))
        self.write_yaml("tekton/pipeline.yaml", self.pipeline_doc())
        self.pipeline.write_text(
            yaml.safe_dump({"kind": "PipelineRun", "spec": {"pipelineRef": {"name": "ci"}}})
        )
        sources = inventory(self.pipeline, self.root)
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
                step = inventory(self.pipeline, self.root)["checks/check"]["step"]
                self.assertEqual(step["image"], image)
        self.write_source([{"name": "check", "image": "x:$(params.v)", "script": "exit 0"}])
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
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
        step = inventory(self.pipeline)["checks/check"]["step"]
        self.assertEqual(step["image"], "x:{{revision}}")
        self.assertEqual(step["script"], "echo $HEAD_SHA")
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        self.plan["steps"][0].update(image=step["image"], script=step["script"], edits=[])
        with self.assertRaisesRegex(ValueError, "image x:\\{\\{revision\\}\\} has an unresolved"):
            self.validate()

    def test_plain_pipeline_file_with_its_tasks_is_an_entry_point(self):
        self.pipeline.write_text(
            yaml.safe_dump_all([self.pipeline_doc(), self.task("lint")])
        )
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        sources = inventory(self.pipeline, self.root)
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
                source = inventory(self.pipeline, self.root)["checks/task"]
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
        sources = inventory(self.pipeline, self.root)
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
                inventory(self.pipeline, self.root)

    def test_referenced_definitions_are_plan_inputs_and_invalidate_it(self):
        task = self.write_yaml("tasks/lint.yaml", self.task("lint"))
        self.pipeline.write_text(yaml.safe_dump(self.pipeline_doc()))
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        with self.assertRaisesRegex(ValueError, "now reads tasks/lint.yaml"):
            self.validate()
        sources = inventory(self.pipeline, self.root)
        plan = assemble_plan(
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
        step = inventory(self.pipeline)["checks/check"]["step"]
        self.assertEqual(step["script"], "git diff $HEAD_SHA $BASE_REF {{ hash }}")
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        self.plan["steps"][0].update(script=step["script"], edits=[])
        with self.assertRaisesRegex(ValueError, r"unresolved Tekton or PaC substitution: \{\{ hash \}\}"):
            self.validate()

    def test_repository_config_adds_cluster_only_patterns(self):
        sources = {
            "checks/docs": {
                "step": {"image": "x", "script": "npx wrangler deploy"},
                "task_context": {},
                "finally": False,
            }
        }
        patterns = cluster_patterns(self.root)
        self.assertEqual(auto_scope(sources, patterns)[0], ["checks/docs"])
        (self.root / ".tekton-innerloop.yaml").write_text("cluster_only: ['\\bwrangler\\s+deploy']\n")
        patterns = cluster_patterns(self.root)
        self.assertEqual(
            auto_scope(sources, patterns)[1], {"checks/docs": "publishes or deploys"}
        )
        for text in ("cluster_only: ['(']\n", "cluster_only: x\n", "other: []\n"):
            (self.root / ".tekton-innerloop.yaml").write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, ".tekton-innerloop.yaml"):
                cluster_patterns(self.root)

    def test_konflux_checkout_steps_are_provided_and_source_is_the_worktree(self):
        self.write_source([
            {"name": "use-trusted-artifact", "image": "quay.io/konflux-ci/build-trusted-artifacts:latest",
             "args": ["use", "$(params.SOURCE_ARTIFACT)=/var/workdir/source"]},
            {"name": "check", "image": "example.invalid/check:1", "workingDir": "/var/workdir/source",
             "script": "ls /var/workdir/source/x /var/workdir/sourced"},
        ])
        sources = inventory(self.pipeline, self.root)
        step = sources["checks/check"]["step"]
        self.assertEqual(step["workingDir"], "$WORKSPACE")
        self.assertEqual(step["script"], "ls $WORKSPACE/x /var/workdir/sourced")
        keep, left = auto_scope(sources)
        self.assertEqual(keep, ["checks/check"])
        self.assertEqual(left, {"checks/use-trusted-artifact": "provided by the local checkout"})

    def test_tkn_pac_resolution_is_cached_and_falls_back_on_failure(self):
        source = yaml.safe_load(self.pipeline.read_text())
        source["metadata"] = {"annotations": {"pipelinesascode.tekton.dev/task": "git-clone"}}
        self.pipeline.write_text(yaml.safe_dump(source))
        resolved = yaml.safe_dump({**source, "metadata": {"name": "resolved", **source["metadata"]}})
        calls = self.root / "calls"
        out = io.StringIO()
        resolver = f"echo \"$@\" >> {calls}\ncat <<'EOF'\n{resolved}EOF"
        for version, runs in [("0.40.0", 1), ("0.40.0", 1), ("0.41.0", 2)]:
            _tkn_pac_versions.clear()
            with self.fake_tkn_pac(resolver, version), contextlib.redirect_stdout(out):
                path, extra = plan_source(self.root, self.pipeline)
            self.assertEqual(len(calls.read_text().splitlines()), runs, version)
        self.assertEqual(path.parent, self.cache)
        self.assertEqual(extra, [])
        self.assertIn("-p revision=$HEAD_SHA", calls.read_text())
        sources = inventory(path, self.root)
        plan = assemble_plan(
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
                plan_source(self.root, self.pipeline, refresh=True)[0], self.pipeline
            )
        self.assertIn("tkn pac resolve failed (boom)", out.getvalue())

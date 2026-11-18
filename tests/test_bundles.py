"""Tekton bundle references fetched with skopeo."""

import contextlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from unittest.mock import patch

import yaml

from tkn_innerloop.bundles import _bundle_digests, _bundle_failures, bundle_path
from tkn_innerloop.inventory import inventory
from tkn_innerloop.plan import assemble_plan

from .helpers import InnerloopCase


class BundleTests(InnerloopCase):
    def bundle_ref(self, image, name, kind="task"):
        params = {"bundle": image, "name": name, "kind": kind}
        return {"resolver": "bundles", "params": [{"name": k, "value": v} for k, v in params.items()]}

    TAGGED = "sha256:" + "b" * 64

    PINNED = "sha256:" + "a" * 64

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
                _bundle_digests.clear()
                _bundle_failures.clear()
                self.pipeline.write_text(yaml.safe_dump(
                    {"kind": "PipelineRun", "spec": {"pipelineSpec": {"tasks": [
                        {"name": "checks", "taskRef": self.bundle_ref(tt["image"], "lint")}]}}}
                ))
                which = patch.object(shutil, "which", return_value=None) if "which" in tt else contextlib.nullcontext()
                with self.fake_bundle(action, task, error=tt.get("error")), which:
                    sources = inventory(self.pipeline, self.root)
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
            sources = inventory(self.pipeline, self.root)
        self.assertEqual(list(sources), ["checks/check"])
        self.assertIn(f"bundle:example.invalid/catalog/pipeline-ci@{self.PINNED}#pipeline/ci", sources["checks/check"]["defined_in"])
        shutil.rmtree(Path(os.environ["XDG_CACHE_HOME"]) / "tkn-innerloop" / "bundles")
        _bundle_failures.clear()
        with self.fake_bundle(error="manifest unknown"), self.assertRaisesRegex(
            ValueError, "bundle pipelineRef: skopeo failed: manifest unknown"
        ):
            inventory(self.pipeline, self.root)

    def test_bundle_objects_are_cached_and_invalidate_the_plan(self):
        image = f"example.invalid/catalog/task-lint:0.1@{self.PINNED}"
        self.pipeline.write_text(yaml.safe_dump(self.pipeline_doc(task_ref=self.bundle_ref(image, "lint"))))
        with self.fake_bundle(self.task("lint")):
            sources = inventory(self.pipeline, self.root)
            _bundle_failures.clear()
            self.assertEqual(inventory(self.pipeline, self.root), sources)
        self.assertEqual(len(self.skopeo_calls()), 1)
        plan = assemble_plan(self.good_proposal(), self.root, "pipeline.yaml", sources, ["checks/check"])
        name = f"bundle:example.invalid/catalog/task-lint@{self.PINNED}#task/lint"
        self.assertIn(name, plan["inputs"])
        self.plan = plan
        self.validate()
        cached = bundle_path(name)
        cached.write_text(cached.read_text().replace("exit 0", "exit 1"))
        with self.assertRaisesRegex(ValueError, f"Stale plan: {name} changed"):
            self.validate()

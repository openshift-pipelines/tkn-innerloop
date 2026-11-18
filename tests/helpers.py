"""Shared fixtures for the tkn-innerloop tests."""

import argparse
import contextlib
import copy
import importlib
import io
import json
import os
import pkgutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import tkn_innerloop
from tkn_innerloop.bundles import BundleError, _bundle_digests, _bundle_failures
from tkn_innerloop.paths import cache_dir, digest
from tkn_innerloop.plan import PLAN_VERSION, validate_plan
from tkn_innerloop.runner import run_plan

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "bin/tkn-innerloop"
CORPUS = REPO / "tests" / "corpus"
MODULES = [importlib.import_module(f"tkn_innerloop.{m.name}") for m in pkgutil.iter_modules(tkn_innerloop.__path__)
           if m.name != "__main__"]


def patch_names(**values):
    """Patch names in every tkn_innerloop module that imported them."""
    stack = contextlib.ExitStack()
    for name, value in values.items():
        modules = [m for m in MODULES if name in vars(m)]
        assert modules, name
        for module in modules:
            stack.enter_context(patch.object(module, name, value))
    return stack


def clear_bundle_memos(test):
    for memos in (_bundle_digests, _bundle_failures):
        memo = patch.dict(memos, clear=True)
        memo.start()
        test.addCleanup(memo.stop)


def offline_skopeo(*args, **kwargs):
    raise BundleError("offline: skopeo " + " ".join(args))


class InnerloopCase(unittest.TestCase):
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
        self.cache = cache_dir(self.root)
        self.pipeline = self.root / "pipeline.yaml"
        self.write_source(
            [{"name": "check", "image": "example.invalid/check:1", "script": "exit 0"}]
        )
        self.plan = {
            "version": PLAN_VERSION,
            "pipeline": "pipeline.yaml",
            "inputs": {"pipeline.yaml": digest(self.pipeline)},
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
        return validate_plan(self.plan, self.root)

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
            patch_names(repo_root=lambda path: self.root),
            contextlib.redirect_stdout(capture or io.StringIO()),
        ):
            return run_plan(args)

    def good_proposal(self):
        proposed = {"inputs": [], "steps": copy.deepcopy(self.plan["steps"])}
        proposed["steps"][0].pop("script")
        return proposed

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

    def multi_step_plan(self, specs):
        """specs: list of (name, script, depends_on). Steps run on the host in this order."""
        image = "example.invalid/check:1"
        self.write_source(
            [{"name": n, "image": image, "script": sc} for n, sc, _ in specs]
        )
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        base = self.plan["steps"][0]
        self.plan["steps"] = [
            dict(copy.deepcopy(base), id=f"checks/{n}", script=sc, depends_on=[f"checks/{d}" for d in deps])
            for n, sc, deps in specs
        ]

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

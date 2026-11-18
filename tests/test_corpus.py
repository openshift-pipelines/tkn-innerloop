"""Real pipelines from tests/corpus."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tkn_innerloop.bundles import BUNDLE_DIR_ENV, _bundle_digests, _bundle_failures
from tkn_innerloop.inventory import auto_scope, cluster_patterns, inventory
from tkn_innerloop.pac import pac_profile
from tkn_innerloop.plan import load_plan

from .helpers import CORPUS, REPO, offline_skopeo, patch_names


class CorpusTests(unittest.TestCase):
    def test_shipped_demo_plan_validates_against_the_pinned_source(self):
        plan = REPO / "demo" / "linters.plan.json"
        load_plan(plan, CORPUS / "pac-linter" / "repo")

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
                patch_names(skopeo=offline_skopeo),
                patch.dict(_bundle_failures, clear=True),
                patch.dict(
                    _bundle_digests,
                    yaml.safe_load(tags.read_text()) if tags.exists() else {},
                    clear=True,
                ),
                patch.dict(
                    os.environ,
                    {"XDG_CACHE_HOME": cache, BUNDLE_DIR_ENV: str(bundles)},
                ),
            ):
                root = Path(tmp)
                shutil.copytree(case / "repo", root, dirs_exist_ok=True)
                entry = root / expected["source"]
                if expected.get("error"):
                    with self.assertRaisesRegex(ValueError, expected["error"]):
                        inventory(entry, root)
                    continue
                sources = inventory(entry, root)
                documents = list(yaml.safe_load_all(entry.read_text()))
                self.assertEqual(pac_profile(documents), expected["pac"])
                self.assertEqual(list(sources), list(expected["steps"]))
                for key, step in expected["steps"].items():
                    with self.subTest(step=key):
                        self.check_step(sources[key], step)
                keep, left = auto_scope(sources, cluster_patterns(root))
                self.assertEqual(keep, expected["scope"]["local"])
                self.assertEqual(left, expected["scope"]["cluster"])

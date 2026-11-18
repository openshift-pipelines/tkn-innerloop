"""Plan validation: coverage, inputs, scripts, edits and environment."""

import copy

from tkn_innerloop.paths import digest

from .helpers import InnerloopCase


class PlanTests(InnerloopCase):
    def test_incomplete_or_duplicate_coverage_rejected(self):
        self.plan["steps"].append(copy.deepcopy(self.plan["steps"][0]))
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.validate()
        self.plan["steps"] = []
        with self.assertRaisesRegex(ValueError, "every source step"):
            self.validate()

    def test_unresolved_reference_cannot_be_run(self):
        self.write_source([{"name": "check", "ref": {"name": "remote"}}])
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        with self.assertRaisesRegex(ValueError, "skip-local"):
            self.validate()

    def test_changed_helper_invalidates_plan(self):
        helper = self.root / "helper.sh"
        helper.write_text("exit 0\n")
        self.plan["inputs"]["helper.sh"] = digest(helper)
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
        self.plan["inputs"][str(self.pipeline)] = digest(self.pipeline)
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
        self.plan["inputs"]["pipeline.yaml"] = digest(self.pipeline)
        second = copy.deepcopy(self.plan["steps"][0])
        second.update(id="checks/second", depends_on=["checks/check"])
        self.plan["steps"].append(second)
        with self.assertRaisesRegex(ValueError, "omits required setup"):
            self.run_plan(steps="second")

    def test_optional_env_cannot_overlap_required(self):
        self.plan["steps"][0]["required_env"] = ["SAME"]
        self.plan["steps"][0]["optional_env"] = ["SAME"]
        with self.assertRaisesRegex(ValueError, "both required and optional"):
            self.validate()

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

# Corpus

Real PipelineRuns and Pipelines from other repositories, used by
`CorpusTests` in `tests/test_innerloop.py`. Each case has:

- `repo/`: the entry file and the local Pipelines, Tasks and StepActions it
  references, copied unchanged at their original paths
- `expected.yaml`: what inventory and the automatic scope must return, checked by
  the test
- `README.md`: what a good local plan should look like, for reviewers and model
  evaluation; not asserted

In `expected.yaml`, `steps` lists every step ID in order. Each step's fields are
compared when present: `reference`, `note` (substring), `image`, `defined_in`,
`finally`, `step_action`, `matrix` and `script_contains`. `scope.local` and
`scope.cluster` must match `auto_scope` exactly. A case that must fail has
`error`, a regular expression, in place of `steps` and `scope`.

## Model and execution checks

`tests/corpus-check` checks out each case's source repository at its pinned
commit, asks the model for a plan of the automatic scope several times, and
compares each plan with the optional `plan` block:

- `run` and `not_run`: steps that must or must not be run
- `contains`: text a step's adapted script must contain, checked when it runs
- `depends_on`: dependencies a running step must declare

With `--execute`, cases that have an `execute` block run the first plan that
met its expectations. Every planned step must pass on the unchanged source.
Each entry in `breaks` then commits the listed `files` on top and the run must
fail exactly on its `fail` steps. `execute.steps` limits the run to some steps,
and `execute.timeout` sets the seconds allowed per step.
Plans get no environment variables unless they are named with `--env NAME`,
and a plan that requires one that was not given is reported as blocked.

```sh
tests/corpus-check --model github-copilot/claude-sonnet-5.5:minimal --execute
tests/corpus-check pac-linter --runs 1 --execute
```

Each plan, log and a `report.json` are written under `tmp/corpus-runs/`. This
calls the model and pulls images, so it is not part of the unit tests.

## Adding a case

To add a case, copy the files into `<case>/repo/`, add a row to `SOURCES.md`,
write `expected.yaml` from the source rather than from the current output, and
describe the expected plan in `README.md`.

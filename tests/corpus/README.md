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

To add a case, copy the files into `<case>/repo/`, add a row to `SOURCES.md`,
write `expected.yaml` from the source rather than from the current output, and
describe the expected plan in `README.md`.

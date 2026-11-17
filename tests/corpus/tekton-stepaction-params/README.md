# tekton-stepaction-params

From tektoncd/pipeline `examples/v1/pipelineruns/stepaction-params.yaml`; the pinned commit is in `../SOURCES.md`.

A StepAction with string, array and object params, called from a PipelineRun
that passes its own values.

The step gets the StepAction's image and args. Inside it, `$(params.x)` refers to
the step's params, then to the StepAction defaults. A good plan renders the args
with the PipelineRun's values: the array expanded, key2 taken from the
PipelineRun object and key1 kept at the StepAction default.

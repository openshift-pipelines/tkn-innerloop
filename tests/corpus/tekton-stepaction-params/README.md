# tekton-stepaction-params

From tektoncd/pipeline `examples/v1/pipelineruns/stepaction-params.yaml`; the pinned commit is in `../SOURCES.md`.

A StepAction with string, array and object params, called from a PipelineRun
that passes its own values.

The step gets the StepAction's image, args and params, with `$(params.x)` inside
it referring to the step's params and then to the StepAction defaults. Since it
has args and no command or script, it runs the image entrypoint, which a plan
cannot express. The automatic scope therefore leaves it on the cluster. The
case covers the inventory: image, params and the step_action record.

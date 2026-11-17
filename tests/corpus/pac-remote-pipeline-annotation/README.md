# pac-remote-pipeline-annotation

From tektoncd/pipelines-as-code `.tekton/boussole.yaml`; the pinned commit is in `../SOURCES.md`.

A PipelineRun whose pipelineRef names a Pipeline that only exists at a URL given
in the `pipelinesascode.tekton.dev/pipeline` annotation.

Local resolution cannot find it, so inventory fails. The error says a PaC
annotation may fetch it and that installing tkn-pac lets tkn pac resolve do so.
With tkn-pac installed, the resolved output is used instead and this error does
not appear.

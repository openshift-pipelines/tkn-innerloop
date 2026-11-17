# tekton-hub-resolver-pipeline

From tektoncd/pipeline `examples/v1/pipelineruns/no-ci/hub-resolver.yaml`; the pinned commit is in `../SOURCES.md`.

A PipelineRun whose whole Pipeline comes from Tekton Hub, next to a PVC.

With no pipeline contents in the repository, inventory fails with "remote
pipelineRef is not supported" and names the hub resolver. The PVC is ignored.

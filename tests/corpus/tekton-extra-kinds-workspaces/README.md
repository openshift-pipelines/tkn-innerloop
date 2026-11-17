# tekton-extra-kinds-workspaces

From tektoncd/pipeline `examples/v1/pipelineruns/mapping-workspaces.yaml`; the pinned commit is in `../SOURCES.md`.

The pipeline file also holds a ConfigMap, a Secret and a PersistentVolumeClaim
that feed its workspaces.

Inventory ignores those resources and reads the PipelineRun, Pipeline and
Tasks. fetch-and-write reads a password from a Secret-backed workspace. The
plan should declare it as required env and not embed the value, or leave the step
unresolved. print-secrets only needs the shared workspace, which maps to a local
directory.

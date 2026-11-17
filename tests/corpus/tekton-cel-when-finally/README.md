# tekton-cel-when-finally

From tektoncd/pipeline `examples/v1/pipelineruns/alpha/pipelinerun-with-cel-when-expressions.yaml`; the pinned commit is in `../SOURCES.md`.

A plain embedded pipelineSpec with CEL `when` guards on parameters, task
results and execution status, and six finally tasks.

create-file and check-file run in ubuntu and alpine with `$(workspaces.source.path)`
pointed at a scratch directory, not at the worktree. task-should-be-skipped-2 and
-3 compare the `path` value (`README.md`) or two constants, and both are false,
so the plan skips them. echo-file-exists and task-should-be-skipped-1 depend on
check-file's result, so they stay unresolved unless the plan wires that result
through a file. Finally tasks stay outside the local run.

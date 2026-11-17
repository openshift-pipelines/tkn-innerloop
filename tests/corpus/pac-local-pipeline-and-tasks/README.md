# pac-local-pipeline-and-tasks

From openshift-pipelines/pipeline-service `.tekton/update-dependencies.yaml`; the pinned commit is in `../SOURCES.md`.

A PaC PipelineRun with a local pipelineRef to `.tekton/pipeline/update-repository.yaml`,
which uses the Task in `.tekton/tasks/update-repository.yaml` twice.

Both files are inlined and recorded in defined_in. clone-git-repository clones
`$(params.TARGET_GH_URL)` into a workspace. Locally, the worktree already exists,
so a good plan either skips the clone or points it at `$WORKSPACE`.
run-update-script uses the image `dependencies-update:{{ source_branch }}`. An
image cannot take a shell variable, so the step stays on the cluster as an
unresolved substitution. create-pull-request runs `git push` and stays on the
cluster.

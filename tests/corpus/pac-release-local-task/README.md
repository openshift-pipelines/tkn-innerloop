# pac-release-local-task

From tektoncd/pipelines-as-code `.tekton/release-pipeline.yaml`; the pinned commit is in `../SOURCES.md`.

A PaC release pipeline that uses a local Task (`.tekton/tasks/goreleaser.yaml`)
through a taskRef, and fetches `git-clone` from the hub through a
`pipelinesascode.tekton.dev/task-1` annotation.

Without tkn-pac, git-clone stays a reference and its note says a PaC annotation
may provide it. The goreleaser Task is inlined and its file recorded in
defined_in, so editing it makes a cached plan stale. gorelease/pull is the only
local step. gorelease/release (`goreleaser release`) and push-release-to-branch
(`git push`) are publishing steps and stay on the cluster. push-release-to-branch
already shows `{{ revision }}` bound to `$HEAD_SHA`.

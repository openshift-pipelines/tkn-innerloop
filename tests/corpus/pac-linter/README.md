# pac-linter

From tektoncd/pipelines-as-code `.tekton/linter.yaml`; the pinned commit is in `../SOURCES.md`.

Pipelines-as-Code's own lint job: one embedded task with fifteen steps across
five images, a hub `git-clone` StepAction for the checkout, and PaC
placeholders in the scripts.

Locally, fetch-repo stays a cluster-only reference because the worktree is
already mounted. The other fourteen steps run in their source images with
`$(workspaces.source.path)` replaced by `$WORKSPACE`. commit-validation and
check-test-conventions read `{{ body.pull_request.base.sha }}` and
`{{ headers['X-Github-Event'] }}`. A good plan binds them to `$BASE_SHA` and
`pull_request` through script edits. Their changed-file logic must also see
uncommitted and untracked files. `HUB_TOKEN` only raises the GitHub download
rate limit, so it belongs in optional_env.

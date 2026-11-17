# tekton-local-stepactions

From tektoncd/plumbing `tekton/ci/jobs/tekton-golang-coverage.yaml`; the pinned commit is in `../SOURCES.md`.

A coverage job whose steps reference StepActions by name: one is defined in the
same file, and two live in `tekton/ci/stepactions/`.

Each StepAction is inlined into its step with its image and script. Its params
are recorded under step_action, and files from elsewhere appear in defined_in.
clone can be skipped because the worktree is mounted. coverage needs GitHub and
GCS settings, so required env or unresolved. upload calls `gsutil` to write to a
bucket, so a good plan skips it as publishing, which the built-in cluster-only
patterns do not catch. A repository can add the pattern to
`.tekton-innerloop.yaml`.

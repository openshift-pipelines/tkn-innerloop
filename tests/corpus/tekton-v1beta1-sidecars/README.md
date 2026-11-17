# tekton-v1beta1-sidecars

From tektoncd/plumbing `tekton/ci/jobs/e2e-kind.yaml`; the pinned commit is in `../SOURCES.md`.

A `tekton.dev/v1beta1` CI job. It uses shared Tasks from
`tekton/ci/shared/common-tasks.yaml`, bundle-resolved clone and log-upload
tasks, a finally task, and a privileged docker-in-docker sidecar.

The shared Tasks are inlined and recorded in defined_in. check-name and
check-files-changed can run locally once their result paths and GitHub command
parameters are bound. e2e-tests/kind needs the dind sidecar, which is never
started locally, so the model should leave it unresolved even though the
automatic scope keeps it.

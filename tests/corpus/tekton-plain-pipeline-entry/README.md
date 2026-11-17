# tekton-plain-pipeline-entry

From konflux-ci/release-service `integration-tests/pipelines/konflux-e2e-tests-pipeline.yaml`; the pinned commit is in `../SOURCES.md`.

A bare `kind: Pipeline` file, with no PipelineRun, used as the entry point. Most
tasks come from the git resolver, one task mixes an inline step with
git-resolved StepActions, and four finally tasks.

Only release-service-e2e-tests/e2e-test is inline. It runs the e2e suite against
a cluster that provision-kind-cluster and deploy-konflux create, and both stay
references. So the model should leave it unresolved, naming the missing cluster.

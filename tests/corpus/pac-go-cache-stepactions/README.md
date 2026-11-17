# pac-go-cache-stepactions

From tektoncd/pipelines-as-code `.tekton/go.yaml`; the pinned commit is in `../SOURCES.md`.

The Go job: a hub `git-clone` StepAction, then cache-fetch and cache-upload
StepActions loaded through the http resolver around the unittest and
golangci-lint steps.

The three resolver steps stay references; tkn pac resolve does not fetch http
resolver refs either. unittest runs in `golang:1.27` and lint in
`golangci-lint:v2.15.0`, with the workspace path rewritten to `$WORKSPACE`. The
plan should say that skipping the cache only costs time.

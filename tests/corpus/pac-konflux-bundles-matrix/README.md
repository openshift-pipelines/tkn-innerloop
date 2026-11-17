# pac-konflux-bundles-matrix

From konflux-ci/build-service `.tekton/build-service-pull-request.yaml`; the pinned commit is in `../SOURCES.md`.

A Konflux pull-request pipeline. Every task comes from an OCI bundle, four use
`matrix`, and triggering uses `on-cel-expression`.

Nothing can run locally: all sixteen tasks are bundle references whose contents
are not read, so the expected local scope is empty and the tool should say there
is nothing to run. The matrix flag is still recorded so the model cannot treat a
matrix task as a single run.

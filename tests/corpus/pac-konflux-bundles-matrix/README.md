# pac-konflux-bundles-matrix

From konflux-ci/build-service `.tekton/build-service-pull-request.yaml`; the pinned commit is in `../SOURCES.md`.

A Konflux pull-request pipeline. Every task comes from an OCI bundle, four use
`matrix`, and triggering uses `on-cel-expression`.

Every bundle is fetched and its Task inlined, so the inventory lists the real
steps and records the bundle object in defined_in. The clone task and every
trusted-artifact step are provided by the local checkout and never reach the
model. Image builds, SBOM uploads and image checks that sign or push stay on the
cluster. The remaining scans need the built image or registry credentials, so a
good plan runs very little and says why. The matrix flag is still recorded so
the model cannot treat a matrix task as a single run.

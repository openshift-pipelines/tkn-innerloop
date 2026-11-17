# pac-konflux-fbc-lightspeed

From openshift/lightspeed-operator `.tekton/fbc-v4-21-pull-request.yaml`; the pinned commit is in `../SOURCES.md`.

A Konflux file-based catalog (FBC) pull-request pipeline. All eleven tasks are
`resolver: bundles` references pinned as `repo:tag@sha256:…`, and the FIPS check
task runs a StepAction through the git resolver.

The bundles are fetched and inlined. The clone, symlink check and every
trusted-artifact step are provided by the local checkout, and `/var/workdir/source`
is bound to the worktree in the other steps. The FIPS StepAction stays a
reference. Building and pushing the catalog image, SBOM uploads and the
deprecated image check stay on the cluster.

The useful local work is the opm command task and the FBC validation scripts.
With empty `OPM_ARGS` the opm step does nothing, and the validation steps inspect
the image the build pushed, so a good plan runs little and says why.
apply-tags and the validate-fbc steps that attach artifacts to the image write to
the registry and must not run.

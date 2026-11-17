# pac-chains-release

From tektoncd/chains `.tekton/release.yaml`; the pinned commit is in `../SOURCES.md`.

Tekton Chains' release run. Its pipelineRef points at `release/release-pipeline.yaml`
in the repository, and most tasks inside come from git and bundles resolvers.

The Pipeline and the catalog bundle tasks are inlined, and the three git resolver
tasks stay references. unit-tests and build can run locally. The two
publish-to-bucket uploads need Oracle Cloud credentials and push artifacts, so the
model should leave them on the cluster even though the automatic scope keeps them. report-bucket and prepare-draft-release write
Tekton results, so the plan has to point `$(results.*.path)` at files it creates.
prepare-draft-release also needs `$(workspaces.workarea.path)/git` mapped onto
the worktree. wait-for-chains polls TaskRuns with `kubectl get` in the release
namespace. That needs a live cluster, so the model should leave it unresolved
even though the automatic scope keeps it.

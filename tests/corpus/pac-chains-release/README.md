# pac-chains-release

From tektoncd/chains `.tekton/release.yaml`; the pinned commit is in `../SOURCES.md`.

Tekton Chains' release run. Its pipelineRef points at `release/release-pipeline.yaml`
in the repository, and most tasks inside come from git and bundles resolvers.

The Pipeline is inlined, and eight resolver tasks stay references. Three inline
steps are proposed for local runs. report-bucket and prepare-draft-release write
Tekton results, so the plan has to point `$(results.*.path)` at files it creates.
prepare-draft-release also needs `$(workspaces.workarea.path)/git` mapped onto
the worktree. wait-for-chains polls TaskRuns with `kubectl get` in the release
namespace. That needs a live cluster, so the model should leave it unresolved
even though the automatic scope keeps it.

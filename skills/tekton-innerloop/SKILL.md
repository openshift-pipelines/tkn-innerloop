---
name: tekton-innerloop
description: Fast inner-loop Tekton validation for coding agents. Use when a repository has `.tekton/` PipelineRuns and you want to validate code edits locally using the exact commands, flags, and container images from the CI pipeline before committing or pushing—without running a Kubernetes cluster.
---

# Tekton Inner-Loop Validation Skill

Validate local edits against the **exact** linters, unit test commands, convention checks, and build flags defined in `.tekton/*.yaml` before committing or opening a pull request.

## Why This Matters

Coding agents often run generic local checks (`go test ./...`, `ruff check`, `trivy`) that differ from the exact commands, flags, or convention scripts in `.tekton/` pipelines. Moving the deterministic inner-loop slice of Tekton pipelines into the local agent loop catches CI failures in seconds instead of waiting 5–10 minutes on a cluster.

## Workflow

### 1. Discover & Classify `.tekton` Pipelines

Run `tkn-innerloop inspect` on the repository or a specific pipeline file (for example `.tekton/linter.yaml`):

```bash
tkn-innerloop inspect .tekton/linter.yaml
```

Classification rules used by the CLI and agent:

| Category | Signals | Action |
| --- | --- | --- |
| **Inner-Loop (`run-local`)** | Linters (`codespell`, `shellcheck`, `yamllint`, `ruff`, `markdownlint`, `golangci-lint`), convention scripts (`check-test-conventions`, `check-e2e-tests-naming`), commit checks (`gitlint`), unit tests (`gotestsum`), schema/manifest generators (`generate-release-yaml`). Also explicit `(run-local)` in `description`/`displayName` or `tekton.dev/local: "true"`. | Extract exact script & flags and run locally (host or container). |
| **Outer-Loop (`skip-local`)** | StepAction / remote resolver (`git-clone`, `cache-fetch`, `cache-upload`), cluster/cloud deployments (`wrangler pages deploy`), release/signing/SBOM (`goreleaser`, `cosign`, `push-release-to-branch`), or explicit `(skip-local)` / `tekton.dev/local: "skip"`. | Skip during inner-loop iteration; leave for cluster CI. |
| **Needs Token / Tool Download** | Steps referencing `secretKeyRef` (e.g., `HUB_TOKEN` for GitHub API release downloads or status posts). | Substitute local binary if installed (`vale`, `yq`, `kubectl`, `goreleaser`), or inject token from `gh auth token` / local env. |

### 2. Generate the Local Inner-Loop Script

Generate a standalone executable script or container quadlet/compose runner from the pipeline:

```bash
# Host/hybrid script using exact CI flags and PaC template substitution
tkn-innerloop generate .tekton/linter.yaml --runtime host -o /tmp/run-linters.sh

# Containerized runner using exact step images (podman / docker)
tkn-innerloop generate .tekton/linter.yaml --runtime podman -o /tmp/run-linters-container.sh
```

How translation works:
1. **Workspace substitution**: Replaces `$(workspaces.source.path)` with the local git worktree root.
2. **Pipelines-as-Code variable injection**: Simulates a Pull Request event locally:
   - `{{ headers['X-Github-Event'] }}` → `pull_request`
   - `{{ body.pull_request.base.sha }}` → `git merge-base HEAD main` (or `HEAD~1`)
   - `{{ body.pull_request.user.login }}` → current developer/agent user
3. **Container / Host adaptation**: Strips redundant container-only package installs (`pip3 install codespell ruff gitlint`, `git config --global`) when running in `--runtime host` if the tool is already on `PATH`, while keeping the **exact** invocation flags (`codespell -d docs/content pkg test`, `yamllint -f parsable -c .yamllint ...`).

### 3. Run Targeted Inner-Loop Checks After Edits

Run either the full inner-loop suite or only the steps matching the files you touched:

```bash
# Run all inner-loop steps in .tekton/linter.yaml
tkn-innerloop run .tekton/linter.yaml

# Run specific inner-loop steps for fast sub-second feedback
tkn-innerloop run .tekton/linter.yaml --steps codespell,shellcheck,check-test-conventions,yamllint
```

### 4. Authoring Hints for Pipeline Maintainers

Pipeline authors can guide inner-loop extraction directly in `.tekton/*.yaml`:
- Add `(run-local)` or `(skip-local)` to a step or task `displayName` / `description`
- Or add annotations `tekton.dev/inner-loop: "true"` / `tekton.dev/inner-loop: "skip"`

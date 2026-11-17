---
title: Fast Inner-Loop Tekton Validation
author: Pipelines-as-Code Team
date: November 2026
style: keynote
theme: tokyo-night
accent: violet
font: modern
footer: Tekton Inner-Loop PoC
numbers: true
presentation: true   # sharei: show the "Play presentation" button
transition: slide
autoplay: true
---

# Fast Inner-Loop Tekton Validation

Validate coding agent edits against your real `.tekton/` pipelines in 3 seconds on your laptop—without running Kubernetes.

<!-- notes:
Welcome! This deck walks through how we give coding agents fast, exact Tekton validation on a laptop without spinning up a local Kubernetes cluster.
-->

<!-- slide: bg=muted reveal -->
## Why coding agents break CI

- **Agents guess local checks** — They run `go test` or `ruff`, see green in 10ms, and think the code is ready.
- **CI runs stricter rules** — Your `.tekton` pipeline passes custom flags, convention scripts, and commit linters.
- **Five minutes to fail** — You push a PR, wait for pods to schedule, and fail on a rule the agent never ran.

<!-- notes:
Agents already know how to run generic linters or unit tests. The problem is that CI runs different flags, custom bash scripts, or specific scanners.
-->

<!-- slide: layout=stat bg=gradient -->
## The 80 / 20 inner-loop insight

**80%** of Tekton PR steps only need your local files and standard CLI tools.

<!-- notes:
We do not need to solve 100% of Kubernetes locally. If we bring 80% of the pipeline checks into the inner loop, most broken PRs disappear before git push.
-->

## Today vs. the Inner-Loop Agent

```mermaid
flowchart LR
  Agent@{ img: "/icons/agent.svg", label: "Coding Agent", w: 48, h: 48, constraint: "on" }
  Local@{ img: "/icons/terminal.svg", label: "tkn-innerloop", w: 48, h: 48, constraint: "on" }
  PR@{ img: "/icons/pull-request.svg", label: "Pull Request", w: 48, h: 48, constraint: "on" }
  Cluster@{ img: "/icons/cluster.svg", label: "Tekton Cluster", w: 48, h: 48, constraint: "on" }
  Fail@{ img: "/icons/fail.svg", label: "Red PR (5m)", w: 48, h: 48, constraint: "on" }
  Pass@{ img: "/icons/success.svg", label: "Green PR", w: 48, h: 48, constraint: "on" }

  Agent naive@-->|generic go test| PR
  PR --> Cluster
  Cluster bad@-->|custom linter fails| Fail
  Fail -->|fix & re-push| Agent

  Agent smart@-->|validate locally| Local
  Local fix@-->|fails in 1.8s| Agent
  Local ok@-->|12/12 green in 3s| PR
  Cluster good@-->|all checks pass| Pass

%% play: Without tkn-innerloop (5m loop)
%% skip Local Pass
%% Agent -> PR @naive : push | The agent runs a basic go test locally and opens a PR
%% PR -> Cluster : webhook
%% wait 1200 | Waiting 5 minutes for cluster pods and git-clone...
%% Cluster -> Fail @bad : red | Custom Tekton test-convention script fails on the cluster
%% Fail -> Agent : logs | The agent has to start over after waiting on CI
%% play: With tkn-innerloop (3s loop)
%% skip Fail
%% Agent -> Local @smart : edit | The agent runs tkn-innerloop before committing
%% Local -> Agent @fix : 1.8s fail | Caught locally in 1.8 seconds! The agent fixes the test name
%% Agent -> Local @smart : re-run
%% Local -> PR @ok : 3.1s green | All 12 Tekton steps pass locally in 3.1 seconds
%% PR -> Cluster : webhook
%% Cluster -> Pass @good : green | The cluster outer-loop pipeline turns green on the first try
```

<!-- notes:
Compare the two stories: in the first story, the agent pushes right after `go test` and waits 5 minutes for a red PR. In the second story, `tkn-innerloop` catches the failure in 1.8 seconds on the laptop.
-->

<!-- slide: bg=accent -->
## How it works under the hood

<!-- notes:
Let's look at the four steps that make this work without running a Kubernetes control plane locally.
-->

## Four steps from Tekton YAML to local execution

```mermaid
flowchart LR
  YAML@{ img: "/icons/file-code.svg", label: ".tekton/*.yaml", w: 48, h: 48, constraint: "on" }
  Inspect@{ img: "/icons/filter.svg", label: "1. Classify", w: 48, h: 48, constraint: "on" }
  Rewrite@{ img: "/icons/config.svg", label: "2. Rewrite Vars", w: 48, h: 48, constraint: "on" }
  Runtime@{ img: "/icons/decision.svg", label: "3. Runtime", w: 48, h: 48, constraint: "on" }
  Host@{ img: "/icons/laptop.svg", label: "Host Mode", w: 48, h: 48, constraint: "on" }
  Podman@{ img: "/icons/container.svg", label: "Podman Mode", w: 48, h: 48, constraint: "on" }
  Green@{ img: "/icons/check.svg", label: "4. Agent Loop", w: 48, h: 48, constraint: "on" }
  Outer@{ img: "/icons/cloud.svg", label: "Skip Outer Loop", w: 48, h: 48, constraint: "on" }

  YAML --> Inspect
  Inspect -->|12 inner steps| Rewrite
  Inspect -->|3 cluster steps| Outer
  Rewrite --> Runtime
  Runtime host@-->|fast| Host
  Runtime pod@-->|isolated| Podman
  Host --> Green
  Podman --> Green

%% play: Host inner-loop translation
%% skip Podman
%% YAML -> Inspect : linter.yaml | Read the real .tekton/linter.yaml recipe from the repository
%% Inspect -> Outer : skip 3 | Skip git-clone and cluster-secret tasks
%% Inspect -> Rewrite : keep 12 | Keep the 12 deterministic linter and convention steps
%% Rewrite -> Runtime : bind paths | Replace $(workspaces.source.path) and PaC {{ ... }} variables with local Git state
%% Runtime -> Host @host : native | Run exact CI commands and flags against the worktree
%% Host -> Green : 3.1s | All 12 steps finish in 3.1 seconds
%% play: Podman container translation
%% skip Host Outer
%% YAML -> Inspect : linter.yaml
%% Inspect -> Rewrite : keep 12
%% Rewrite -> Runtime : bind paths
%% Runtime -> Podman @pod : image | Launch podman run --rm -v $WORKSPACE:/workspace with each step's exact container image
%% Podman -> Green : isolated | Identical container image without Kubernetes overhead
```

<!-- notes:
1. Read `.tekton/*.yaml` as a recipe.
2. Separate inner-loop checks from outer-loop cluster plumbing.
3. Rewrite workspace and Pipelines-as-Code variables.
4. Run either natively on the host or inside Podman containers.
-->

<!-- slide: layout=columns reveal -->
## Step 1: Separate inner loop from outer loop

### Inner Loop (Run Locally)

- **Linters & formatting** — `codespell`, `shellcheck`, `yamllint`, `ruff`, `markdownlint`, `vale`.
- **Repo conventions** — Custom test naming, assertion rules, and `gitlint` commit checks.
- **Explicit hints** — Honors `(run-local)` in step descriptions or annotations.

<!-- column -->

### Outer Loop (Leave to Cluster)

- **Source & cache plumbing** — `git-clone`, `cache-fetch`, and `cache-upload` StepActions.
- **Cluster secrets & root mounts** — Steps needing cluster tokens or writing to `/usr/bin`.
- **Release & deploy** — Image signing, SBOMs, Goreleaser pushes, and Cloudflare deploys.

<!-- notes:
You don't need `git-clone` locally because your edited files are already on disk. And you don't need to build SBOMs or deploy docs on every code edit.
-->

## Step 2: Translate cluster variables to laptop paths

```bash {1-3|5-7|9-12|all}
# Tekton Workspace -> Local Git Worktree
WORKSPACE="$(git rev-parse --show-toplevel)"
cd "$WORKSPACE"

# Pipelines-as-Code PR Event -> Local Git Branch Range
EVENT_TYPE="pull_request"
BASE_SHA="$(git merge-base HEAD main)"

# Exact CI script from .tekton/linter.yaml (step: check-test-conventions)
files=$({ git diff --name-only "${BASE_SHA}" -- '*.go'; \
          git ls-files --others --exclude-standard -- '*.go'; } \
        | sort -u | grep -v '^vendor/' | grep '_test\.go$')
```

<!-- notes:
Tekton steps use `$(workspaces.source.path)` and Pipelines-as-Code uses `{{ body.pull_request.base.sha }}`. Rewriting those two things lets PR-only scripts run before a commit even exists.
-->

## Step 3: Real test on Pipelines-as-Code

- **What the agent wrote** — A new test `TestInnerLoop_ConfigValidation` using `t.Errorf` and a comment typo (`teh`).
- **Standard `go test`** — Passes in **0.01s** (`ok .../pkg/params`). A normal agent stops here and pushes!
- **`tkn-innerloop run`** — Runs `.tekton/linter.yaml` locally and catches **both** CI failures in **1.82s**.
- **Fix & re-verify** — The agent renames the test, switches to `assert.Equal`, and passes all **12/12 steps in 3.13s**.

<!-- notes:
This is real output from running `tkn-innerloop` on the `pipelines-as-code` repository's `.tekton/linter.yaml`.
-->

## Step 4: Ship the Skill inside the CLI

- **Self-contained CLI + Skill** — `tkn-innerloop skills install` drops `SKILL.md` straight into `.agents/skills/`.
- **Zero Kubernetes tax** — Works with native host binaries for speed or `podman run`/`docker run` for container parity.
- **Links** — [Watch Recorded Demo (MP4)](https://share.chmouel.com/public/jZRofFOy4r48bL-fgQtGUw/tkn-innerloop-demo.mp4) · [Agent Skill (`SKILL.md`)](https://share.chmouel.com/public/Kp0H0sAZIfR7NAipLx3THg/SKILL.md)

<!-- notes:
Following the Android CLI pattern we discussed in the meeting, the CLI can print or install the Agent Skill directly so coding agents always stay in sync.
-->

<!-- slide: bg=accent -->
## Step 5: When the rules are not enough, ask an agent

<!-- notes:
Rules cover linters and convention scripts. Real pipelines also carry remote StepActions, cluster secrets, caches and generated code, so we need an escape hatch.
-->

## The AI fallback: YAML in, Dockerfile out

```mermaid
flowchart LR
  YAML@{ img: "/icons/file-code.svg", label: "go.yaml", w: 48, h: 48, constraint: "on" }
  Rules@{ img: "/icons/filter.svg", label: "Rule engine", w: 48, h: 48, constraint: "on" }
  Odd@{ img: "/icons/warning.svg", label: "Too custom", w: 48, h: 48, constraint: "on" }
  Agent@{ img: "/icons/agent.svg", label: "pi + DeepSeek", w: 48, h: 48, constraint: "on" }
  Dockerfile@{ img: "/icons/container.svg", label: "Dockerfile", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/terminal.svg", label: "docker run", w: 48, h: 48, constraint: "on" }
  Green@{ img: "/icons/success.svg", label: "Lint + tests", w: 48, h: 48, constraint: "on" }

  YAML --> Rules
  Rules -->|lint, tests, conventions| Dockerfile
  Rules -->|remote steps, secrets, caches| Odd
  Odd --> Agent
  Agent --> Dockerfile
  Agent --> Run
  Dockerfile --> Green
  Run --> Green

%% play: Known steps, translated by rules
%% skip Odd Agent Run
%% YAML -> Rules : 15 steps | Read the pipeline once
%% Rules -> Dockerfile : known | Linters, tests and convention scripts are translated deterministically
%% Dockerfile -> Green : docker build | Multi-stage build reproduces the exact CI images and flags
%% play: Odd steps, translated by the agent
%% skip Dockerfile
%% YAML -> Rules : 15 steps
%% Rules -> Odd : unsupported | Remote StepActions, cluster secrets and caches cannot be rewritten by rules
%% Odd -> Agent : yaml | The whole pipeline is handed to a coding agent with no tools
%% wait 800 | About five seconds of thinking
%% Agent -> Run : script | A ready docker run script, or a Dockerfile with the same images
%% Run -> Green : pass | Same linters, same flags, no cluster
```

<!-- notes:
The fallback is opt-in: `--ai`. We demo it with `.tekton/go.yaml`, which needs gotestsum, a Go cache and a cluster token, so the agent writes a multi-stage Dockerfile for the unittest and lint steps. The model is chosen with `--model` or `TKN_INNERLOOP_AI_MODEL`; here it is DeepSeek V4.1 Flash on enmaas.
-->


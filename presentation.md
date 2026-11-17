---
title: The inner loop eats the pipeline
author: Chmouel Boudjnah
date: November 2026
style: keynote
theme: tokyo-night
accent: violet
font: modern
footer: tkn-innerloop · Pipelines as Code
numbers: true
transition: slide
autoplay: true
presentation: true
---

# The inner loop eats the pipeline

Running the real Tekton checks on a laptop, in seconds, without a cluster.

<!-- notes:
The whole idea in one line: the checks that decide whether your pull request is green should run where the agent is typing, not five minutes later on a cluster.
-->

<!-- slide: layout=quote bg=accent -->
> “Even if you solve 40% of it, that might be useful still.”

Siamak, on how much of the pipeline we could move into the inner loop.

<!-- notes:
This is the permission slip for the whole project. We are not rebuilding Kubernetes. We are taking the slice that never needed a cluster.
-->

## Agents pass locally, then fail in CI

- **Generic checks** — `go test`, `ruff`, markdown lint. They pass, so the agent commits.
- **Your checks** — the pipeline runs its own flags, its own shell scripts, its own conventions.
- **Five minutes later** — pods schedule, `git-clone` runs, and the PR goes red on a rule nobody ran.

<!-- notes:
Nothing is wrong with the agent here. It simply does not know your pipeline, and reading it out loud is the missing step.
-->

<!-- slide: layout=stat bg=muted -->
## The slice that never needed a cluster

**80%** of the pull-request steps only read your source tree.

<!-- notes:
On pipelines-as-code's own linter pipeline that is 12 of 15 steps: codespell, shellcheck, yamllint, ruff, markdownlint, vale, goreleaser check, the test-naming convention, the commit validator. All of them need files and a binary, nothing else.
-->

<!-- slide: layout=section bg=gradient -->
## The walkthrough

<!-- notes:
Four moves: read the pipeline, sort it, bind the cluster variables, run what is left. Then one escape hatch for the odd pipelines.
-->

## Two ways to find out you broke it

```mermaid
flowchart LR
  Agent@{ img: "/icons/agent.svg", label: "Agent", w: 48, h: 48, constraint: "on" }
  PR@{ img: "/icons/pull-request.svg", label: "Pull request", w: 48, h: 48, constraint: "on" }
  CI@{ img: "/icons/cluster.svg", label: "Tekton CI", w: 48, h: 48, constraint: "on" }
  Red@{ img: "/icons/fail.svg", label: "Red", w: 48, h: 48, constraint: "on" }
  Local@{ img: "/icons/terminal.svg", label: "Inner loop", w: 48, h: 48, constraint: "on" }
  Green@{ img: "/icons/success.svg", label: "Green", w: 48, h: 48, constraint: "on" }

  Agent --> PR --> CI
  CI red@--> Red
  Red --> Agent
  Agent loop@--> Local
  Local back@--> Agent
  Local pr@--> PR
  CI green@--> Green

%% play: The slow way
%% skip Local Green
%% Agent -> PR : push | The agent runs a generic test locally and pushes
%% PR -> CI : event
%% wait 1200 | Pods schedule, the repository is cloned again
%% CI -> Red @red : red | A convention script fails, five minutes after the push
%% Red -> Agent : logs | The agent starts over with a five-minute loop
%% play: The fast way
%% skip Red Green
%% Agent -> Local @loop : edit | The agent validates before it pushes
%% Local -> Agent @back : 1.8s fail | The exact CI script fails in under two seconds
%% Agent -> Local @loop : fix
%% Local -> PR @pr : green | Only green edits reach the pull request
%% PR -> CI : event
%% CI -> Green @green : green | The cluster pipeline agrees on the first try
```

<!-- notes:
Same agent, same code, same rules. The only difference is when the rules run. Space steps through both stories.
-->

## Read the pipeline as a recipe

```mermaid
flowchart LR
  YAML@{ img: "/icons/file-code.svg", label: ".tekton/*.yaml", w: 48, h: 48, constraint: "on" }
  Sort@{ img: "/icons/filter.svg", label: "Sort steps", w: 48, h: 48, constraint: "on" }
  Inner@{ img: "/icons/task.svg", label: "12 need files", w: 48, h: 48, constraint: "on" }
  Outer@{ img: "/icons/cloud.svg", label: "3 need a cluster", w: 48, h: 48, constraint: "on" }
  Bind@{ img: "/icons/config.svg", label: "Bind variables", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/check.svg", label: "Run it", w: 48, h: 48, constraint: "on" }

  YAML --> Sort
  Sort --> Inner
  Sort --> Outer
  Inner --> Bind --> Run

%% play: From YAML to a green light
%% YAML -> Sort : one file | The pipeline is read, never executed as Kubernetes objects
%% Sort -> Outer : skip | Cloning, caches, signing and deploys stay on the cluster
%% Sort -> Inner : keep | Linters, conventions and unit tests only need the source tree
%% Inner -> Bind : rewrite | Two cluster placeholders become local Git facts
%% Bind -> Run : 12 steps | The exact commands run against the working tree
```

<!-- notes:
The sorting is not magic and it is not a language model: descriptions, annotations, images and the shell scripts themselves carry the answer. Authors can also say it outright.
-->

<!-- slide: layout=columns reveal -->
## What stays, what goes

### Runs on your laptop

- **Spell and syntax** — codespell, shellcheck, yamllint, ruff, markdownlint, vale.
- **Conventions** — test naming, assertions, conventional commits.
- **Unit tests** — the same runner and the same flags as the pipeline.

<!-- column -->

### Stays on the cluster

- **Source and caches** — `git-clone`, `cache-fetch`, `cache-upload`. You already have the files.
- **Secrets** — tokens that only exist in the namespace.
- **Shipping** — signed images, SBOMs, releases, docs deploys.

<!-- notes:
The right column is not junk. It is simply not part of a code edit, and no amount of local cleverness should fake a signature.
-->

## Two placeholders do most of the work

```bash {1-2|4-5|all}
WORKSPACE="$(git rev-parse --show-toplevel)"   # was $(workspaces.source.path)
cd "$WORKSPACE"

EVENT="pull_request"                            # was {{ headers['X-Github-Event'] }}
BASE="$(git merge-base HEAD main)"              # was {{ body.pull_request.base.sha }}

codespell -d docs/content pkg test              # unchanged: the flags are the point
```

<!-- notes:
The commands are untouched on purpose. The flags are what CI disagrees with you about, so the local run has to use the same flags, not a friendlier version.
-->

<!-- slide: bg=muted -->
## What it caught on a real repo

A new test named `TestInnerLoop_ConfigValidation`, using `t.Errorf` and a `teh` typo.

- **`go test`** — green in 11 ms.
- **The Tekton steps** — two failures in 1.8 s: the naming convention and the spell checker.

<!-- notes:
This is the whole argument in one example. The generic check has no opinion about pascal case or spelling in comments, and your pipeline does.
-->

<!-- slide: layout=section bg=accent -->
## The escape hatch

<!-- notes:
Rules cover the common shape. Real pipelines also carry remote step actions, cluster secrets, generated caches and custom images, so there has to be a way out that is not "give up".
-->

## When the rules run out, ask an agent

```mermaid
flowchart LR
  YAML@{ img: "/icons/file-code.svg", label: "go.yaml", w: 48, h: 48, constraint: "on" }
  Rules@{ img: "/icons/filter.svg", label: "Rules", w: 48, h: 48, constraint: "on" }
  Odd@{ img: "/icons/warning.svg", label: "Too custom", w: 48, h: 48, constraint: "on" }
  Agent@{ img: "/icons/agent.svg", label: "pi + DeepSeek", w: 48, h: 48, constraint: "on" }
  File@{ img: "/icons/container.svg", label: "Dockerfile", w: 48, h: 48, constraint: "on" }
  Done@{ img: "/icons/success.svg", label: "Same 12 checks", w: 48, h: 48, constraint: "on" }

  YAML --> Rules
  Rules known@--> Done
  Rules --> Odd
  Odd --> Agent
  Agent --> File
  File --> Done

%% play: Rules first
%% skip Odd Agent
%% YAML -> Rules : 15 steps | A known pipeline is translated without a model in the loop
%% Rules -> Done @known : exact flags | Same images, same commands, nothing inferred
%% play: Then the agent
%% skip Done
%% YAML -> Rules : 15 steps
%% Rules -> Odd : stuck | Remote steps, cached toolchains and cluster tokens
%% Odd -> Agent : whole yaml | One prompt, no tools, five seconds of thinking
%% wait 700 | The agent only reads and writes
%% Agent -> File : dockerfile | A multi-stage build with the pipeline's own images
%% File -> Done : build | The checks run the same way, with or without a cluster
```

<!-- notes:
Opt-in with --ai, model chosen with --model or an environment variable. No tools means it cannot wander off and edit your repo; it reads the YAML and writes the artifact.
-->

## Say it in the pipeline, not in a prompt

- **`(run-local)`** in a step's description — keep this in the fast loop.
- **`(skip-local)`** — runnable, but pointless on every edit. Signing, SBOMs, deploys.
- **No marker** — the classifier guesses from the image and the script, and says why.

<!-- notes:
The author already knows which steps are release chores. Writing it in the file beats teaching every agent the same folklore, and it survives when the pipeline changes.
-->

<!-- slide: bg=muted -->
## What I would build next

- **A local transcript** — feed past runs back in so translations get sharper instead of generic.
- **Ship it in the CLI** — `tkn local` with the skill embedded, the way Android ships its skills.
- **Evals, not vibes** — a corpus of pipelines as the regression suite for every classifier change.

<!-- notes:
The first two are cheap. The third is what turns a demo into something a customer can trust unattended.
-->

## Try it

- **`tkn-innerloop inspect .tekton/linter.yaml`** — see what is runnable before you run anything.
- **`tkn-innerloop run`** — the exact steps, in seconds.
- **`--ai`** — a Dockerfile when the rules get stuck.

Code and the agent skill: the `tkn-innerloop` repository.

<!-- notes:
If someone only remembers one command, it should be inspect: it turns an argument about classification into a table you can both read.
-->

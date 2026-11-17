---
title: Tekton checks in the local edit loop
author: Pipelines-as-Code
date: November 2026
style: keynote
theme: tokyo-night
accent: violet
font: modern
footer: Tekton inner-loop PoC
numbers: true
presentation: true
transition: slide
autoplay: true
---

# Tekton checks in the local edit loop

An agent converts the pipeline with repository context. A saved plan runs the selected checks after each code edit.

<!-- notes:
The conversion happens once per set of pipeline inputs. The repeated edit loop uses a deterministic runner and does not call a model.
-->

## Generic checks miss repository rules

`go test` can pass while the pipeline rejects a test name or a spelling mistake.

Read the actual CI recipe, including its helper scripts and configuration.

## Convert once with repository context

```mermaid
flowchart LR
  Repo@{ img: "/icons/repository.svg", label: "YAML and helpers", w: 48, h: 48, constraint: "on" }
  Agent@{ img: "/icons/agent.svg", label: "Read and propose", w: 48, h: 48, constraint: "on" }
  Plan@{ img: "/icons/document.svg", label: "Review edits", w: 48, h: 48, constraint: "on" }
  Validate@{ img: "/icons/check.svg", label: "Validate plan", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/container.svg", label: "Run local scope", w: 48, h: 48, constraint: "on" }
  Repo --> Agent --> Plan --> Validate --> Run

%% play: Initial conversion
%% Repo -> Agent | The agent reads the pipeline, Makefiles and referenced scripts with read-only tools
%% Agent -> Plan : exact edits | Every source step gets a run, skip or unresolved decision
%% Plan -> Validate | Review the changes; the CLI checks hashes, provenance and syntax
%% Validate -> Run | The saved plan executes inside the declared source images
```

## Keep the source script visible

```diff
-files=$(git diff --name-only "${base_sha}..HEAD" -- '*.go')
+files=$({ git diff --name-only "$BASE_SHA" -- '*.go';
+          git ls-files --others --exclude-standard -- '*.go'; })
```

The plan records exact replacements and reasons. Review changes to file selection and PR context.

<!-- notes:
This slide abbreviates the file filters for readability. The execution plan preserves the full script and shows the actual unified diff with validate --diff.
-->

## Unknown requirements stay unresolved

- Every source step has a decision and a reason.
- Runnable steps retain their image and declare setup dependencies.
- Unresolved steps block their selected scope.
- Syntax and provenance checks cannot prove equivalent behavior.

## Run the generated artifact

```sh
tkn-innerloop generate .tekton/linter.yaml \
  --steps codespell,check-test-conventions -o /tmp/plan.json
tkn-innerloop validate /tmp/plan.json --diff
tkn-innerloop run /tmp/plan.json --base main
```

Docker executes the scripts. Generation alone does not establish that they work.

<!-- slide: bg=accent -->
## Two checks from the real PaC pipeline

The fixture passes `go test`. Codespell rejects the typo; the convention check rejects the test name.

Fix the fixture and rerun the same plan. The `t.Errorf` rule only warns.

## Reuse the plan after code edits

```mermaid
flowchart LR
  Edit@{ img: "/icons/code.svg", label: "Edit source", w: 48, h: 48, constraint: "on" }
  Hash@{ img: "/icons/fingerprint.svg", label: "Check inputs", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/container.svg", label: "Run saved plan", w: 48, h: 48, constraint: "on" }
  Result@{ img: "/icons/report.svg", label: "Scoped result", w: 48, h: 48, constraint: "on" }
  Convert@{ img: "/icons/agent.svg", label: "Regenerate", w: 48, h: 48, constraint: "on" }
  Edit --> Hash
  Hash valid@-->|unchanged| Run
  Hash stale@-->|changed| Convert
  Convert --> Hash
  Run --> Result --> Edit

%% play: Code edit
%% skip Convert
%% Edit -> Hash | Pipeline and listed helper/configuration hashes remain unchanged
%% Hash -> Run @valid : no model
%% Run -> Result | Report the executed checks and skipped or unresolved scope
%% Result -> Edit
%% play: Pipeline input changed
%% skip Run Result
%% Edit -> Hash
%% Hash -> Convert @stale | A changed pipeline or listed helper invalidates the plan
%% Convert -> Hash : reviewed plan
```

## Report the scope and setup costs

This demo selects 2 of 15 steps. The other 13 stay outside its scope.

Conversion and image pulls are setup costs. Reruns use cached images and still execute the setup commands in their scripts.

## Cluster CI still owns the rest

Local validation covers the executed checks. Cluster services, releases and other excluded requirements still need CI.

Moving image tags, downloads and omitted input dependencies can change behavior between runs.

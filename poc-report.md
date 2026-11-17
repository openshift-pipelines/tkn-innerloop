# Tekton checks in the local edit loop

A coding agent reads a Tekton PipelineRun and its repository helpers, proposes a
local execution plan, and explains the changes. After reviewing that plan, you
can reuse it in Docker or Podman after code edits without another model call.

The demo selects two checks from Pipelines-as-Code's real linter pipeline. A
fixture passes `go test`, but the spelling and test-name checks reject it. Fixing
the fixture makes the same saved plan pass. The remaining 13 steps are outside
this demo's scope, and cluster CI is still required.

## Why conversion needs repository context

A YAML file can refer to a Makefile target, a helper that downloads a tool, or a
remote task with additional prerequisites. Command names alone do not explain
whether those requirements can be satisfied locally. Generation therefore gives
the agent read-only file tools and leaves unknown requirements unresolved.

The agent proposes exact replacements in the original script. The CLI applies
those replacements, retains the source image, and saves the result with the
pipeline and declared helper/configuration hashes. Review includes the actual
script diff, ordinary environment values, working directory and dependencies.

```mermaid
flowchart LR
  Repo@{ img: "/icons/repository.svg", label: "Pipeline and helpers", w: 48, h: 48, constraint: "on" }
  Agent@{ img: "/icons/agent.svg", label: "Propose edits", w: 48, h: 48, constraint: "on" }
  Plan@{ img: "/icons/document.svg", label: "Review plan", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/container.svg", label: "Execute scope", w: 48, h: 48, constraint: "on" }
  Edit@{ img: "/icons/code.svg", label: "Edit code", w: 48, h: 48, constraint: "on" }
  Repo --> Agent --> Plan --> Run --> Edit
  Edit --> Run

%% play: Convert and validate
%% Repo -> Agent | The agent reads repository context and accounts for every source step
%% Agent -> Plan | Review adaptations, skipped checks and unresolved requirements
%% Plan -> Run | The CLI validates the artifact and actually executes its scripts
%% Run -> Edit
%% Edit -> Run : same plan | Repeated checks use no model call; changed conversion inputs require regeneration
```

## What a passing result means

A result covers the checks that executed. The CLI reports skipped and unresolved
scope alongside the pass count. It rejects unknown step selections, empty scope,
unresolved selected steps, missing declared prerequisites and stale inputs.

Structural checks catch missing source steps, altered image references,
undeclared script changes, invalid shell syntax and unbound Tekton/PaC
placeholders. They cannot prove equivalent behavior or safe commands. Review
and an actual execution remain necessary.

Conversion and image pulls are setup costs. The recording uses cached images;
reruns still execute setup commands present in the source scripts. Mutable image
tags, network downloads and omitted input dependencies limit reproducibility.
There is no general pipeline coverage percentage or fixed runtime claim.

<details>
<summary>Commands, scope and validation details</summary>

```sh
tkn-innerloop generate .tekton/linter.yaml \
  --steps codespell,check-test-conventions -o /tmp/linters.plan.json
tkn-innerloop validate /tmp/linters.plan.json --diff
tkn-innerloop run /tmp/linters.plan.json --runtime docker --base main
```

The plan accounts for all 15 source steps, with 2 runnable and 13 skipped for the
requested scope. The source images are
`registry.access.redhat.com/ubi9/python-312` and `docker.io/golang:1.27`.
The assertion rule emits a warning; the invalid test name causes failure.

The runner supplies `WORKSPACE=/workspace` in containers and computes `BASE_SHA`
with `git merge-base HEAD <base-ref>` at execution. The changed-file adaptation
needs both tracked changes against that base and untracked non-ignored files.
Declared setup dependencies must precede their users and cannot be omitted from
a selected scope. Secret names require explicit forwarding with `--env NAME`.

The PoC supports embedded PipelineRuns, shell scripts and shell-rendered command
steps. It does not emulate Tekton scheduling, result propagation, cluster
services or arbitrary volume layouts. A conversion must account for those
requirements or leave the affected steps unresolved. pi's file tools are
read-only, but are not a filesystem sandbox. Executed scripts have write access
to the mounted worktree.

Verification includes 28 passing regression tests, Ruff, ShellCheck and aniflow
checks. The recording verifies actual command exit statuses: `go test` passes
with the fixture, both pipeline checks fail separately, and the fixed fixture
passes the same 2-check plan in Docker. It shows plain CLI commands, the generated
diff and actual results, with a jazz soundtrack. Typing and command-execution
waits are cut and disclosed in the opening. On-screen runner times include setup
commands; conversion and image pulls remain separate costs.

```sh
python3 -m unittest discover -s tests -v
ruff check bin/tkn-innerloop tests
shellcheck demo/*.sh
aniflow --check README.md poc-report.md presentation.md
PAC_REPO=~/git/pac/main ./demo/record.sh
```

The recording helper refuses to overwrite an existing fixture. Cleanup only
removes its own unchanged fixture. Regenerate the plan against another checkout
or changed pipeline inputs. Code is in `~/git/work/tkn-innerloop` (local repository,
no origin configured).

</details>

## Public links

- [Presentation](https://share.chmouel.com/public/ESO11s7v6V3aIVXkas5XTA/presentation.md#slide=1)
- [Recorded demo](https://share.chmouel.com/public/IcLisUXEiQsgfT1N7tQzsA/tkn-innerloop-demo.mp4)
- [Agent skill](https://share.chmouel.com/public/bCKjBFak-pNzlQabwE7dJQ/SKILL.md)

Anyone with these links can read them. Public links expire after 30 days.

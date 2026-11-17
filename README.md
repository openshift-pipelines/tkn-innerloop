# Tekton checks in the local edit loop

`tkn-innerloop` asks a coding agent to turn a Tekton PipelineRun into a local
execution plan. You review the changes once, then run the saved plan in Docker
or Podman after code edits. Kubernetes stays in CI.

The agent reads repository helpers as well as YAML. It proposes exact edits to
source scripts and explains which steps run, which are skipped, and which still
have unresolved requirements. The CLI handles validation and execution.

## Try it

Install Python 3.10+, PyYAML, the [pi coding agent](https://github.com/earendil-works/pi),
and Docker or Podman. Authenticate a provider in pi. Conversion defaults to Haiku;
use `--model` or `TKN_INNERLOOP_AI_MODEL` to choose another model.

```sh
python3 -m pip install -r requirements.txt
export PATH="$PWD/bin:$PATH"
cd /path/to/your/repository

tkn-innerloop inspect .tekton/linter.yaml
tkn-innerloop generate .tekton/linter.yaml \
  --steps codespell,check-test-conventions -o /tmp/linters.plan.json
tkn-innerloop validate /tmp/linters.plan.json --diff
tkn-innerloop run /tmp/linters.plan.json --runtime docker --base origin/main
```

The step names above are the PaC demo's scope; choose names from your pipeline.
Use the full step ID from `inspect` when a step name is ambiguous. Files containing
multiple YAML documents use `pipelinerun/task/step` IDs; a single PipelineRun uses
`task/step`. Empty YAML documents are ignored. Generation supports embedded
PipelineRuns; pipeline references must be resolved first. Remote task/step
references appear in the inventory and cannot be executed as unresolved refs.

## Conversion and execution

```mermaid
flowchart LR
  Source@{ img: "/icons/repository.svg", label: "Pipeline and helpers", w: 48, h: 48, constraint: "on" }
  Agent@{ img: "/icons/agent.svg", label: "Read and propose", w: 48, h: 48, constraint: "on" }
  Plan@{ img: "/icons/document.svg", label: "Review the plan", w: 48, h: 48, constraint: "on" }
  Check@{ img: "/icons/check.svg", label: "Validate inputs", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/container.svg", label: "Run local scope", w: 48, h: 48, constraint: "on" }
  Edit@{ img: "/icons/code.svg", label: "Edit source", w: 48, h: 48, constraint: "on" }
  Source --> Agent --> Plan --> Check --> Run --> Edit
  Edit --> Check

%% play: Convert once
%% Source -> Agent | The agent reads YAML, Makefiles and referenced helpers with read-only tools
%% Agent -> Plan : edits | Review exact script changes, prerequisites and excluded checks
%% Plan -> Check | The CLI verifies source hashes, image provenance and script structure
%% Check -> Run | Execute the selected checks in their declared images
%% play: Reuse after editing
%% skip Source Agent Plan
%% Run -> Edit
%% Edit -> Check | Code edits reuse the saved plan; changed pipeline inputs require regeneration
%% Check -> Run : no model | Run the same local scope again
```

Every source step needs a decision. Unknown steps start unresolved; there is no
command-name allowlist or deployment blacklist deciding what executes. Generation
uses pi's `read`, `grep`, `find` and `ls` tools. Read-only tools are not a filesystem
sandbox. Model selection uses `--model` or `TKN_INNERLOOP_AI_MODEL`.

Conversion sends the requested scope directly to the agent and disables pi's
startup update checks. Only referenced helpers and configuration need further
reads. Terminal output shows live progress and elapsed time, with colored step
badges and results. Redirected output stays free of ANSI styling; `NO_COLOR`
disables color. Use `validate --diff` for all decisions and adaptation details.

`validate --diff` shows the original and adapted scripts. Script edits must match
source fragments exactly once. Runnable steps retain their source image and
specify their shell, environment, working directory and dependencies. The runner
rejects stale input hashes, unknown selections, missing declared setup steps,
unbound Tekton/PaC placeholders and unresolved steps in the selected scope.

Review generated commands before executing them. Structural validation cannot
prove equivalent behavior or safe commands. The runner executes scripts with a
writable worktree; it does not emulate Tekton's scheduler, services or result
propagation. The agent must account for those requirements or leave a step
unresolved. The declared input list determines which helper/configuration changes
invalidate the plan.

## Repeat a check

```sh
tkn-innerloop run /tmp/linters.plan.json --steps check-test-conventions --base origin/main
tkn-innerloop run /tmp/linters.plan.json --runtime podman --base origin/main
tkn-innerloop skills install
```

Docker is the default. Both container runtimes mount the worktree at `/workspace`,
override the image entrypoint with the declared shell, and pass scripts via stdin.
`BASE_SHA` uses `git merge-base HEAD <base-ref>` at execution; the default ref is
`main`. Forward declared secrets explicitly with `--env NAME`. No credentials
are fetched automatically.

`--runtime host` executes the same scripts with host tools, setup commands and
environment. Use it only after reviewing those commands. Source image tags and
network downloads can change tool versions, including during container execution.

A successful run reports the executed scope and skipped/unresolved counts.
Cluster CI still covers the remaining pipeline requirements.

## Demo and verification

The recording uses two checks from PaC's real `.tekton/linter.yaml`: `codespell`
and `check-test-conventions`. It shows plan generation, review, container
execution, two injected failures, and a passing rerun after the fixture is fixed.
Plain CLI commands are shown, with typing and command-execution waits cut.
The opening states this edit; results and on-screen timings come from real runs.
The other 13 steps are explicitly outside this demo scope. The `t.Errorf` check
only warns; the failing conditions are the spelling mistake and test-name rule.

Conversion and image pulls are separate setup costs. The recording uses cached
source images; measured rerun times include any setup still in the scripts.
There is no general coverage percentage or fixed runtime claim.

```sh
python3 -m unittest discover -s tests -v
ruff check bin/tkn-innerloop tests
shellcheck demo/*.sh
PAC_REPO=~/git/pac/main ./demo/record.sh
aniflow --check README.md poc-report.md presentation.md
```

`demo/record.sh` generates the plan during recording and checks expected command
exit statuses. It refuses to overwrite an existing fixture, cleans up its own
unchanged fixture on exit, and leaves an execution plan beside the video.
Recordings and slide previews also go under `~/tmp/sharei/tkn-innerloop/shots/`.

## Public links

- [Report](https://share.chmouel.com/public/mgR03lI6LCmhiLLCnnY8VQ/poc-report.md)
- [Presentation](https://share.chmouel.com/public/ESO11s7v6V3aIVXkas5XTA/presentation.md#slide=1)
- [Recorded demo](https://share.chmouel.com/public/IcLisUXEiQsgfT1N7tQzsA/tkn-innerloop-demo.mp4)
- [Agent skill](https://share.chmouel.com/public/bCKjBFak-pNzlQabwE7dJQ/SKILL.md)

Anyone with these links can read them. Public links expire after 30 days.

## Publishing the deck

`presentation.md` references pictures in `art/` (drawn by `art/make_art.py`). A public sharei page only
loads inline images, so build the inlined copy first, then upload it under the deck's name:

```sh
python3 art/build.py
sharec dist/presentation.public.md -p <project> -n presentation.md --replace -y --public-ttl 0
```

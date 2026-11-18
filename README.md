# Tekton checks in the local edit loop

`tkn-innerloop` asks a coding agent to turn a Tekton PipelineRun or Pipeline into a local
execution plan, then runs the plan in Podman or Docker after code edits.
Kubernetes stays in CI. One command does it all:

```sh
tkn-innerloop run .tekton/go.yaml
```

The first run converts the pipeline and caches the plan; later runs reuse it with
no agent call until the pipeline or a helper it reads changes.

The agent reads repository helpers as well as YAML. It proposes exact edits to
source scripts and explains which steps run, which are skipped, and which still
have unresolved requirements. The CLI handles validation and execution.

## Try it

Install Python 3.11+, the [pi coding agent](https://github.com/earendil-works/pi),
and Docker or Podman. Authenticate a provider in pi. Conversion defaults to Haiku;
use `--model` or `TKN_INNERLOOP_AI_MODEL` to choose another model. Thinking is
off unless the model name ends in a pi level such as
`github-copilot/claude-sonnet-5.5:minimal`; some models refuse to turn it off.

```sh
python3 -m pip install -e .
# or run from the checkout: python3 -m pip install pyyaml; export PATH="$PWD/bin:$PATH"
cd /path/to/your/repository

tkn-innerloop run .tekton/go.yaml                  # convert if needed, then run
tkn-innerloop run .tekton/linter.yaml --steps codespell,yamllint
tkn-innerloop run                                  # when the repository has one pipeline
```

`run` picks the steps itself: steps that point at an external reference
(`git-clone`), steps provided by the local checkout, `finally` tasks, steps marked `(skip-local)` and steps that
push, deploy or post to an API stay on the cluster. tekton-caches steps are replaced
by a local cache directory (see below). Add patterns for your own
publishing tools in `.tekton-innerloop.yaml` at the repository root:

```yaml
cluster_only:
  - '\bwrangler\s+deploy'
```

The entries are regular expressions added to the built-in list. The agent decides the rest.
`--steps` replaces that choice. The plan is cached in
`${XDG_CACHE_HOME:-~/.cache}/tkn-innerloop/<repo>-<hash>/`, outside the working tree,
so nothing in your repository changes and repository-wide linters never see it.

**`run` executes agent-edited scripts without a review stop.** To read them first:

```sh
tkn-innerloop inspect .tekton/linter.yaml
tkn-innerloop generate .tekton/linter.yaml \
  --steps codespell,check-test-conventions -o /tmp/linters.plan.json
tkn-innerloop validate /tmp/linters.plan.json --diff
tkn-innerloop run /tmp/linters.plan.json
```

Model choice matters. On 10 conversions of one step (`go/unittest`), `haiku`, the
default, was valid on the first try every time (6.5s on average). DeepSeek V4.1
Flash through enmaas needed a retry 5 times and failed 3 times, usually from
unescaped quotes in shell edits. Malformed answers are retried twice with the exact
error; a step the agent leaves unresolved is never retried.

The step names above are the PaC demo's scope; choose names from your pipeline.
Use the full step ID from `inspect` when a step name is ambiguous. Files containing
multiple YAML documents use `pipelinerun/task/step` IDs; a single PipelineRun uses
`task/step`. Empty YAML documents are ignored.

## Pipelines, references and Pipelines-as-Code

A file can hold PipelineRuns, or Pipelines when it has no PipelineRun, along with
any Tasks and StepActions they use; ConfigMaps, Secrets and other resources next
to them are ignored. A `pipelineRef`, `taskRef` or step `ref` by name is looked up
in the repository's tracked and untracked YAML files (git-ignored ones are
skipped) and inlined, so a PipelineRun pointing at `tekton/pipeline.yaml` and Tasks under
`tekton/tasks/` works as is. Those files become plan inputs: editing a Task makes
the plan stale. A missing `pipelineRef`, or a remote one other than a bundle, is
an error, since there is no step list to work from. A Task or StepAction that is
missing, defined twice, a ClusterTask, or fetched by another resolver (hub, git)
stays an unresolved reference, and `inspect` says why.

Tekton bundles (`resolver: bundles`) are fetched with
[skopeo](https://github.com/containers/skopeo), using your registry login, for
`pipelineRef`, `taskRef` and step `ref`. Each object is pinned to its image
digest; a tag-only reference is looked up once per run. Objects are cached in
`${XDG_CACHE_HOME:-~/.cache}/tkn-innerloop/bundles/`, so later runs work offline,
and they are plan inputs like local files. `TKN_INNERLOOP_BUNDLE_DIR` points at a
mirror with the same layout, checked before the cache. When skopeo is missing or
the fetch fails, a Task stays a reference with the reason and a bundle
`pipelineRef` is an error.

In Konflux pipelines the clone task and the `build-trusted-artifacts` steps only
recreate the source checkout, so they are marked as provided by the local
checkout and never run. The directory they restore the source into
(`/var/workdir/source`) becomes `$WORKSPACE` in the other steps. Parameter values reach the agent with the task's defaults;
the agent applies them in its script edits. It also evaluates `when` guards it can
decide from parameters, and has to loop over a `matrix` task's combinations or
leave it unresolved (`inspect` marks those tasks).

The `cache-fetch` and `cache-upload` StepActions from
[tekton-caches](https://github.com/openshift-pipelines/tekton-caches) are skipped
locally. The runner recognises dedicated uses of `/ko-app/cache` and upstream
cache scripts in known cache images. For unresolved references, it checks the
action name, URL or path. Steps in the same
PipelineRun that use the same image and workspace directory can share a persistent
path such as `$CACHES/go-build-cache-<hash>`. Go cache directories configured
through `GOCACHE`, `GOMODCACHE`, and `GOLANGCILINT_CACHE`, and npm download
caches configured through `npm_config_cache` or `NPM_CONFIG_CACHE`, can share
storage across images with the same runtime UID. Each cache directory must
serve one tool. Mixed-use paths and caches used by different container UIDs
stay at their original paths. Overlapping caches,
relative paths, `node_modules`, caches containing a step's working directory,
and cache paths in scripts with here-documents or command substitutions also
stay in place.

Remove the local caches with `tkn-innerloop cache clean`.

If Docker owns files the host user cannot delete, cleanup uses an Alpine
container and may pull the image.

Without an argument, `run` looks in `.tekton/`, preferring PipelineRuns over
Pipelines. It searches the whole repository only when `.tekton/` has no pipeline
at all; if `.tekton/` has pipelines but none can run locally, it lists each one
with the reason.

A file with `pipelinesascode.tekton.dev/` annotations turns on Pipelines-as-Code
handling. `{{ revision }}`, `{{ source_branch }}`, `{{ target_branch }}` and
`{{ event_type }}` become `$HEAD_SHA`, `$HEAD_REF`, `$BASE_REF` and
`pull_request`; the agent binds other placeholders or leaves the step unresolved.
When `tkn-pac` is installed, the file first goes through `tkn pac resolve`, which
also fetches tasks named in `pipelinesascode.tekton.dev/task` annotations. Its
output is cached next to the plan and reused until an input changes or
`--refresh`. If `tkn pac resolve` fails, local resolution takes over with a
warning. Elsewhere `{{ … }}` is left alone, since Go templates are ordinary script
text.

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
tkn-innerloop run .tekton/linter.yaml --steps check-test-conventions
tkn-innerloop run .tekton/go.yaml --runtime docker --refresh
tkn-innerloop run /tmp/linters.plan.json        # a saved plan skips conversion
tkn-innerloop skills install
```

`--runtime auto` is the default: Podman if installed, else Docker. Both mount the
worktree at `/workspace`, override the image entrypoint with the declared shell, and
pass scripts via stdin. Docker's daemon runs as root, so after each step the runner
gives files created in your checkout back to you; Podman already writes as you.
`BASE_SHA` uses `git merge-base HEAD <base>`, where the base is origin's default
branch, else `main`, else `master` (override with `--base`). `BASE_REF` is that
branch name without the remote, `HEAD_SHA` and `HEAD_REF` the current commit and
branch. Each is computed only when a selected step uses it. `RESULTS` is an empty
directory outside the checkout (`/results` in containers), shared by the steps of
one run and removed afterwards: task results are written there and read by the
tasks that consume them. `CACHES` is the persistent tekton-caches directory
(`/caches` in containers), under
`${XDG_CACHE_HOME:-~/.cache}/tkn-innerloop/<repo>-<hash>/caches/`, and is only
mounted when a step uses it. Host runs and each container runtime and image ID
have separate storage. Go and npm caches shared across images use storage keyed
by tool, container runtime, and UID.

Steps keep running after a failure unless a later step depends on the failed one.
A failure under `onError: continue` does not fail the run. On a step it is
ignored; on a pipeline task the rest of that task's steps are skipped, as in
Tekton, and the tasks after it still run. A container that fails to start still
fails the run.
Exit codes: `0` every selected check passed, `1` a check failed, `2` all passed
but some steps stayed unresolved, so the result is incomplete.

Forward secrets explicitly with `--env NAME`. `required_env` blocks the run without
it; `optional_env` (a token that only raises a rate limit, say) is forwarded only
when you pass it and never blocks. No credentials are fetched automatically. The
runner notes when an image tag now resolves to a different image than last time.

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

The code lives in `src/tkn_innerloop/`; `bin/tkn-innerloop` runs it straight from
the checkout. `make test` runs the unit tests and `make lint` runs ruff and
shellcheck. `.tekton/ci.yaml` is this repository's own pipeline, and the
pre-commit hook (`pre-commit install`) runs it locally with tkn-innerloop before
each commit; GitHub Actions runs the same lint and tests.

```sh
make lint test
make innerloop                                     # run .tekton/ci.yaml locally
PAC_REPO=~/git/pac/main ./demo/record.sh
aniflow --check README.md poc-report.md presentation.md
```

`tests/corpus/` holds 17 real PipelineRuns and Pipelines from Pipelines-as-Code,
Konflux, Tekton Chains, tektoncd/pipeline examples and tektoncd/plumbing, each
pinned to a commit. The unit tests check what inventory and the automatic scope
return for each one, and each case's README describes the plan a good model
should produce. `hack/corpus-check` asks the model for plans of each case,
checks them against per-case expectations, and with `--execute` runs some of them
on the source repository, then again with a planted failure that must be caught.
`tests/corpus/README.md` explains both and how to add a case.

`demo/record.sh` generates the plan during recording and checks expected command
exit statuses. It refuses to overwrite an existing fixture, cleans up its own
unchanged fixture on exit, and leaves an execution plan beside the video.
Recordings and screenshots are written to `demo/out/` (git-ignored); upload them with `sharec`.

## Public links

- [Report](https://share.chmouel.com/public/mgR03lI6LCmhiLLCnnY8VQ/poc-report.md)
- [Presentation](https://share.chmouel.com/public/YUzvD7r-N9fGd6w4V5Urdg/presentation.md#slide=1)
- [Recorded demo](https://share.chmouel.com/public/IcLisUXEiQsgfT1N7tQzsA/tkn-innerloop-demo.mp4)

Anyone with these links can read them. Public links expire after 30 days.

## Publishing the deck

`presentation.md` references pictures in `art/` (drawn by `art/make_art.py`). A public sharei page only
loads inline images, so build the inlined copy first, then upload it under the deck's name:

```sh
python3 art/build.py
sharec dist/presentation.public.md -p <project> -n presentation.md --replace -y --public-ttl 0
```

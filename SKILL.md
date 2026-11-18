---
name: tekton-innerloop
description: Run a repository's Tekton checks locally. One command converts a PipelineRun or Pipeline with an agent (cached), then runs it in Podman or Docker without a Kubernetes cluster; use generate and validate to review the plan first.
---

# Tekton inner-loop validation

Use the repository's Tekton commands to check local edits. An agent decides what
can run locally and explains the adaptations. The CLI validates the resulting
plan and executes it without calling a model during the edit loop.

## The one command

```sh
tkn-innerloop run .tekton/go.yaml            # or just `run` if the repository has one pipeline
tkn-innerloop run .tekton/linter.yaml --steps codespell,yamllint
```

`run` chooses the local scope, converts when there is no current plan, caches the
plan outside the working tree, and executes it. Exit `0`: passed.
`1`: a check failed. `2`: passed, but some steps were unresolved, so tell the user
the result is incomplete. It does not stop for review, so read the plan first
(`generate`, `validate --diff`, below) when the scripts matter. Run it after every
edit and report the exit code and the failing check output.

## Convert once (to review first)

Inspect the source inventory. Every step starts unresolved; inspection does not
infer that a command is safe or runnable from its name.

All PipelineRuns in a multi-document YAML file are inspected; a file without a
PipelineRun uses its Pipelines. Local `pipelineRef` and `taskRef` names are
inlined from the repository's YAML files and those files become plan inputs.
Tekton bundle references (`resolver: bundles`) are fetched with skopeo, cached
and inlined the same way. A missing or other remote `pipelineRef` is an error.
Missing, duplicate, ClusterTask and other resolver task references stay unresolved;
`inspect` shows the reason. Konflux clone and trusted-artifact steps are provided
by the local checkout and never run. tekton-caches `cache-fetch`/`cache-upload`
steps are skipped locally. Explicit cache paths can use `$CACHES/<name>` when
the consumers use the same image and workspace directory within a PipelineRun.
`GOCACHE`, `GOMODCACHE`, `GOLANGCILINT_CACHE`, and npm's explicit
`npm_config_cache` or `NPM_CONFIG_CACHE` can share storage across images with
the same runtime UID. Each shared cache directory serves one tool. Other cross-image caches, relative paths,
`node_modules`, and caches containing a working directory stay in place. Cache paths in scripts
with here-documents or command substitutions need explicit adaptation.
`tkn-innerloop cache clean` removes persistent local caches. Use the full ID
shown by `inspect` to select ambiguous names: `pipelinerun/task/step` for multiple
PipelineRuns, or `task/step` for a single one. Preserve follow-up annotations and
cross-PipelineRun result requirements during conversion; leave them unresolved
when the local plan cannot account for them.

```sh
tkn-innerloop inspect .tekton/linter.yaml
tkn-innerloop generate .tekton/linter.yaml \
  --steps codespell,check-test-conventions -o /tmp/linters.plan.json
```

Generation uses `pi` with only `read`, `grep`, `find` and `ls`. It can inspect
Makefiles, helper scripts and tool configuration. Select its model with
`--model` or `TKN_INNERLOOP_AI_MODEL` (default: Haiku); a `:minimal` style
suffix sets pi's thinking level, which is off otherwise. The agent receives the
requested source inventory directly and inspects only relevant referenced
helpers/configuration. Startup update checks are disabled. These tools permit repository discovery;
they do not constitute a filesystem sandbox.

The plan accounts for every source step as `run`, `skip` or `unresolved`. A
requested subset is explicit scope, and other checks remain recorded as skipped.
Keep the source image, commands and flags. Propose exact script edits with a
reason for each one; the CLI applies them to the original script. Preserve
ordinary environment values, working directories and shell behavior. Record
prerequisites in `depends_on` and configuration/helper paths in `inputs`.

Apply Tekton `$(params.x)` values from the pipeline task's params or the Task
defaults through script edits. For files with `pipelinesascode.tekton.dev/`
annotations, `{{ revision }}`, `{{ source_branch }}`, `{{ target_branch }}` and
`{{ event_type }}` are already bound to `$HEAD_SHA`, `$HEAD_REF`, `$BASE_REF` and
`pull_request`; bind any other `{{ }}` placeholder or leave the step unresolved.
With `tkn-pac` installed, those files first go through `tkn pac resolve`, which
fetches annotation tasks; the cached output is a plan input. Add repository
publishing commands to `cluster_only` regexes in `.tekton-innerloop.yaml`.

Treat checkout as the mounted worktree. Explain optional cache removal. Leave
unresolved references, services, volume requirements and unsupported interpreters
unresolved unless their requirements can be accounted for. Skip deployment,
release and signing actions. Honor `(skip-local)` in descriptions/display names.
A `(run-local)` hint does not satisfy missing prerequisites.

## Review and execute

```sh
tkn-innerloop validate /tmp/linters.plan.json --diff
tkn-innerloop run /tmp/linters.plan.json --runtime docker --base origin/main
```

Review the script diff, images, adaptations, environment and skipped/unresolved
steps before execution. Validation checks complete source-step coverage, input
hashes, image provenance, exact edits, plan dependencies and shell syntax. It
cannot establish semantic equivalence or decide whether arbitrary commands are
safe. Generated scripts execute with write access to the worktree.

`--runtime auto` is the default: Podman if installed, else Docker. Both execute the
plan inside the declared source images, pass the script over stdin, and mount
the worktree at `/workspace`. After each Docker step the runner gives root-owned
files back to the user; Podman already writes as the user. Host mode is an explicit `--runtime host` option;
it executes the same scripts with local tool versions and the host environment,
including any setup commands in those scripts.

`BASE_SHA` comes from `git merge-base HEAD <base>` at execution; the base is
origin's default branch, else `main`, else `master` (`--base` overrides). Declare any changed-file selection changes explicitly,
including coverage of staged, unstaged and untracked files. Environment values
and working directories support literal text plus `$WORKSPACE`, `$BASE_SHA`,
`$BASE_REF`, `$HEAD_SHA`, `$HEAD_REF`, `$RESULTS` and `$CACHES`. Keep `$CACHES`
paths as given. Preserve the location of caches without a binding. Task results go to
`$RESULTS/<task>/<name>`, a run-scoped directory outside the checkout, and
consumers read them from there.
Secrets stay outside the plan: list names in `required_env` (the run is blocked
without them) or, when a step still works without the value, in `optional_env`,
and forward them explicitly with `--env NAME`. Do not fetch credentials automatically.
Environment and working-directory values do not evaluate shell commands. Put
result-file reads and required directory creation in declared script edits.

## Reuse after edits

```sh
tkn-innerloop run /tmp/linters.plan.json --steps check-test-conventions --base origin/main
```

Source edits reuse the plan without a model call. Changes to the pipeline or
listed helper/configuration inputs invalidate its hashes and require conversion
again. Keep relevant dependencies in the input list; the CLI cannot discover
files the agent omitted. Moving image tags and network installs can still change
tool versions between runs.

Unknown/ambiguous step selections, empty scope, missing declared prerequisites,
and unresolved steps in the selected scope must fail. A passed local scope says
which checks executed and reports skipped/unresolved counts. Cluster CI remains
required for everything outside that scope.

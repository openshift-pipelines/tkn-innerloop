---
name: tekton-innerloop
description: Convert Tekton PipelineRuns into reviewed local execution plans using repository context, then reuse those plans to validate edits in Docker or Podman without a Kubernetes cluster.
---

# Tekton inner-loop validation

Use the repository's Tekton commands to check local edits. An agent decides what
can run locally and explains the adaptations. The CLI validates the resulting
plan and executes it without calling a model during the edit loop.

## Convert once

Inspect the source inventory. Every step starts unresolved; inspection does not
infer that a command is safe or runnable from its name.

All PipelineRuns in a multi-document YAML file are inspected. Use the full ID
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
`--model` or `TKN_INNERLOOP_AI_MODEL` (default: Haiku). The agent receives the
requested source inventory directly and inspects only relevant referenced
helpers/configuration. Startup update checks are disabled. These tools permit repository discovery;
they do not constitute a filesystem sandbox.

The plan accounts for every source step as `run`, `skip` or `unresolved`. A
requested subset is explicit scope, and other checks remain recorded as skipped.
Keep the source image, commands and flags. Propose exact script edits with a
reason for each one; the CLI applies them to the original script. Preserve
ordinary environment values, working directories and shell behavior. Record
prerequisites in `depends_on` and configuration/helper paths in `inputs`.

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

Docker is the default runtime. Podman uses `--runtime podman`. Both execute the
plan inside the declared source images, pass the script over stdin, and mount
the worktree at `/workspace`. Host mode is an explicit `--runtime host` option;
it executes the same scripts with local tool versions and the host environment,
including any setup commands in those scripts.

`BASE_SHA` comes from `git merge-base HEAD <base-ref>` at execution, with `main`
as the default target. Declare any changed-file selection changes explicitly,
including coverage of staged, unstaged and untracked files. Environment values
and working directories support literal text plus `$WORKSPACE` and `$BASE_SHA`.
Secrets stay outside the plan: list names in `required_env` and explicitly
forward them with `--env NAME` when needed. Do not fetch credentials automatically.
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

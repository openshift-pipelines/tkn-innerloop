<p align="center">
  <img src="docs/site/logo.svg" alt="tkn-innerloop logo: a pipeline line with a dotted loop dropping to a local station" width="128">
</p>

<h1 align="center">tkn-innerloop</h1>

<p align="center">
  <a href="https://github.com/openshift-pipelines/tkn-innerloop/actions/workflows/ci.yaml"><img src="https://github.com/openshift-pipelines/tkn-innerloop/actions/workflows/ci.yaml/badge.svg" alt="CI"></a>
  <a href="https://openshift-pipelines.github.io/tkn-innerloop/"><img src="https://github.com/openshift-pipelines/tkn-innerloop/actions/workflows/pages.yaml/badge.svg" alt="Website"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/openshift-pipelines/tkn-innerloop" alt="License: Apache 2.0"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&amp;logoColor=white" alt="Python 3.11 or newer">
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/install%20with-uv-de5fe9?logo=uv&amp;logoColor=white" alt="Install with uv"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://tekton.dev"><img src="https://img.shields.io/badge/Tekton-pipelines-fd495c?logo=tekton&amp;logoColor=white" alt="Tekton"></a>
  <img src="https://img.shields.io/badge/runs%20on-Podman%20%7C%20Docker-892ca0?logo=podman&amp;logoColor=white" alt="Runs on Podman or Docker">
</p>

Your change passes its tests on your laptop, then the pull request goes red in
Tekton because a spelling check or a naming rule failed. `tkn-innerloop` runs
those pipeline checks on your machine before you push, in the same container
images, with Podman or Docker.

```sh
tkn-innerloop run .tekton/linter.yaml
```

The first run hands the pipeline file to a coding agent. The agent reads the
scripts and the helpers they call, decides which steps can run locally, and
writes a plan with the exact script changes they need. The plan is saved in
your cache directory. Later runs reuse it with no model call until the pipeline
or one of those helpers changes. Cloning, publishing and deploying stay on the
cluster.

The [website](https://openshift-pipelines.github.io/tkn-innerloop/) walks
through a real session in the Pipelines-as-Code repository.

## Install

You need Python 3.11 or newer, Podman or Docker, and the
[pi coding agent](https://github.com/earendil-works/pi) logged in to a model
provider. Install the command with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/openshift-pipelines/tkn-innerloop
uv tool upgrade tkn-innerloop
```

To try it without installing:

```sh
uvx --from git+https://github.com/openshift-pipelines/tkn-innerloop tkn-innerloop --help
```

## Use it

Run it from the repository you want to check:

```sh
tkn-innerloop run                                  # the only pipeline in .tekton/
tkn-innerloop run .tekton/go.yaml
tkn-innerloop run .tekton/linter.yaml --steps codespell,yamllint
```

`run` executes the scripts the agent edited without stopping for review. To
read the plan first, generate it, look at the diff, then run it:

```sh
tkn-innerloop generate .tekton/linter.yaml --steps codespell -o /tmp/plan.json
tkn-innerloop validate /tmp/plan.json --diff
tkn-innerloop run /tmp/plan.json
```

To run the checks before every commit, add the
[pre-commit hook](https://openshift-pipelines.github.io/tkn-innerloop/usage.html#run-it-before-each-commit).

`tkn-innerloop skills install` copies a skill into `.agents/skills/` so your
coding agent knows how to use the tool in that repository.

The [usage guide](https://openshift-pipelines.github.io/tkn-innerloop/usage.html) covers step selection, Pipelines-as-Code,
Tekton bundles, caches, secrets, runtimes and exit codes.
[DEVELOPER.md](DEVELOPER.md) explains how to work on the tool itself.

## License

Apache License 2.0, see [LICENSE](LICENSE).

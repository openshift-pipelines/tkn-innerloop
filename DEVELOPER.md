# Working on tkn-innerloop

## Layout

| Path | What it holds |
| --- | --- |
| `src/tkn_innerloop/` | the package. `cli.py` parses commands, `inventory.py` reads pipelines and picks steps, `agent.py` talks to pi, `plan.py` validates plans, `runner.py` runs them. `caches.py`, `bundles.py`, `pac.py` and `konflux.py` handle tekton-caches, bundles, Pipelines-as-Code and Konflux. |
| `bin/tkn-innerloop` | runs the package from the checkout with no install step. It needs PyYAML. |
| `skills/tekton-innerloop/SKILL.md` | the agent skill. The wheel ships it inside the package. |
| `tests/` | unit tests, split by area, and `tests/corpus/` |
| `hack/` | scripts that check real pipelines against a model |
| `.tekton/ci.yaml` | this repository's own pipeline |
| `docs/usage.md` | the user guide |
| `docs/site/` | the website, published to GitHub Pages from `main` |
| `docs/demo/`, `docs/art/` | the recorded demo and the pictures for the slides |
| `docs/poc-report.md`, `docs/presentation.md` | the proof-of-concept report and slides |

## Set up

```sh
python3 -m pip install -e .
pip install pre-commit && pre-commit install
```

You can also install PyYAML and put `bin/` on your `PATH`.

## Checks

```sh
make lint test       # ruff, shellcheck and the unit tests
make fix             # apply ruff's automatic fixes
make innerloop       # run .tekton/ci.yaml locally with tkn-innerloop itself
make site            # preview the website on http://127.0.0.1:8000
aniflow --check docs/usage.md docs/poc-report.md docs/presentation.md
```

The pre-commit hook runs `.tekton/ci.yaml` through `bin/tkn-innerloop` before
each commit. The first run asks the agent for a plan, and later commits reuse
it. GitHub Actions runs the same lint and tests on Python 3.11 and 3.13.

## The corpus

`tests/corpus/` holds 17 real PipelineRuns and Pipelines from
Pipelines-as-Code, Konflux, Tekton Chains, the tektoncd/pipeline examples and
tektoncd/plumbing, each pinned to a commit. The unit tests check what the
inventory and the automatic step choice return for each one. Each case's README
describes the plan a good model should produce.

`hack/corpus-check` asks the model for a plan for each case and checks it
against the case's expectations. With `--execute` it also runs some of them on
the source repository, then again with a planted failure that the run must
catch. `make corpus` runs it. `make corpus-bundles` copies the bundle objects that
cases reference into each case, so the unit tests run offline. It needs skopeo
and network access.
`tests/corpus/README.md` explains how to add a case.

## The demo

The recording uses two checks from the Pipelines-as-Code `.tekton/linter.yaml`,
`codespell` and `check-test-conventions`. It shows plan generation, the review,
the container run, two planted failures and a passing run after the fix. The
other 13 steps are outside its scope. The `t.Errorf` line in the fixture only
produces a warning. The failures come from the spelling mistake and the test
name.

```sh
PAC_REPO=~/git/pac/main ./docs/demo/record.sh
```

`record.sh` generates the plan while recording and checks that each command
exits as expected. It refuses to overwrite an existing fixture, removes its own
fixture on exit when it is unchanged, and writes the plan next to the video.
Recordings and screenshots go to `docs/demo/out/`, which git ignores. The video
cuts typing and waiting time. The timings it shows come from real runs with
images already pulled.

## The slides

`docs/presentation.md` uses pictures from `docs/art/`, drawn by
`docs/art/make_art.py`. A public sharei page only loads inline images, so build
the inlined copy first and upload that:

```sh
python3 docs/art/build.py
sharec dist/presentation.public.md -p <project> -n presentation.md --replace -y --public-ttl 0
```

## The banner

`docs/art/banner.html` draws the 1280×640 picture used as the GitHub social
preview and the website's link preview. The command to render it to
`docs/site/social.png` is at the top of the file. Upload the PNG under
Settings, General, Social preview.

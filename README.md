# Fast Inner-Loop Tekton Validation for Coding Agents (PoC)

Instead of re-implementing Kubernetes locally, this PoC pairs an **Agent Skill (`tekton-innerloop`)** with a lightweight CLI (`tkn-innerloop`) that inspects `.tekton/` PipelineRuns, separates fast inner-loop checks from outer-loop cluster steps, and runs the **exact** CI commands and flags against your local Git working tree in **~3 seconds**.

## Public Links

- **Animated Presentation Deck (`presentation.md`)**: [Open on sharei](https://share.chmouel.com/public/T-iBled8FjZNJrCzNoPrEw/presentation.md#slide=1)
- **Recorded Demo Video (MP4 with Elevator Music)**: <https://share.chmouel.com/public/jZRofFOy4r48bL-fgQtGUw/tkn-innerloop-demo.mp4>
- **Agent Skill (`SKILL.md`)**: <https://share.chmouel.com/public/Kp0H0sAZIfR7NAipLx3THg/SKILL.md>

## How It Works (Plain English)

1. **Read & Sort the Pipeline (80% Inner Loop vs. 20% Outer Loop)**
   - Running a full Kubernetes + Tekton control plane locally is heavy when coding agents just need to know whether their code edits will break CI.
   - `tkn-innerloop inspect .tekton/linter.yaml` classifies **12 out of 15 steps (80%)** in Pipelines-as-Code's `.tekton/linter.yaml` as runnable in the local inner loop (`codespell`, `shellcheck`, `commit-validation`, `check-e2e-tests-naming`, `check-test-conventions`, `yamllint`, `ruff-lint`, `ruff-format`, `markdownlint`, `vale`, `goreleaser-check`, `generate-release-yaml`) and skips cluster-only steps (`fetch-repo` StepAction, root `/usr/bin` OpenAPI schema installer, and `zizmor` requiring `HUB_TOKEN`).

2. **Translate Cluster Variables to Local Git Paths**
   - Replaces `$(workspaces.source.path)` with the local Git working tree root (`git rev-parse --show-toplevel`).
   - Binds Pipelines-as-Code Pull Request variables (`{{ headers['X-Github-Event'] }}` and `{{ body.pull_request.base.sha }}`) to your local Git branch range (`git merge-base HEAD main`).

3. **Catch CI-Only Failures in ~2 Seconds**
   - When an agent writes a new Go unit test (`TestInnerLoop_ConfigValidation`) that uses `t.Errorf(...)` and contains a comment typo (`teh`), a standard `go test ./pkg/params` **passes** in 11ms.
   - Running `tkn-innerloop run .tekton/linter.yaml` executes the exact `check-test-conventions` and `codespell` scripts from `.tekton/linter.yaml`, catching both failures in **1.8 seconds**.

4. **Pluggable Podman / Host Script Generation + Built-in Skill**
   - `tkn-innerloop generate .tekton/linter.yaml --runtime podman` outputs a ready-to-run `podman run --rm -v "$WORKSPACE:/workspace:Z"` script using each step's exact container image.
   - `tkn-innerloop skills show` / `tkn-innerloop skills install` ships the skill inside the CLI.

5. **AI Fallback When the Rules Are Not Enough**
   - Some pipelines carry remote StepActions, cluster secrets, caches or generated code that rules cannot safely rewrite.
   - `tkn-innerloop generate .tekton/go.yaml --ai --runtime dockerfile` hands the YAML to a coding agent (here `pi`) and gets back a multi-stage `Dockerfile` with the same images and flags; `--ai --runtime docker` returns a `docker run` script instead.
   - The agent runs with no tools (`pi -p --no-tools`), so it only reads the YAML and writes the artifact. Pick a model with `--model` or `TKN_INNERLOOP_AI_MODEL` (the demo uses `enmaas/rits/deepseek-ai/DeepSeek-V4.1-Flash-1017`).

```mermaid
flowchart LR
  YAML@{ img: "/icons/file-code.svg", label: ".tekton/linter.yaml", w: 48, h: 48, constraint: "on" }
  Inspect@{ img: "/icons/filter.svg", label: "tkn-innerloop inspect", w: 48, h: 48, constraint: "on" }
  Inner@{ img: "/icons/task.svg", label: "12 Inner-Loop Steps", w: 48, h: 48, constraint: "on" }
  Outer@{ img: "/icons/cloud.svg", label: "3 Outer-Loop Steps", w: 48, h: 48, constraint: "on" }
  Bind@{ img: "/icons/config.svg", label: "Rewrite PaC & Paths", w: 48, h: 48, constraint: "on" }
  Run@{ img: "/icons/success.svg", label: "12/12 PASS in 3.1s", w: 48, h: 48, constraint: "on" }

  YAML --> Inspect
  Inspect --> Inner
  Inspect --> Outer
  Inner --> Bind --> Run

%% play: Inner-loop execution
%% YAML -> Inspect : 15 steps | Read .tekton/linter.yaml from the repository
%% Inspect -> Outer : skip 3 | Skip cluster-only steps (git-clone, cluster secrets)
%% Inspect -> Inner : keep 12 | Select the 12 deterministic linter and convention checks
%% Inner -> Bind : translate | Bind $(workspaces.source.path) and PaC PR variables to local Git
%% Bind -> Run : execute | Run all 12 Tekton steps locally in 3.1 seconds
```

<details>
<summary>Repository Layout</summary>

- `SKILL.md` & `skills/tekton-innerloop/SKILL.md` — The Agent Skill instructing coding agents how to inspect, generate, and run `.tekton` inner-loop checks and honor `(run-local)` / `(skip-local)` descriptions.
- `presentation.md` — Animated `aniflow` slide deck explaining the architecture and workflow.
- `bin/tkn-innerloop` — CLI with `inspect`, `generate` (`--runtime host|podman|docker`), `run` (`--steps`, `--fail-fast`), and `skills` (`show|install`).
- `demo/demo.tape` — VHS tape used to record the end-to-end execution against `.tekton/linter.yaml` and the `--ai` Dockerfile fallback.
- `demo/record.sh` — Rebuilds the recording and its soundtrack (`PAC_REPO=/path/to/pipelines-as-code ./demo/record.sh`).
- `demo/banner.sh` — Terminal explanation banners used in the recording.
- `demo/generate_elevator_music.py` — Synthesizes the Bossa-Nova / Rhodes lounge background track.
- `demo/toggle-defects.sh` — Helper script that injects and fixes the `check-test-conventions` + `codespell` violations during the recording.

</details>

#!/usr/bin/env bash
# Renders big, high-contrast explanation cards in the terminal using `gum style`.
set -euo pipefail

STEP="${1:-intro}"
clear

case "$STEP" in
  intro)
    gum style \
      --border double \
      --border-foreground 212 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "⚡ FAST INNER-LOOP TEKTON VALIDATION FOR CODING AGENTS" \
      "" \
      "THE PROBLEM:" \
      "• Agents run generic local checks (go test, ruff) that PASS locally..." \
      "• ...then FAIL 5 minutes later in Tekton CI due to custom flags & scripts." \
      "• Running a full Kubernetes + Tekton control plane locally is too heavy." \
      "" \
      "HOW THIS POC WORKS:" \
      "1. Agent Skill + CLI (tkn-innerloop) reads .tekton/*.yaml directly" \
      "2. Classifies steps: RUN LOCAL (80% inner loop) vs SKIP (outer loop)" \
      '3. Rewrites $(workspaces.source.path) & PaC {{ ... }} PR variables' \
      "4. Runs exact CI commands in Podman, Docker or Host in ~3 seconds!" \
      "5. For odd pipelines, an AI agent can write you the Dockerfile instead"
    ;;
  step1)
    gum style \
      --border rounded \
      --border-foreground 86 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "STEP 1 / 5 — ANALYZE .tekton/linter.yaml" \
      "" \
      "• Inspects all 15 steps in Pipelines-as-Code's real .tekton/linter.yaml" \
      "• Skips cluster-only outer loop (git-clone StepAction, cluster secrets)" \
      "• Selects 12 inner-loop checks (codespell, shellcheck, conventions, vale)"
    ;;
  step2)
    gum style \
      --border rounded \
      --border-foreground 214 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "STEP 2 / 5 — TRANSLATE TEKTON YAML TO LOCAL SCRIPT" \
      "" \
      "• No Kubernetes API server or virtual cluster required!" \
      '• Binds $(workspaces.source.path) -> local Git working tree' \
      "• Simulates Pull Request context ({{ body.pull_request.base.sha }})" \
      "• Emits a Podman/Docker container runner or native host script"
    ;;
  step3)
    gum style \
      --border rounded \
      --border-foreground 220 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "STEP 3 / 5 — WHY GENERIC AGENT CHECKS FAIL IN CI" \
      "" \
      "• The coding agent writes a new Go test: TestInnerLoop_ConfigValidation" \
      '• Standard `go test ./pkg/params` PASSES in 11ms!' \
      '• Without Tekton inner-loop validation, the agent pushes broken code...' \
      '• Let us run `tkn-innerloop run` and see what Tekton catches locally.'
    ;;
  step4)
    gum style \
      --border rounded \
      --border-foreground 46 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "STEP 4 / 5 — FIX & VALIDATE FULL 12-STEP TEKTON INNER LOOP" \
      "" \
      "• Tekton inner-loop caught 2 CI-only errors in 1.8 seconds:" \
      "  1. check-test-conventions: underscore in test name + t.Errorf usage" \
      "  2. codespell: typo ('teh' -> 'the') in comments" \
      "• Now the agent fixes both and runs all 12 Tekton steps in ~3 seconds!"
    ;;
  step5)
    gum style \
      --border rounded \
      --border-foreground 135 \
      --padding "1 3" \
      --margin "1 2" \
      --width 78 \
      "STEP 5 / 5 — AI FALLBACK FOR THE WEIRD PIPELINES" \
      "" \
      "• Rules cover linters and convention scripts, but real pipelines also" \
      "  have remote StepActions, cluster secrets, caches and generated code." \
      '• Fallback: hand the whole YAML to a coding agent and ask it to write' \
      '  the Dockerfile or the `docker run` script for you.' \
      '• Here that agent is `pi` on DeepSeek V4.1 Flash over enmaas.' \
      "• No tools, no cluster, no guessing: it reads .tekton/go.yaml and" \
      "  returns a multi-stage Dockerfile with the exact CI images and flags." \
      '• It can also emit a plain `docker run` script: --ai --runtime docker' 
    ;;
esac

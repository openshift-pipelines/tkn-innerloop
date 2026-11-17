#!/usr/bin/env bash
set -euo pipefail
clear
case "${1:-intro}" in
  intro)
    title="GO TEST PASSES. CI STILL FINDS THE BUGS."
    lines=("A typo. A test name. Rules your unit tests never check."
           "Run the real Tekton checks before you push."
           ""
           "Convert once  →  catch failures  →  fix  →  rerun"
           ""
           "2 real PaC checks · Docker · idle time cut")
    ;;
  defects)
    title="01   THE TEST IS GREEN. THE SOURCE ISN'T."
    lines=("Spot the typo: teh. Spot the test name: an underscore."
           "Go accepts both. The pipeline has stricter rules.")
    ;;
  convert)
    title="02   TURN THE PIPELINE INTO LOCAL CHECKS"
    lines=("An agent reads the selected CI steps and proposes a plan."
           "Review the exact changes once. Keep the original images.")
    ;;
  failures)
    title="03   CATCH BOTH FAILURES BEFORE PUSHING"
    lines=("Codespell catches the typo. The naming check catches the underscore."
           "These are the real pipeline scripts, running in Docker.")
    ;;
  fixed)
    title="04   FIX THE SOURCE. REUSE THE SAME PLAN."
    lines=("Correct the spelling and test name; use the assertion helper."
           "Run both checks again. No agent call.")
    ;;
  outro)
    title="CATCH CI RULES WHILE YOU'RE STILL EDITING."
    lines=("Convert once. Review once. Rerun after every edit."
           ""
           "2 checks passed locally · 13 outside this scope"
           "Cluster CI still covers the rest.")
    ;;
  *) exit 1 ;;
esac
gum style --border rounded --border-foreground 141 --foreground 252 \
  --padding "1 3" --margin "1 2" --width 100 \
  "$title" "" "${lines[@]}"

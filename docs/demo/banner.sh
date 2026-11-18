#!/usr/bin/env bash
set -euo pipefail
clear
case "${1:-intro}" in
  intro)
    title="GO TEST PASSES. CI STILL FINDS THE BUGS."
    lines=("You change some code and go test is green."
           "Then the pipeline rejects it: a typo, a test name,"
           "rules your unit tests never check."
           ""
           "This demo runs two real checks from the pipelines-as-code"
           "pipeline on a laptop, before you push."
           ""
           "Convert once  →  catch failures  →  fix  →  rerun"
           "Waiting time is cut from the video.")
    ;;
  defects)
    title="01   A TEST WITH TWO MISTAKES"
    lines=("We added a test file. Look for two things:"
           "  a typo, teh, in a comment"
           "  an underscore in the test name"
           ""
           "Go accepts both, so a normal test run will pass.")
    ;;
  green)
    title="GO TEST SAYS: ALL GOOD"
    lines=("The test passed. Nothing in a normal run flags"
           "the typo or the test name."
           ""
           "This is the moment you would push, and the pipeline"
           "would fail later on the cluster.")
    ;;
  convert)
    title="02   TURN THE PIPELINE INTO LOCAL CHECKS"
    lines=("An agent reads the CI steps and writes a plan."
           ""
           "The plan is a file: each original script, the edits made"
           "to run it locally, and the image it runs in."
           ""
           "You review it once. Nothing has run yet.")
    ;;
  plan)
    title="WHAT YOU REVIEW"
    lines=("validate --diff shows every change made to the original"
           "CI script, so nothing is hidden in the conversion."
           ""
           "Mostly: cluster paths become your checkout, and the pull"
           "request base becomes your local git branch.")
    ;;
  failures)
    title="03   RUN THE CHECKS IN DOCKER"
    lines=("We run two checks from the plan. They are the pipeline's"
           "own scripts, in the images the pipeline names."
           ""
           "Expect two failures: codespell first, then the test"
           "naming check.")
    ;;
  verdict)
    title="WHAT JUST HAPPENED"
    lines=("Both checks failed, locally, before any push."
           "go test could not see either problem."
           ""
           "Without this, you find out after pushing,"
           "when the cluster pipeline goes red.")
    ;;
  fixed)
    title="04   FIX THE SOURCE, REUSE THE PLAN"
    lines=("We fix the typo and rename the test."
           ""
           "Then we rerun the same plan. No agent call this time:"
           "the saved plan is reused until the pipeline changes.")
    ;;
  outro)
    title="CATCH CI RULES WHILE YOU'RE STILL EDITING."
    lines=("Convert once. Review once. Rerun after every edit."
           ""
           "This demo: 2 of 15 steps passed locally, 13 are outside"
           "its scope. Docker runs the real scripts, but cluster CI"
           "still covers the rest.")
    ;;
  *) exit 1 ;;
esac
gum style --border rounded --border-foreground 141 --foreground 252 \
  --padding "1 3" --margin "1 2" --width 100 \
  "$title" "" "${lines[@]}"

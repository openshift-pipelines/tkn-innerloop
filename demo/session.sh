#!/usr/bin/env bash
demo_labels=(INJECT GO PLAN REVIEW SPELL NAMING FIX FINAL CLEAN)
demo_expected=(0 0 0 0 1 1 0 0 0)
demo_index=0
demo_command=
demo_status() {
  local rc=$? label expected
  case "$demo_command" in
    tkn-innerloop\ *|go\ test\ *|toggle-defects.sh\ *) ;;
    *) return ;;
  esac
  label=${demo_labels[$demo_index]:-UNEXPECTED}
  expected=${demo_expected[$demo_index]:-unknown}
  printf '%s actual=%s expected=%s\n' "$label" "$rc" "$expected" >> "$TKN_INNERLOOP_DEMO_STATE/status.log"
  [[ "$rc" == "$expected" ]] || touch "$TKN_INNERLOOP_DEMO_STATE/failed"
  demo_index=$((demo_index + 1))
}
PROMPT_COMMAND=demo_status
PS1='pac> '
trap '[[ $BASH_COMMAND == demo_status ]] || demo_command=$BASH_COMMAND' DEBUG

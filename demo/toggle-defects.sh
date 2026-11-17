#!/usr/bin/env bash
# Creates realistic inner-loop defects in the working tree that pass `go test`
# and `git status`, OR fixes them so `.tekton/linter.yaml` turns green.
set -euo pipefail

MODE="${1:-break}"

if [[ "$MODE" == "break" ]]; then
  cat > pkg/params/innerloop_demo_test.go <<'EOF'
package params

import "testing"

// Intentionally violates .tekton/linter.yaml check-test-conventions & codespell:
// 1. Underscore in test function name (TestInnerLoop_ConfigValidation)
// 2. Uses t.Errorf instead of gotest.tools/v3/assert
// 3. Contains a codespell typo ("teh" instead of "the")
func TestInnerLoop_ConfigValidation(t *testing.T) {
	// verifying teh default configuration in inner loop
	got := "pac"
	if got != "pac" {
		t.Errorf("unexpected value: %s", got)
	}
}
EOF
  echo "Injected inner-loop defects into pkg/params/innerloop_demo_test.go"
elif [[ "$MODE" == "fix" ]]; then
  cat > pkg/params/innerloop_demo_test.go <<'EOF'
package params

import (
	"testing"

	"gotest.tools/v3/assert"
)

func TestInnerLoopConfigValidation(t *testing.T) {
	// verifying the default configuration in inner loop
	got := "pac"
	assert.Equal(t, got, "pac")
}
EOF
  echo "Fixed pkg/params/innerloop_demo_test.go to match .tekton/linter.yaml conventions"
elif [[ "$MODE" == "clean" ]]; then
  rm -f pkg/params/innerloop_demo_test.go
  echo "Cleaned pkg/params/innerloop_demo_test.go"
fi

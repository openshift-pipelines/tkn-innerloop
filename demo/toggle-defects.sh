#!/usr/bin/env bash
set -euo pipefail
: "${TKN_INNERLOOP_DEMO_STATE:?run this helper through demo/record.sh}"
fixture=pkg/params/innerloop_demo_test.go
checksum="$TKN_INNERLOOP_DEMO_STATE/fixture.sha256"
case "${1:?use break, fix or clean}" in
  break)
    [[ ! -e "$fixture" ]] || { echo "Refusing to overwrite $fixture" >&2; exit 1; }
    cat > "$fixture" <<'EOF'
package params

import "testing"

func TestInnerLoop_ConfigValidation(t *testing.T) {
	// Verify teh default configuration.
	got := "pac"
	if got != "pac" {
		t.Errorf("unexpected value: %s", got)
	}
}
EOF
    sha256sum "$fixture" > "$checksum"
    echo "Created fixture: spelling error and invalid test name (t.Errorf only warns)."
    ;;
  fix)
    sha256sum --check --status "$checksum"
    cat > "$fixture" <<'EOF'
package params

import (
	"testing"

	"gotest.tools/v3/assert"
)

func TestInnerLoopConfigValidation(t *testing.T) {
	// Verify the default configuration.
	got := "pac"
	assert.Equal(t, got, "pac")
}
EOF
    sha256sum "$fixture" > "$checksum"
    echo "Fixed the fixture. The execution plan is unchanged."
    ;;
  clean)
    if [[ -f "$checksum" ]]; then
      sha256sum --check --status "$checksum" || {
        echo "Fixture changed outside the demo; leaving it in place: $fixture" >&2
        exit 1
      }
      rm "$fixture" "$checksum"
      echo "Removed the demo fixture."
    fi
    ;;
  *) exit 1 ;;
esac

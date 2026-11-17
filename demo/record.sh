#!/usr/bin/env bash
# Rebuild the demo video: VHS recording + synthesized background music.
#
#   PAC_REPO=/path/to/pipelines-as-code ./demo/record.sh
#
# Requires: vhs, ffmpeg, python3 (numpy), gum, and `pi` for the AI fallback step.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$PWD"
: "${PAC_REPO:?set PAC_REPO to a checkout of pipelines-as-code}"

vhs demo/demo.tape

# Drop files the demo wrote into the target repository.
rm -f "$PAC_REPO/pkg/params/innerloop_demo_test.go" "$PAC_REPO/bin/release.yaml"

# Jazz backing track, generated to the exact length of the recording.
DURATION=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 demo/tkn-innerloop-demo.mp4)
python3 demo/generate_jazz.py "$DURATION" demo/jazz-backing.wav

ffmpeg -y -loglevel error \
  -i demo/tkn-innerloop-demo.mp4 \
  -i demo/jazz-backing.wav \
  -c:v copy -c:a aac -b:a 192k -shortest \
  demo/tkn-innerloop-demo-sound.mp4
mv demo/tkn-innerloop-demo-sound.mp4 demo/tkn-innerloop-demo.mp4
rm -f demo/jazz-backing.wav

echo "Rebuilt $REPO_ROOT/demo/tkn-innerloop-demo.mp4 (${DURATION}s)"

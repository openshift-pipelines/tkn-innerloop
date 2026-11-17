#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export INNERLOOP_REPO="$PWD"
: "${PAC_REPO:?set PAC_REPO to a checkout of pipelines-as-code}"
export PAC_REPO
export TKN_INNERLOOP_AI_MODEL="${TKN_INNERLOOP_AI_MODEL:-enmaas/rits/deepseek-ai/DeepSeek-V4.1-Flash-1017}"
export TKN_INNERLOOP_DEMO_STATE
TKN_INNERLOOP_DEMO_STATE=$(mktemp -d -t tkn-innerloop-demo.XXXXXX)
export DEMO_PLAN=/tmp/linters.json
record_dir="$HOME/tmp/sharei/tkn-innerloop/shots"
mkdir -p "$record_dir"
fixture="$PAC_REPO/pkg/params/innerloop_demo_test.go"
[[ ! -e "$fixture" ]] || { echo "Refusing to overwrite $fixture" >&2; exit 1; }
cleanup() {
  (cd "$PAC_REPO" && "$INNERLOOP_REPO/demo/toggle-defects.sh" clean)
}
trap cleanup EXIT
vhs demo/demo.tape --output "$record_dir/tkn-innerloop-demo.mp4"
[[ ! -e "$TKN_INNERLOOP_DEMO_STATE/failed" ]] || {
  echo "A recorded command had an unexpected exit status; refusing to publish a successful demo." >&2
  exit 1
}
[[ -e "$TKN_INNERLOOP_DEMO_STATE/COMPLETE" ]] || { echo "Recording did not finish." >&2; exit 1; }
[[ $(wc -l < "$TKN_INNERLOOP_DEMO_STATE/status.log") -eq 9 ]] || {
  echo "Recording did not verify every command." >&2
  exit 1
}
cp "$DEMO_PLAN" demo/linters.plan.json
video="$record_dir/tkn-innerloop-demo.mp4"
duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$video")
python3 demo/generate_jazz.py "$duration" "$TKN_INNERLOOP_DEMO_STATE/jazz.wav"
ffmpeg -y -loglevel error -i "$video" -i "$TKN_INNERLOOP_DEMO_STATE/jazz.wav" \
  -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -shortest \
  "$record_dir/tkn-innerloop-demo-sound.mp4"
mv "$record_dir/tkn-innerloop-demo-sound.mp4" "$video"
cp "$video" demo/tkn-innerloop-demo.mp4
printf 'Recorded %s (%ss). Execution plan: %s\n' "$video" "$duration" "$DEMO_PLAN"

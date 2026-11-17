Output "demo/tkn-innerloop-demo.mp4"

Set Shell "bash"
Set FontSize 24
Set Width 1640
Set Height 980
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 70ms

Hide
Type "export PATH=$PWD/bin:$PWD/demo:$PATH"
Enter
Type "cd ${PAC_REPO:?set PAC_REPO to a checkout of pipelines-as-code}"
Enter
Type "export TKN_INNERLOOP_AI_MODEL=enmaas/rits/deepseek-ai/DeepSeek-V4.1-Flash-1017"
Enter
Type "export PS1='\[\e[1;36m\]pipelines-as-code\[\e[0m\] \[\e[1;32m\]❯\[\e[0m\] '"
Enter
Type "banner.sh intro"
Enter
Show

Sleep 7s

Hide
Type "banner.sh step1"
Enter
Show
Sleep 5s

Type "tkn-innerloop inspect .tekton/linter.yaml"
Enter
Sleep 7s

Hide
Type "banner.sh step2"
Enter
Show
Sleep 5s

Type "tkn-innerloop generate .tekton/linter.yaml --runtime podman --steps codespell,check-test-conventions -o /tmp/innerloop.sh"
Enter
Sleep 1.5s
Type "sed -n '1,20p' /tmp/innerloop.sh"
Enter
Sleep 6s

Hide
Type "banner.sh step3"
Enter
Show
Sleep 6s

Type "toggle-defects.sh break && sed -n '6,16p' pkg/params/innerloop_demo_test.go"
Enter
Sleep 3s
Type "go test -run TestInnerLoop ./pkg/params"
Enter
Sleep 2.5s
Type "tkn-innerloop run .tekton/linter.yaml --steps codespell,shellcheck,check-test-conventions,yamllint"
Enter
Sleep 6s

Hide
Type "banner.sh step4"
Enter
Show
Sleep 6s

Type "toggle-defects.sh fix"
Enter
Sleep 2s
Type "tkn-innerloop run .tekton/linter.yaml"
Enter
Sleep 8s

Hide
Type "banner.sh step5"
Enter
Show
Sleep 8s

Type "tkn-innerloop generate .tekton/go.yaml --ai --runtime dockerfile --steps unittest,lint -o /tmp/Dockerfile.go"
Enter
Sleep 8s
Type "cat /tmp/Dockerfile.go"
Enter
Sleep 10s

Hide
Type "toggle-defects.sh clean"
Enter
Sleep 500ms
Show

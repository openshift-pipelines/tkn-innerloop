Output "/home/chmouel/git/work/tkn-innerloop/demo/tkn-innerloop-demo.mp4"
Output "/home/chmouel/git/work/tkn-innerloop/demo/tkn-innerloop-demo.gif"

Set Shell "bash"
Set FontSize 24
Set Width 1640
Set Height 980
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export PATH=/home/chmouel/git/work/tkn-innerloop/bin:$PATH"
Enter
Type "export PS1='\[\e[1;36m\]pac/main\[\e[0m\] \[\e[1;32m\]❯\[\e[0m\] '"
Enter
Type "/home/chmouel/git/work/tkn-innerloop/demo/banner.sh intro"
Enter
Show

Sleep 5.5s

Hide
Type "/home/chmouel/git/work/tkn-innerloop/demo/banner.sh step1"
Enter
Show
Sleep 3.5s

Type "tkn-innerloop inspect .tekton/linter.yaml"
Enter
Sleep 5.5s

Hide
Type "/home/chmouel/git/work/tkn-innerloop/demo/banner.sh step2"
Enter
Show
Sleep 3.5s

Type "tkn-innerloop generate .tekton/linter.yaml --runtime podman --steps codespell,check-test-conventions -o /tmp/innerloop.sh"
Enter
Sleep 1s
Type "sed -n '1,24p' /tmp/innerloop.sh"
Enter
Sleep 5s

Hide
Type "/home/chmouel/git/work/tkn-innerloop/demo/banner.sh step3"
Enter
Show
Sleep 4s

Type "/home/chmouel/git/work/tkn-innerloop/demo/toggle-defects.sh break && sed -n '6,16p' pkg/params/innerloop_demo_test.go"
Enter
Sleep 2.5s
Type "go test -run TestInnerLoop ./pkg/params"
Enter
Sleep 2s
Type "tkn-innerloop run .tekton/linter.yaml --steps codespell,shellcheck,check-test-conventions,yamllint"
Enter
Sleep 5.5s

Hide
Type "/home/chmouel/git/work/tkn-innerloop/demo/banner.sh step4"
Enter
Show
Sleep 4s

Type "/home/chmouel/git/work/tkn-innerloop/demo/toggle-defects.sh fix"
Enter
Sleep 1.5s
Type "tkn-innerloop run .tekton/linter.yaml"
Enter
Sleep 7s

Hide
Type "/home/chmouel/git/work/tkn-innerloop/demo/toggle-defects.sh clean"
Enter
Sleep 500ms
Show

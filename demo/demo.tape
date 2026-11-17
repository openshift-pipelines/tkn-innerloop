Output "demo/tkn-innerloop-demo.mp4"
Set Shell "bash"
Set FontSize 24
Set Width 1800
Set Height 1040
Set Padding 30
Set Margin 20
Set MarginFill "#11111b"
Set BorderRadius 16
Set WindowBar Colorful
Set Theme "Catppuccin Mocha"
Set TypingSpeed 0ms
Set CursorBlink false
Hide
Type "export PATH=$INNERLOOP_REPO/bin:$INNERLOOP_REPO/demo:$PATH; unset NO_COLOR; export TERM=xterm-256color"
Enter
Type "cd $PAC_REPO"
Enter
Type "source $INNERLOOP_REPO/demo/session.sh"
Enter
Wait+Line /pac> ?$/
Type "toggle-defects.sh break"
Enter
Wait+Line /pac> ?$/
Type "banner.sh intro"
Enter
Wait+Screen /GO TEST PASSES/
Wait+Line /pac> ?$/
Show
Sleep 300ms
Screenshot "/home/chmouel/tmp/sharei/tkn-innerloop/shots/demo-opening.png"
Sleep 4s
Hide
Type "banner.sh defects"
Enter
Wait+Screen /THE TEST IS GREEN/
Wait+Line /pac> ?$/
Show
Sleep 2s
Type "cat pkg/params/innerloop_demo_test.go"
Sleep 700ms
Enter
Hide
Wait+Line /pac> ?$/
Show
Sleep 3s
Type "go test -run TestInnerLoop ./pkg/params"
Sleep 700ms
Enter
Hide
Wait+Line@120s /pac> ?$/
Show
Sleep 3s
Hide
Type "banner.sh convert"
Enter
Wait+Screen /TURN THE PIPELINE/
Wait+Line /pac> ?$/
Show
Sleep 2s
Type "tkn-innerloop generate .tekton/linter.yaml --steps codespell,check-test-conventions -o /tmp/linters.json"
Sleep 1s
Enter
Hide
Wait+Line@300s /pac> ?$/
Show
Sleep 300ms
Screenshot "/home/chmouel/tmp/sharei/tkn-innerloop/shots/demo-conversion.png"
Sleep 3s
Type "tkn-innerloop validate /tmp/linters.json --diff"
Sleep 700ms
Enter
Hide
Wait+Line@10s /pac> ?$/
Show
Sleep 4s
Hide
Type "banner.sh failures"
Enter
Wait+Screen /CATCH BOTH FAILURES/
Wait+Line /pac> ?$/
Show
Sleep 2s
Type "tkn-innerloop run /tmp/linters.json --steps codespell --base main"
Sleep 700ms
Enter
Hide
Wait+Line@180s /pac> ?$/
Show
Sleep 300ms
Screenshot "/home/chmouel/tmp/sharei/tkn-innerloop/shots/demo-spelling-failure.png"
Sleep 3s
Type "tkn-innerloop run /tmp/linters.json --steps check-test-conventions --base main"
Sleep 700ms
Enter
Hide
Wait+Line@180s /pac> ?$/
Show
Sleep 3s
Hide
Type "toggle-defects.sh fix"
Enter
Wait+Line /pac> ?$/
Type "banner.sh fixed"
Enter
Wait+Screen /FIX THE SOURCE/
Wait+Line /pac> ?$/
Show
Sleep 2s
Type "cat pkg/params/innerloop_demo_test.go"
Sleep 700ms
Enter
Hide
Wait+Line /pac> ?$/
Show
Sleep 3s
Hide
Ctrl+L
Show
Type "tkn-innerloop run /tmp/linters.json --base main"
Sleep 700ms
Enter
Hide
Wait+Line@180s /pac> ?$/
Show
Sleep 300ms
Screenshot "/home/chmouel/tmp/sharei/tkn-innerloop/shots/demo-passing.png"
Sleep 5s
Hide
Type "toggle-defects.sh clean"
Enter
Wait+Line /pac> ?$/
Type "banner.sh outro"
Enter
Wait+Screen /CATCH CI RULES/
Wait+Line /pac> ?$/
Show
Sleep 4s
Hide
Type "touch $TKN_INNERLOOP_DEMO_STATE/COMPLETE"
Enter
Sleep 300ms

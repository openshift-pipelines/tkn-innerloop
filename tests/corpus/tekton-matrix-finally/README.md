# tekton-matrix-finally

From tektoncd/pipeline `examples/v1/pipelineruns/beta/ignore-task-error.yaml`; the pinned commit is in `../SOURCES.md`.

Tasks marked `onError: continue`, one of them fanned out with `matrix`, plus a
finally task that checks the ignored failures.

echo-continue fails on purpose. Tekton ignores that, but the local runner would
report it, so the plan records the onError semantics as an adaptation.
echo-continue-with-matrix must loop over both `param1` values or stay
unresolved. Running it once is wrong. The finally verification stays outside the
local run.

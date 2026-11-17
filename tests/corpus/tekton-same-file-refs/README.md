# tekton-same-file-refs

From tektoncd/pipeline `examples/v1/pipelineruns/4808-regression.yaml`; the pinned commit is in `../SOURCES.md`.

A PipelineRun, Pipeline and two Tasks in one file, all linked by name through
pipelineRef and taskRef.

Both Tasks are inlined from the same file, so defined_in is empty. print-result
receives `$(tasks.generate-result.results.RESULT_STRING)`. A good plan has
gen-result write its result to a file, reads that file in print-result through a
script edit, and lists gen-result in depends_on.

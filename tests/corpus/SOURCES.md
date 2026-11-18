# Corpus sources

Each case vendors only the YAML it needs, copied unchanged at its original path under
`<case>/repo/`. Tekton bundle objects the case references are mirrored under
`<case>/bundles/` by `hack/corpus-bundles`. All sources are Apache-2.0 licensed.

| Case | Repository | Commit | Files | Licence |
| --- | --- | --- | --- | --- |
| pac-linter | [tektoncd/pipelines-as-code](https://github.com/tektoncd/pipelines-as-code/tree/71548140eca5d8d7b5c3d4df27319a3875c738ec) | `71548140eca5` | `.tekton/linter.yaml` | Apache-2.0 |
| pac-go-cache-stepactions | [tektoncd/pipelines-as-code](https://github.com/tektoncd/pipelines-as-code/tree/71548140eca5d8d7b5c3d4df27319a3875c738ec) | `71548140eca5` | `.tekton/go.yaml` | Apache-2.0 |
| pac-release-local-task | [tektoncd/pipelines-as-code](https://github.com/tektoncd/pipelines-as-code/tree/71548140eca5d8d7b5c3d4df27319a3875c738ec) | `71548140eca5` | `.tekton/release-pipeline.yaml`<br>`.tekton/tasks/goreleaser.yaml` | Apache-2.0 |
| pac-remote-pipeline-annotation | [tektoncd/pipelines-as-code](https://github.com/tektoncd/pipelines-as-code/tree/71548140eca5d8d7b5c3d4df27319a3875c738ec) | `71548140eca5` | `.tekton/boussole.yaml` | Apache-2.0 |
| pac-konflux-bundles-matrix | [konflux-ci/build-service](https://github.com/konflux-ci/build-service/tree/07182b00cba234389f431e5c77e9475510bfbfdb) | `07182b00cba2` | `.tekton/build-service-pull-request.yaml` | Apache-2.0 |
| pac-konflux-fbc-lightspeed | [openshift/lightspeed-operator](https://github.com/openshift/lightspeed-operator/tree/bd455a2036a86a8c5cde29ab96593134b0f89381) | `bd455a2036a8` | `.tekton/fbc-v4-21-pull-request.yaml` | Apache-2.0 |
| pac-local-pipeline-and-tasks | [openshift-pipelines/pipeline-service](https://github.com/openshift-pipelines/pipeline-service/tree/5b34974a82703dfba477bbec73b0658d49ddc69b) | `5b34974a8270` | `.tekton/pipeline/update-repository.yaml`<br>`.tekton/tasks/update-repository.yaml`<br>`.tekton/update-dependencies.yaml` | Apache-2.0 |
| pac-chains-release | [tektoncd/chains](https://github.com/tektoncd/chains/tree/252e2ab89b0548236aaa486fac2b669b79b6d4b1) | `252e2ab89b05` | `.tekton/release.yaml`<br>`release/release-pipeline.yaml` | Apache-2.0 |
| tekton-cel-when-finally | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/alpha/pipelinerun-with-cel-when-expressions.yaml` | Apache-2.0 |
| tekton-same-file-refs | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/4808-regression.yaml` | Apache-2.0 |
| tekton-extra-kinds-workspaces | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/mapping-workspaces.yaml` | Apache-2.0 |
| tekton-v1beta1-sidecars | [tektoncd/plumbing](https://github.com/tektoncd/plumbing/tree/ebbd4af7c0177c5f3a40bed87e3dccb3ee32a62c) | `ebbd4af7c017` | `tekton/ci/jobs/e2e-kind.yaml`<br>`tekton/ci/shared/common-tasks.yaml` | Apache-2.0 |
| tekton-local-stepactions | [tektoncd/plumbing](https://github.com/tektoncd/plumbing/tree/ebbd4af7c0177c5f3a40bed87e3dccb3ee32a62c) | `ebbd4af7c017` | `tekton/ci/jobs/tekton-golang-coverage.yaml`<br>`tekton/ci/shared/common-tasks.yaml`<br>`tekton/ci/stepactions/gcs-upload.yaml`<br>`tekton/ci/stepactions/git-batch-merge.yaml` | Apache-2.0 |
| tekton-stepaction-params | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/stepaction-params.yaml` | Apache-2.0 |
| tekton-matrix-finally | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/beta/ignore-task-error.yaml` | Apache-2.0 |
| tekton-git-resolver-task | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/beta/git-resolver.yaml` | Apache-2.0 |
| tekton-hub-resolver-pipeline | [tektoncd/pipeline](https://github.com/tektoncd/pipeline/tree/4ee5e53d57f49682d267f009591b165a18e77478) | `4ee5e53d57f4` | `examples/v1/pipelineruns/no-ci/hub-resolver.yaml` | Apache-2.0 |
| tekton-plain-pipeline-entry | [konflux-ci/release-service](https://github.com/konflux-ci/release-service/tree/46488d998d96380b0be7cda8f0e77693f2b43494) | `46488d998d96` | `integration-tests/pipelines/konflux-e2e-tests-pipeline.yaml` | Apache-2.0 |

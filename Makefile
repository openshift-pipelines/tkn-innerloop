PYTHON ?= python3
CORPUS_MODEL ?= github-copilot/claude-sonnet-5.5:minimal

.PHONY: all test lint fix install corpus corpus-bundles innerloop

all: lint test

test:
	$(PYTHON) -m unittest discover -s tests -t .

lint:
	ruff check .
	shellcheck docs/demo/*.sh

fix:
	ruff check --fix .

install:
	$(PYTHON) -m pip install -e .

# Generates plans for every corpus case with a real model; add CORPUS_ARGS=--execute to run them.
corpus:
	hack/corpus-check --model $(CORPUS_MODEL) $(CORPUS_ARGS)

corpus-bundles:
	hack/corpus-bundles

# Runs this repository's own Tekton pipeline locally.
innerloop:
	bin/tkn-innerloop run .tekton/ci.yaml

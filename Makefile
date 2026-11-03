# Second Look (in development). See README.md, Quickstart.
PY := .venv/bin/python
LOCK := uv pip compile --generate-hashes --python-version 3.12 --no-header -q

.PHONY: demo setup test clean-clone-test lock

demo:              ## set up, then serve on http://127.0.0.1:8765
	./scripts/demo.sh

setup:             ## set up only: venv, pinned deps, weights, trust lists, samples
	./scripts/demo.sh --no-serve

$(PY):
	./scripts/demo.sh --no-serve

test: $(PY)        ## the test suite (offline)
	$(PY) -m pytest -q

clean-clone-test:  ## clone HEAD to a temp path, set up, then test and serve with the network denied
	./scripts/clean_clone_test.sh

lock:              ## re-pin requirements/*.lock from requirements/*.in
	$(LOCK) requirements/build.in -o requirements/build.lock
	$(LOCK) requirements/demo.in -o requirements/demo.lock
	$(LOCK) requirements/classifier.in -o requirements/classifier.lock

.PHONY: check backend-test frontend-build browser-acceptance syntax compile
.DEFAULT_GOAL := check

PYTHON ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
NPM ?= npm

backend-test:
	PYTHONPATH=backend $(PYTEST) -q

frontend-build:
	cd frontend && $(NPM) run build

browser-acceptance: frontend-build
	PYTHONPATH=backend $(PYTHON) scripts/browser_acceptance.py

syntax:
	bash -n scripts/*.sh

compile:
	$(PYTHON) -m py_compile backend/app/*.py backend/tests/*.py

check: syntax compile backend-test frontend-build browser-acceptance

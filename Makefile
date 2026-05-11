.PHONY: help install lint typecheck test check
.DEFAULT_GOAL := help

PKG := authority-runtime-python
VENV := $(PKG)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:  ## Show this help
	@grep -hE '^[a-z][a-z0-9_-]*:.*##' $(MAKEFILE_LIST) | \
		awk -F':.*##' '{printf "  %-12s %s\n", $$1, $$2}'

$(VENV):  ## Create the venv on first use
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)  ## Editable install with dev extras
	$(PIP) install -e "$(PKG)[dev]"

lint:  ## ruff check (CI parity)
	cd $(PKG) && .venv/bin/ruff check src/ tests/

typecheck:  ## mypy src/ (CI parity)
	cd $(PKG) && .venv/bin/mypy src/

test:  ## pytest (CI parity)
	cd $(PKG) && .venv/bin/pytest

check: lint typecheck test  ## Run everything CI runs

precommit-install:  ## Install pre-commit hooks
	pip install pre-commit
	pre-commit install

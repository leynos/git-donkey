MDLINT ?= markdownlint-cli2
NIXIE ?= nixie
# `make fmt` and `make check-fmt` call mdtablefix directly. `--git` selects the
# Markdown files Git tracks and `--include-untracked` adds the untracked files
# Git does not ignore, so a new document is formatted before it is staged.
# Both modes need mdtablefix 0.6.0 or later; CI pins the version in
# MDTABLEFIX_VERSION in .github/workflows/ci.yml.
MDTABLEFIX ?= mdtablefix
MDTABLEFIX_SELECT = --git --include-untracked
MDTABLEFIX_RULES = --wrap --renumber --breaks --ellipsis --fences
# Pin Ruff so local and CI runs agree; keep in sync with the ruff== dev
# dependency in pyproject.toml and .github/workflows/ci.yml. Invoking it through
# uv means the pinned version is used regardless of what is on PATH.
RUFF_VERSION ?= 0.16.6
RUFF ?= $(UV_ENV) uv tool run ruff@$(RUFF_VERSION)
# Pin ty likewise. This is the sole ty version declaration: CI runs
# `make typecheck` and installs no separate ty. Diagnostics differ between ty
# releases, so an unpinned ty makes CI fail on errors that never appear locally.
TY_VERSION ?= 0.0.79
TY ?= $(UV_ENV) uv tool run ty@$(TY_VERSION)
TOOLS = $(MDLINT) uv
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
# The CV-005 CodeScene contracts live in shared-actions and run from a full
# commit, so a fix is a pin bump. `.github/cv005.toml` holds this repository's
# only parameters.
CV005_CONTRACTS_REF ?= a38feb9be25755c30eca5bda96bd3786a5b89c6b
CV005_CONTRACTS = $(UV_ENV) uv tool run --python 3.13 \
	--from 'git+https://github.com/leynos/shared-actions@$(CV005_CONTRACTS_REF)\#subdirectory=packages/cv005-contracts' \
	cv005-contracts
# The spelling gate regenerates typos.toml from the live shared dictionary and
# the typos.local.toml overlay on every run, then runs Typos itself, so no
# separate typos pin is needed here.
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.3
TYPOS_CONFIG_BUILDER = $(UV_ENV) uv tool run --python 3.14 --from \
        "git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
        typos-config-builder
# The docstring gate scans these targets recursively, subject to interrogate's
# default exclusions; a test checks that every tracked Python file lies under one.
INTERROGATE_TARGETS ?= git_donkey tests conftest.py
# Level 2 prints a MISSED row per undocumented definition, which the
# `interrogate` target fails on: the rounded percentage alone can pass a lone gap.
INTERROGATE_FLAGS ?= -vv --fail-under 100
INTERROGATE ?= $(UV_ENV) uv run interrogate
# Pylint targets shared by both passes.
PYLINT_TARGETS ?= git_donkey tests
# Spend a tenth of the available cores on Pylint, leaving room for the other
# agents and builds sharing this machine. The floor of two keeps the pass
# parallel on small CI runners, whose core count never reaches the tenth.
PYLINT_JOBS ?= $(shell n=$$(nproc 2>/dev/null || echo 2); \
        echo $$(( n / 10 > 2 ? n / 10 : 2 )))
# Both passes run under the project virtual-env's CPython so they parse the
# project's own syntax and can import the df12-python-lints plugin.
PYLINT = $(UV_ENV) uv run pylint -j $(PYLINT_JOBS)
# The built-in pass reads the [tool.pylint] config in pyproject.toml.
PYLINT_BUILTIN = $(PYLINT)
# The df12-python-lints plugin pass keeps its own config.
PYLINT_DF12 = $(PYLINT) --rcfile=.pylintrc-df12.toml
# The dead-code gate is the installed df12-skylos command; its `skylos` extra
# carries the one Skylos pin. Skylos parses source with its own runtime AST, so
# the tool environment runs Python 3.14.
DF12_SKYLOS_SHA = 56ad71721c3f1f2164ec84b69150582488f1a927
SKYLOS = $(UV_ENV) uv tool run --python 3.14 --from \
	'df12-python-lints[skylos] @ git+https://github.com/leynos/df12-python-lints.git@$(DF12_SKYLOS_SHA)' \
	df12-skylos

.PHONY: help all clean build build-release lint fmt check-fmt \
        markdownlint nixie spelling skylos skylos-allow test test-workflow-contracts typecheck \
        makeutil interrogate \
        $(TOOLS) $(VENV_TOOLS)
.PHONY: pytest test

.DEFAULT_GOAL := all

all: build check-fmt lint typecheck test spelling

.venv: pyproject.toml
	$(UV_ENV) uv venv --clear

build: uv .venv ## Build virtual-env and install deps
	$(UV_ENV) uv sync --group dev

build-release: ## Build artefacts (sdist & wheel)
	python -m build --sdist --wheel

clean: ## Remove build artifacts
	rm -rf build dist *.egg-info \
	  .mypy_cache .pytest_cache .coverage coverage.* \
	  lcov.info htmlcov .venv
	find . -type d -name '__pycache__' -print0 | xargs -0 -r rm -rf

define ensure_tool
	@command -v $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required, but not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

define ensure_tool_venv
	@$(UV_ENV) uv run which $(1) >/dev/null 2>&1 || { \
	  printf "Error: '%s' is required in the virtualenv, but is not installed\n" "$(1)" >&2; \
	  exit 1; \
	}
endef

ifneq ($(strip $(TOOLS)),)
$(TOOLS): ## Verify required CLI tools
	$(call ensure_tool,$@)
endif


ifneq ($(strip $(VENV_TOOLS)),)
.PHONY: $(VENV_TOOLS)
$(VENV_TOOLS): ## Verify required CLI tools in venv
	$(call ensure_tool_venv,$@)
endif

makeutil: ## Verify the Makefile parser required by the contract tests
	$(call ensure_tool,makeutil)

fmt: uv ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDTABLEFIX) --in-place $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)
	@unset FORCE_COLOR; $(MDLINT) --fix "**/*.md"

check-fmt: uv ## Verify formatting
	$(RUFF) format --check
	$(MDTABLEFIX) --check $(MDTABLEFIX_SELECT) $(MDTABLEFIX_RULES)

# No `build` prerequisite: CI runs `make build` as an explicit setup step, and
# every venv-backed command below goes through `uv run`, which syncs the
# project environment on demand. That keeps CI to a single synchronisation
# while leaving `make lint` usable from a clean checkout.
# Interrogate rounds its percentage to one decimal, so `--fail-under 100` alone
# can pass a lone gap in a large repository; fail on any MISSED row as well.
interrogate: uv ## Enforce complete Python docstring coverage
	@report="$$($(INTERROGATE) $(INTERROGATE_FLAGS) $(INTERROGATE_TARGETS))"; \
	  status=$$?; \
	  printf '%s\n' "$$report"; \
	  test "$$status" -eq 0 || exit "$$status"; \
	  if printf '%s\n' "$$report" | grep -q '| *MISSED *|'; then \
	    printf '%s\n' 'interrogate: undocumented definitions remain; see the MISSED rows above' >&2; \
	    exit 1; \
	  fi

lint: uv ## Run linters
	$(RUFF) check
	$(MAKE) --no-print-directory interrogate
	pyscn check git_donkey tests --skip-clones
	$(PYLINT_BUILTIN) $(PYLINT_TARGETS)
	$(PYLINT_DF12) $(PYLINT_TARGETS)
	# ambrleaks is a console script of the df12-python-lints dev dependency, so
	# `uv run` finds it in the synced venv; it is not a separate distribution.
	$(UV_ENV) uv run ambrleaks tests
	$(SKYLOS) check --repository .

skylos: ## Detect dead production code
	$(SKYLOS) check --repository .

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception (SYMBOL=name REASON=text)
	$(SKYLOS) allow --repository . --symbol="$${SKYLOS_SYMBOL}" --reason="$${SKYLOS_REASON}"

typecheck: build uv ## Run typechecking
	$(TY) --version
	$(TY) check

markdownlint: spelling $(MDLINT) ## Lint Markdown files and enforce spelling
	$(MDLINT) '**/*.md'

spelling: ## Enforce en-GB-oxendict spelling in Markdown prose
	$(TYPOS_CONFIG_BUILDER) gate --repository .

nixie: ## Validate Mermaid diagrams
	$(call ensure_tool,nixie)
	$(NIXIE) --no-sandbox

test: build uv $(VENV_TOOLS) makeutil ## Run tests
	$(UV_ENV) uv run pytest -v -n auto

test-workflow-contracts: uv ## Run the shared CV-005 CodeScene contracts
	$(CV005_CONTRACTS) check --repository .

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'

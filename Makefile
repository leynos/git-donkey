MDLINT ?= markdownlint-cli2
NIXIE ?= nixie
MDFORMAT_ALL ?= mdformat-all
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
SKYLOS_VERSION ?= 4.33.2
TYPOS_VERSION ?= 1.48.0
TOOLS = $(MDFORMAT_ALL) $(MDLINT) uv
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
# Pylint targets shared by both passes.
PYLINT_TARGETS ?= git_donkey scripts tests
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
# Skylos parses source using its own runtime AST. Python 3.14 prevents phantom
# dead-code findings when source uses syntax unavailable to an older runtime.
SKYLOS_CLI = $(UV_ENV) uv tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS = $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= git_donkey
SKYLOS_EXCLUDE_FOLDERS ?= tests

.PHONY: help all clean build build-release lint fmt check-fmt \
        markdownlint nixie spelling spelling-helper-test skylos-allow test typecheck \
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

fmt: uv $(MDFORMAT_ALL) ## Format sources
	$(RUFF) format
	$(RUFF) check --select I --fix
	$(MDFORMAT_ALL)

check-fmt: uv ## Verify formatting
	$(RUFF) format --check
	# mdformat-all doesn't currently do checking

# No `build` prerequisite: CI runs `make build` as an explicit setup step, and
# every venv-backed command below goes through `uv run`, which syncs the
# project environment on demand. That keeps CI to a single synchronisation
# while leaving `make lint` usable from a clean checkout.
lint: uv ## Run linters
	$(RUFF) check
	$(UV_ENV) uv run interrogate --fail-under 100 git_donkey
	pyscn check git_donkey tests --skip-clones
	$(PYLINT_BUILTIN) $(PYLINT_TARGETS)
	$(PYLINT_DF12) $(PYLINT_TARGETS)
	# ambrleaks is a console script of the df12-python-lints dev dependency, so
	# `uv run` finds it in the synced venv; it is not a separate distribution.
	$(UV_ENV) uv run ambrleaks tests
	$(SKYLOS) $(SKYLOS_PRODUCTION_TARGETS) --exclude $(SKYLOS_EXCLUDE_FOLDERS) \
		--category dead_code --gate --format concise \
		--no-upload --no-provenance --no-grep-verify

skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\n" >&2; exit 2;; esac
	$(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

typecheck: build uv ## Run typechecking
	$(TY) --version
	# scripts/ holds PEP 723 single-file helpers that import each other by
	# module name; ty needs them on the search path to resolve those imports.
	$(TY) check --extra-search-path scripts

markdownlint: spelling $(MDLINT) ## Lint Markdown files and enforce spelling
	$(MDLINT) '**/*.md'

spelling: spelling-helper-test ## Enforce en-GB-oxendict spelling in Markdown prose
	@$(UV_ENV) uv run scripts/generate_typos_config.py
	@git ls-files -z '*.md' | \
		xargs -0 -r env $(UV_ENV) uv tool run typos@$(TYPOS_VERSION) \
		--config typos.toml --force-exclude

spelling-helper-test: ## Validate the shared spelling-policy integration
	@$(RUFF) format --isolated \
		--target-version py313 --check scripts/generate_typos_config.py \
		scripts/typos_rollout.py scripts/typos_rollout_cache.py \
		scripts/tests/test_typos_rollout.py
	# --extend-select S310: these helpers open URLs, and their `noqa: S310`
	# directives carry the justification for doing so. Ruff's default set omits
	# S310, which would leave those suppressions unused rather than earned.
	@$(RUFF) check --isolated --extend-select S310 \
		--target-version py313 scripts/generate_typos_config.py \
		scripts/typos_rollout.py scripts/typos_rollout_cache.py \
		scripts/tests/test_typos_rollout.py
	@PYTHONPATH=scripts $(UV_ENV) uv run --no-project --python 3.13 \
		--with pytest==9.0.2 --with pytest-cov==7.0.0 \
		python -m pytest scripts/tests/test_typos_rollout.py \
		-c /dev/null --rootdir=. -p no:cacheprovider \
		--cov=generate_typos_config --cov=typos_rollout \
		--cov=typos_rollout_cache --cov-fail-under=90

nixie: ## Validate Mermaid diagrams
	$(call ensure_tool,nixie)
	$(NIXIE) --no-sandbox

test: build uv $(VENV_TOOLS) ## Run tests
	$(UV_ENV) uv run pytest -v -n auto

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'

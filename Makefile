MDLINT ?= markdownlint-cli2
NIXIE ?= nixie
MDFORMAT_ALL ?= mdformat-all
# Pin Ruff so local and CI runs agree; keep in sync with .github/workflows/ci.yml.
RUFF_VERSION ?= 0.15.22
TYPOS_VERSION ?= 1.48.0
TOOLS = $(MDFORMAT_ALL) ty $(MDLINT) uv
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
# Pylint targets shared by both passes.
PYLINT_TARGETS ?= git_donkey scripts tests
# The built-in Pylint pass runs under the PyPy shim for speed, mirroring lading.
# It reads the [tool.pylint] config in pyproject.toml.
PYLINT_PYTHON ?= pypy
PYLINT_PYPY_SHIM_REF ?= 726d09f968b4d729ee4b29c71fc732e744854f3b
PYLINT_PYPY_SHIM = git+https://github.com/leynos/pylint-pypy-shim.git@$(PYLINT_PYPY_SHIM_REF)
PYLINT_PYPY = $(UV_ENV) uv tool run --python $(PYLINT_PYTHON) \
        --from '$(PYLINT_PYPY_SHIM)' pylint-pypy
# The df12-python-lints plugin runs under CPython with its own config so it can
# import the plugin and parse the project's Python 3.13 syntax.
PYLINT_DF12 = $(UV_ENV) uv run pylint --rcfile=.pylintrc-df12.toml

.PHONY: help all clean build build-release lint fmt check-fmt \
        markdownlint nixie spelling spelling-helper-test test typecheck ruff \
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

ruff: ## Verify Ruff is installed and pinned to $(RUFF_VERSION)
	$(call ensure_tool,ruff)
	@scripts/check-ruff-version.sh "$(RUFF_VERSION)"

fmt: ruff $(MDFORMAT_ALL) ## Format sources
	ruff format
	ruff check --select I --fix
	$(MDFORMAT_ALL)

check-fmt: ruff ## Verify formatting
	ruff format --check
	# mdformat-all doesn't currently do checking

lint: ruff build ## Run linters
	ruff check
	$(UV_ENV) uv run interrogate --fail-under 100 git_donkey
	pyscn check git_donkey tests --skip-clones
	$(PYLINT_PYPY) $(PYLINT_TARGETS)
	$(PYLINT_DF12) $(PYLINT_TARGETS)
	$(UV_ENV) uv run ambrleaks tests

typecheck: build ty ## Run typechecking
	ty --version
	ty check

markdownlint: spelling $(MDLINT) ## Lint Markdown files and enforce spelling
	$(MDLINT) '**/*.md'

spelling: spelling-helper-test ## Enforce en-GB-oxendict spelling in Markdown prose
	@$(UV_ENV) uv run scripts/generate_typos_config.py
	@git ls-files -z '*.md' | \
		xargs -0 -r env $(UV_ENV) uv tool run typos@$(TYPOS_VERSION) \
		--config typos.toml --force-exclude

spelling-helper-test: ## Validate the shared spelling-policy integration
	@$(UV_ENV) uv tool run ruff@$(RUFF_VERSION) format --isolated \
		--target-version py313 --check scripts/generate_typos_config.py \
		scripts/typos_rollout.py scripts/typos_rollout_cache.py \
		scripts/tests/test_typos_rollout.py
	@$(UV_ENV) uv tool run ruff@$(RUFF_VERSION) check --isolated \
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

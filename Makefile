MDLINT ?= markdownlint-cli2
NIXIE ?= nixie
MDFORMAT_ALL ?= mdformat-all
# Pin Ruff so local and CI runs agree; keep in sync with .github/workflows/ci.yml.
RUFF_VERSION ?= 0.15.12
TOOLS = $(MDFORMAT_ALL) ty $(MDLINT) uv
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools

.PHONY: help all clean build build-release lint fmt check-fmt \
        markdownlint nixie test typecheck ruff $(TOOLS) $(VENV_TOOLS)

.DEFAULT_GOAL := all

all: build check-fmt lint typecheck test

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
	@installed="$$(ruff --version | awk '{print $$2}')"; \
	if [ "$$installed" != "$(RUFF_VERSION)" ]; then \
	  printf "Error: ruff %s is required, but %s is installed\n" \
	    "$(RUFF_VERSION)" "$$installed" >&2; \
	  exit 1; \
	fi

fmt: ruff $(MDFORMAT_ALL) ## Format sources
	ruff format
	ruff check --select I --fix
	$(MDFORMAT_ALL)

check-fmt: ruff ## Verify formatting
	ruff format --check
	# mdformat-all doesn't currently do checking

lint: ruff ## Run linters
	ruff check
	pyscn check git_donkey tests --skip-clones

typecheck: build ty ## Run typechecking
	ty --version
	ty check

markdownlint: $(MDLINT) ## Lint Markdown files
	$(MDLINT) '**/*.md'

nixie: ## Validate Mermaid diagrams
	$(call ensure_tool,nixie)
	$(NIXIE) --no-sandbox

test: build uv $(VENV_TOOLS) ## Run tests
	$(UV_ENV) uv run pytest -v -n auto

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'

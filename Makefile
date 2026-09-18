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
SKYLOS_VERSION ?= 4.33.2
TOOLS = $(MDLINT) uv
VENV_TOOLS = pytest
UV_ENV = UV_CACHE_DIR=.uv-cache UV_TOOL_DIR=.uv-tools
# The spelling gate regenerates typos.toml from the live shared dictionary and
# the typos.local.toml overlay on every run, then runs Typos itself, so no
# separate typos pin is needed here.
TYPOS_CONFIG_BUILDER_VERSION ?= v0.1.1
TYPOS_CONFIG_BUILDER = $(UV_ENV) uv tool run --from \
        "git+https://github.com/leynos/typos-config-builder.git@$(TYPOS_CONFIG_BUILDER_VERSION)" \
        typos-config-builder
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
# Skylos parses source using its own runtime AST. Python 3.14 prevents phantom
# dead-code findings when source uses syntax unavailable to an older runtime.
SKYLOS_CLI = $(UV_ENV) uv tool run --python 3.14 --from 'skylos==$(SKYLOS_VERSION)' skylos
SKYLOS = $(SKYLOS_CLI) --config-file pyproject.toml
SKYLOS_PRODUCTION_TARGETS ?= git_donkey
SKYLOS_EXCLUDE_FOLDERS ?= tests
SKYLOS_WHITELIST_LOCK ?= .skylos-whitelist.lock

.PHONY: help all clean build build-release lint fmt check-fmt \
        markdownlint nixie spelling skylos-allow test typecheck \
        makeutil \
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

# flock execs its command directly, with no shell to apply them, so the cache
# and tool directories reach uv as env arguments rather than as the leading
# assignments a recipe line would otherwise carry.
skylos-allow: export SKYLOS_SYMBOL = $(value SYMBOL)
skylos-allow: export SKYLOS_REASON = $(value REASON)
skylos-allow: ## Document one named Skylos exception, not an entry point
	@case "$${SKYLOS_SYMBOL}" in *[![:space:]]*) ;; *) printf "Error: SYMBOL is required for a named whitelist exception\n" >&2; exit 2;; esac
	@case "$${SKYLOS_REASON}" in *[![:space:]]*) ;; *) printf "Error: REASON is required for a named whitelist exception\n" >&2; exit 2;; esac
	flock "$(SKYLOS_WHITELIST_LOCK)" env $(SKYLOS_CLI) whitelist "$${SKYLOS_SYMBOL}" --reason "$${SKYLOS_REASON}"

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

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS=":"; printf "Available targets:\n"} {printf "  %-20s %s\n", $$1, $$2}'

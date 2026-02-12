.PHONY: help lint test validate_schemas validate_specs smoke android_smoke oracle_smoke audit_bundle run_public report report_regression_subset autoglm_setup autoglm_example phase1_smoke_host phase1_smoke_compose phase1_smoke phase1_smoke_down clean

PYTHON ?= python3
PYTHONPATH_VALUE := mas-harness/src
AUDIT_PATH ?= runs/public

help:
	@echo "Targets:"
	@echo "  validate_schemas  Validate MAS spec JSON schemas and all case specs"
	@echo "  validate_specs    Alias for validate_schemas"
	@echo "  lint              Run ruff lint"
	@echo "  test              Run unit tests"
	@echo "  smoke             Run the toy smoke case (no Android required)"
	@echo "  phase1_smoke      Run AgentBeats Phase 1 smoke (requires host emulator)"
	@echo "  android_smoke     Probe Android controller capabilities"
	@echo "  oracle_smoke      Run Phase2 Oracle regression mini-suite"
	@echo "  audit_bundle      Audit an Evidence Pack bundle (set AUDIT_PATH=...)"
	@echo "  run_public         Run all public cases"
	@echo "  report            Aggregate latest run results"
	@echo "  report_regression_subset  Bucket regression subset + assertion stats"
	@echo "  autoglm_setup     Create venv for autoglm adapter"
	@echo "  autoglm_example   Run AutoGLM example via its venv"
	@echo "  clean             Remove run artifacts"

validate_schemas:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.spec.validate_specs \
		--spec_dir mas-spec/schemas \
		--cases_dir mas-public/cases

validate_specs: validate_schemas

lint:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m ruff check mas-harness/src mas-harness/tests

test:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m pytest -q

smoke:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.runtime.run_public \
		--cases_dir mas-public/cases/smoke_001 \
		--out_dir runs/smoke \
		--seed 0

UNAME_S := $(shell uname -s)
PHASE1_SMOKE_ENV_FILE :=
ifneq ("$(wildcard .env)","")
PHASE1_SMOKE_ENV_FILE := --env-file .env
endif
PHASE1_SMOKE_ENV_PREFIX :=
ifeq ($(UNAME_S),Darwin)
# Work around Docker Desktop (macOS) buildx/bake gRPC header issue:
#   header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters
PHASE1_SMOKE_ENV_PREFIX := DOCKER_BUILDKIT=0 COMPOSE_BAKE=0
endif
PHASE1_SMOKE_COMPOSE := $(PHASE1_SMOKE_ENV_PREFIX) docker compose $(PHASE1_SMOKE_ENV_FILE) -f agentbeats/scenarios/phase1_smoke/compose.yaml
ifeq ($(UNAME_S),Linux)
PHASE1_SMOKE_COMPOSE += -f agentbeats/scenarios/phase1_smoke/compose.linux.yaml
endif

phase1_smoke_host:
	$(MAKE) -C agentbeats/emulator_host host-emulator-up

phase1_smoke_compose:
	$(PHASE1_SMOKE_COMPOSE) up --abort-on-container-exit --exit-code-from phase1_smoke --build

phase1_smoke: phase1_smoke_host phase1_smoke_compose

phase1_smoke_down:
	$(PHASE1_SMOKE_COMPOSE) down -v --remove-orphans

android_smoke:
	@PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.tools.android_smoke \
		--out_dir runs/android_smoke --print_out_dir

oracle_smoke:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m pytest -q mas-harness/tests/unit/oracles/test_oracle_regression_minisuite.py

audit_bundle:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.tools.audit_bundle $(AUDIT_PATH)

run_public:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.runtime.run_public \
		--cases_dir mas-public/cases \
		--out_dir runs/public \
		--seed 0

report:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.reporting.aggregate \
		--runs_dir runs/public \
		--out runs/report.json

report_regression_subset:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m mas_harness.cli.report_regression_subset \
		--runs_dir runs/public \
		--out runs/regression_subset_report.json

AUTOGLM_DIR := mas-agents/adapters/autoglm
AUTOGLM_ENV_PY := $(AUTOGLM_DIR)/env/.venv/bin/python

autoglm_setup:
	bash $(AUTOGLM_DIR)/bootstrap_venv.sh

autoglm_example: autoglm_setup
	$(AUTOGLM_ENV_PY) $(AUTOGLM_DIR)/example_autoglm.py

clean:
	rm -rf runs

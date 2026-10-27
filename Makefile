.DEFAULT_GOAL := help
MODEL ?=
ORIGINAL_MODEL := $(shell /usr/bin/plutil -extract backend raw -o - service.json)
DEFAULT_MODEL := $(shell /usr/bin/plutil -extract model raw -o - service_defaults.json)
ACTIVE_MODEL := $(or $(strip $(MODEL)),$(DEFAULT_MODEL))
export MODEL_BACKEND := $(ACTIVE_MODEL)
MLX_MODELS := voxcpm2 qwen3 fish
NATIVE_MODELS := moss higgs
VENV_EXPLICIT := $(if $(filter undefined,$(origin VENV)),0,1)
VENV ?= $(CURDIR)/$(if $(filter $(ACTIVE_MODEL),$(ORIGINAL_MODEL)),.venv,$(if $(filter $(ACTIVE_MODEL),$(MLX_MODELS)),.venv-mlx,$(if $(filter $(ACTIVE_MODEL),$(NATIVE_MODELS)),.venv-native,.venv-$(ACTIVE_MODEL))))
export VENV
PYTHON := $(VENV)/bin/python
BOOTSTRAP_PYTHON ?=
BOOTSTRAP := sh ./bootstrap_python.sh
PORT_EXPLICIT := $(if $(filter undefined,$(origin PORT)),0,1)
PORT ?= $(shell /usr/bin/plutil -extract port raw -o - backends/$(ACTIVE_MODEL)/service.json)
DATA_DIR_EXPLICIT := $(if $(filter undefined,$(origin DATA_DIR)),0,1)
OUTPUT_DIR_EXPLICIT := $(if $(filter undefined,$(origin OUTPUT_DIR)),0,1)
NONINTERACTIVE ?= 0
NO_LLM ?= 0
LLM_PYTHON ?=
QUEUE_BOOTSTRAP_PYTHON ?=
export BOOTSTRAP_PYTHON LLM_PYTHON QUEUE_BOOTSTRAP_PYTHON
LLM_FLAG := $(if $(filter 1,$(NO_LLM)),--no-llm,)
ifneq ($(origin PROMPT_LLM_DIR),undefined)
export PROMPT_LLM_DIR
endif
# Python configuration reads saved storage.json; Make only exports explicit paths.
DATA_DIR ?= $(CURDIR)
OUTPUT_DIR ?= $(DATA_DIR)/outputs
ifeq ($(DATA_DIR_EXPLICIT),1)
export DATA_DIR
endif
ifeq ($(OUTPUT_DIR_EXPLICIT),1)
export OUTPUT_DIR
endif
export PORT
QUEUE_PORT ?= $(shell /usr/bin/plutil -extract port raw -o - queue_service.json)
TEST_PYTHON ?= $(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,$(PYTHON))
QUEUE_PYTHON ?= $(if $(wildcard $(CURDIR)/.venv-queue/bin/python),$(CURDIR)/.venv-queue/bin/python,$(PYTHON))
export QUEUE_PORT QUEUE_PYTHON

.PHONY: help setup-selected serve-selected setup setup-env setup-queue setup-llm setup-transcriber download-llm configure-storage download download-extensions verify serve queue stop-queue queue-status start stop status health test smoke open sync-check
help:
	@echo 'make setup | download | verify | serve | queue | stop-queue | queue-status | start | stop | status | health | test | smoke | open'
	@echo 'Overrides: MODEL=voxcpm2|qwen3|chatterbox|indextts|moss|fish|higgs, PORT, VENV, BOOTSTRAP_PYTHON, DATA_DIR, WEIGHTS_DIR, OUTPUT_DIR'
	@echo 'make setup installs/reuses the shared prompt LLM; make setup-llm adds it to an existing installation'
	@echo 'make setup includes automatic local voice transcription; make setup-transcriber adds it to existing installations'
	@echo 'make serve enables the prompt LLM by default; NO_LLM=1 disables it and skips the LLM question'
	@echo 'Generator UI/API: localhost:$(PORT); independent queue CRUD: localhost:$(QUEUE_PORT)'
	@echo 'Default model: $(DEFAULT_MODEL); queue submissions may omit model to use this default'
	@echo 'Setup reuses compatible installed Python or installs it project-locally; serving stays offline'
setup:
	@$(BOOTSTRAP) setup startup_select.py setup $(if $(MODEL),--model "$(MODEL)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,) $(if $(filter 1,$(VENV_EXPLICIT)),--keep-venv,) $(if $(filter 1,$(PORT_EXPLICIT)),--keep-port,)
setup-selected: configure-storage
	$(MAKE) setup-env
	$(MAKE) setup-queue
	$(MAKE) download
	$(MAKE) setup-llm
	$(MAKE) setup-transcriber
setup-transcriber:
	$(BOOTSTRAP) setup setup_transcription.py
setup-queue:
	$(BOOTSTRAP) setup python_runtime.py setup-queue
setup-llm:
	$(BOOTSTRAP) setup setup_prompt_llm.py
download-llm:
	"$(CURDIR)/.venv-enhancer/bin/python" prompt_assets.py
configure-storage:
	$(BOOTSTRAP) setup configure_storage.py $(if $(filter 1,$(DATA_DIR_EXPLICIT)),--data-dir "$(DATA_DIR)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,)
setup-env:
	$(BOOTSTRAP) setup runtime_setup.py
	"$(PYTHON)" preflight.py --runtime

download:
	"$(PYTHON)" download.py
download-extensions:
	"$(PYTHON)" extension_assets.py
verify:
	"$(PYTHON)" -c "from assets import all_errors; errors=all_errors(full=True); print(errors or 'All model and extension checksums verified'); raise SystemExit(bool(errors))"
serve:
	@$(BOOTSTRAP) run startup_select.py serve $(if $(MODEL),--model "$(MODEL)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,) $(if $(filter 1,$(VENV_EXPLICIT)),--keep-venv,) $(if $(filter 1,$(PORT_EXPLICIT)),--keep-port,) $(LLM_FLAG)
serve-selected:
	"$(PYTHON)" server.py --model $(ACTIVE_MODEL) --port $(PORT) --queue-port $(QUEUE_PORT) $(LLM_FLAG)
queue:
	"$(QUEUE_PYTHON)" queue_manage.py serve --port $(QUEUE_PORT)
stop-queue:
	"$(QUEUE_PYTHON)" queue_manage.py stop --port $(QUEUE_PORT)
queue-status:
	"$(QUEUE_PYTHON)" queue_manage.py status --port $(QUEUE_PORT)
start:
	$(BOOTSTRAP) run manage.py start --port $(PORT) --queue-port $(QUEUE_PORT) $(if $(MODEL),--model "$(MODEL)",) $(LLM_FLAG)
stop:
	$(BOOTSTRAP) run manage.py stop --port $(PORT)
status:
	$(BOOTSTRAP) run manage.py status --port $(PORT)
health:
	$(BOOTSTRAP) run manage.py health --port $(PORT)
open:
	open http://127.0.0.1:$(PORT)/
test:
	MODEL_BACKEND=voxcpm2 "$(TEST_PYTHON)" -c 'from config import prepare_storage; prepare_storage()'
	MODEL_BACKEND=voxcpm2 "$(TEST_PYTHON)" -m unittest discover -s tests -t . -p 'test_*.py' -v
smoke:
	"$(PYTHON)" -m tests.smoke_test --port $(PORT)
sync-check:
	"$(PYTHON)" sync_source.py --check "$(PEER)"

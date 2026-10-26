.DEFAULT_GOAL := help
MODEL ?=
ORIGINAL_MODEL := $(shell /usr/bin/plutil -extract backend raw -o - service.json)
ifneq ($(strip $(MODEL)),)
export MODEL_BACKEND := $(MODEL)
endif
MLX_MODELS := voxcpm2 qwen3 fish
NATIVE_MODELS := moss higgs
VENV_EXPLICIT := $(if $(filter undefined,$(origin VENV)),0,1)
VENV ?= $(CURDIR)/$(if $(filter $(MODEL),$(ORIGINAL_MODEL)),.venv,$(if $(MODEL),$(if $(filter $(MODEL),$(MLX_MODELS)),.venv-mlx,$(if $(filter $(MODEL),$(NATIVE_MODELS)),.venv-native,.venv-$(MODEL))),.venv))
export VENV
PYTHON := $(VENV)/bin/python
BOOTSTRAP_PYTHON ?= $(if $(MODEL),$(if $(filter $(MODEL),$(MLX_MODELS)),python3.14,$(or $(firstword $(wildcard $(CURDIR)/.python/*/bin/python3.11)),python3.11)),python3.14)
PORT_EXPLICIT := $(if $(filter undefined,$(origin PORT)),0,1)
PORT ?= $(shell /usr/bin/plutil -extract port raw -o - $(if $(MODEL),backends/$(MODEL)/service.json,service.json))
DATA_DIR_EXPLICIT := $(if $(filter undefined,$(origin DATA_DIR)),0,1)
OUTPUT_DIR_EXPLICIT := $(if $(filter undefined,$(origin OUTPUT_DIR)),0,1)
NONINTERACTIVE ?= 0
DATA_DIR ?= $(shell "$(BOOTSTRAP_PYTHON)" -c 'import json; from pathlib import Path; p=Path("storage.json"); print(json.loads(p.read_text())["data_dir"] if p.is_file() else Path.cwd())')
OUTPUT_DIR ?= $(DATA_DIR)/outputs
ifeq ($(DATA_DIR_EXPLICIT),1)
export DATA_DIR
endif
ifeq ($(OUTPUT_DIR_EXPLICIT),1)
export OUTPUT_DIR
endif
export PORT

.PHONY: help setup-selected serve-selected setup setup-env configure-storage download download-extensions verify serve start stop status health test smoke open sync-check
help:
	@echo 'make setup | download | verify | serve | start | stop | status | health | test | smoke | open'
	@echo 'Overrides: MODEL=voxcpm2|qwen3|chatterbox|indextts|moss|fish|higgs, PORT, VENV, BOOTSTRAP_PYTHON, DATA_DIR, WEIGHTS_DIR, OUTPUT_DIR'
setup:
	@"$(BOOTSTRAP_PYTHON)" startup_select.py setup $(if $(MODEL),--model "$(MODEL)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,) $(if $(filter 1,$(VENV_EXPLICIT)),--keep-venv,) $(if $(filter 1,$(PORT_EXPLICIT)),--keep-port,)
setup-selected: configure-storage
	$(MAKE) setup-env
	$(MAKE) download
configure-storage:
	"$(BOOTSTRAP_PYTHON)" configure_storage.py $(if $(filter 1,$(DATA_DIR_EXPLICIT)),--data-dir "$(DATA_DIR)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,)
setup-env:
	"$(BOOTSTRAP_PYTHON)" runtime_setup.py
	"$(PYTHON)" preflight.py --runtime

download:
	"$(PYTHON)" download.py
download-extensions:
	"$(PYTHON)" extension_assets.py
verify:
	"$(PYTHON)" -c "from assets import all_errors; errors=all_errors(full=True); print(errors or 'All model and extension checksums verified'); raise SystemExit(bool(errors))"
serve:
	@"$(BOOTSTRAP_PYTHON)" startup_select.py serve $(if $(MODEL),--model "$(MODEL)",) $(if $(filter 1,$(NONINTERACTIVE)),--non-interactive,) $(if $(filter 1,$(VENV_EXPLICIT)),--keep-venv,) $(if $(filter 1,$(PORT_EXPLICIT)),--keep-port,)
serve-selected:
	"$(PYTHON)" server.py --port $(PORT)
start:
	"$(PYTHON)" manage.py start --port $(PORT)
stop:
	"$(PYTHON)" manage.py stop --port $(PORT)
status:
	"$(PYTHON)" manage.py status --port $(PORT)
health:
	"$(PYTHON)" manage.py health --port $(PORT)
open:
	open http://127.0.0.1:$(PORT)/
test:
	"$(PYTHON)" -c 'from config import prepare_storage; prepare_storage()'
	"$(PYTHON)" -m unittest -v test_contract test_extensions test_storage_configuration test_backends test_pause_edits test_queue test_inference_locks test_performance_controls test_punctuation_pacing
smoke:
	"$(PYTHON)" smoke_test.py --port $(PORT)
sync-check:
	"$(PYTHON)" sync_source.py --check "$(PEER)"

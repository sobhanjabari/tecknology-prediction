# ---------------------------------------------------------------------------
# Technology-intelligence repository tasks.
#
# GNU make is a convenience here, not a requirement: every target is a single
# plain command you can copy-paste and run by hand. Windows frequently has no
# GNU make installed, so `tasks.ps1` mirrors these targets exactly:
#
#     .\tasks.ps1 <target>
#
# Override any variable, e.g.
#     make pipeline INPUT=my_export.txt OUT=out/run1
#     make demo DEMO_N=200
# ---------------------------------------------------------------------------

PYTHON       ?= python
INPUT        ?=
OUT          ?= out
DEMO_N       ?= 80
SPECS        ?=
NETWORKS_CSV ?= Networks/acoustic sensor.csv

.DEFAULT_GOAL := help
.PHONY: help test test-techintel test-openalex test-unittest demo pipeline networks compile clean

# ------------------------------------------------------------------- help
help:
	@echo Technology-intelligence repository tasks
	@echo -------------------------------------------------------------
	@echo   make test            both test suites via pytest
	@echo   make test-unittest   both test suites via unittest discover
	@echo   make demo            pipeline on synthetic data, output in $(OUT)/demo
	@echo   make pipeline        pipeline on INPUT (default: unset)
	@echo   make networks        Networks/Networks.py on NETWORKS_CSV
	@echo   make compile         byte-compile every Python file (syntax check)
	@echo   make clean           remove caches and generated output
	@echo -------------------------------------------------------------
	@echo   PYTHON=$(PYTHON) OUT=$(OUT) DEMO_N=$(DEMO_N) SPECS=$(SPECS)

# ------------------------------------------------------------------ tests
test:
	$(PYTHON) -m pytest openalex/tests techintel/tests -q

test-openalex:
	$(PYTHON) -m pytest openalex/tests -q

test-techintel:
	$(PYTHON) -m pytest techintel/tests -q

test-unittest:
	$(PYTHON) -m unittest discover -s openalex/tests -t .
	$(PYTHON) -m unittest discover -s techintel/tests -t .

# --------------------------------------------------------------- pipeline
demo:
	$(PYTHON) -m techintel.pipeline --demo $(DEMO_N) --out $(OUT)/demo $(if $(SPECS),--specs $(SPECS),)

pipeline:
	$(PYTHON) -m techintel.pipeline --input "$(INPUT)" --out $(OUT) $(if $(SPECS),--specs $(SPECS),)

networks:
	$(PYTHON) "Networks/Networks.py" --csv "$(NETWORKS_CSV)" --out $(OUT)/networks

# ---------------------------------------------------------------- hygiene
compile:
	$(PYTHON) -m compileall -q techintel openalex Networks

clean:
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('.pytest_cache', '$(OUT)')]"

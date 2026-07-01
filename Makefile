# Campgrounds ingestion pipeline - single documented entrypoint.
# Each target delegates to `python3 -m pipeline.cli <stage>`. No third-party
# dependencies: stdlib Python 3.10+ only.

PY ?= python3
SOURCE ?=
SNAPSHOT ?=
TARGET ?=
CONFIRM ?=

# Sibling repo checkouts that consume this pipeline's output.
WEB_REPO ?= ../gpxplore-web
IOS_REPO ?= ../gpxplore-ios

# Optional args passed through to the CLI.
SOURCE_ARG = $(if $(SOURCE),--source $(SOURCE),)
SNAPSHOT_ARG = $(if $(SNAPSHOT),--snapshot $(SNAPSHOT),)
CONFIRM_ARG = $(if $(CONFIRM),--confirm,)
DOWNSTREAM_SNAPSHOT_ARG = $(if $(SNAPSHOT),--snapshot=$(SNAPSHOT),)

.PHONY: help fetch fetch-live normalize merge validate compact publish publish-confirm ios-snapshot publish-downstream publish-all pipeline check-updates blm-verify test clean

help:
	@echo "Campgrounds ingestion pipeline"
	@echo ""
	@echo "  make fetch [SOURCE=usfs]     Copy checksummed offline snapshots into data/raw/"
	@echo "  make fetch-live [SOURCE=id]  Fetch from confirmed live ArcGIS endpoints"
	@echo "  make normalize [SOURCE=mt]   Normalize raw -> canonical GeoJSON"
	@echo "  make merge                   Merge all sources into one dated snapshot"
	@echo "  make validate                Schema/bounds/dupe checks + diff report (fails loudly)"
	@echo "  make compact                 Build app-facing CampRecord[] JSON files"
	@echo "  make publish                 Write reviewable artifact to data/publish/"
	@echo "  make publish-confirm TARGET=<dir>  Copy compact output into an external dir"
	@echo "  make ios-snapshot            Build gpxplore-ios's gzipped marker/detail snapshot from compact output"
	@echo "  make publish-downstream [CONFIRM=1]  Stage into gpxplore-web + build/copy iOS snapshot (dry run by default)"
	@echo "  make publish-all [CONFIRM=1]  Run pipeline, then publish-downstream"
	@echo "  make pipeline                Run fetch->normalize->merge->validate->compact"
	@echo "  make check-updates [SOURCE=blm]  Check if a source changed upstream since our snapshot (network, read-only)"
	@echo "  make blm-verify              Probe candidate BLM live endpoint vs snapshot"
	@echo "  make test                    Run the unittest suite"
	@echo "  make clean                   Remove generated data/ (keeps raw snapshots in the bundle)"

fetch:
	$(PY) -m pipeline.cli fetch $(SOURCE_ARG)

fetch-live:
	$(PY) -m pipeline.cli fetch $(SOURCE_ARG) --live

normalize:
	$(PY) -m pipeline.cli normalize $(SOURCE_ARG) $(SNAPSHOT_ARG)

merge:
	$(PY) -m pipeline.cli merge $(SNAPSHOT_ARG)

validate:
	$(PY) -m pipeline.cli validate $(SNAPSHOT_ARG)

compact:
	$(PY) -m pipeline.cli compact $(SNAPSHOT_ARG)

publish:
	$(PY) -m pipeline.cli publish $(SNAPSHOT_ARG)

publish-confirm:
	$(PY) -m pipeline.cli publish $(SNAPSHOT_ARG) --target $(TARGET) --confirm

ios-snapshot:
	$(PY) -m pipeline.cli ios-snapshot $(SNAPSHOT_ARG)

publish-downstream:
	WEB_REPO=$(WEB_REPO) IOS_REPO=$(IOS_REPO) \
		scripts/publish_downstream.sh $(CONFIRM_ARG) $(DOWNSTREAM_SNAPSHOT_ARG)

publish-all: pipeline publish-downstream

pipeline:
	$(PY) -m pipeline.cli pipeline

check-updates:
	$(PY) -m pipeline.cli check-updates $(SOURCE_ARG)

blm-verify:
	$(PY) -m pipeline.cli blm-verify

test:
	$(PY) -m unittest discover -s tests -v

clean:
	rm -rf data/raw data/processed data/merged data/reports data/compact data/publish data/ios-snapshot

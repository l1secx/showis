.PHONY: help bootstrap dev lint typecheck test schemas docker-up docker-down backup restore demo demo-smoke seed-keys eval gate

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
PYTEST ?= .venv/bin/pytest

help:
	@echo "Showis OSINT Monorepo Komutları:"
	@echo "  make bootstrap   - .venv kurar ve tüm bağımlılıkları + yerel paketleri kurar (tek komut)"
	@echo "  make dev          - API ve Web uygulamasını geliştirme modunda başlatır"
	@echo "  make lint         - Kod format ve lint kontrollerini çalıştırır"
	@echo "  make typecheck    - Mypy ile statik tip kontrollerini çalıştırır"
	@echo "  make test         - Tüm birim ve entegrasyon testlerini çalıştırır"
	@echo "  make schemas      - Pydantic modellerinden JSON ve TypeScript şemalarını üretir"
	@echo "  make docker-up    - Docker compose servislerini başlatır"
	@echo "  make docker-down  - Docker compose servislerini durdurur"

schemas:
	PYTHONPATH=packages/contracts $(PYTHON) packages/contracts/scripts/export_schemas.py

# SET-01/SET-02: tek komut bootstrap. Yerel showis-* paketleri PyPI'da yok;
# bu yüzden root pyproject bağımlılıklarında değil, bu hedefte -e ile kurulur.
# Root meta-paket (pyproject.toml) runtime + dev bağımlılıklarını getirir.
bootstrap:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e packages/contracts -e packages/engine -e packages/connectors \
		-e packages/llm -e packages/settings -e '.[dev]'

lint:
	$(RUFF) check packages apps tests

typecheck:
	PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors $(MYPY) packages/contracts/showis_contracts packages/settings packages/engine/showis_engine packages/connectors/showis_connectors apps/api/showis

test:
	PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors $(PYTEST)

dev:
	@echo "Geliştirme sunucuları başlatılıyor..."
	@bash -c "trap 'kill 0' SIGINT; (cd apps/api && PYTHONPATH=. ../../.venv/bin/uvicorn showis.main:app --reload --port 8000) & (cd apps/web && npm run dev) & wait"

docker-up:
	docker compose -f infra/docker-compose.yml up -d

docker-down:
	docker compose -f infra/docker-compose.yml down

backup:
	bash infra/scripts/backup.sh

restore:
	bash infra/scripts/restore.sh

demo:
	bash infra/scripts/demo.sh

demo-smoke:
	bash infra/scripts/demo_smoke.sh

eval:
	PYTHONPATH=apps/api:packages/contracts:packages/settings:packages/llm:packages/engine:packages/connectors $(PYTHON) tests/eval/run_eval.py

gate:
	bash scripts/final_gate.sh

seed-keys:
	bash infra/scripts/seed_keys.sh

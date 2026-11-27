.PHONY: help bootstrap dev web-build lint typecheck test schemas docker-up docker-rebuild docker-down backup backup-install restore demo demo-smoke eval gate

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
RUFF ?= .venv/bin/ruff
MYPY ?= .venv/bin/mypy
PYTEST ?= .venv/bin/pytest

# NixOS: pip'in indirdiği/derlediği native uzantılar (greenlet → SQLAlchemy
# asyncio) sistem kütüphane yolunda olmayan `libstdc++.so.6` arar ve
# "cannot open shared object file" ile düşer. Sistem derleyicisinin kütüphane
# dizinini yalnızca gerçekten bir yol döndüğünde ekle; standart dağıtımlarda
# ve macOS'ta zaten yolda olduğu için bu blok sessizce atlanır.
LIBSTDCXX := $(shell cc -print-file-name=libstdc++.so.6 2>/dev/null)
ifneq ($(filter /%,$(LIBSTDCXX)),)
LD_LIBRARY_PATH := $(dir $(LIBSTDCXX))$(if $(LD_LIBRARY_PATH),:$(LD_LIBRARY_PATH))
export LD_LIBRARY_PATH
endif

help:
	@echo "Showis OSINT Monorepo Komutları:"
	@echo "  make bootstrap     - .venv kurar ve tüm bağımlılıkları + yerel paketleri kurar (tek komut)"
	@echo "  make dev           - API ve Web uygulamasını geliştirme modunda başlatır"
	@echo "  make web-build     - Web (Next.js) üretim derlemesi (apps/web)"
	@echo "  make lint          - Kod format ve lint kontrollerini çalıştırır"
	@echo "  make typecheck     - Mypy ile statik tip kontrollerini çalıştırır"
	@echo "  make test          - Tüm birim ve entegrasyon testlerini çalıştırır"
	@echo "  make schemas       - Pydantic modellerinden JSON ve TypeScript şemalarını üretir"
	@echo "  make gate          - Statik final kapı (ruff, mypy, pytest, web build, şema/sır)"
	@echo "  make docker-up     - Docker compose servislerini başlatır (imajları yeniden derlemez)"
	@echo "  make docker-rebuild - İmajları yeniden derler ve servisleri başlatır"
	@echo "  make docker-down   - Docker compose servislerini durdurur"
	@echo "  make backup        - Elle PostgreSQL + rapor/kanıt yedeği alır"
	@echo "  make backup-install - Saatlik yedek systemd user timer'ını kurar"
	@echo "  make restore       - Yedeği (varsayılan: test DB) geri yükler"
	@echo "  make demo          - Sentetik uçtan uca demo çalıştırır"
	@echo "  make demo-smoke    - Demo duman testini çalıştırır"
	@echo "  make eval          - Değerlendirme koşusunu çalıştırır"

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
	@bash scripts/dev.sh

# Web (Next.js) üretim derlemesi. Kökteki package.json yalnız npm installer'ıdır
# (`@l1secx/showis`) — kökte `npm run build` yoktur; bu hedef doğru dizinde çalıştırır.
web-build:
	cd apps/web && npm run build

docker-up:
	docker compose -f infra/docker-compose.yml up -d

# Kod değişikliğinden sonra: imajları yeniden derler, migration entrypoint'te koşar.
docker-rebuild:
	docker compose -f infra/docker-compose.yml up -d --build

docker-down:
	docker compose -f infra/docker-compose.yml down

backup:
	bash infra/scripts/backup.sh

# Saatlik yedekleme systemd user timer'ini kurar (D-004).
backup-install:
	bash infra/scripts/install-backup-timer.sh

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

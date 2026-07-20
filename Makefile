PYTHON ?= .venv/bin/python

.PHONY: lint typecheck test frontend contract migrate compose-config truenas-validate truenas-integration release
lint:
	$(PYTHON) -m ruff check backend/app backend/tests backend/alembic worker simulator scripts tools
	$(PYTHON) -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts tools
	cd frontend && npm run lint

typecheck:
	cd backend && ../$(PYTHON) -m mypy app
	MYPYPATH=backend $(PYTHON) -m mypy worker simulator --explicit-package-bases
	cd frontend && npm run typecheck

test:
	$(PYTHON) -m pytest backend/tests simulator/tests -q
	cd frontend && npm test

frontend:
	cd frontend && npm ci && npm run build

contract:
	$(PYTHON) scripts/validate_contracts.py

migrate:
	cd backend && ../$(PYTHON) -m alembic upgrade head

compose-config:
	docker compose config --quiet

truenas-validate:
	$(PYTHON) tools/validate-truenas-compose.py

truenas-integration:
	$(PYTHON) tools/validate-truenas-compose.py --deployment --pool $(TRUENAS_POOL) --gateway-port $(TRUENAS_GATEWAY_PORT) $(TRUENAS_COMPOSE_FILE)
	$(PYTHON) tools/test-truenas-workflow.py --compose $(TRUENAS_COMPOSE_FILE) --base-url $(TRUENAS_BASE_URL) --ca-certificate $(TRUENAS_CA_CERTIFICATE) --setup-token-file $(TRUENAS_SETUP_TOKEN_FILE) --gateway-port $(TRUENAS_GATEWAY_PORT)

release:
	bash scripts/release.sh

PYTHON ?= .venv/bin/python

.PHONY: lint typecheck test frontend contract migrate compose-config release
lint:
	$(PYTHON) -m ruff check backend/app backend/tests backend/alembic worker simulator scripts
	$(PYTHON) -m ruff format --check backend/app backend/tests backend/alembic worker simulator scripts
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

release:
	bash scripts/release.sh

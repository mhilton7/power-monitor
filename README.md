# Power Monitor Server

Power Monitor Server is a self-hosted control plane for fleets of ESP32-S3 power sensors using a PZEM-004T V4.x, one current transformer, and mandatory microSD storage. It enrolls devices with unique credentials, accepts signed outbound data, pulls retained history from reachable devices, preserves raw readings in PostgreSQL, calculates effective-dated SCE time-of-use estimates, and presents the result in a responsive React application.

The normative interoperability identifier is `pm-protocol/1.0.0`. The browser talks only to this server; device credentials never reach browser code.

```text
ESP32 agents -- signed heartbeat / batches --> FastAPI --> PostgreSQL
      ^                                       |  |
      +---------- HMAC pull/sync worker <-----+  +--> rate/alert/report workers
Browser <------------ HTTPS + SSE ---------- Caddy <--- React frontend
```

## Quick development start

Requirements are Python 3.13, Node.js 24 LTS, and PostgreSQL 17. Docker Compose is the simplest way to provide PostgreSQL.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]'
cd frontend && npm ci && cd ..
cp .env.example .env                 # replace every CHANGE_ME value
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres
cd backend && alembic upgrade head && uvicorn app.main:app --reload
```

In separate terminals run `python -m worker.app.main` and `cd frontend && npm run dev`. Run all portable gates with `make lint typecheck test contract frontend`.

For production use [Installation](docs/INSTALLATION.md), then [First run](docs/FIRST_RUN.md). Protocol and deployment details are in [Architecture](docs/ARCHITECTURE.md), [Device protocol](docs/DEVICE_PROTOCOL.md), [Security](docs/SECURITY.md), and [Operations](docs/OPERATIONS.md).

Verification evidence is recorded in [Testing](docs/TESTING.md) and [Load test results](docs/LOAD_TEST_RESULTS.md).

## Safety and scope

This software does not control or connect to mains wiring. Installation of PZEM modules and CTs belongs to the separate sensor project and must be performed by a qualified person under applicable electrical rules. Measurements are not represented as revenue-grade. Every displayed cost is an estimate, not a utility bill; monitored coverage, meter accuracy, baseline allocation, CCA/Direct Access, taxes, credits, rounding, tariff changes, and utility adjustments can differ.

Licensed under MIT. See [LICENSE](LICENSE).

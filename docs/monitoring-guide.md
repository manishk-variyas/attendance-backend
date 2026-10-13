# Observability & Monitoring Guide

This guide documents the centralized monitoring, metrics collection, distributed tracing, and dashboards across the attendance backend and infrastructure.

---

## Architecture Overview

```
                                 ┌─────────────────────────────────────────────────────────┐
                                 │                   GRAFANA (Port 3001)                   │
                                 │   • Host & Container Stats (CPU, RAM, Disk, IO)         │
                                 │   • API Performance (Overall RPS, Status Codes)         │
                                 │   • Per-Endpoint Breakdown (Top Slowest & Crashes)      │
                                 │   • Distributed Traces (Waterfalls & Spans via Tempo)   │
                                 └───────────────▲──────────────────────▲──────────────────┘
                                                 │                      │
                                                 │ PromQL               │ Trace Query
                                                 │                      │
                                       ┌─────────┴─────────┐  ┌─────────┴────────┐
                                       │    PROMETHEUS     │  │      TEMPO       │
                                       │    (Port 9090)    │  │ (Port 3200/4317) │
                                       └─▲───────▲───────▲─┘  └─────────▲────────┘
                                         │       │       │              │
                       ┌─────────────────┘       │       └───────────┐  │ OTLP
                       │                         │                   │  │ Spans
               ┌───────┴────────┐        ┌───────┴──────┐      ┌─────┴──┴─────┐
               │ vm-node-export │        │ vm-cadvisor  │      │   Backend    │
               │ (Host OS stats)│        │ (Containers) │      │  (FastAPI)   │
               │   Port 9100    │        │   Port 8086  │      │  (Port 8000) │
               └────────────────┘        └──────────────┘      └──────────────┘
```

---

## 1. Storage & Resource Safety

To ensure monitoring never exhausts disk space on the host, strict retention limits are configured:

| Component | Retention Policy | Maximum Disk Cap | Storage Location |
|---|---|---|---|
| **Prometheus** | `--storage.tsdb.retention.time=7d`<br>`--storage.tsdb.retention.size=2GB` | **2 GB** (hard limit, self-pruning) | Docker Volume: `infra_prometheus_data` |
| **Tempo** | `block_retention: 24h` | **1 GB** (daily compact cycle) | Docker Volume: `infra_tempo_data` |
| **Grafana** | N/A (SQLite metadata only) | **~50 MB** | Docker Volume: `infra_grafana_data` |

**Total Monitoring Footprint:** Under **3.5 GB** total disk space, and under **260 MiB RAM**.

---

## 2. Accessing Grafana & Dashboards

* **URL:** `http://127.0.0.1:3001` (accessible locally or through an SSH tunnel: `ssh -L 3001:localhost:3001 ...`)
* **Default Credentials:**
  * **User:** `admin`
  * **Password:** `admin` *(prompted to change upon initial login)*
* **Pre-loaded Dashboard:**
  * Navigate to **Dashboards** → **System & API Overview** (`uid: system-api-overview`).
  * The dashboard updates automatically every 10 seconds.

---

## 3. What is Monitored (Dashboard Breakdown)

### Section A: System & Host Resources
* **Host CPU Usage:** Real-time percentage gauge based on Linux kernel idle metrics.
* **Host RAM Usage:** Real-time memory consumption based on `MemAvailable` vs `MemTotal`.
* **Root Disk Usage (% & Free GB):** Tracks available disk space on `/` so you always know your disk headroom.

### Section B: Docker Container Metrics (cAdvisor)
* **Per-Container CPU %:** Breaks down CPU across `infra-backend-1`, `backend_postgres`, `infra-keycloak-1`, `infra-nginx-1`, etc.
* **Per-Container Working Set Memory:** Detects memory leaks or unexpected spikes per container.

### Section C: API Overview & Performance
* **Total API Request Rate (RPS):** Sum of requests processed per second.
* **Error Rate (4xx & 5xx):** Highlights client error spikes (4xx) and unhandled server exceptions (5xx).
* **Global Latency ($p50$ & $p95$):** 50th and 95th percentile response times across the entire API.

### Section D: Per-Endpoint Breakdown (Slowdowns & Crashes)
* **Top 10 Slowest Endpoints ($p95$ Latency):** Live table and time-series ranking the slowest endpoints in the application.
* **Crashes & Errors by Endpoint:** Shows exact route paths returning HTTP 4xx or 5xx status codes.
* **Throughput by Endpoint (RPS):** Real-time breakdown of traffic distribution across different API handlers.

---

## 4. Backend Implementation Details

### Telemetry Module: `apps/backend/app/core/telemetry.py`
The FastAPI application automatically initializes both Prometheus metrics and OpenTelemetry tracing:
1. **Prometheus Metrics (`init_metrics`)**:
   * Uses `prometheus-fastapi-instrumentator`.
   * Automatically instruments all route handlers using templated paths (e.g. `/api/leaves/{leave_id}`) to prevent label cardinality explosion.
   * Excludes `/metrics` and `/health` from stats to avoid skewing data with health checks and scrapers.
   * Exposes metrics internally on `http://backend:8000/metrics`.
2. **OpenTelemetry Tracing (`init_tracing`)**:
   * Auto-instruments FastAPI routes and SQLAlchemy database engine.
   * Batches and exports traces via OTLP gRPC to `http://tempo:4317`.

### Docker Configuration: `infra/backend/docker-compose.yml`
The backend service includes the following environment variables:
```yaml
environment:
  BACKEND_URL: http://backend:8000
  OTEL_SERVICE_NAME: attendance-backend
  OTEL_EXPORTER_OTLP_ENDPOINT: http://tempo:4317
  OTEL_EXPORTER_OTLP_INSECURE: "true"
```

---

## 5. Operations Runbook

### Checking Monitoring Service Status
```bash
cd infra
docker compose ps prometheus tempo grafana
```

### Checking Scrape Targets in Prometheus
```bash
curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
```
All 3 targets (`backend`, `cadvisor`, `node-exporter`) should report `"health": "up"`.

### Viewing Backend Metrics Output
```bash
docker exec infra-backend-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode()[:500])"
```

### Restarting the Monitoring Containers
```bash
cd infra
docker compose restart prometheus tempo grafana
```

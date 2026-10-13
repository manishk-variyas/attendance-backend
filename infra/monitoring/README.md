# Monitoring & Observability Stack

This folder contains the complete configuration for the central observability stack:
- **Prometheus**: Time-series metrics collection & storage (retention capped at 7 days / 2 GB).
- **Tempo**: Distributed trace collection & storage (OpenTelemetry OTLP receiver).
- **Grafana**: Unified dashboards and trace visualization (bound to port 3001).

---

## Directory Layout

```
infra/monitoring/
├── docker-compose.yml              # Service definitions (Prometheus, Tempo, Grafana)
├── prometheus/
│   └── prometheus.yml              # Scrape jobs (node-exporter, cAdvisor, backend)
├── tempo/
│   └── tempo.yml                   # Tempo storage and OTLP receiver configuration
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── datasources.yml     # Auto-configures Prometheus & Tempo as datasources
│   │   └── dashboards/
│   │       └── dashboards.yml      # Auto-loads dashboards from /var/lib/grafana/dashboards
│   └── dashboards/
│       └── overview.json           # System, container & API overview dashboard
└── README.md
```

---

## Services & Ports

| Service | Container Name | Port (Host) | Description |
|---|---|---|---|
| **Grafana** | `infra-grafana` | `127.0.0.1:3001` | Web UI for dashboards & traces |
| **Prometheus** | `infra-prometheus` | `127.0.0.1:9090` | Metrics scraper & TSDB |
| **Tempo** | `infra-tempo` | `127.0.0.1:3200` (HTTP)<br>`4317` (gRPC OTLP)<br>`4318` (HTTP OTLP) | Trace collector & storage |

> **Note on Grafana Port:** Grafana is mapped to port **3001** because port 3000 is occupied by the Redmine container.

---

## Default Credentials

* **Grafana URL:** `http://127.0.0.1:3001` (accessible via SSH tunnel or local browser)
* **Username:** `admin`
* **Password:** `admin` *(prompted to change upon first login)*

---

## Management Commands

Run all commands from `infra/`:

```bash
# Check status of monitoring services
docker compose ps prometheus tempo grafana

# View logs
docker compose logs -f prometheus
docker compose logs -f tempo
docker compose logs -f grafana

# Restart monitoring services
docker compose restart prometheus tempo grafana

# Stop monitoring services
docker compose stop prometheus tempo grafana
```

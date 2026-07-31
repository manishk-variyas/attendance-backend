# attendance-backend

Backend monorepo for attendance system with Keycloak authentication.

## Tech Stack

- **Package Manager**: uv
- **Backend**: FastAPI (Python 3.14+)
- **Database**: PostgreSQL
- **Cache**: Redis
- **Auth**: Keycloak

## Prerequisites

- Python 3.14+ (uv)
- Docker & Docker Compose

## Setup

### 1. Install dependencies

```bash
cd apps/backend
uv sync
```

### 2. Set up environment variables

Copy the example env file:

```bash
cp apps/backend/.env.example apps/backend/.env
```

Edit `.env` with your configuration (database URL, Keycloak settings, etc.).

### 3. Start infrastructure services

```bash
# Start PostgreSQL, Redis, Keycloak, Nginx
docker-compose -f infra/docker-compose.yml up -d
```

### 4. Start the backend

```bash
# Development mode with hot reload (from apps/backend)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The API runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

## Available Scripts

| Command | Description |
|---------|-------------|
| `uv sync` | Install Python dependencies |
| `uv run uvicorn app.main:app --reload` | Run backend in dev mode |

## Project Structure

```
backend-monorepo/
├── apps/
│   └── backend/          # FastAPI application
├── docs/                 # Documentation
├── infra/                # Docker configs (Keycloak, Redis, Nginx)
└── scripts/              # Internal scripts (logdash, etc.)
```

## Documentation

- [Architecture](docs/architecture.md)
- [Auth Flow](docs/auth_flow.md)
- [Keycloak Setup](docs/keycloak_setup.md)
- [Frontend API Setup](docs/frontend_api_setup.md)
- [Migration Guide](docs/migration.md)

## License

ISC
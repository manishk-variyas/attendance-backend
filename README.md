# attendance-backend

Backend monorepo for attendance system with Keycloak authentication.

## Tech Stack

- **Package Manager**: pnpm
- **Orchestration**: Turbo
- **Backend**: FastAPI (Python 3.14+)
- **Database**: PostgreSQL
- **Cache**: Redis
- **Auth**: Keycloak

## Prerequisites

- Node.js + pnpm
- Python 3.14+
- Docker & Docker Compose

## Setup

### 1. Install dependencies

```bash
pnpm install
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

# Or start only Keycloak
pnpm run infra:up
```

### 4. Start the backend

```bash
# Development mode with hot reload
pnpm run dev

# Or in backend directory
cd apps/backend
uv sync
pnpm run dev
```

The API runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

## Available Scripts

| Command | Description |
|---------|-------------|
| `pnpm install` | Install all dependencies |
| `pnpm dev` | Run all apps in dev mode |
| `pnpm build` | Build all apps |
| `pnpm test` | Run tests |
| `pnpm lint` | Lint all apps |
| `pnpm format` | Format code with Prettier |
| `pnpm infra:up` | Start Keycloak |
| `pnpm infra:down` | Stop Keycloak |

## Project Structure

```
backend-monorepo/
├── apps/
│   └── backend/          # FastAPI application
├── docs/                 # Documentation
├── infra/                # Docker configs (Keycloak, Redis, Nginx)
└── packages/             # Shared packages
```

## Documentation

- [Architecture](docs/architecture.md)
- [Auth Flow](docs/auth_flow.md)
- [Keycloak Setup](docs/keycloak_setup.md)
- [Frontend API Setup](docs/frontend_api_setup.md)
- [Migration Guide](docs/migration.md)

## License

ISC
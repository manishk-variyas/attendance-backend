# Codebase Analysis & Learning Roadmap

This document provides a comprehensive breakdown of the technologies, patterns, and architectural decisions used in this project. Since the codebase was built using various tools, this guide will help you gain the confidence to manage, extend, and debug it effectively.

## 1. Project Overview
This repository is a **Monorepo** managed with **Turborepo** and **PNPM**. The core service is a **FastAPI Backend** designed as a **Backend-for-Frontend (BFF)**. It handles authentication via **Keycloak**, manages sessions in **Redis**, and persists data in **PostgreSQL**.

---

## 2. Core Technology Stack

| Category | Technology | Purpose |
| :--- | :--- | :--- |
| **Language** | Python 3.14+ | Modern, high-performance asynchronous Python. |
| **Framework** | FastAPI | High-performance API framework based on Starlette and Pydantic. |
| **Persistence** | PostgreSQL | Primary relational database. |
| **ORM** | SQLAlchemy 2.0 | Advanced SQL toolkit and Object Relational Mapper (Async). |
| **Identity** | Keycloak | Open-source Identity and Access Management (OIDC/OAuth2). |
| **Caching** | Redis | Session storage and rate-limiting state. |
| **Infrastructure** | Docker & Compose | Containerization and local service orchestration. |
| **Package Mgr** | `uv` | Extremely fast Python package installer and resolver. |

---

## 3. Detailed Learning Roadmap

### Phase 1: The Foundations (Python & FastAPI)
To understand the code, you must be comfortable with asynchronous programming and the FastAPI paradigm.
- **Topics to Master:**
    - `async`/`await` and how the event loop works in Python.
    - Pydantic models (Type hints, validation, and serialization).
    - FastAPI Dependency Injection (`Depends`) — *Critical for auth and DB access.*
    - Path operations, Query parameters, and Request bodies.
- **Resources:**
    - [FastAPI Official Tutorial](https://fastapi.tiangolo.com/tutorial/)
    - [Pydantic V2 Documentation](https://docs.pydantic.dev/latest/)
    - [TestDriven.io: FastAPI Dependency Injection](https://testdriven.io/blog/fastapi-dependency-injection/)
    - [YouTube: ArjanCodes - FastAPI Best Practices](https://www.youtube.com/watch?v=gQTRsZJ79mU)
    - [Python.org: Asyncio Documentation](https://docs.python.org/3/library/asyncio.html)

### Phase 2: Data Handling (SQLAlchemy 2.0 & Redis)
The project uses the "New Style" SQLAlchemy 2.0 API which is significantly different from 1.x.
- **Topics to Master:**
    - Async sessions and engines.
    - Declarative Mapping (Modern `Mapped` and `mapped_column` syntax).
    - Relationship loading (Joined vs. Selectin loading).
    - Redis basics (Key-value pairs, TTL/Expirations for sessions).
- **Resources:**
    - [SQLAlchemy 2.0 Unified Tutorial](https://docs.sqlalchemy.org/en/20/tutorial/index.html)
    - [SQLAlchemy 2.0 Async Guide](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
    - [Redis Crash Course (University)](https://university.redis.com/courses/ru101/)
    - [TestDriven.io: FastAPI, SQLAlchemy, and Alembic](https://testdriven.io/blog/fastapi-sql-alchemy-alembic/)
    - [Full Stack Python: PostgreSQL](https://www.fullstackpython.com/postgresql.html)
    - [YouTube: Amigoscode - Redis in 100 Seconds](https://www.youtube.com/watch?v=jgpVdJB2sKQ)

### Phase 3: Security & Identity (Keycloak & OAuth2)
This is the most complex part of the backend. It uses a BFF pattern where the backend exchanges codes for tokens and keeps them in a secure Redis session.
- **Topics to Master:**
    - OAuth2 Authorization Code Flow.
    - OpenID Connect (OIDC) basics.
    - JWT (JSON Web Tokens) structure and validation.
    - Keycloak administration (Realms, Clients, Scopes, Roles).
- **Resources:**
    - [OAuth2.com: Authorization Code Flow](https://oauth.net/2/grant-types/authorization-code/)
    - [Auth0: The BFF Pattern](https://auth0.com/blog/backend-for-frontend-pattern-with-auth0-and-nextjs/)
    - [RealPython: Python Jose (JWT) Tutorial](https://realpython.com/token-based-authentication-with-flask/)
    - [YouTube: Keycloak Masterclass (Amigoscode)](https://www.youtube.com/watch?v=mS9-S6S-mXQ)
    - [OIDC Explained: OpenID Connect Simply Explained](https://openid.net/connect/)

### Phase 4: Infrastructure & Architecture (Docker & Monorepos)
- **Topics to Master:**
    - Docker Compose for multi-container environments.
    - Nginx as a Reverse Proxy (Routing traffic to Backend vs. Redmine).
    - Monorepo Management (Turborepo caching, task execution).
    - BFF Pattern (Why we keep tokens on the server instead of the browser).
- **Resources:**
    - [Docker Official: Getting Started](https://docs.docker.com/get-started/)
    - [Docker Compose Specification](https://docs.docker.com/compose/compose-file/)
    - [Turborepo: Core Concepts](https://turbo.build/repo/docs/core-concepts)
    - [Nginx Beginner's Guide](https://nginx.org/en/docs/beginners_guide.html)
    - [BFF Architecture (Microsoft Docs)](https://learn.microsoft.com/en-us/azure/architecture/patterns/backends-for-frontends)
    - [YouTube: Docker Compose Tutorial for Beginners](https://www.youtube.com/watch?v=HG6yIjZapSA)
    - [Astray: Why we use Turborepo](https://turbo.build/blog/why-turborepo)

---

## 4. Key Design Patterns in This Codebase

### 1. Feature-Based Modularity
Instead of putting all routes in one folder and all models in another, the code is organized by feature (e.g., `app/features/redmine`, `app/features/auth`).
- **Why?** It makes it easier to scale. Everything related to "Redmine" is in one place.

### 2. Dependency Injection for Security
Look at `app/features/auth/dependencies.py`. It provides `get_current_user`.
- **How it works:** Any endpoint that needs a user just adds `user = Depends(get_current_user)`. FastAPI handles the logic of checking the session cookie.

### 3. Middleware for Cross-Cutting Concerns
We use custom middleware for:
- **Logging**: Tracking every incoming request.
- **Correlation IDs**: Attaching a unique ID to every log line in a single request flow.
- **Rate Limiting**: Preventing API abuse using `slowapi`.

---

## 6. Advanced & Community Resources

If you want to go beyond the basics and see how the industry handles large-scale FastAPI apps:

- **Awesome Lists (The "Gold Mines"):**
    - [Awesome FastAPI](https://github.com/mjhea0/awesome-fastapi): A curated list of the best FastAPI libraries and resources.
    - [Awesome SQLAlchemy](https://github.com/vinta/awesome-python#orm): Best tools for database management.
- **Testing & Quality:**
    - [Pytest Documentation](https://docs.pytest.org/): Our testing framework.
    - [FastAPI: Testing a Database](https://fastapi.tiangolo.com/advanced/testing-database/): Crucial for backend reliability.
- **Performance & Deployment:**
    - [Uvicorn Documentation](https://www.uvicorn.org/): The server running our app.
    - [Gunicorn with Uvicorn workers](https://www.uvicorn.org/deployment/#gunicorn): How we run this in production.
- **API Design Standards:**
    - [Zalando RESTful API Guidelines](https://opensource.zalando.com/restful-api-guidelines/): The industry standard for "Clean" APIs.

---

## 7. Tips for Confidence
1.  **Read the Logs**: We have structured logging. Run `docker compose logs -f backend` and watch what happens when you click things.
2.  **Interactive API Docs**: Go to `http://localhost:8000/docs` (or your server URL) to see the Swagger UI. It's the best way to test endpoints.
3.  **Check the `docs/` folder**: There are detailed docs on `auth_flow.md` and `backend_architecture.md`. They contain mermaid diagrams that visualize how data moves.

## Next Steps
I recommend picking one "Feature" (like `redmine`) and tracing a request from the `route` to the `service` and finally to the `model`. This will give you a "vertical" understanding of how the layers interact.

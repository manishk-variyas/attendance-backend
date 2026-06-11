# PostgreSQL Migration: Local Backup to VM Infrastructure

This document outlines the end-to-end process of migrating the local `attendance-app.dump` PostgreSQL database into the new Dockerized backend infrastructure on the VM. It includes the commands executed, the errors encountered, and how they were resolved.

## 1. Initial Infrastructure Setup

Before restoring the database, we needed to spin up a dedicated PostgreSQL container for the backend, alongside the existing MongoDB, Keycloak, and Redmine services.

### Configuration Changes:
1. **Added Backend Credentials** to `infra/.env`:
   ```env
   BACKEND_POSTGRES_DB=attendance
   BACKEND_POSTGRES_USER=backend_user
   BACKEND_POSTGRES_PASSWORD=backend_password
   ```

2. **Created the Service** in `infra/postgres/docker-compose.yml`:
   ```yaml
   services:
     postgres:
       image: postgres:17  # Initially set to 16, later upgraded to 17
       container_name: backend_postgres
       restart: always
       environment:
         POSTGRES_DB: ${BACKEND_POSTGRES_DB:-attendance}
         POSTGRES_USER: ${BACKEND_POSTGRES_USER:-backend_user}
         POSTGRES_PASSWORD: ${BACKEND_POSTGRES_PASSWORD:-backend_password}
       volumes:
         - backend_postgres_data:/var/lib/postgresql/data
       ports:
         - "127.0.0.1:5432:5432"
       networks:
         - infra-network

   volumes:
     backend_postgres_data:
   ```

3. **Linked the Service** by adding `- postgres/docker-compose.yml` to the `include` block in the main `infra/docker-compose.yml`.

---

## 2. Command Cheat Sheet

Here are all the commands used during the migration process (run from the root directory unless specified):

### Starting the Infrastructure
```bash
# Start all containers including the new postgres service
cd infra
docker compose up -d --build
```

### Restoring the Database
```bash
# Stream the dump file into the container to restore the database
docker exec -i backend_postgres pg_restore -U backend_user -d attendance < attendance-app.dump

# To run the restore cleanly without ownership warnings in the future:
docker exec -i backend_postgres pg_restore --no-owner -U backend_user -d attendance < attendance-app.dump
```

### Fixing the Crash Loop (Volume Reset)
```bash
# When upgrading from postgres:16 to postgres:17, the volume must be cleared
cd infra
docker compose stop postgres
docker compose rm -f postgres
docker volume rm infra_backend_postgres_data
docker compose up -d postgres
```

### Verification
```bash
# Verify the tables exist inside the container
docker exec -i backend_postgres psql -U backend_user -d attendance -c "\dt"

# Verify the data was populated
docker exec -i backend_postgres psql -U backend_user -d attendance -c "SELECT count(*) FROM employee_master;"
```

---

## 3. Errors Encountered & Resolutions

During the restore process, we faced several expected and unexpected issues.

### Error 1: Unsupported Version in File Header
* **Error Message:** `pg_restore: error: unsupported version (1.16) in file header`
* **Cause:** The `attendance-app.dump` file was exported using `pg_dump` from PostgreSQL 17. However, the container was initially configured to use the `postgres:16` image. PostgreSQL cannot restore a newer dump into an older major version.
* **Resolution:** We edited `infra/postgres/docker-compose.yml` and changed the image from `postgres:16` to `postgres:17`.

### Error 2: Container Stuck in Restart Loop
* **Error Message:** `Error response from daemon: Container [...] is restarting, wait until the container is running`
* **Cause:** After upgrading the image to `postgres:17`, the container crashed immediately on startup. This happened because the Docker volume `infra_backend_postgres_data` was originally initialized with PostgreSQL 16 files, which are incompatible with the PostgreSQL 17 server engine.
* **Resolution:** We stopped the container, deleted the incompatible version 16 Docker volume, and started the container again to let version 17 create fresh database files. *(See the Command Cheat Sheet for the exact commands).*

### Error 3: Role Ownership Warnings
* **Error Message:** `pg_restore: error: could not execute query: ERROR: role "attendance_user" does not exist`
* **Cause:** The dump file contained commands at the very end to assign ownership of the tables back to the original creator (`attendance_user`). Since our new database uses `backend_user` instead, it threw warnings. 
* **Resolution:** These warnings are harmless. When `pg_restore` fails to assign the original owner, it automatically falls back to assigning ownership to the user executing the restore (`backend_user`). The tables and data were already fully and successfully imported before these warnings appeared. *(Adding the `--no-owner` flag prevents these warnings).*

### Error 4: Relation Already Exists
* **Error Message:** `pg_restore: error: could not execute query: ERROR: relation "employee_master" already exists` and `duplicate key value violates unique constraint`
* **Cause:** This occurred when we attempted to run the `pg_restore` command a second time. Since the first run actually succeeded in importing all the data, the second run attempted to recreate tables that already existed and insert data that was already there.
* **Resolution:** No action needed. The database was already successfully restored.

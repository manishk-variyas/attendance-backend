# Redis In-Memory Data Store
#
# Redis is used in this project for:
# - Storing user sessions (server-side)
# - Caching JWKS (JSON Web Key Set) from Keycloak
#
# The Alpine version is lightweight (~10MB) and sufficient for our needs.
# Session data is persisted via a Docker volume (redis_data).

FROM redis:7-alpine

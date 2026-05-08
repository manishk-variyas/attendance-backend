# Backend Testing & "Stretching" Guidelines

This document outlines the strategy for building a "bulletproof" backend by stretching our application's limits through Property-Based Testing and Stress Testing.

---

## 1. The Goal: A Bulletproof Backend
A bulletproof backend is one that:
- **Never crashes (No 500s):** Regardless of how malformed or unexpected the input is.
- **Enforces Contracts:** Strictly adheres to the OpenAPI schema.
- **Handles Load:** Maintains performance under peak traffic and fails gracefully under extreme stress.
- **Has Self-Healing Logic:** Logic that has been tested against thousands of edge cases automatically.

---

## 2. The "Stretch" Testing Layers

### A. API Contract Stretching (Fuzzing)
We use **Schemathesis** to test our FastAPI endpoints. It "stretches" the API by generating exhaustive combinations of valid and invalid data based on our OpenAPI schema.

- **Tool:** `schemathesis`
- **Key Checks:**
  - `not_a_server_error`: Ensures no request causes a `500 Internal Server Error`.
  - `status_code_conformance`: Ensures the API only returns status codes defined in the schema.
  - `content_type_conformance`: Ensures the response format (JSON/XML) matches the schema.
  - `response_schema_conformance`: Ensures the actual data returned matches the Pydantic models.

### B. Logic Stretching (Property-Based)
For complex business logic (e.g., date calculations, permission logic, or data transformations), we use **Hypothesis**.

- **Tool:** `hypothesis`
- **When to use:** Use this when a function has many branches or edge cases (e.g., empty lists, null values, massive integers, or special characters).
- **Example:** Instead of testing a function with `calculate(10, 20)`, Hypothesis will test it with `calculate(0, -1)`, `calculate(max_int, None)`, etc.

### C. Performance & Stress Stretching
We use **Locust** to simulate high-concurrency scenarios to find the system's breaking point.

- **Tool:** `locust`
- **Levels of Stress:**
  - **Load Testing:** Can we handle our expected daily peak (e.g., 100 concurrent users)?
  - **Stress Testing:** At what point does the system break (e.g., 1,000 users)?
  - **Soak Testing:** Does the system leak memory or database connections over 24 hours of steady load?

---

## 3. Implementation Workflow

### 1. Local Testing
Run standard unit tests and basic API fuzzer before pushing code.
```bash
# Example command (Conceptual)
pytest tests/
st run http://localhost:8000/openapi.json --checks all
```

### 2. CI/CD Integration
The "Stretch" tests should be part of the automated pipeline:
- **Pull Requests:** Run Unit + Schemathesis (Lightweight).
- **Weekly/Nightly:** Run full Stress Tests and Deep Fuzzing.

### 3. Handling Failures
When a stretch test fails (e.g., Schemathesis finds a crash):
1. **Isolate the Payload:** Schemathesis provides a `curl` command to reproduce the exact failure.
2. **Fix the Validation:** Usually requires adding a Pydantic validator or a more specific `Field` constraint.
3. **Regression:** Add the failing payload as a standard unit test to ensure it never happens again.

---

## 4. Best Practices
- **Strict Pydantic Models:** The better your schemas are, the more effective Schemathesis becomes.
- **Database Isolation:** Always run stretch tests against a dedicated test database (e.g., using Docker).
- **Observability:** Monitor logs and database connection pools during stress tests to identify bottlenecks.

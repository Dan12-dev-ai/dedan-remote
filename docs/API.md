# REST API Reference — DEDAN Remote

DEDAN Remote exposes a RESTful HTTP API powered by FastAPI.
All canonical routes are served under `/api/*`, with backwards-compatible versioned aliases under `/api/v1/*`.

Interactive Swagger documentation is available locally at:
- **Swagger UI**: `http://localhost:8000/api/docs`
- **OpenAPI Schema**: `http://localhost:8000/api/openapi.json`
- **ReDoc UI**: `http://localhost:8000/redoc`

---

## Global Response & Error Envelope

All API errors return structured JSON with correlation IDs and consistent error keys:

```json
{
  "detail": "Resource not found",
  "code": "NOT_FOUND",
  "status": 404,
  "request_id": "req-9843a8c4-4b55-4d0f",
  "path": "/api/jobs/missing-slug"
}
```

Every response includes the correlation header:
- `X-Request-ID`: Generated UUID or propagated from request header `X-Request-ID`.

---

## Authentication & Sessions

Authentication is session-token based (`Bearer <token>`). Tokens are returned upon registration or login.

Header format:
```http
Authorization: Bearer <token>
```

---

## Public Endpoints

### 1. Health & Readiness

#### `GET /api/health` (Alias: `/api/v1/health`)
- **Auth**: None
- **Description**: Lightweight health probe for load balancers.
- **Response `200 OK`**:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-09-28T10:00:00Z"
}
```

#### `GET /api/ready` (Alias: `/api/v1/ready`)
- **Auth**: None
- **Description**: Deep readiness probe verifying database connectivity.
- **Response `200 OK`**:
```json
{
  "ready": true,
  "database": "connected",
  "active_scrapers": 9
}
```

---

### 2. Job Catalog & Discovery

#### `GET /api/jobs` (Alias: `/api/v1/jobs`)
- **Auth**: None
- **Query Parameters**:
  - `page` (int, default `1`): 1-indexed page number.
  - `page_size` (int, default `20`, max `100`): Results per page.
  - `source` (string, optional): Platform filter (`outlier`, `alignerr`, `oneforma`, `telus`, `welocalize`, `appen`, `dataannotation`, `clickworker`, `toloka`).
  - `min_score` (float, optional): Filter by minimum overall score (0.0 to 100.0).
  - `remote_only` (bool, default `false`): Restrict to remote listings.
  - `sort_by` (string, default `newest`): Sorting order (`newest`, `best_match`, `score`, `salary`, `freshness`).
- **Response `200 OK`**: Paginated job envelope containing `items`, `page`, `page_size`, `total`, `total_pages`.

#### `GET /api/jobs/{ident}` (Alias: `/api/v1/jobs/{ident}`)
- **Auth**: None
- **Parameters**: `ident` can be either the numeric ID or URL slug string.
- **Response `200 OK`**: Full job detail with validated external `apply_url`.
- **Error `404 Not Found`**: Returned when `ident` does not match any indexed job.

#### `GET /api/search` (Alias: `/api/v1/search`)
- **Auth**: None
- **Query Parameters**:
  - `q` (string, required): Search query string.
  - `page` (int, default `1`)
  - `page_size` (int, default `20`)
- **Response `200 OK`**: Paginated job item envelope.

#### `GET /api/sources` (Alias: `/api/v1/sources`)
- **Auth**: None
- **Description**: Returns monitored platform sources and operational status.

#### `GET /api/categories` (Alias: `/api/v1/categories`)
- **Auth**: None
- **Description**: Returns active job categories derived dynamically from indexed tags.

#### `GET /api/stats` (Alias: `/api/v1/stats`)
- **Auth**: None
- **Description**: Returns truthful aggregated statistics from the active database ledger.

#### `GET /api/status` (Alias: `/api/v1/status`)
- **Auth**: None
- **Rate limit**: 120 requests/minute per IP
- **Description**: Returns real discovery-engine status (cycle state and timing). No simulated live activity.


---

## Authentication Endpoints

### `POST /api/auth/register` (Alias: `/api/v1/auth/register`)
- **Auth**: None
- **Request Body**:
```json
{
  "email": "engineer@example.com",
  "password": "SecretPassword123!",
  "full_name": "Test Engineer"
}
```
- **Response `201 Created`**: Returns auth token and user record.
- **Errors**: `409 Conflict` if email registered; `422 Unprocessable` if password too short (< 8 chars).

### `POST /api/auth/login` (Alias: `/api/v1/auth/login`)
- **Auth**: None
- **Request Body**:
```json
{
  "email": "engineer@example.com",
  "password": "SecretPassword123!"
}
```
- **Response `200 OK`**: Returns session `token` and `user` object.
- **Errors**: `401 Unauthorized` on invalid credentials.

### `GET /api/auth/me` (Alias: `/api/v1/auth/me`)
- **Auth**: Required (`Bearer <token>`)
- **Response `200 OK`**: Current user profile.

### `POST /api/auth/logout` (Alias: `/api/v1/auth/logout`)
- **Auth**: Required
- **Response `200 OK`**: Confirms session invalidation.

---

## User Data & Activity Endpoints (Authenticated)

### `GET /api/saved` (Alias: `/api/v1/saved`)
- **Auth**: Required
- **Response `200 OK`**: List of jobs saved by the user.

### `POST /api/jobs/{ident}/save` (Alias: `/api/v1/jobs/{ident}/save`)
- **Auth**: Required
- **Response `200 OK`**: `{ "saved": true, "job_id": "telus-70231" }`

### `DELETE /api/jobs/{ident}/save` (Alias: `/api/v1/jobs/{ident}/save`)
- **Auth**: Required
- **Response `200 OK`**: `{ "saved": false, "job_id": "telus-70231" }`
- **Errors**: `404 Not Found` if job was not previously saved.

### `GET /api/applications` & `POST /api/applications`
- **Auth**: Required
- **Description**: Track job application pipeline states (`saved`, `applied`, `interviewing`, `offered`, `rejected`).

### `PATCH /api/applications/{app_id}`
- **Auth**: Required
- **Description**: Update application tracking state. Returns `404` if ID unknown or owned by another user.

### `GET /api/profile` & `PATCH /api/profile`
- **Auth**: Required
- **Description**: Read and configure candidate skills, experience level, and rate expectations.

### `GET /api/recommendations`
- **Auth**: Required
- **Description**: Candidate-specific recommendations weighted by user profile match.

### `GET /api/notifications` (Alias: `/api/v1/notifications`)
- **Auth**: Required (`Bearer <token>`)
- **Query**: `limit` (integer, 1-100, default 30)
- **Rate limit**: 120 requests/minute per IP
- **Response `200 OK`**: Activity feed for the signed-in user — saves, application updates, and real discovery events on matching listings. No synthetic events are produced.

---

## System Diagnostics

### `GET /api/system/overview`
- **Auth**: Internal token via header `X-System-Token` or `Authorization: Bearer <SYSTEM_TOKEN>`.
- **Response `200 OK`**: Diagnostic operational metrics, memory utilization, and worker health.

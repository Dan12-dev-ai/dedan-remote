# Security Policy — DEDAN Remote

DEDAN Remote is engineered with defensive security principles across its public REST API, external discovery scrapers, authentication flows, and storage boundaries.

---

## 1. Supported Versions

Security updates and critical patches are actively applied to the following release lines:

| Version | Supported | Status |
|---|---|---|
| `1.0.x` | Yes | Current active line |
| `< 1.0.0` | No | Unsupported |

---

## 2. Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security defect, vulnerability, or exposure:
1. Open a private [GitHub Security Advisory](https://github.com/Dan12-dev-ai/dedan-remote/security/advisories).
2. Or contact the maintainers directly at: `danieldaniel122333@gmail.com`.

### Report Contents
Please include:
- Vulnerability classification (e.g. Authentication Bypass, SQLi, SSRF, XSS, Path Traversal).
- Affected routes, parameters, or file paths.
- Step-by-step reproduction instructions and minimal proof-of-concept.
- Potential impact assessment.

### Response & Disclosure SLA
- **Initial Response**: Within 48 hours.
- **Triage & Status Assessment**: Within 5 business days.
- **Critical CVE Remediation Window**: Within 72 hours of verification.
- **Coordinated Disclosure**: Fixes will be released via security advisory pull requests prior to public release disclosure.

---

## 3. Threat Model & Defensive Architecture

### Outbound HTTP Security & SSRF Protection
The discovery pipeline makes outbound HTTP requests to scrape job postings from external portals. To mitigate Server-Side Request Forgery (SSRF):
- Dynamic URLs supplied to the scraper registry are validated against an allowlist of supported domains and schemas.
- Outbound requests strictly enforce `https://` or `http://` protocols; file schemas (`file://`), loopback addresses (`127.0.0.1`, `localhost`), and cloud metadata IP ranges (`169.254.169.254`) are systematically rejected.
- Redirects are bounded to prevent infinite redirect loops.

### Authentication & Session Security
- User passwords are stored using salted `bcrypt` hashes. Plaintext passwords are never logged, cached, or persisted.
- Authentication tokens are issued via stateless HS256 JWT sessions or cryptographic bearer tokens validated on each private endpoint.
- Private routes enforce strict tenant data isolation: users can only read, update, or delete their own application trackers and bookmarks.

### SQL Safety & Parameterization
- All SQL queries against SQLite and async PostgreSQL use parameterized queries (`?` in SQLite, `$1, $2` in asyncpg).
- Dynamic SQL string concatenation is strictly prohibited across all repositories and query helpers.

### Input Sanitization & XSS Defense
- External job postings and user inputs are sanitized before rendering or persisting.
- HTML tags and JavaScript event handlers (`onerror`, `onload`, `<script>`) are stripped or escaped.
- The FastAPI REST layer transmits standard defensive HTTP response headers:
  - `Content-Security-Policy: default-src 'self'`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains`

### Secrets Management
- Application configuration and secrets are read from environment variables via `config/settings.py`.
- The `.env` file is excluded in `.gitignore` and `.dockerignore`.
- CI automation checks prevent committing hardcoded private keys or production credentials.

### API Rate Limiting & HMAC Idempotency
- Experimental and paid transaction hooks integrate HMAC request signatures to prevent replay attacks and duplicate operations.
- Token-bucket rate limiting restricts burst requests per client IP.

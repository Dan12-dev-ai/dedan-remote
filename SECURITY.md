# 🔒 Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅ Yes |

## Reporting Vulnerabilities

Please report security vulnerabilities responsibly by emailing
security@yourdomain.com or opening a private [GitHub Security Advisory](https://github.com/yourusername/dedan-remote/security/advisories).

### What to Include

- Type of vulnerability (SQLi, XSS, SSRF, command injection, etc.)
- Affected component and file path
- Steps to reproduce
- Proof-of-concept code (if applicable)
- Your contact information (optional, for follow-up)

## Security Practices

### Secrets Management

- **Never commit `.env`** — it is gitignored and loaded at runtime only
- All secrets (DB passwords, API tokens, bot keys) must live in `.env` exclusively
- The `.env.example` file contains only placeholder values
- Rotate any accidentally committed secrets immediately

### Code Security

- **Parameterized queries** — all SQLite and asyncpg calls use `?` or `$1` placeholders
- **Input validation** — scraper data is sanitized before storage
- **TLS/SSL** — all outbound HTTP requests use `aiohttp` with default TLS
- **Circuit breakers** — per-source failure tracking prevents cascading failures
- **HMAC idempotency** — replay-protected keys for paid API calls

### Dependency Hygiene

- Run `pip install --upgrade` regularly to patch vulnerability CVEs
- `mypy` and `flake8` are enforced in CI to catch type and style issues
- Security penetration tests (`test_security_penetration.py`) run weekly on CI

### Report Handling

- Valid reports are acknowledged within 48 hours
- Critical issues (credential exposure, RCE) receive a patch within 72 hours
- Non-critical issues are addressed in the next release cycle

## Local Security Testing

```bash
# Run security-focused tests
pytest tests/ -m security -v

# Run property-based financial invariant tests
pytest tests/ -m property_based -v
```
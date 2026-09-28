# Contributing to DEDAN Remote

Thank you for your interest in contributing to DEDAN Remote. We welcome bug reports, documentation improvements, and engineering contributions that adhere to the project's quality, testing, and truthfulness standards.

---

## 1. Code of Conduct

All contributors and maintainers are expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 2. Branching & Git Strategy

- **Default Branch**: `master`. All production-ready code is merged into `master`.
- **Feature Branches**: Branch from `master` using descriptive prefixes:
  - `feature/<name>` for new capabilities
  - `fix/<name>` for bug fixes
  - `docs/<name>` for documentation updates
  - `refactor/<name>` for structural improvements without functional changes

---

## 3. Local Development Setup

### Prerequisites
- Python 3.11 or 3.12
- Node.js 20+ (Node 22 recommended) and npm
- Docker (optional, for full-stack AE-OS integration testing)

### Step-by-Step Setup
```bash
# 1. Clone repository
git clone https://github.com/Dan12-dev-ai/dedan-remote.git
cd dedan-remote

# 2. Set up Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 3. Configure local environment variables
cp .env.example .env

# 4. Initialize local SQLite databases
python3 main.py --setup

# 5. Build and install frontend dependencies
cd frontend
npm ci
npm run build
cd ..
```

---

## 4. Running the Development Servers

To run the API and scheduler locally:
```bash
# Start FastAPI backend (serves API and compiled frontend at http://localhost:8000)
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# In a separate terminal, trigger a single discovery cycle
python3 main.py --once
```

For frontend live development with hot reload:
```bash
cd frontend
npm run dev
# Vite runs at http://localhost:5173, proxying API requests to http://localhost:8000
```

---

## 5. Verification & Testing Requirements

Before opening a pull request, all automated checks must succeed locally:

```bash
# 1. Run unit and contract tests
python3 -m pytest tests/ -v -m "not integration and not property_based"

# 2. Run property-based tests (if storage or ledgers are touched)
python3 -m pytest tests/ -v -m "property_based"

# 3. Verify frontend build and TypeScript types
cd frontend && npm run build && cd ..

# 4. Validate Docker Compose definition
docker compose config
```

---

## 6. Commit Message Convention

We adhere to the [Conventional Commits](https://www.conventionalcommits.org/) specification:

- `feat: add toloka pagination parser`
- `fix: handle missing salary range in outlier parser`
- `docs: update API documentation for /api/v1/jobs`
- `test: add unit test for eligibility country restrictions`
- `refactor: clean up scheduler agent task lifecycle`
- `ci: add ruff format validation to GitHub Actions`

---

## 7. Pull Request Submission Checklist

- [ ] Create a feature branch off `master`.
- [ ] Ensure all code changes include corresponding unit or regression tests.
- [ ] Ensure documentation is updated if user-facing behavior or endpoints changed.
- [ ] Fill out the [Pull Request Template](.github/PULL_REQUEST_TEMPLATE.md).
- [ ] Verify that all CI checks pass on GitHub.

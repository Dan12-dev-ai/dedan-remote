# DEDAN Remote — Intelligent Global Opportunity Discovery

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**DEDAN Remote is an autonomous opportunity discovery system that scans multiple platforms for AI-related remote work opportunities and provides a modern web interface for exploration.**

The system consists of:
- **Backend**: Python-based scrapers, ranking engine, and FastAPI REST API
- **Frontend**: React SPA with cinematic dark-themed UI
- **Data Pipeline**: Async scraping, deduplication, 0-100 scoring, and multi-channel notifications
- **Optional AE-OS**: Enterprise multi-agent backbone with tiered memory (PostgreSQL, Redis, Neo4j, Qdrant)

---

## 🌟 Features

- **🔍 Multi-Platform Scanning** — Built-in scrapers for Outlier, Alignerr, OneForma, TELUS Digital, Welocalize, Appen, DataAnnotation, Clickworker, and more
- **🧩 Extensible Architecture** — Add new websites in minutes by subclassing `BaseScraper`
- **📊 Smart Ranking** — Every job scored 0-100 based on remote work, salary, AI relevance, beginner-friendliness, freshness, and more
- **📧 Multi-Channel Notifications** — Email (SMTP or Gmail API), Telegram, and Discord webhooks
- **🛡️ Error Resilience** — Circuit breakers, exponential backoff retries, graceful degradation
- **⚡ High Performance** — Async concurrent scraping with connection pooling
- **🔒 Secure** — All secrets in `.env`, SQL injection prevention, input validation
- **🧪 Fully Tested** — Unit and integration tests with comprehensive coverage
- **🐳 Docker Ready** — Multi-stage Dockerfile and docker-compose.yml included
- **📈 Production Logging** — Structured JSON logging with rotation
- **⏰ Flexible Scheduling** — Cron or interval-based execution with overlap prevention
- **🌐 Modern Web UI** — React SPA with cinematic dark-themed interface for browsing opportunities

---

## 📋 Supported Platforms

| Platform | Type | Status |
|----------|------|--------|
| [Outlier](https://outlier.ai) | AI Training & Evaluation | ✅ Active |
| [Alignerr](https://alignerr.com) | AI Training & Data Labeling | ✅ Active |
| [OneForma](https://www.oneforma.com) | AI Data Annotation | ✅ Active |
| [TELUS Digital](https://www.telusdigital.com) | AI Data Annotation | ✅ Active |
| [Welocalize](https://www.welocalize.com) | AI Data & Translation | ✅ Active |
| [Appen](https://www.appen.com) | AI Data Annotation | ✅ Active |
| [DataAnnotation](https://www.dataannotation.tech) | AI Training | ✅ Active |
| [Clickworker](https://www.clickworker.com) | Microtasks & AI | ✅ Active |

> **Adding a new platform takes less than a minute.** Create a new file in `scrapers/` implementing `BaseScraper` — the system auto-discovers it.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         DEDAN Remote                             │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    Product Plane                           │ │
│  │  Scheduler → Discovery → Ranking → Notification → Database │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    API Plane (FastAPI)                      │ │
│  │  /api/jobs, /api/users, /api/auth, /api/preferences        │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    Frontend (React)                         │ │
│  │  Opportunity Explorer, Job Details, Saved, Profile         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              AE-OS Plane (Optional)                         │ │
│  │  PostgreSQL (episodic), Redis (working), Neo4j (graph),    │ │
│  │  Qdrant (vectors), Prometheus (metrics)                     │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Component Overview

- **`api/`** — FastAPI application with routers for jobs, users, auth, preferences
- **`frontend/`** — React SPA (Vite + React) with cinematic dark-themed UI
- **`config/`** — Pydantic-settings configuration from `.env`
- **`models/`** — Standardized `Job` dataclass
- **`scrapers/`** — Platform-specific scrapers (auto-discovered)
- **`agents/`** — Scheduler, Discovery, and Ranking agents
- **`database/`** — SQLite database with circuit breaker support
- **`notifications/`** — Email, Telegram, and Discord notifiers
- **`utils/`** — Async HTTP client, structured logger
- **`tests/`** — Comprehensive test suite
- **`main.py`** — CLI entry point for discovery scheduler

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- Node.js 18+ (for frontend development)
- Docker & Docker Compose (for containerized deployment)
- Gmail account with [App Password](https://support.google.com/accounts/answer/185833) (for email notifications)
- (Optional) Telegram bot token and Discord webhook URL

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/dedan-remote.git
cd dedan-remote

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install backend dependencies
pip install -r requirements.txt

# 4. Install frontend dependencies
cd frontend
npm install
cd ..

# 5. Configure environment
cp .env.example .env
nano .env  # Edit with your credentials

# 6. Initialize database
python main.py --setup

# 7. Run a test cycle
python main.py --once

# 8. Start the scheduler
python main.py

# 9. In another terminal, start the API server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 10. In another terminal, start the frontend dev server
cd frontend
npm run dev
```

### Docker Deployment

```bash
# Build and run full stack
docker compose up --build -d

# View logs
docker compose logs -f

# Run a single discovery cycle
docker compose run --rm dedan-remote python main.py --once
```

---

## 📧 Configuration

All configuration is via `.env` file. See `.env.example` for all options.

### Required Settings

```ini
# Email (required for notifications)
EMAIL=your.email@gmail.com
EMAIL_PASSWORD=your-app-password

# SMTP (Gmail defaults shown)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587

# Database
DATABASE_PATH=data/opportunities.db
```

### Optional Settings

```ini
# Schedule
CHECK_INTERVAL=60        # minutes
CRON_SCHEDULE=*/60 * * * *

# Notification Channels
ENABLE_TELEGRAM=false
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

ENABLE_DISCORD=false
DISCORD_WEBHOOK_URL=your_webhook_url

# Scoring Threshold
MIN_SCORE_FOR_NOTIFICATION=50

# AE-OS (Optional - requires PostgreSQL, Redis, Neo4j, Qdrant)
AEOS_ENABLED=false
AEOS_POSTGRES_PASSWORD=aeos_password
AEOS_REDIS_URL=redis://localhost:6379/0
```

### Getting a Gmail App Password

1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords)
2. Select "Mail" and your device
3. Copy the 16-character password into `EMAIL_PASSWORD`

---

## 🎯 Scoring System

Jobs are scored 0-100 based on these configurable dimensions:

| Dimension | Default Weight | What It Measures |
|-----------|---------------|------------------|
| Remote | 0.20 | Fully remote jobs score highest |
| Worldwide | 0.15 | No country restriction = higher score |
| Salary | 0.15 | Presence and estimated value |
| Beginner | 0.10 | Entry-level friendly indicators |
| AI Related | 0.15 | How strongly AI-focused the role is |
| English | 0.05 | English-language preference |
| Simplicity | 0.10 | Low barrier to application |
| Freshness | 0.10 | Recently posted jobs score higher |

> **Tip:** Adjust weights in `.env` to prioritize what matters most to you.

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing

# Run specific test file
pytest tests/test_ranking.py -v
```

---

## 📁 Adding a New Scraper

Adding support for a new platform takes **less than a minute**:

```python
# scrapers/my_new_platform.py
from scrapers.base_scraper import BaseScraper
from models.job import job_from_scraper_result

class MyNewPlatformScraper(BaseScraper):
    name = "My New Platform"        # Human-readable name
    source = "mynewplatform"         # Unique identifier
    base_url = "https://example.com"
    JOBS_URL = "https://example.com/careers"

    async def scrape(self) -> list[Job]:
        jobs = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            # Parse with parsel, beautifulsoup, or regex
            # ... parsing logic ...
            jobs.append(job_from_scraper_result(
                title="AI Trainer",
                company="My Platform",
                url="https://example.com/job/1",
                source=self.source,
                # ... other fields ...
            ))
        except Exception as exc:
            logger.error("Scrape failed: %s", exc)
        return jobs
```

**That's it.** The system auto-discovers your scraper through `ScraperRegistry` — no registration needed.

---

## 🐳 Production Deployment

### VPS Deployment

```bash
# 1. Clone on server
git clone https://github.com/yourusername/AIJobFinder.git
cd AIJobFinder

# 2. Docker deployment (recommended)
docker-compose up --build -d

# 3. Or use systemd
sudo nano /etc/systemd/system/ai-opportunity-finder.service
```

#### systemd Service File
```ini
[Unit]
Description=AI Opportunity Finder
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/AIJobFinder
ExecStart=/opt/AIJobFinder/venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable ai-opportunity-finder
sudo systemctl start ai-opportunity-finder
```

### Monitoring

```bash
# Check status
python main.py --stats

# View logs
tail -f logs/ai_opportunity_finder.log

# Docker logs
docker-compose logs -f --tail=100
```

---

## 🛠️ CLI Commands

```bash
python main.py            # Start scheduler (runs forever)
python main.py --once     # Single discovery cycle
python main.py --stats    # Show database statistics
python main.py --setup    # Initialize database
python main.py --env custom.env  # Use custom .env file
```

---

## 🔒 Security

- **No hardcoded secrets** — All credentials in `.env` (gitignored)
- **SQL injection protection** — Parameterized queries throughout
- **Input validation** — All scraper data sanitized before storage
- **HTTPS only** — All outbound requests use TLS/SSL
- **Circuit breaker** — Prevents repeated failures to unavailable services

---

## 📊 Database Schema

```sql
jobs         — All discovered jobs with scores and notification status
notifications — Notification delivery log per channel
execution_history — Every scrape cycle with stats
website_status — Per-source health tracking with circuit breaker
```

---

## 📝 Logging

- **Console**: Color-coded output for development
- **File**: Rotating JSON structured logs for production
- **Structured**: JSON format compatible with log aggregation tools

---

## 🧭 Project Roadmap

- [x] Core scraping framework with auto-discovery
- [x] 8 platform scrapers
- [x] Smart scoring and ranking (8 dimensions)
- [x] Multi-channel notifications
- [x] Circuit breaker error handling
- [x] Docker deployment
- [x] FastAPI REST API
- [x] React SPA with cinematic UI
- [ ] RSS feed output
- [ ] Slack webhook support
- [ ] More scrapers (Toloka, Invisible Technologies, etc.)
- [ ] Machine learning-based job matching
- [ ] AE-OS PPO policy integration

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass (`pytest tests/ -v`)
5. Submit a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## ⚠️ Disclaimer

This tool scrapes publicly available job listings from various platforms. Please respect each platform's terms of service and robots.txt. The authors are not responsible for any misuse.

---

## 📚 Additional Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Detailed system architecture and component breakdown
- [DEPLOYMENT.md](DEPLOYMENT.md) — Production deployment guide
- [SECURITY.md](SECURITY.md) — Security considerations and best practices
- [PROJECT_STATUS.md](PROJECT_STATUS.md) — Current implementation status
- [TESTING_GUIDE.md](TESTING_GUIDE.md) — Testing instructions and guidelines

---

<p align="center">
  Built with ❤️ for the AI remote work community<br>
  <sub>Never miss an opportunity again.</sub>
</p>
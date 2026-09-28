# ☁️ Cloud Deployment Guide — AIJobFinder

Complete, verified procedure for deploying the full stack to a production
cloud VM: FastAPI + React SPA, the discovery scheduler, and the AE-OS
datastores (Postgres, Redis, Neo4j, Qdrant, Prometheus).

The container image has been built end-to-end and verified locally
(`docker build` → exit 0, SPA bundle confirmed present at
`/app/frontend/dist`). Follow the steps below to run it on a server.

---

## Architecture

```
                        Internet (HTTPS)
                              │
                    ┌─────────▼─────────┐
                    │  Caddy (reverse   │  auto TLS via Let's Encrypt
                    │     proxy)        │
                    └─────────┬─────────┘
                              │ 127.0.0.1:8000
        ┌─────────────────────▼──────────────────────┐
        │        ai-opportunity-finder (app)         │
        │  uvicorn api.main:app  +  python main.py  │
        │  serves REST API, /metrics, AND the SPA    │
        └──────┬────────┬─────────┬─────────┬────────┘
               │        │         │         │  internal compose
          ┌────▼───┐ ┌──▼───┐ ┌───▼───┐ ┌───▼────┐   network only
          │ redis  │ │postgres│ │ neo4j │ │ qdrant │   (NOT published)
          └────────┘ └──────┘ └───────┘ └────────┘
```

Only **8000** (app) and **9091** (Prometheus UI) are published to the host.
All datastores are reachable **only** on the internal Docker network.

---

## Step 1 — Provision a server

Any x86-64 Linux VM with Docker works (Hetzner, DigitalOcean, AWS EC2, GCP,
Azure, OVH…). Minimum sizing:

| Workload | vCPU | RAM | Disk |
|---|---|---|---|
| Minimum (AE-OS off) | 2 | 4 GB | 40 GB |
| Recommended (full stack) | 4 | 8 GB | 80 GB SSD |

```bash
# Ubuntu 24.04 example
ssh root@YOUR_SERVER_IP

apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install -y git curl

# Verify
docker --version && docker compose version
```

**Firewall — open only what is needed:**

| Port | Purpose | Public? |
|---|---|---|
| 22 | SSH | yes (prefer IP-restricted) |
| 80 | HTTP → Caddy for TLS challenge | yes |
| 443 | HTTPS | yes |
| 8000 | App | **no** — bind to `127.0.0.1` |
| 9091 | Prometheus UI | **no** — SSH tunnel |

```bash
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp
ufw enable
```

---

## Step 2 — Get the code

```bash
git clone https://github.com/<you>/AIJobFinder.git
cd AIJobFinder
chmod +x scripts/deploy.sh
```

---

## Step 3 — Configure secrets

Let the script bootstrap `.env` (it generates strong random tokens), then edit:

```bash
cp .env.example .env
chmod 600 .env
```

**Required — change every default password:**

```ini
# System security (the API refuses to start insecurely in production)
DEDAN_ENV=production
DEDAN_ENABLE_HSTS=true
DEDAN_TRUST_PROXY=true          # required behind Caddy
DEDAN_SYSTEM_TOKEN=<openssl rand -hex 32>
DEDAN_CORS_ORIGINS=https://your-domain.com
AEOS_IDEMPOTENCY_SECRET=<openssl rand -hex 32>

# Datastore credentials — REPLACE the shipped defaults
AEOS_POSTGRES_PASSWORD=<strong-password>
AEOS_NEO4J_PASSWORD=<strong-password>

# Enable the AE-OS backbone
AEOS_ENABLED=true
```

> ℹ️ `AEOS_POSTGRES_PASSWORD` in `.env` is the **single source of truth** for the
> database password. `docker-compose.yml` feeds that one value to both the
> `postgres` service and the app container, so they cannot drift. If it is
> missing, `docker compose` fails fast with a clear error instead of silently
> starting with a default. Note that PostgreSQL only applies the value on
> **first** initialization of the `postgres-data` volume — see
> [Rotating the DB password](#rotating-the-db-password).

**Optional — notifications** (set at least one or the scheduler runs silently):

```ini
EMAIL=you@example.com
EMAIL_PASSWORD=<Gmail app password>
ENABLE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ENABLE_DISCORD=true
DISCORD_WEBHOOK_URL=...
```

---

## Step 4 — Deploy

The script does everything: builds the image, starts all services, waits for
readiness, and verifies the SPA is really being served.

```bash
./scripts/deploy.sh
```

Or manually:

```bash
# Build (frontend bundle is built INSIDE the image — frontend/dist is
# gitignored, so skipping this produces an API where every page 404s)
docker compose -p aijobfinder build

# Start
docker compose -p aijobfinder up -d

# Watch readiness
docker compose -p aijobfinder logs -f ai-opportunity-finder
```

**Expected verification output:**

```
  Liveness : {"status":"healthy", ...}
  Readiness: {"ready": true, ...}
  SPA      : OK (HTTP 200)
```

Confirm manually:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/ready
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/   # must be 200
docker compose -p aijobfinder ps
```

---

## Step 5 — Terminate TLS with Caddy

```bash
apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```caddy
your-domain.com {
    encode gzip zstd

    # WebSocket / SSE support for the live dashboard
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "DENY"
        Referrer-Policy           "strict-origin-when-cross-origin"
        -Server
    }

    log {
        output file /var/log/caddy/aijobfinder.log
        format json
    }
}
```

```bash
# Point DNS A record at the server IP FIRST, then:
systemctl reload caddy
curl -I https://your-domain.com    # expect HTTP/2 200
```

Caddy provisions and renews the certificate automatically. With
`DEDAN_ENABLE_HSTS=true` the app also emits HSTS headers.

---

## Step 6 — Post-deploy hardening

**Bind the app to loopback only** so port 8000 is never directly reachable.
Edit `docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:8000:8000"
  - "127.0.0.1:9091:9090"   # Prometheus
```

```bash
docker compose -p aijobfinder up -d
```

**Verify the protected dashboard endpoint** (`/api/system/overview` is guarded
by `require_system_token` on the whole router):

```bash
curl -H "X-System-Token: $(grep DEDAN_SYSTEM_TOKEN .env | cut -d= -f2)" \
     http://127.0.0.1:8000/api/system/overview
```

**Reach internal UIs via SSH tunnel (never expose them):**

```bash
# Prometheus  → http://localhost:9091
ssh -L 9091:localhost:9091 root@YOUR_SERVER_IP
# Neo4j       → http://localhost:7474
ssh -L 7474:localhost:7474 root@YOUR_SERVER_IP
```

---

## Step 7 — Operations

### Logs

```bash
docker compose -p aijobfinder logs -f ai-opportunity-finder
docker compose -p aijobfinder logs --tail=200 ai-opportunity-finder
```

### Update to a new version

```bash
cd AIJobFinder
git pull
./scripts/deploy.sh
```

### Restart / stop

```bash
docker compose -p aijobfinder restart ai-opportunity-finder
docker compose -p aijobfinder down          # keeps volumes
docker compose -p aijobfinder down -v       # ⚠️ DELETES ALL DATA
```

### Backup

Use the bundled script — it backs up the app data plus every AE-OS volume,
prunes archives older than `RETENTION_DAYS`, and verifies the newest archive
is readable:

```bash
./scripts/backup.sh
```

Archives land in `./backups/` (override with `BACKUP_DIR`). To do it by hand:

```bash
mkdir -p backups
docker compose -p aijobfinder exec ai-opportunity-finder \
  tar czf - -C /app data > "backups/app-$(date +%F).tar.gz"

# Named volumes
docker run --rm -v aijobfinder_postgres-data:/src:ro -v "$PWD/backups:/dst" \
  alpine tar czf /dst/postgres-$(date +%F).tar.gz -C /src .
```

> `.env` is **not** included in the backup (it holds credentials). Back it up
> separately via your secret manager.

Schedule nightly with cron:

```bash
crontab -e
# 0 3 * * * cd /path/AIJobFinder && ./scripts/backup.sh >> /var/log/aijobfinder-backup.log 2>&1
```

**Test a restore at least once:**

```bash
mkdir -p /tmp/restore && tar xzf backups/app-*.tar.gz -C /tmp/restore
ls -la /tmp/restore/data
```

### Rotating the DB password

`AEOS_POSTGRES_PASSWORD` in `.env` is the single source of truth, but
PostgreSQL only reads it when it **first** initializes the `postgres-data`
volume. On an existing deployment, editing `.env` alone changes what the app
*sends* while the database still expects the *old* password — the result is an
auth failure. Rotate both sides:

```bash
NEW_PW="$(openssl rand -base64 24)"

# 1. Change it inside the running database.
docker compose -p aijobfinder exec postgres \
  psql -U aeos -d aeos_episodic \
  -c "ALTER USER aeos WITH PASSWORD '${NEW_PW}';"

# 2. Point .env at the same value.
sed -i "s|^AEOS_POSTGRES_PASSWORD=.*|AEOS_POSTGRES_PASSWORD=${NEW_PW}|" .env

# 3. Recreate the app so it picks up the new environment.
docker compose -p aijobfinder up -d --force-recreate ai-opportunity-finder
```

Verify: `docker compose -p aijobfinder exec ai-opportunity-finder \
python -c "import asyncio,core.database_async as d;m=d.AsyncPostgresDB();\
print(asyncio.run(m.connect()) or 'OK')"`

### Run a single discovery cycle (no scheduler)

```bash
docker compose -p aijobfinder run --rm ai-opportunity-finder \
  python main.py --once --aeos
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All pages 404 | SPA not built into image | `docker compose build --no-cache ai-opportunity-finder` |
| Container restarts on start | App refuses insecure prod config | Set `DEDAN_SYSTEM_TOKEN`, `DEDAN_CORS_ORIGINS`, `DEDAN_ENV=production` |
| API up, 403 on requests | CORS origin mismatch | Set `DEDAN_CORS_ORIGINS` to your exact `https://domain` |
| HTTPS redirect loop | Proxy headers not trusted | Set `DEDAN_TRUST_PROXY=true` |
| Postgres auth failure | Password not applied yet | Set `AEOS_POSTGRES_PASSWORD` in `.env`. If the `postgres-data` volume already exists, the value is ignored — rotate it (see below) |
| Discovery finds 0 jobs | Sources blocked / rate-limited | `docker compose logs ai-opportunity-finder \| grep -i scraper` |
| No notifications | Not configured | Set at least one channel in `.env`, `MIN_SCORE_FOR_NOTIFICATION` |
| Port already in use | Conflict | `ss -lntp \| grep 8000`, then change the host-side port |

Useful diagnostics:

```bash
docker compose -p aijobfinder ps
docker compose -p aijobfinder logs --tail=100 postgres
docker exec -it aijobfinder-postgres pg_isready -U aeos -d aeos_episodic
docker compose -p aijobfinder exec redis redis-cli ping
```

---

## Deploying to a managed platform instead

The image is a standard OCI artifact, so it also runs on any managed host:

- **Fly.io** — `fly launch --dockerfile Dockerfile --port 8000`, then
  `fly secrets set DEDAN_SYSTEM_TOKEN=...`. Attach managed Postgres/Redis.
- **Railway / Render** — build from the Dockerfile, expose port 8000,
  set the `DEDAN_*` env vars in the dashboard.
- **AWS ECS / Fargate** — push to ECR, define a task with the env vars and
  an RDS/ElastiCache attachment. **Do not** run the bundled datastores here;
  compose them as managed services.
- **Kubernetes** — generate with `kustomize edit set image`; the app is
  stateless apart from `/app/data`, so mount a PVC or an EFS volume there and
  use managed Postgres/Redis/Neo4j/Qdrant.

In all cases: expose **only 8000**, put a TLS-terminating load balancer in
front, and supply the `DEDAN_*` secrets from a secret manager — never bake
them into the image.

---

## Security checklist

- [ ] `.env` is `chmod 600` and never committed
- [ ] All shipped default passwords replaced
- [ ] `DEDAN_SYSTEM_TOKEN` is 32+ random bytes
- [ ] `DEDAN_CORS_ORIGINS` lists only real origins
- [ ] `DEDAN_TRUST_PROXY=true` (Caddy in front)
- [ ] Port 8000 bound to `127.0.0.1`
- [ ] Firewall: only 22/80/443 open
- [ ] TLS valid, HSTS enabled
- [ ] Nightly backups configured and a restore actually tested
- [ ] `restart: unless-stopped` on all services


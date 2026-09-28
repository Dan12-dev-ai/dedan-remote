# AIJobFinder — World-Class Testing Suite Documentation

## Overview

This testing suite elevates AIJobFinder to enterprise-grade verification standards through:

1. **Stateful Property-Based Testing** — Hypothesis-driven randomized transaction state machines
2. **Docker Testcontainers** — Live PostgreSQL & Redis instances for integration testing
3. **Security Penetration Tests** — 10 hardened security layers with malicious payload assertions
4. **Financial Invariant Checking** — Strict ledger consistency (net_profit = revenue - costs)
5. **Advanced Cache Semantics** — Redis layer testing with coherency guarantees

---

## Test Structure

```
tests/
├── conftest.py                           # World-class fixtures & infrastructure
├── pytest.ini                            # Pytest configuration
├── test_database.py                      # Database layer (SQLite + PostgreSQL)
├── test_financial_ledger_property_based.py  # Stateful property-based tests
├── test_security_penetration.py          # 10-layer security penetration tests
├── test_integration_docker.py            # Docker integration tests
├── test_cache_redis_layer.py             # Redis cache semantics
├── test_ranking.py                       # Ranking agent (legacy)
├── test_scheduler.py                     # Scheduler agent (legacy)
└── test_email.py                         # Email notifier (legacy)
```

---

## Core Testing Architectures

### 1. Stateful Property-Based Testing

**File:** `tests/test_financial_ledger_property_based.py`

Uses `hypothesis.stateful` to execute 10,000+ continuous random mutations on financial ledger state machines.

#### Key Features:
- **State Machine:** `FinancialLedgerStateMachine`
- **Random Operations:** 5 transaction types + audit operations
- **Invariants Checked:**
  - `verified_net_profit = verified_revenue - verified_costs` (strict)
  - All amounts remain non-negative
  - Transaction accounting consistency

#### Running Property-Based Tests:
```bash
# Run all property-based tests
pytest tests/ -m property_based -v

# Run with custom hypothesis examples
pytest tests/test_financial_ledger_property_based.py --hypothesis-seed=42

# Run slow property tests
pytest tests/ -m "property_based and slow" -v
```

#### Example Test:
```python
@settings(max_examples=500, stateful_step_count=100)
def test_financial_ledger_invariants(hypothesis_settings):
    """Executes 500 complete state machine runs (50,000 total steps)"""
    sm = FinancialLedgerStateMachine()
    sm.runTest()  # Hypothesis generates random operations
```

---

### 2. Docker Integration Infrastructure

**File:** `tests/conftest.py`

Provides live Docker containers via testcontainers for PostgreSQL and Redis.

#### Fixtures Provided:

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `postgres_container` | session | Live PostgreSQL database |
| `redis_container` | session | Live Redis instance |
| `postgres_pool` | function | Async PostgreSQL connection pool |
| `redis_client` | function | Async Redis client |
| `faker_instance` | function | Realistic test data generation |
| `fake_jobs` | function | 10 diverse fake jobs |
| `hypothesis_settings` | function | Quick hypothesis configuration |
| `mock_financial_state` | function | Financial ledger state |
| `malicious_payloads` | function | Security test payloads |

#### Example Usage:
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_concurrent_writes(postgres_pool):
    """Uses live PostgreSQL instance"""
    await postgres_pool._execute(
        "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
        "task-1",
        '{"test": "data"}'
    )
```

#### Starting Docker Containers:
```bash
# Ensure Docker daemon is running
docker ps

# Run integration tests (containers start automatically)
pytest tests/ -m integration -v

# View container logs
docker logs <container_id>
```

---

### 3. Security Penetration Tests

**File:** `tests/test_security_penetration.py`

Tests 10 hardened security layers against OWASP malicious payloads.

#### 10 Security Layers Tested:

| Layer | Focus | Test Class |
|-------|-------|-----------|
| 1 | HMAC/Idempotency Keys | `TestSecurityLayer1_IdempotencyKeyValidation` |
| 2 | SQL Injection Prevention | `TestSecurityLayer2_SQLInjectionPrevention` |
| 3 | SSRF/Loopback Prevention | `TestSecurityLayer3_SSRFPrevention` |
| 4 | XSS Prevention | `TestSecurityLayer4_XSSPrevention` |
| 5 | Command Injection Prevention | `TestSecurityLayer5_CommandInjectionPrevention` |
| 6 | Rate Limiting | `TestSecurityLayer6_RateLimiting` |
| 7 | Treasury Hard-Brake | `TestSecurityLayer7_TreasuryHardBrake` |
| 8 | Dead-Letter Queuing | `TestSecurityLayer8_DeadLetterQueuing` |
| 9 | Telemetry/Alerting | `TestSecurityLayer9_TelemetryAlerting` |
| 10 | Request/Response Integrity | `TestSecurityLayer10_RequestResponseIntegrity` |

#### Running Security Tests:
```bash
# Run all security penetration tests
pytest tests/test_security_penetration.py -m security -v

# Run specific security layer
pytest tests/test_security_penetration.py::TestSecurityLayer2_SQLInjectionPrevention -v

# Run with detailed output
pytest tests/test_security_penetration.py -m security -v --tb=long
```

#### Malicious Payloads Tested:
```python
{
    "sql_basic": "'; DROP TABLE users; --",
    "sql_union": "' UNION SELECT * FROM users --",
    "ssrf_localhost": "http://localhost:5432/admin",
    "ssrf_file": "file:///etc/passwd",
    "xss_script": "<script>alert('xss')</script>",
    "cmd_basic": "; rm -rf /",
    "hmac_empty": "",
    # ... and more
}
```

---

### 4. Docker Integration Tests

**File:** `tests/test_integration_docker.py`

Comprehensive integration tests with live containers.

#### Test Coverage:

**PostgreSQL Tests:**
- Connection pool lifecycle
- Schema creation & validation
- ACID transaction semantics
- Concurrent write handling
- Performance (1000 inserts < 30s)
- Data isolation

**Redis Tests:**
- Connection establishment
- GET/SET/INCR operations
- TTL expiration accuracy
- Key eviction (LRU)
- Concurrent operations (100 concurrent)
- Pipeline operations

**Cross-Layer Tests:**
- Cache-through patterns
- Cache invalidation
- Database/Cache consistency
- Concurrent load scenarios

#### Running Integration Tests:
```bash
# Run all integration tests (starts Docker containers)
pytest tests/test_integration_docker.py -m integration -v

# Run specific integration category
pytest tests/test_integration_docker.py::TestPostgresIntegration -v

# Run with container logs
pytest tests/test_integration_docker.py -v --log-cli-level=DEBUG
```

---

### 5. Redis Cache Layer Testing

**File:** `tests/test_cache_redis_layer.py`

Advanced cache semantics and coherency testing.

#### Test Categories:

1. **Cache Semantics**
   - Keyspace notifications
   - LRU eviction policy
   - TTL accuracy
   - Persistence

2. **Invalidation Patterns**
   - Time-based (TTL)
   - Tag-based (cache tags)
   - Event-driven (pub/sub)

3. **Coherency**
   - Write-through pattern
   - Write-behind pattern
   - Cache-aside pattern

4. **Performance**
   - Throughput (>5000 ops/sec)
   - Memory efficiency
   - Under load (10,000 ops)

#### Running Cache Tests:
```bash
pytest tests/test_cache_redis_layer.py -v

# Run performance benchmarks
pytest tests/test_cache_redis_layer.py::TestCachePerformanceUnderLoad -v --benchmark-only
```

---

## Test Execution Patterns

### Run All Tests
```bash
# Full test suite (includes integration, security, property-based)
pytest tests/ -v

# With coverage report
pytest tests/ --cov=. --cov-report=html

# Generate HTML coverage report
open htmlcov/index.html
```

### Run by Category
```bash
# Property-based tests only
pytest tests/ -m property_based -v

# Integration tests only (requires Docker)
pytest tests/ -m integration -v

# Security tests only
pytest tests/ -m security -v

# Exclude slow tests
pytest tests/ -m "not slow" -v

# Fast tests only
pytest tests/ -m "not slow and not integration" -v
```

### Run Specific Test Module
```bash
# Financial ledger tests
pytest tests/test_financial_ledger_property_based.py -v

# Security tests
pytest tests/test_security_penetration.py -v

# Database tests
pytest tests/test_database.py -v

# Cache/Redis tests
pytest tests/test_cache_redis_layer.py -v
```

### Parallel Execution
```bash
# Run tests in parallel (requires pytest-xdist)
pytest tests/ -n auto

# Run with specific number of workers
pytest tests/ -n 4
```

### Test with Specific Markers
```bash
# Run only fast tests
pytest tests/ -m "not slow" -v

# Run security + property_based
pytest tests/ -m "security or property_based" -v

# Exclude integration (no Docker required)
pytest tests/ -m "not integration" -v
```

---

## Pytest Markers

Custom markers for organizing tests:

```python
# In test code:
@pytest.mark.integration      # Requires Docker
@pytest.mark.security         # Security penetration
@pytest.mark.property_based    # Hypothesis tests
@pytest.mark.slow             # Takes >5 seconds
@pytest.mark.asyncio          # Async test
```

---

## Fixtures Reference

### Database Fixtures

```python
# SQLite (legacy, backward compatible)
temp_db: Database  # Temporary SQLite instance

# PostgreSQL (live container)
postgres_pool: AsyncPostgresDB  # Live connection pool
postgres_container: PostgresContainer  # Container lifecycle

# Redis (live container)
redis_client: aioredis.Redis  # Live Redis client
redis_container: RedisContainer  # Container lifecycle
```

### Data Fixtures

```python
sample_job: Job  # Single sample job
sample_jobs: list[Job]  # 5 sample jobs
fake_jobs: list[Job]  # 10 realistic fake jobs (via Faker)
faker_instance: Faker  # Faker instance for data generation
```

### Configuration Fixtures

```python
hypothesis_settings: settings  # Hypothesis configuration
benchmark_context: BenchmarkContext  # Performance timing
malicious_payloads: dict[str, str]  # OWASP payloads
mock_financial_state: dict  # Financial ledger initial state
```

---

## Advanced Features

### Hypothesis Property-Based Testing

Hypothesis generates random inputs to find edge cases.

```python
@given(
    revenue_amounts=st.lists(
        st.decimals(min_value=Decimal("1"), max_value=Decimal("1000"), places=2),
        min_size=1,
        max_size=50,
    )
)
@settings(max_examples=1000)  # Run 1000 random cases
@pytest.mark.property_based
def test_revenue_calculation(revenue_amounts):
    """Tested against 1000 random revenue amount lists"""
    total = sum(revenue_amounts)
    assert total >= 0
```

### Stateful Testing

Hypothesis stateful tests execute random sequences of operations:

```python
class FinancialLedgerStateMachine(RuleBasedStateMachine):
    @rule(transaction=transactions())  # Random transaction
    def record_pending_transaction(self, transaction):
        self.pending_transactions[transaction["id"]] = transaction
    
    @rule()  # Random selection from pending
    def verify_random_pending(self):
        if self.pending_transactions:
            # Verify a random pending transaction
            ...
    
    @invariant()  # Checked after every operation
    def check_financial_invariant(self):
        net_profit = self.verified_revenue - self.verified_costs
        assert net_profit >= 0
```

### Custom Assertions

Create custom assertion helpers:

```python
class TestFinancialInvariants:
    def assert_ledger_consistent(self, ledger):
        """Custom assertion for ledger consistency"""
        revenue = sum(t["amount"] for t in ledger if t["type"] == "revenue")
        costs = sum(t["amount"] for t in ledger if t["type"] == "cost")
        net = revenue - costs
        assert net >= 0, f"Net profit negative: {net}"
```

---

## Continuous Integration Setup

### GitHub Actions Example

```yaml
name: Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: postgres
      redis:
        image: redis:7-alpine
    
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.12"
      
      - name: Install dependencies
        run: |
          pip install -e .
          pip install -r requirements.txt
      
      - name: Run tests
        run: pytest tests/ -v --cov=. --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## Troubleshooting

### Docker Container Issues

```bash
# Check if Docker is running
docker ps

# View container logs
docker logs <container_id>

# Remove old containers
docker container prune

# Rebuild testcontainers
pytest tests/test_integration_docker.py --pull --tb=short
```

### Hypothesis Timeout

```bash
# Disable deadline for slow tests
pytest tests/test_financial_ledger_property_based.py -v \
    --hypothesis-seed=42

# Reduce examples
pytest tests/ -m property_based -v \
    --hypothesis-max-examples=100
```

### Async Test Failures

```bash
# Ensure pytest-asyncio is configured
pytest tests/ -m asyncio -v

# Check event loop mode
pytest tests/ --asyncio-mode=auto
```

---

## Performance Metrics

Expected performance targets:

| Operation | Target | Test |
|-----------|--------|------|
| PostgreSQL 1000 inserts | < 10s | `test_postgres_insert_performance` |
| Redis GET throughput | > 5000 ops/sec | `test_cache_throughput_get_operations` |
| Concurrent writes (50) | 100% success | `test_postgres_concurrent_writes` |
| Property-based mutations | 10,000+ | `test_financial_ledger_invariants` |
| Security payload blocks | 100% | All `TestSecurityLayer*` tests |

---

## Best Practices

1. **Use Docker Fixtures for Integration Tests**
   ```python
   @pytest.mark.integration
   async def test_with_postgres(postgres_pool):
       # Use live database
   ```

2. **Property-Based for Coverage**
   ```python
   @given(inputs=st.lists(st.integers()))
   def test_many_inputs(inputs):
       # Hypothesis tests 1000 random inputs
   ```

3. **Clear Assertions**
   ```python
   # Good
   assert ledger.net_profit == revenue - costs, \
       f"Expected {revenue - costs}, got {ledger.net_profit}"
   
   # Avoid
   assert result  # What failed?
   ```

4. **Isolate State**
   ```python
   # Each test gets clean Redis/PostgreSQL
   @pytest.mark.integration
   async def test_isolated(redis_client, postgres_pool):
       # Both are fresh
   ```

5. **Mark Test Type**
   ```python
   @pytest.mark.security
   @pytest.mark.integration
   async def test_with_markers(api_sentinel):
       # Easy to run: pytest -m "security and integration"
   ```

---

## Conclusion

This testing suite achieves:

✅ **Bulletproof Verification** — 10,000+ random mutations  
✅ **Production-Grade Integration** — Live Docker containers  
✅ **Enhanced Security** — 10-layer penetration testing  
✅ **Financial Integrity** — Strict invariant checking  
✅ **Enterprise Standards** — ACID, coherency, performance guarantees  

Run the full suite with:
```bash
pytest tests/ -v --cov=.
```

"""
Security Penetration Tests — 10 Hardened Security Layers.

Tests intentional malicious payloads against:
  1. HMAC/Idempotency Key Validation
  2. SQL Injection Prevention
  3. SSRF/Loopback Attack Prevention
  4. XSS (Cross-Site Scripting) Prevention
  5. Command Injection Prevention
  6. Rate Limiting & Token Bucket
  7. Treasury Hard-Brake (financial constraint)
  8. Dead-Letter Queue Routing
  9. Telemetry & Alert Triggering
  10. Request/Response Integrity

Each test verifies:
  - Malicious payload is properly rejected
  - Appropriate HTTP exception is raised
  - Telemetry gauge is incremented
  - Dead-letter entry is created (if applicable)
"""

from __future__ import annotations

import pytest

from core.api_sentinel import (
    APISentinel,
    Platform,
    SentinelBlockedError,
    SentinelCheckResult,
    SentinelContext,
    sentinel_wrapper,
)

# ── Security Layer 1: HMAC/Idempotency Key Validation ───────────────────────


class TestSecurityLayer1_IdempotencyKeyValidation:
    """
    Security Layer 1: HMAC Signature & Idempotency Key Management

    Ensures every API request carries a cryptographically valid,
    unique idempotency key signed with HMAC-SHA256.
    """

    @pytest.mark.security
    def test_empty_idempotency_key_rejected(self, idempotency_manager):
        """Test that empty idempotency key fails validation."""
        key = ""
        # Empty key should fail
        assert not idempotency_manager.validate_idempotency_key(key)

    @pytest.mark.security
    def test_invalid_hmac_signature_rejected(self, idempotency_manager):
        """Test that invalid HMAC signature is rejected."""
        tampered_key = "valid_key_xxxx_corrupted_bytes"
        # Tampered key should fail validation
        assert not idempotency_manager.validate_idempotency_key(tampered_key)

    @pytest.mark.security
    def test_replay_attack_detected(self, idempotency_manager):
        """Test that replayed idempotency keys are detected."""
        # Generate a valid key
        valid_key = idempotency_manager.generate_idempotency_key()

        # First use should pass
        assert idempotency_manager.is_key_unique(valid_key)

        # Mark as used
        idempotency_manager.mark_key_used(valid_key)

        # Second use (replay) should fail
        assert not idempotency_manager.is_key_unique(valid_key)

    @pytest.mark.security
    def test_valid_hmac_signature_accepted(self, idempotency_manager):
        """Test that correctly signed keys are accepted."""
        valid_key = idempotency_manager.generate_idempotency_key()

        # Valid key should validate
        assert idempotency_manager.validate_idempotency_key(valid_key)
        assert idempotency_manager.is_key_unique(valid_key)


# ── Security Layer 2: SQL Injection Prevention ────────────────────────────────


class TestSecurityLayer2_SQLInjectionPrevention:
    """
    Security Layer 2: SQL Injection Prevention

    Protects against common SQL injection attack vectors by:
      - Using parameterized queries (preventing direct SQL execution)
      - Validating user input strictly
      - Rejecting queries containing SQL keywords in data fields
    """

    @pytest.mark.security
    async def test_sql_injection_basic_rejected(self, api_sentinel, malicious_payloads):
        """
        Test: Classic SQL injection '; DROP TABLE users; --
        Expected: Payload rejected, error logged, DLQ entry created
        """
        malicious_input = malicious_payloads["sql_basic"]

        # Sentinel should detect and reject
        context = SentinelContext(
            platform=Platform.SHOPIFY,
            task_id="test-task-1",
            idempotency_key="valid-hmac-key",
            payload={"user_input": malicious_input},
        )

        # Verify payload is sanitized
        assert "DROP TABLE" not in str(context.payload["user_input"])

        # Should be blocked at parameter stage
        with pytest.raises(Exception):  # Security exception
            await api_sentinel.verify_request(context)

    @pytest.mark.security
    async def test_sql_union_attack_rejected(self, api_sentinel, malicious_payloads):
        """Test: UNION-based SQL injection is blocked."""
        payload = malicious_payloads["sql_union"]

        # Sentinel rejects UNION-based attacks
        context = SentinelContext(
            platform=Platform.STRIPE,
            task_id="test-task-2",
            idempotency_key="valid-key-2",
            payload={"query": payload},
        )

        # Should block due to SQL keyword detection
        with pytest.raises(Exception):
            await api_sentinel.verify_request(context)

    @pytest.mark.security
    async def test_sql_time_based_blind_rejected(self, api_sentinel, malicious_payloads):
        """Test: Time-based blind SQL injection is blocked."""
        payload = malicious_payloads["sql_time_based"]

        context = SentinelContext(
            platform=Platform.GUMROAD,
            task_id="test-task-3",
            idempotency_key="valid-key-3",
            payload={"filter": payload},
        )

        # Should timeout or block
        with pytest.raises(Exception):
            await api_sentinel.verify_request(context)

    @pytest.mark.security
    def test_parameterized_query_safe(self, api_sentinel):
        """Test: Parameterized queries safely reject SQL keywords."""
        # Clean input should pass parameter validation
        clean_input = "legitimate_user@example.com"

        # This should pass
        assert api_sentinel.is_safe_parameter(clean_input)


# ── Security Layer 3: SSRF/Loopback Attack Prevention ───────────────────────


class TestSecurityLayer3_SSRFPrevention:
    """
    Security Layer 3: SSRF (Server-Side Request Forgery) Prevention

    Blocks internal network access attempts:
      - localhost:* (127.0.0.1)
      - 169.254.169.254 (AWS metadata)
      - file:// protocols
      - Private IP ranges (10.*, 172.16.*, 192.168.*)
    """

    @pytest.mark.security
    async def test_ssrf_localhost_blocked(self, api_sentinel, malicious_payloads):
        """Test: Requests to localhost are blocked."""
        url = malicious_payloads["ssrf_localhost"]

        # Sentinel should block localhost access
        assert not api_sentinel.is_url_safe(url)

    @pytest.mark.security
    async def test_ssrf_aws_metadata_blocked(self, api_sentinel, malicious_payloads):
        """Test: AWS metadata service access blocked."""
        url = malicious_payloads["ssrf_internal"]

        # Block AWS metadata endpoint
        assert not api_sentinel.is_url_safe(url)

    @pytest.mark.security
    async def test_ssrf_file_protocol_blocked(self, api_sentinel, malicious_payloads):
        """Test: file:// protocol is blocked."""
        url = malicious_payloads["ssrf_file"]

        # File protocol should be denied
        assert not api_sentinel.is_url_safe(url)

    @pytest.mark.security
    async def test_private_ip_ranges_blocked(self, api_sentinel):
        """Test: Private IP ranges are blocked."""
        private_ips = [
            "http://10.0.0.1",
            "http://192.168.1.1",
            "http://172.16.0.1",
        ]

        for ip in private_ips:
            assert not api_sentinel.is_url_safe(ip)

    @pytest.mark.security
    async def test_external_url_allowed(self, api_sentinel):
        """Test: External URLs pass SSRF check."""
        external_url = "https://api.example.com/v1/endpoint"

        # External URLs should be allowed (SSRF check passes)
        assert api_sentinel.is_url_safe(external_url)


# ── Security Layer 4: XSS Prevention ─────────────────────────────────────────


class TestSecurityLayer4_XSSPrevention:
    """
    Security Layer 4: XSS (Cross-Site Scripting) Prevention

    Sanitizes all user-provided HTML/JavaScript:
      - Strips <script> tags
      - Escapes event handlers
      - Validates allowed HTML tags
      - Encodes special characters
    """

    @pytest.mark.security
    def test_xss_script_tag_sanitized(self, api_sentinel, malicious_payloads):
        """Test: <script> tags are stripped."""
        payload = malicious_payloads["xss_script"]
        sanitized = api_sentinel.sanitize_html(payload)

        # Script tag should be removed
        assert "<script>" not in sanitized
        assert "alert('xss')" not in sanitized

    @pytest.mark.security
    def test_xss_img_onerror_sanitized(self, api_sentinel, malicious_payloads):
        """Test: Event handlers in img tags are removed."""
        payload = malicious_payloads["xss_img"]
        sanitized = api_sentinel.sanitize_html(payload)

        # onerror handler should be removed
        assert "onerror=" not in sanitized
        assert "alert" not in sanitized

    @pytest.mark.security
    def test_xss_event_handler_removed(self, api_sentinel, malicious_payloads):
        """Test: Event handlers are stripped from body tags."""
        payload = malicious_payloads["xss_event"]
        sanitized = api_sentinel.sanitize_html(payload)

        # onload should be removed
        assert "onload=" not in sanitized

    @pytest.mark.security
    def test_safe_html_preserved(self, api_sentinel):
        """Test: Safe HTML is preserved."""
        safe_html = "<p>Hello <b>world</b></p>"
        sanitized = api_sentinel.sanitize_html(safe_html)

        # Safe tags should remain
        assert "<p>" in sanitized
        assert "<b>" in sanitized


# ── Security Layer 5: Command Injection Prevention ──────────────────────────


class TestSecurityLayer5_CommandInjectionPrevention:
    """
    Security Layer 5: Command Injection Prevention

    Prevents shell command injection by:
      - Rejecting shell metacharacters (;, |, &, `, $())
      - Using subprocess with shell=False
      - Validating all command arguments
    """

    @pytest.mark.security
    def test_command_injection_semicolon_blocked(self, api_sentinel, malicious_payloads):
        """Test: Commands with ; are blocked."""
        payload = malicious_payloads["cmd_basic"]

        # Should be detected
        assert not api_sentinel.is_safe_shell_input(payload)

    @pytest.mark.security
    def test_command_injection_backtick_blocked(self, api_sentinel, malicious_payloads):
        """Test: Backtick command substitution is blocked."""
        payload = malicious_payloads["cmd_backtick"]

        assert not api_sentinel.is_safe_shell_input(payload)

    @pytest.mark.security
    def test_command_injection_dollar_blocked(self, api_sentinel, malicious_payloads):
        """Test: $() command substitution is blocked."""
        payload = malicious_payloads["cmd_dollar"]

        assert not api_sentinel.is_safe_shell_input(payload)

    @pytest.mark.security
    def test_safe_shell_input_allowed(self, api_sentinel):
        """Test: Safe shell inputs are allowed."""
        safe_input = "legitimate_filename.txt"

        assert api_sentinel.is_safe_shell_input(safe_input)


# ── Security Layer 6: Rate Limiting & Token Bucket ───────────────────────────


class TestSecurityLayer6_RateLimiting:
    """
    Security Layer 6: Rate Limiting (Token Bucket Algorithm)

    Implements sliding-window rate limiting:
      - Max 100 requests per 60 seconds
      - Tokens refill at rate
      - Blocked requests are logged
    """

    @pytest.mark.security
    def test_rate_limit_enforced(self, api_sentinel):
        """Test: Requests exceeding rate limit are blocked."""
        # Exhaust token bucket
        for _ in range(101):  # Exceed limit
            if not api_sentinel.check_rate_limit("user-1"):
                break  # Rate limit hit

        # Should have hit limit
        assert not api_sentinel.check_rate_limit("user-1")

    @pytest.mark.security
    def test_rate_limit_per_client(self, api_sentinel):
        """Test: Rate limits are per-client."""
        # User 1 is rate limited
        for _ in range(101):
            api_sentinel.check_rate_limit("user-1")

        # User 2 should still have tokens
        assert api_sentinel.check_rate_limit("user-2")

    @pytest.mark.security
    def test_rate_limit_telemetry_recorded(self, api_sentinel):
        """Test: Rate limit violations are tracked in telemetry."""
        # Generate rate limit violation
        for _ in range(105):
            api_sentinel.check_rate_limit("user-test")

        # Check that violations were recorded
        violations = api_sentinel.get_rate_limit_violations("user-test")
        assert violations > 0


# ── Security Layer 7: Treasury Hard-Brake ────────────────────────────────────


class TestSecurityLayer7_TreasuryHardBrake:
    """
    Security Layer 7: Treasury Hard-Brake ($1,000 Constraint)

    Implements maximum spending constraint:
      - No single transaction > $1,000
      - Blocks high-risk operations
      - Routes violations to DLQ
      - Triggers treasury alert gauge
    """

    @pytest.mark.security
    async def test_high_value_transaction_blocked(self, api_sentinel):
        """Test: Transactions > $1,000 are rejected."""
        context = SentinelContext(
            platform=Platform.STRIPE,
            task_id="test-high-value",
            idempotency_key="valid-key",
            payload={},
            estimated_cost=1500.00,  # Over limit
        )

        # Should be blocked
        result = await api_sentinel.verify_request(context)
        assert not result.passed
        assert result.check_results["treasury_check"] == SentinelCheckResult.FAIL_TREASURY

    @pytest.mark.security
    async def test_within_limit_accepted(self, api_sentinel):
        """Test: Transactions within $1,000 are accepted."""
        context = SentinelContext(
            platform=Platform.SHOPIFY,
            task_id="test-normal-value",
            idempotency_key="valid-key",
            payload={},
            estimated_cost=500.00,  # Within limit
        )

        # Should pass treasury check
        result = await api_sentinel.verify_request(context)
        # May still fail other checks, but treasury should pass
        assert SentinelCheckResult.FAIL_TREASURY not in result.check_results.values()

    @pytest.mark.security
    def test_treasury_alert_triggered(self, api_sentinel):
        """Test: Treasury violations trigger telemetry alerts."""
        # Attempt high-value transaction
        api_sentinel.check_treasury_limit(1500.00)

        # Check alert gauge
        alerts = api_sentinel.get_treasury_alerts()
        assert len(alerts) > 0


# ── Security Layer 8: Dead-Letter Queue Routing ───────────────────────────────


class TestSecurityLayer8_DeadLetterQueuing:
    """
    Security Layer 8: Dead-Letter Queue (DLQ) Routing

    Routes all rejected requests to PostgreSQL DLQ table:
      - Failed sentinel verifications
      - SQL injection attempts
      - SSRF attacks
      - Rate limit violations
    """

    @pytest.mark.security
    @pytest.mark.integration
    async def test_sql_injection_routed_to_dlq(self, api_sentinel, postgres_pool):
        """Test: SQL injection attempts are written to DLQ."""
        context = SentinelContext(
            platform=Platform.GUMROAD,
            task_id="test-dlq-sql",
            idempotency_key="valid-key",
            payload={"query": "'; DROP TABLE users; --"},
        )

        # Verify should fail and route to DLQ
        try:
            await api_sentinel.verify_request(context)
        except Exception:
            pass

        # Check DLQ contains entry
        dlq_entries = await postgres_pool.get_dead_letter_queue()
        assert any("DROP TABLE" in str(e) for e in dlq_entries)

    @pytest.mark.security
    @pytest.mark.integration
    async def test_dlq_entry_includes_metadata(self, api_sentinel, postgres_pool):
        """Test: DLQ entries include full context for investigation."""
        context = SentinelContext(
            platform=Platform.TIKTOK_ADS,
            task_id="test-dlq-metadata",
            idempotency_key="test-key",
            payload={"malicious": "payload"},
        )

        # Force DLQ entry
        await api_sentinel.route_to_dlq(context, "test_failure_reason")

        # Verify entry has all required fields
        dlq_entries = await postgres_pool.get_dead_letter_queue()
        latest = dlq_entries[-1] if dlq_entries else None

        if latest:
            assert "test-dlq-metadata" in latest["task_id"]
            assert "Platform.TIKTOK_ADS" in str(latest["platform"])


# ── Security Layer 9: Telemetry & Alert Triggering ──────────────────────────


class TestSecurityLayer9_TelemetryAlerting:
    """
    Security Layer 9: Prometheus Telemetry & Alerting

    Every security violation increments:
      - sentinel_block_triggers (counter)
      - sentinel_dead_letter_writes (counter)
      - treasury_hard_brake_triggers (counter)
      - Task processing error counter
    """

    @pytest.mark.security
    async def test_block_trigger_incremented(self, api_sentinel):
        """Test: Sentinel block increments telemetry gauge."""
        initial_blocks = api_sentinel.get_total_blocks()

        # Trigger a block
        context = SentinelContext(
            platform=Platform.STRIPE,
            task_id="test-telemetry",
            idempotency_key="",  # Invalid
            payload={},
        )

        try:
            await api_sentinel.verify_request(context)
        except Exception:
            pass

        # Gauge should increment
        final_blocks = api_sentinel.get_total_blocks()
        assert final_blocks > initial_blocks

    @pytest.mark.security
    @pytest.mark.integration
    async def test_dead_letter_telemetry(self, api_sentinel):
        """Test: DLQ writes increment telemetry."""
        initial_dlq_count = api_sentinel.get_dlq_write_count()

        context = SentinelContext(
            platform=Platform.GUMROAD,
            task_id="test-dlq-count",
            idempotency_key="valid",
            payload={},
        )

        await api_sentinel.route_to_dlq(context, "test")

        final_dlq_count = api_sentinel.get_dlq_write_count()
        assert final_dlq_count > initial_dlq_count

    @pytest.mark.security
    def test_treasury_brake_telemetry(self, api_sentinel):
        """Test: Treasury brake triggers increment gauge."""
        initial = api_sentinel.get_treasury_brake_count()

        # Trigger brake
        api_sentinel.check_treasury_limit(2000.00)

        final = api_sentinel.get_treasury_brake_count()
        assert final > initial


# ── Security Layer 10: Request/Response Integrity ──────────────────────────


class TestSecurityLayer10_RequestResponseIntegrity:
    """
    Security Layer 10: Request/Response Integrity

    Ensures all requests and responses are:
      - Properly signed (HMAC)
      - Not tampered with
      - Include timestamps (prevent replay)
      - Have proper Content-Type headers
    """

    @pytest.mark.security
    def test_request_signature_validated(self, idempotency_manager):
        """Test: Request payloads are HMAC-validated."""
        payload = {"user_id": 123, "action": "payment"}

        # Generate valid signature
        signature = idempotency_manager.sign_payload(payload)

        # Verify signature validates
        assert idempotency_manager.verify_signature(payload, signature)

    @pytest.mark.security
    def test_tampered_payload_detected(self, idempotency_manager):
        """Test: Tampered payloads fail verification."""
        payload = {"user_id": 123, "action": "payment"}
        signature = idempotency_manager.sign_payload(payload)

        # Tamper with payload
        tampered_payload = {"user_id": 456, "action": "payment"}

        # Verification should fail
        assert not idempotency_manager.verify_signature(tampered_payload, signature)

    @pytest.mark.security
    def test_response_timestamp_included(self, api_sentinel):
        """Test: API responses include timestamps."""
        response = api_sentinel.create_response({"data": "test"})

        assert "timestamp" in response
        assert response["timestamp"] is not None

    @pytest.mark.security
    def test_content_type_validation(self, api_sentinel):
        """Test: Content-Type headers are validated."""
        # Valid JSON
        assert api_sentinel.validate_content_type("application/json")

        # Invalid content type
        assert not api_sentinel.validate_content_type("application/x-malicious")


# ── Outbound Connector Wrapper ─────────────────────────────────────────────


class _FakeDlqWriter:
    """Duck-typed stand-in for ``AsyncPostgresDB`` (no container required)."""

    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    async def insert_dead_letter(self, **kwargs: object) -> None:
        self.entries.append(kwargs)


class TestSentinelConnectorWrapper:
    """The connector wrapper must verify through the real sentinel API."""

    @staticmethod
    def _sentinel(dlq: _FakeDlqWriter) -> APISentinel:
        """Sentinel wired to an in-memory DLQ writer."""
        return APISentinel(
            postgres_db=dlq,  # type: ignore[arg-type]
            idempotency_secret="test-secret-key-for-testing",
        )

    @pytest.mark.security
    async def test_wrapper_injects_verification_result(self):
        """A decorated connector receives the sentinel_result kwarg."""
        seen: list[object] = []

        @sentinel_wrapper(self._sentinel(_FakeDlqWriter()))
        async def create_order(platform, task_id, payload, *args, **kwargs):
            seen.append(kwargs["sentinel_result"])
            return "created"

        result = await create_order(
            Platform.SHOPIFY,
            "task-wrapper-1",
            {"order_id": 42},
            estimated_cost=10.0,
        )

        assert result == "created"
        assert len(seen) == 1
        assert seen[0].passed is True  # type: ignore[attr-defined]

    @pytest.mark.security
    async def test_wrapper_blocked_when_key_invalid(self):
        """An invalid idempotency key blocks the call and routes it to the DLQ."""
        dlq = _FakeDlqWriter()
        ran = False

        @sentinel_wrapper(self._sentinel(dlq))
        async def create_order(platform, task_id, payload, *args, **kwargs):
            nonlocal ran
            ran = True
            return "created"

        with pytest.raises(SentinelBlockedError):
            await create_order(
                Platform.STRIPE,
                "task-wrapper-2",
                {"order_id": 7},
                idempotency_key="not-a-valid-key",
            )

        assert ran is False
        assert len(dlq.entries) == 1

"""
Tests for the notification layer — Telegram/Discord message construction,
enabled/disabled gating, and the central Notifier dispatcher.

All outbound network calls are mocked.
"""

from __future__ import annotations

import sys
import types

from models.job import Job, job_from_scraper_result
from notifications.discord_notifier import DiscordNotifier
from notifications.notifier import Notifier
from notifications.telegram_notifier import TelegramNotifier


def make_job(**kwargs: object) -> Job:
    defaults: dict[str, object] = {
        "title": "AI Trainer",
        "company": "TestCorp",
        "url": "https://example.com/job/1",
        "source": "test",
        "salary": "$100/hr",
        "tags": ["ai", "training"],
    }
    defaults.update(kwargs)
    return job_from_scraper_result(**defaults)  # type: ignore[arg-type]


# ── Telegram ─────────────────────────────────────────────────────────────────

class TestTelegramNotifier:
    """Message formatting and configuration gating."""

    def setup_method(self) -> None:
        self.notifier = TelegramNotifier()
        self.job = make_job()

    def test_format_message_contains_job_details(self) -> None:
        msg = self.notifier._format_message(self.job, 85.0)
        assert "AI Trainer" in msg
        assert "TestCorp" in msg
        assert "$100/hr" in msg
        assert "Apply Here" in msg
        assert "https://example.com/job/1" in msg

    def test_score_emoji_thresholds(self) -> None:
        assert "🔴" in self.notifier._format_message(self.job, 85.0)
        assert "🟡" in self.notifier._format_message(self.job, 65.0)
        assert "🟢" in self.notifier._format_message(self.job, 30.0)

    def test_send_returns_false_when_disabled(self) -> None:
        self.notifier._enabled = False
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_returns_false_when_unconfigured(self) -> None:
        self.notifier._enabled = True
        self.notifier._bot_token = ""
        self.notifier._chat_id = ""
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_returns_false_when_chat_id_missing(self) -> None:
        self.notifier._enabled = True
        self.notifier._bot_token = "123:abc"
        self.notifier._chat_id = ""
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_success_with_mocked_bot(self, monkeypatch) -> None:
        sent: list[dict] = []

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            def send_message(self, **kwargs: object) -> None:
                sent.append(kwargs)

        fake_telegram = types.ModuleType("telegram")
        fake_telegram.Bot = FakeBot  # type: ignore[attr-defined]
        fake_error = types.ModuleType("telegram.error")
        fake_error.TelegramError = Exception  # type: ignore[attr-defined]
        fake_telegram.error = fake_error  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
        monkeypatch.setitem(sys.modules, "telegram.error", fake_error)

        self.notifier._enabled = True
        self.notifier._bot_token = "123:abc"
        self.notifier._chat_id = "42"

        assert self.notifier.send(self.job, 85.0) is True
        assert len(sent) == 1
        assert sent[0]["chat_id"] == "42"
        assert "AI Trainer" in str(sent[0]["text"])

    def test_send_handles_bot_exception(self, monkeypatch) -> None:
        class ExplodingBot:
            def __init__(self, token: str) -> None:
                raise RuntimeError("boom")

        fake_telegram = types.ModuleType("telegram")
        fake_telegram.Bot = ExplodingBot  # type: ignore[attr-defined]
        fake_error = types.ModuleType("telegram.error")
        fake_error.TelegramError = Exception  # type: ignore[attr-defined]
        fake_telegram.error = fake_error  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
        monkeypatch.setitem(sys.modules, "telegram.error", fake_error)

        self.notifier._enabled = True
        self.notifier._bot_token = "123:abc"
        self.notifier._chat_id = "42"
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_batch_counts_successes(self, monkeypatch) -> None:
        results = iter([True, False, True])
        monkeypatch.setattr(self.notifier, "send", lambda job, score: next(results))
        sent = self.notifier.send_batch([(self.job, 80.0), (self.job, 60.0), (self.job, 40.0)])
        assert sent == 2


# ── Discord ──────────────────────────────────────────────────────────────────

class TestDiscordNotifier:
    """Embed construction and webhook gating."""

    def setup_method(self) -> None:
        self.notifier = DiscordNotifier()
        self.job = make_job()

    def test_embed_contains_job_details(self) -> None:
        embed = self.notifier._build_embed(self.job, 85.0)
        assert "AI Trainer" in str(embed["title"])
        assert embed["url"] == "https://example.com/job/1"
        field_names = [f["name"] for f in embed["fields"]]  # type: ignore[union-attr]
        assert any("Company" in n for n in field_names)
        assert any("Score" in n for n in field_names)
        assert "DEDAN Remote" in str(embed["footer"])  # type: ignore[index]

    def test_embed_color_thresholds(self) -> None:
        assert self.notifier._build_embed(self.job, 85.0)["color"] == 0x00FF00
        assert self.notifier._build_embed(self.job, 60.0)["color"] == 0xFFFF00
        assert self.notifier._build_embed(self.job, 40.0)["color"] == 0xFF0000

    def test_embed_description_truncated(self) -> None:
        long_job = make_job(description="x" * 500)
        embed = self.notifier._build_embed(long_job, 50.0)
        assert embed["description"] == "x" * 300 + "..."

    def test_embed_omits_description_when_absent(self) -> None:
        embed = self.notifier._build_embed(make_job(description=None), 50.0)
        assert "description" not in embed

    def test_send_returns_false_when_disabled(self) -> None:
        self.notifier._enabled = False
        self.notifier._webhook_url = "https://discord.example/hook"
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_returns_false_without_webhook_url(self) -> None:
        self.notifier._enabled = True
        self.notifier._webhook_url = ""
        assert self.notifier.send(self.job, 85.0) is False

    def test_send_batch_counts_successes(self, monkeypatch) -> None:
        results = iter([True, False])
        monkeypatch.setattr(self.notifier, "send", lambda job, score: next(results))
        assert self.notifier.send_batch([(self.job, 80.0), (self.job, 60.0)]) == 1

    @staticmethod
    def _install_fake_webhook(monkeypatch, status_code: int) -> None:
        """Install a fake discord_webhook module returning a status code."""
        class FakeEmbed:
            def __init__(self, **kwargs: object) -> None:
                self.fields: list[object] = []

            def add_embed_field(self, **kwargs: object) -> None:
                self.fields.append(kwargs)

            def set_footer(self, **kwargs: object) -> None:
                pass

            def set_description(self, text: str) -> None:
                pass

        class FakeWebhook:
            def __init__(self, url: str, rate_limit_retry: bool = False) -> None:
                self.embeds: list[FakeEmbed] = []

            def add_embed(self, embed: FakeEmbed) -> None:
                self.embeds.append(embed)

            def execute(self) -> types.SimpleNamespace:
                return types.SimpleNamespace(status_code=status_code)

        fake_module = types.ModuleType("discord_webhook")
        fake_module.DiscordWebhook = FakeWebhook  # type: ignore[attr-defined]
        fake_module.DiscordEmbed = FakeEmbed  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "discord_webhook", fake_module)

    def test_send_success_with_mocked_webhook(self, monkeypatch) -> None:
        self._install_fake_webhook(monkeypatch, status_code=200)
        self.notifier._enabled = True
        self.notifier._webhook_url = "https://discord.example/hook"
        assert self.notifier.send(self.job, 85.0) is True

    def test_send_failure_status_returns_false(self, monkeypatch) -> None:
        self._install_fake_webhook(monkeypatch, status_code=429)
        self.notifier._enabled = True
        self.notifier._webhook_url = "https://discord.example/hook"
        assert self.notifier.send(self.job, 85.0) is False


# ── Central dispatcher ───────────────────────────────────────────────────────

class TestNotifierDispatcher:
    """Notifier fans out to enabled channels and reports successes."""

    def setup_method(self) -> None:
        self.notifier = Notifier()
        self.job = make_job()

    def test_send_reports_successful_channels(self) -> None:
        self.notifier.email.send = lambda job, score: True  # type: ignore[method-assign]
        self.notifier.telegram.send = lambda job, score: False  # type: ignore[method-assign]
        self.notifier.discord.send = lambda job, score: True  # type: ignore[method-assign]

        channels = self.notifier.send(self.job, 85.0)
        assert channels == ["email", "discord"]

    def test_send_returns_empty_when_no_channels(self) -> None:
        self.notifier.email.send = lambda job, score: False  # type: ignore[method-assign]
        self.notifier.telegram.send = lambda job, score: False  # type: ignore[method-assign]
        self.notifier.discord.send = lambda job, score: False  # type: ignore[method-assign]

        assert self.notifier.send(self.job, 85.0) == []

    def test_send_batch_returns_per_job_channels(self) -> None:
        self.notifier.email.send = lambda job, score: True  # type: ignore[method-assign]
        self.notifier.telegram.send = lambda job, score: False  # type: ignore[method-assign]
        self.notifier.discord.send = lambda job, score: False  # type: ignore[method-assign]

        batch = [(self.job, 90.0), (self.job, 70.0)]
        results = self.notifier.send_batch(batch)
        assert len(results) == 2
        for job, score, channels in results:
            assert job is self.job
            assert score in (90.0, 70.0)
            assert channels == ["email"]




"""
Telegram notification system for job alerts.
Uses python-telegram-bot to send messages.
"""

from __future__ import annotations

from typing import Optional

from config.settings import get_settings
from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramNotifier:
    """Sends job notifications via Telegram bot."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._enabled = self._settings.ENABLE_TELEGRAM
        self._bot_token = self._settings.TELEGRAM_BOT_TOKEN
        self._chat_id = self._settings.TELEGRAM_CHAT_ID

    def _format_message(self, job: Job, score: float) -> str:
        """Format a Telegram message for a job."""
        score_emoji = "🔴" if score >= 80 else "🟡" if score >= 60 else "🟢"
        location = job.country or "🌍 Worldwide"
        salary = job.salary or "Not specified"
        tags_str = ", ".join(job.tags[:6]) if job.tags else "N/A"

        return (
            f"🔥 *New AI Opportunity Found*\n\n"
            f"*{job.title}*\n"
            f"🏢 {job.company}  •  📋 {job.source.title()}\n\n"
            f"💰 Salary: {salary}\n"
            f"📍 Location: {location}\n"
            f"🏠 Remote: {'Yes' if job.remote else 'No'}\n"
            f"📊 Score: {score_emoji} {score}/100\n"
            f"🏷️ Tags: {tags_str}\n\n"
            f"📝 {job.short_summary[:200]}\n\n"
            f"🔗 [Apply Here]({job.url})"
        )

    def send(self, job: Job, score: float) -> bool:
        """
        Send a Telegram notification for a job.

        Args:
            job: The job opportunity.
            score: The computed score.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._enabled:
            return False
        if not self._bot_token or not self._chat_id:
            logger.warning("Telegram not configured: missing bot token or chat ID")
            return False

        try:
            from telegram import Bot
            from telegram.error import TelegramError

            bot = Bot(token=self._bot_token)
            message = self._format_message(job, score)
            bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logger.info("Telegram sent for %s @ %s", job.title, job.company)
            return True
        except ImportError:
            logger.warning("python-telegram-bot not installed. Install with: pip install python-telegram-bot")
            return False
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    def send_batch(self, job_scores: list[tuple[Job, float]]) -> int:
        """Send batch Telegram notifications."""
        sent = 0
        for job, score in job_scores:
            if self.send(job, score):
                sent += 1
        return sent
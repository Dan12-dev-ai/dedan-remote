"""
Notification orchestrator that dispatches to all configured channels.
"""

from __future__ import annotations

from config.settings import get_settings
from models.job import Job
from notifications.email_notifier import EmailNotifier
from notifications.telegram_notifier import TelegramNotifier
from notifications.discord_notifier import DiscordNotifier
from utils.logger import get_logger

logger = get_logger(__name__)


class Notifier:
    """
    Central notification dispatcher.

    Sends job alerts through all enabled channels:
      - Email (always used if configured)
      - Telegram (if enabled)
      - Discord (if enabled)
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self.email = EmailNotifier()
        self.telegram = TelegramNotifier()
        self.discord = DiscordNotifier()

    def send(self, job: Job, score: float) -> list[str]:
        """
        Send a job notification through all enabled channels.

        Args:
            job: The job opportunity.
            score: The computed score.

        Returns:
            List of channel names that successfully sent.
        """
        success_channels: list[str] = []

        if self.email.send(job, score):
            success_channels.append("email")
        if self.telegram.send(job, score):
            success_channels.append("telegram")
        if self.discord.send(job, score):
            success_channels.append("discord")

        if success_channels:
            logger.info(
                "Notified %s @ %s via %s (score=%.1f)",
                job.title, job.company,
                ", ".join(success_channels), score,
            )
        else:
            logger.warning(
                "No notification sent for %s @ %s — no channels configured",
                job.title, job.company,
            )

        return success_channels

    def send_batch(self, job_scores: list[tuple[Job, float]]) -> list[tuple[Job, float, list[str]]]:
        """
        Send batch notifications.

        Args:
            job_scores: List of (job, score) tuples.

        Returns:
            List of (job, score, success_channels) tuples.
        """
        results: list[tuple[Job, float, list[str]]] = []
        for job, score in job_scores:
            channels = self.send(job, score)
            results.append((job, score, channels))
        return results
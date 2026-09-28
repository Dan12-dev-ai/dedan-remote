"""
Discord notification system for job alerts.
Uses discord-webhook to send messages to Discord channels.
"""

from __future__ import annotations

from config.settings import get_settings
from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class DiscordNotifier:
    """Sends job notifications via Discord webhook."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._enabled = self._settings.ENABLE_DISCORD
        self._webhook_url = self._settings.DISCORD_WEBHOOK_URL

    def _build_embed(self, job: Job, score: float) -> dict[str, object]:
        """Build a Discord embed object for the job."""
        color = 0x00ff00 if score >= 70 else 0xffff00 if score >= 50 else 0xff0000
        location = job.country or "🌍 Worldwide"
        salary = job.salary or "Not specified"
        tags_str = ", ".join(job.tags[:8]) if job.tags else "N/A"

        embed: dict[str, object] = {
            "title": f"🔥 {job.title}",
            "url": job.url,
            "color": color,
            "fields": [
                {"name": "🏢 Company", "value": job.company, "inline": True},
                {"name": "📋 Source", "value": job.source.title(), "inline": True},
                {"name": "💰 Salary", "value": salary, "inline": True},
                {"name": "📍 Location", "value": location, "inline": True},
                {"name": "🏠 Remote", "value": "✅ Yes" if job.remote else "❌ No", "inline": True},
                {"name": "📊 AI Score", "value": f"{score:.0f}/100", "inline": True},
                {"name": "🏷️ Tags", "value": tags_str, "inline": False},
            ],
            "footer": {
                "text": f"DEDAN Remote • {job.discovered_at.strftime('%Y-%m-%d %H:%M UTC')}"
            },
        }
        if job.description:
            embed["description"] = job.description[:300] + "..."
        return embed

    def send(self, job: Job, score: float) -> bool:
        """
        Send a Discord notification for a job.

        Args:
            job: The job opportunity.
            score: The computed score.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._enabled or not self._webhook_url:
            return False

        try:
            from discord_webhook import DiscordWebhook, DiscordEmbed

            webhook = DiscordWebhook(url=self._webhook_url, rate_limit_retry=True)
            embed_data = self._build_embed(job, score)
            embed = DiscordEmbed(
                title=embed_data["title"],  # type: ignore[arg-type]
                url=embed_data["url"],  # type: ignore[arg-type]
                color=embed_data["color"],  # type: ignore[arg-type]
            )
            for field in embed_data["fields"]:  # type: ignore[typeddict-item]
                embed.add_embed_field(**field)  # type: ignore[arg-type]
            embed.set_footer(text=embed_data["footer"]["text"])  # type: ignore[index]
            if "description" in embed_data:
                embed.set_description(embed_data["description"])  # type: ignore[arg-type]

            webhook.add_embed(embed)
            response = webhook.execute()
            if response and response.status_code == 200:
                logger.info("Discord sent for %s @ %s", job.title, job.company)
                return True
            logger.warning("Discord response: %s", response.status_code if response else "none")
            return False
        except ImportError:
            logger.warning(
                "discord-webhook not installed. Install with: pip install discord-webhook"
            )
            return False
        except Exception as exc:
            logger.error("Discord send failed: %s", exc)
            return False

    def send_batch(self, job_scores: list[tuple[Job, float]]) -> int:
        """Send batch Discord notifications."""
        sent = 0
        for job, score in job_scores:
            if self.send(job, score):
                sent += 1
        return sent
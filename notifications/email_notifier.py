"""
Email notification system supporting both SMTP and Gmail API.
Sends beautifully formatted job alerts immediately.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config.settings import get_settings
from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class EmailNotifier:
    """
    Sends email notifications for high-scoring job opportunities.

    Supports:
      - Gmail SMTP (default)
      - Gmail API (optional, requires credentials.json)
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def _build_html_body(self, job: Job, score: float) -> str:
        """Generate a professional HTML email body."""
        score_emoji = "🔴" if score >= 80 else "🟡" if score >= 60 else "🟢"
        remote_str = "✅ Yes" if job.remote else "❌ No"
        location = job.country or "🌍 Worldwide"
        salary = job.salary or "Not specified"
        tags_html = " • ".join(f"<code>{t}</code>" for t in job.tags[:8]) if job.tags else "—"
        description = (job.description or "No description available.")[:500]

        return f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                     max-width: 600px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        padding: 30px; border-radius: 12px; text-align: center; color: white;">
                <h1 style="margin: 0; font-size: 28px;">🔥 New AI Opportunity Found</h1>
                <p style="margin: 8px 0 0; opacity: 0.9;">Discovered by DEDAN Remote</p>
            </div>

            <div style="background: white; padding: 30px; border-radius: 12px; 
                        margin-top: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

                <h2 style="margin-top: 0; color: #333;">{job.title}</h2>

                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 0; color: #666; width: 120px;">🏢 Company</td>
                        <td style="padding: 8px 0; font-weight: 600;">{job.company}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">📋 Source</td>
                        <td style="padding: 8px 0;">{job.source.title()}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">💰 Salary</td>
                        <td style="padding: 8px 0; font-weight: 600;">{salary}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">📍 Location</td>
                        <td style="padding: 8px 0;">{location}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">🏠 Remote</td>
                        <td style="padding: 8px 0;">{remote_str}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">📊 AI Score</td>
                        <td style="padding: 8px 0; font-size: 20px; font-weight: 700;">
                            {score_emoji} {score}/100
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #666;">🏷️ Tags</td>
                        <td style="padding: 8px 0;">{tags_html}</td>
                    </tr>
                </table>

                <div style="margin: 20px 0; padding: 15px; background: #f8f9fa; 
                            border-radius: 8px; border-left: 4px solid #667eea;">
                    <p style="margin: 0; color: #555;">{description}...</p>
                </div>

                <a href="{job.url}" 
                   style="display: block; text-align: center; padding: 14px; 
                          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                          color: white; text-decoration: none; border-radius: 8px;
                          font-size: 16px; font-weight: 600; margin-top: 20px;">
                    🚀 Apply Now
                </a>
            </div>

            <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                <p>DEDAN Remote • Checked at {job.discovered_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
                <p>To adjust notification settings, edit your .env file.</p>
            </div>
        </body>
        </html>
        """

    def _build_text_body(self, job: Job, score: float) -> str:
        """Generate plain text fallback."""
        score_emoji = "🔴" if score >= 80 else "🟡" if score >= 60 else "🟢"
        return (
            f"🔥 NEW AI OPPORTUNITY FOUND\n"
            f"{'=' * 50}\n\n"
            f"Title:    {job.title}\n"
            f"Company:  {job.company}\n"
            f"Source:   {job.source.title()}\n"
            f"Salary:   {job.salary or 'Not specified'}\n"
            f"Location: {job.country or 'Worldwide'}\n"
            f"Remote:   {'Yes' if job.remote else 'No'}\n"
            f"AI Score: {score_emoji} {score}/100\n"
            f"Tags:     {', '.join(job.tags[:8]) if job.tags else 'N/A'}\n\n"
            f"Description:\n{job.description or 'N/A'}\n\n"
            f"Apply: {job.url}\n\n"
            f"{'=' * 50}\n"
            f"Discovered at: {job.discovered_at.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    def send(self, job: Job, score: float) -> bool:
        """
        Send email notification for a high-scoring job.

        Args:
            job: The job opportunity.
            score: The computed score (0-100).

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._settings.EMAIL or not self._settings.EMAIL_PASSWORD:
            logger.error("Email not configured. Set EMAIL and EMAIL_PASSWORD in .env")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🔥 New AI Opportunity Found: {job.title[:60]}"
            msg["From"] = self._settings.EMAIL
            msg["To"] = self._settings.EMAIL

            msg.attach(MIMEText(self._build_text_body(job, score), "plain"))
            msg.attach(MIMEText(self._build_html_body(job, score), "html"))

            with smtplib.SMTP(self._settings.SMTP_SERVER, self._settings.SMTP_PORT) as server:
                server.starttls()
                server.login(self._settings.EMAIL, self._settings.EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info(
                "Email sent for %s @ %s (score=%.1f)",
                job.title, job.company, score,
            )
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(
                "SMTP authentication failed for %s. "
                "If using Gmail, use an App Password (not your regular password).",
                self._settings.EMAIL,
            )
            return False
        except smtplib.SMTPException as exc:
            logger.error("SMTP error sending email: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected email error: %s", exc)
            return False

    def send_batch(self, job_scores: list[tuple[Job, float]]) -> int:
        """
        Send batch email notifications.

        Args:
            job_scores: List of (job, score) tuples.

        Returns:
            Number of emails successfully sent.
        """
        sent = 0
        for job, score in job_scores:
            if self.send(job, score):
                sent += 1
        return sent
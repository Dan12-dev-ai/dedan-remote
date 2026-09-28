"""
Tests for the email notifier.
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

from models.job import job_from_scraper_result
from notifications.email_notifier import EmailNotifier


class TestEmailNotifier:
    """Test email notification functionality."""

    def setup_method(self) -> None:
        self.notifier = EmailNotifier()
        self.job = job_from_scraper_result(
            title="AI Trainer",
            company="TestCorp",
            url="https://example.com/job/1",
            source="test",
            salary="$100/hr",
            tags=["ai", "training"],
        )

    def test_send_success(self) -> None:
        """Test successful email send."""
        with patch("smtplib.SMTP") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance

            result = self.notifier.send(self.job, 85.0)
            assert result is True
            mock_instance.starttls.assert_called_once()
            mock_instance.login.assert_called_once()
            mock_instance.send_message.assert_called_once()

    def test_send_auth_failure(self) -> None:
        """Test SMTP authentication failure."""
        with patch("smtplib.SMTP") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance
            mock_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")

            result = self.notifier.send(self.job, 85.0)
            assert result is False

    def test_send_smtp_error(self) -> None:
        """Test SMTP generic failure."""
        with patch("smtplib.SMTP") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance
            mock_instance.send_message.side_effect = smtplib.SMTPException("Connection refused")

            result = self.notifier.send(self.job, 85.0)
            assert result is False

    def test_send_no_config(self, monkeypatch) -> None:
        """Test send with no email configured."""
        monkeypatch.setenv("EMAIL", "")
        monkeypatch.setenv("EMAIL_PASSWORD", "")

        notifier = EmailNotifier()
        result = notifier.send(self.job, 85.0)
        assert result is False

    def test_build_html_body(self) -> None:
        """Test HTML body generation."""
        body = self.notifier._build_html_body(self.job, 85.0)
        assert "AI Trainer" in body
        assert "TestCorp" in body
        assert "$100/hr" in body
        assert "85" in body and "100" in body
        assert "Apply Now" in body

    def test_build_text_body(self) -> None:
        """Test text body generation."""
        body = self.notifier._build_text_body(self.job, 85.0)
        assert "AI Trainer" in body
        assert "TestCorp" in body
        assert "$100/hr" in body
        assert "85" in body and "100" in body

    def test_send_batch(self) -> None:
        """Test batch sending."""
        with patch.object(self.notifier, "send", return_value=True) as mock_send:
            jobs = [
                (self.job, 85.0),
                (self.job, 90.0),
            ]
            count = self.notifier.send_batch(jobs)
            assert count == 2
            assert mock_send.call_count == 2

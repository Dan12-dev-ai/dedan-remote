"""
Tests for the scraper layer — all platform scrapers, error resilience,
and the auto-discovery registry.

HTTP is fully mocked: no network access ever occurs.
"""

from __future__ import annotations

import pytest

from models.job import Job
from scrapers.alignerr_scraper import AlignerrScraper
from scrapers.appen_scraper import AppenScraper
from scrapers.base_scraper import BaseScraper
from scrapers.clickworker_scraper import ClickworkerScraper
from scrapers.dataannotation_scraper import DataAnnotationScraper
from scrapers.oneforma_scraper import OneFormaScraper
from scrapers.outlier_scraper import OutlierScraper
from scrapers.scraper_registry import ScraperRegistry, get_registry
from scrapers.telus_scraper import TelusScraper
from scrapers.toloka_scraper import TolokaScraper
from scrapers.welocalize_scraper import WelocalizeScraper


# ── HTTP mocks ────────────────────────────────────────────────────────────────

class FakeClient:
    """Minimal stand-in for utils.http_client.HttpClient."""

    def __init__(self, html: str = "", error: Exception | None = None) -> None:
        self.html = html
        self.error = error
        self.calls: list[str] = []

    async def fetch_html(self, url: str, **kwargs: object) -> str:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.html


def attach_client(scraper: BaseScraper, client: FakeClient) -> BaseScraper:
    """Replace a scraper's lazy HTTP client with the fake one."""
    async def _get_client() -> FakeClient:
        return client

    scraper._get_client = _get_client  # type: ignore[method-assign]
    return scraper


# HTML that matches the card selectors used by the platform scrapers.
GENERIC_LISTING_HTML = """
<html><body>
<ul>
  <li class="job-listing">
    <div class="job-card">
      <h3>AI Data Annotator</h3>
      <a href="/jobs/ai-data-annotator">View role</a>
      <span class="salary">$15/hr</span>
      <span class="location">Remote</span>
      <p class="desc">Label images and text for AI training</p>
      <span class="tag">annotation</span>
      <span class="badge">ai</span>
    </div>
  </li>
</ul>
</body></html>
"""

TOLOKA_TASK_HTML = """
<html><body>
  <div class="task-card">
    <h3>Image Classification</h3>
    <a href="/tasks/1234">Open task</a>
    <span class="reward">$0.05 per task</span>
    <p class="hint">Classify images for AI training</p>
    <span class="tag">CV</span>
  </div>
</body></html>
"""


# ── BaseScraper contract ─────────────────────────────────────────────────────

class TestBaseScraperContract:
    """The abstract base class enforces required metadata."""

    def test_subclass_without_name_rejected(self) -> None:
        class Bad(BaseScraper):
            source = "bad"
            base_url = "https://bad.example"

            async def scrape(self) -> list[Job]:
                return []

        with pytest.raises(ValueError, match="name"):
            Bad()

    def test_subclass_without_source_rejected(self) -> None:
        class Bad(BaseScraper):
            name = "Bad"
            base_url = "https://bad.example"

            async def scrape(self) -> list[Job]:
                return []

        with pytest.raises(ValueError, match="source"):
            Bad()

    def test_subclass_without_base_url_rejected(self) -> None:
        class Bad(BaseScraper):
            name = "Bad"
            source = "bad"

            async def scrape(self) -> list[Job]:
                return []

        with pytest.raises(ValueError, match="base_url"):
            Bad()

    def test_repr_contains_name_and_source(self) -> None:
        scraper = OutlierScraper()
        text = repr(scraper)
        assert "Outlier" in text
        assert "outlier" in text


# ── Per-platform parsing ─────────────────────────────────────────────────────

# (scraper class, expected source, expected company)
PLATFORM_CASES = [
    (OutlierScraper, "outlier", "Outlier"),
    (AlignerrScraper, "alignerr", "Alignerr"),
    (OneFormaScraper, "oneforma", "OneForma"),
    (TelusScraper, "telus", "TELUS Digital"),
    (WelocalizeScraper, "welocalize", "Welocalize"),
    (AppenScraper, "appen", "Appen"),
    (DataAnnotationScraper, "dataannotation", "DataAnnotation"),
    (ClickworkerScraper, "clickworker", "Clickworker"),
]


class TestPlatformScrapers:
    """Each implemented scraper parses listings into standard Job objects."""

    @pytest.mark.parametrize("scraper_cls,source,company", PLATFORM_CASES)
    async def test_parse_listing(
        self, scraper_cls: type[BaseScraper], source: str, company: str,
    ) -> None:
        scraper = attach_client(scraper_cls(), FakeClient(GENERIC_LISTING_HTML))
        jobs = await scraper.scrape()

        assert isinstance(jobs, list)
        assert len(jobs) >= 1, f"{source} parsed no jobs from valid HTML"
        for job in jobs:
            assert isinstance(job, Job)
            assert job.source == source
            assert job.company == company
            assert job.url.startswith("https://")
            assert len(job.id) == 16
            assert job.remote is True

    @pytest.mark.parametrize("scraper_cls,source,company", PLATFORM_CASES)
    async def test_network_failure_returns_empty_list(
        self, scraper_cls: type[BaseScraper], source: str, company: str,
    ) -> None:
        """Scrapers never raise — failures degrade to an empty list."""
        scraper = attach_client(
            scraper_cls(), FakeClient(error=ConnectionError("network down")),
        )
        jobs = await scraper.scrape()
        assert jobs == []

    @pytest.mark.parametrize("scraper_cls,source,company", PLATFORM_CASES)
    async def test_empty_html_returns_list(
        self, scraper_cls: type[BaseScraper], source: str, company: str,
    ) -> None:
        scraper = attach_client(scraper_cls(), FakeClient("<html></html>"))
        jobs = await scraper.scrape()
        assert isinstance(jobs, list)
        assert len(jobs) == 0

    async def test_outlier_extracts_salary_and_tags(self) -> None:
        scraper = attach_client(OutlierScraper(), FakeClient(GENERIC_LISTING_HTML))
        jobs = await scraper.scrape()
        outlier = jobs[0]
        assert outlier.salary == "$15/hr"
        assert "annotation" in outlier.tags
        assert outlier.description == "Label images and text for AI training"

    async def test_dataannotation_extracts_pay(self) -> None:
        scraper = attach_client(
            DataAnnotationScraper(), FakeClient(GENERIC_LISTING_HTML),
        )
        jobs = await scraper.scrape()
        assert jobs[0].salary == "$15/hr"

    async def test_telus_deduplicates_titles(self) -> None:
        """TELUS parses anchor links — duplicate titles must collapse."""
        html = """
        <div class="job-card">
          <a href="/careers/1">Data Annotator</a>
          <a href="/careers/1">Data Annotator</a>
          <a href="/careers/2">Search Evaluator</a>
        </div>
        """
        scraper = attach_client(TelusScraper(), FakeClient(html))
        jobs = await scraper.scrape()
        titles = [j.title for j in jobs]
        assert len(titles) == len(set(titles))
        assert "Search Evaluator" in titles

    async def test_clickworker_adds_translation_tag(self) -> None:
        html = """
        <div class="job-card">
          <h3>Translation Task</h3>
          <a href="/jobs/1">Apply</a>
        </div>
        """
        scraper = attach_client(ClickworkerScraper(), FakeClient(html))
        jobs = await scraper.scrape()
        assert any("translation" in j.tags for j in jobs)


# ── Toloka scraper (previously a stub) ───────────────────────────────────────

class TestTolokaScraper:
    """Toloka must behave like every other fully-implemented scraper."""

    async def test_parses_task_cards(self) -> None:
        scraper = attach_client(TolokaScraper(), FakeClient(TOLOKA_TASK_HTML))
        jobs = await scraper.scrape()

        assert len(jobs) == 1
        job = jobs[0]
        assert job.title == "Image Classification"
        assert job.company == "Toloka"
        assert job.source == "toloka"
        assert job.url == "https://toloka.ai/tasks/1234"
        assert job.salary == "$0.05 per task"
        assert job.remote is True
        assert "cv" in job.tags  # tags are normalised to lowercase

    async def test_default_tags_when_none_present(self) -> None:
        html = """
        <div class="task-card">
          <h3>Survey Participation</h3>
          <a href="/tasks/9">Open</a>
        </div>
        """
        scraper = attach_client(TolokaScraper(), FakeClient(html))
        jobs = await scraper.scrape()
        assert jobs[0].tags == ["ai", "data labeling", "crowdsourcing"]

    async def test_falls_back_to_second_catalog_url(self) -> None:
        """If the first catalog URL fails, the next one is tried."""
        class FailingFirstClient(FakeClient):
            async def fetch_html(self, url: str, **kwargs: object) -> str:
                self.calls.append(url)
                if len(self.calls) == 1:
                    raise ConnectionError("primary URL down")
                return TOLOKA_TASK_HTML

        scraper = attach_client(TolokaScraper(), FailingFirstClient())
        jobs = await scraper.scrape()
        assert len(jobs) == 1

    async def test_returns_empty_when_all_urls_fail(self) -> None:
        scraper = attach_client(
            TolokaScraper(), FakeClient(error=TimeoutError("all down")),
        )
        jobs = await scraper.scrape()
        assert jobs == []


# ── Registry auto-discovery ──────────────────────────────────────────────────

class TestScraperRegistry:
    """The registry auto-discovers every scraper in the package."""

    def test_discovers_all_nine_scrapers(self) -> None:
        registry = ScraperRegistry()
        registry.discover_scrapers()
        sources = registry.scraper_sources
        expected = {
            "outlier", "alignerr", "oneforma", "telus", "welocalize",
            "appen", "dataannotation", "clickworker", "toloka",
        }
        assert expected.issubset(set(sources)), (
            f"Missing scrapers: {expected - set(sources)}"
        )

    def test_get_by_source(self) -> None:
        registry = ScraperRegistry()
        scraper = registry.get_by_source("toloka")
        assert scraper is not None
        assert scraper.name == "Toloka"
        assert isinstance(scraper, TolokaScraper)

    def test_get_by_name_normalises_spaces(self) -> None:
        registry = ScraperRegistry()
        scraper = registry.get_by_name("TELUS Digital")
        assert scraper is not None
        assert scraper.source == "telus"

    def test_get_by_unknown_source_returns_none(self) -> None:
        registry = ScraperRegistry()
        assert registry.get_by_source("does-not-exist") is None
        assert registry.get_by_name("does not exist") is None

    def test_get_all_returns_instantiable_scrapers(self) -> None:
        registry = ScraperRegistry()
        scrapers = registry.get_all()
        assert len(scrapers) >= 9
        for scraper in scrapers:
            assert scraper.name
            assert scraper.source
            assert scraper.base_url

    def test_module_singleton_is_reused(self) -> None:
        assert get_registry() is get_registry()

    async def test_registry_scrapers_never_raise_on_fetch_error(self) -> None:
        """Every discovered scraper degrades gracefully on network failure."""
        registry = get_registry()
        for scraper in registry.get_all():
            attach_client(scraper, FakeClient(error=ConnectionError("down")))
            jobs = await scraper.scrape()
            assert jobs == [], f"{scraper.source} raised or leaked jobs"

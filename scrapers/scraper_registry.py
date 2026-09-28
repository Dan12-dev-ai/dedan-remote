"""
Dynamic scraper registry that auto-discovers all scrapers by scanning
the scrapers package and registering any BaseScraper subclass.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Optional

from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class ScraperRegistry:
    """
    Registry that holds all discovered scrapers and provides
    methods to access them by name or source.
    """

    def __init__(self) -> None:
        self._scrapers: dict[str, type[BaseScraper]] = {}
        self._by_source: dict[str, type[BaseScraper]] = {}
        self._loaded = False

    def discover_scrapers(self) -> None:
        """
        Scan the scrapers package for all BaseScraper subclasses
        and register them.
        """
        if self._loaded:
            return

        import scrapers  # noqa: F811
        scrapers_pkg = scrapers

        for importer, modname, ispkg in pkgutil.iter_modules(
            scrapers_pkg.__path__,  # type: ignore[attr-defined]
            scrapers_pkg.__name__ + ".",
        ):
            if modname.endswith("base_scraper") or modname.endswith("scraper_registry"):
                continue

            try:
                module = importlib.import_module(modname)
            except Exception as exc:
                logger.warning("Failed to import module %s: %s", modname, exc)
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseScraper)
                    and obj is not BaseScraper
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()  # validates name/source
                        key = instance.name.lower().replace(" ", "_")
                        self._scrapers[key] = obj
                        self._by_source[instance.source] = obj
                        logger.debug(
                            "Registered scraper: %s (source=%s)", instance.name, instance.source,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Skipping scraper %s (init failed): %s", name, exc,
                        )

        self._loaded = True
        logger.info(
            "Discovered %d scrapers: %s",
            len(self._scrapers),
            list(self._scrapers.keys()),
        )

    def get_all(self) -> list[BaseScraper]:
        """Instantiate and return all registered scrapers."""
        self.discover_scrapers()
        scrapers = []
        for cls in self._scrapers.values():
            try:
                scrapers.append(cls())
            except Exception as exc:
                logger.error("Failed to instantiate scraper %s: %s", cls.__name__, exc)
        return scrapers

    def get_by_name(self, name: str) -> Optional[BaseScraper]:
        """Get a scraper instance by its normalized name."""
        self.discover_scrapers()
        key = name.lower().replace(" ", "_")
        cls = self._scrapers.get(key)
        return cls() if cls else None

    def get_by_source(self, source: str) -> Optional[BaseScraper]:
        """Get a scraper instance by its source identifier."""
        self.discover_scrapers()
        cls = self._by_source.get(source)
        return cls() if cls else None

    @property
    def scraper_names(self) -> list[str]:
        """Return list of registered scraper names."""
        self.discover_scrapers()
        return list(self._scrapers.keys())

    @property
    def scraper_sources(self) -> list[str]:
        """Return list of registered scraper source identifiers."""
        self.discover_scrapers()
        return list(self._by_source.keys())


# Module-level singleton
_registry: Optional[ScraperRegistry] = None


def get_registry() -> ScraperRegistry:
    """Get or create the global scraper registry singleton."""
    global _registry
    if _registry is None:
        _registry = ScraperRegistry()
    return _registry
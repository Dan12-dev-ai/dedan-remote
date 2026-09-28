#!/usr/bin/env python3
"""
AI Opportunity Discovery Automation System.

Automatically discovers new online earning opportunities and sends
immediate notifications via Email, Telegram, or Discord.

Usage:
    python main.py              # Start in scheduler mode (runs forever)
    python main.py --once       # Run a single discovery cycle and exit
    python main.py --stats      # Show database stats and exit
    python main.py --setup      # Initialize database tables and exit

Environment variables are loaded from .env (see .env.example).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, NoReturn, Optional

from config.settings import get_settings
from database.database import Database
from agents.scheduler_agent import SchedulerAgent
from utils.logger import get_logger, setup_logger

logger = setup_logger("ai_opportunity_finder")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="AI Opportunity Discovery Automation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single discovery cycle and exit",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics and exit",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Initialize database tables and exit",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=".env",
        help="Path to .env file (default: .env)",
    )
    parser.add_argument(
        "--aeos",
        action="store_true",
        help="Enable AE-OS enterprise multi-agent backbone",
    )
    return parser.parse_args()


# ── AE-OS Orchestrator holder (forward reference) ─────────────────────────
_aeos_orchestrator: Any = None


async def run_once() -> dict[str, object]:
    """Run a single discovery cycle."""
    logger.info("Running single discovery cycle...")
    scheduler = SchedulerAgent()
    result = await scheduler.run_once()
    logger.info("Cycle complete: %s", result)
    return result


async def show_stats() -> None:
    """Display database statistics."""
    scheduler = SchedulerAgent()
    stats = scheduler.stats()
    print()
    print("=" * 50)
    print("📊 AI Opportunity Finder — Database Stats")
    print("=" * 50)
    print(f"  Total jobs discovered:   {stats.get('total_jobs', 0)}")
    print(f"  Jobs notified:           {stats.get('notified_jobs', 0)}")
    print(f"  Jobs pending (unread):   {stats.get('pending_jobs', 0)}")
    print(f"  Total execution cycles:  {stats.get('total_executions', 0)}")
    print("=" * 50)
    print()


def setup_database() -> None:
    """Initialize the database schema."""
    db = Database()
    db.create_tables()
    print("[OK] Database tables created at:", db._db_path)
    db.close()


async def run_scheduler(enable_aeos: bool = False) -> NoReturn:
    """Start the scheduler and run forever."""
    logger.info("Starting AI Opportunity Discovery System...")
    logger.info("Press Ctrl+C to stop")

    # ── Start AE-OS Enterprise Backbone (if enabled) ──────────────────
    global _aeos_orchestrator
    if enable_aeos:
        from core.orchestrator import AEOSOrchestrator
        # Build PostgreSQL DSN if not provided directly
        if settings.AEOS_POSTGRES_DSN:
            postgres_dsn = settings.AEOS_POSTGRES_DSN
        else:
            postgres_dsn = f"postgresql://{settings.AEOS_POSTGRES_USER}:{settings.AEOS_POSTGRES_PASSWORD}@{settings.AEOS_POSTGRES_HOST}:{settings.AEOS_POSTGRES_PORT}/{settings.AEOS_POSTGRES_DATABASE}"
        _aeos_orchestrator = AEOSOrchestrator(
            postgres_dsn=postgres_dsn,
            redis_url=settings.AEOS_REDIS_URL,
            neo4j_uri=settings.AEOS_NEO4J_URI,
            qdrant_host=settings.AEOS_QDRANT_HOST,
            qdrant_port=settings.AEOS_QDRANT_PORT,
            metrics_port=settings.AEOS_METRICS_PORT,
            idempotency_secret=settings.AEOS_IDEMPOTENCY_SECRET,
            treasury_max=settings.AEOS_TREASURY_MAX,
        )
        await _aeos_orchestrator.start()
        logger.info("AE-OS Enterprise Backbone active")

    scheduler = SchedulerAgent()
    scheduler.start()

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        if _aeos_orchestrator:
            await _aeos_orchestrator.shutdown()
        await scheduler.shutdown()

    sys.exit(0)  # never reached normally


async def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Load settings early to validate .env
    settings = get_settings()
    logger.info("AI Opportunity Finder v1.0.0")
    logger.info("Log level: %s", settings.LOG_LEVEL)

    # Determine if AE-OS is enabled
    enable_aeos = args.aeos or settings.AEOS_ENABLED
    if enable_aeos:
        logger.info("AE-OS Enterprise Multi-Agent Backbone: ENABLED")

    if args.setup:
        setup_database()
        return

    if args.stats:
        await show_stats()
        return

    if args.once:
        result = await run_once()
        # Exit with code 1 if errors occurred
        if result.get("errors", 0) and isinstance(result["errors"], int) and result["errors"] > 0:
            sys.exit(1)
        return

    # Default: run scheduler forever with optional AE-OS backbone
    await run_scheduler(enable_aeos=enable_aeos)


if __name__ == "__main__":
    asyncio.run(main())

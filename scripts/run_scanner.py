"""Stand-alone scanner loop (uses real exchanges if creds provided, else mocks)."""
import asyncio

from app.common.logging import configure_logging, get_logger
from app.config.settings import get_settings
from app.runtime.app_bootstrap import bootstrap


async def main():
    s = get_settings()
    configure_logging(s.log_level)
    log = get_logger("scripts.run_scanner")
    log.info("starting", mode=s.mode)
    c = await bootstrap(s)
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())

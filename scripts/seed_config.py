"""Quick sanity check: apply schema and print config."""

import asyncio

from app.config.settings import get_settings


async def main():
    s = get_settings()
    print("mode:", s.mode)
    print("enabled_symbols:", s.enabled_symbol_list)
    print("min_net_edge_bps:", s.min_net_edge_bps)
    print("max_notional_per_trade:", s.max_notional_per_trade)


if __name__ == "__main__":
    asyncio.run(main())

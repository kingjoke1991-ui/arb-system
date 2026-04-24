"""Strategy registry.

A single place that lists every arbitrage strategy the system knows about,
plus an implementation-status flag and a human-readable description.

Only strategies with status=READY can actually be enabled at runtime; the
others are surfaced in the UI as "coming in a future release" with their
own tooltip so operators can see the roadmap but cannot silently enable
something that doesn't execute.

Keep the catalog definition in a single place (this file). Anything that
needs strategy metadata (UI, API, scanner plumbing, metrics) reads from
here.
"""

from __future__ import annotations

from dataclasses import dataclass


class StrategyStatus:
    READY = "ready"  # implemented and safe to enable
    DETECT_ONLY = "detect_only"  # scanner runs but execution is disabled
    PLANNED = "planned"  # not implemented; enabling is rejected


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    name_zh: str
    name_en: str
    status: str
    short_zh: str  # one-line gist for the card header
    description_zh: str  # multi-line explanation of the logic
    caveat_zh: str  # required conditions / risks operator should know


STRATEGIES: list[StrategyMeta] = [
    StrategyMeta(
        id="cross_exchange_spot",
        name_zh="跨交易所现货套利",
        name_en="Cross-Exchange Spot Arbitrage",
        status=StrategyStatus.READY,
        short_zh="两家交易所同币对价差套利（当前系统的主策略）",
        description_zh=(
            "同一交易对在交易所 A 和 B 的价差为正且大于手续费+滑点时，"
            "在低价所以限价买入、在高价所以限价卖出，两腿同时下单、同时完成。\n"
            "系统会在收到盘口快照后计算净 edge（扣除双边手续费、VWAP 滑点、保护偏移），"
            "只有超过 min_net_edge_bps 且预期净利润 ≥ min_profit_quote 才会放行。\n"
            "执行模式：两腿 asyncio.gather 并发提交 → 任一腿失败立即走 rollback/repair。"
        ),
        caveat_zh=(
            "需要两家交易所都持有对应基础资产与计价资产（BTC + USDT）。"
            "本地延迟越低越好——东京/新加坡机房对 Binance/OKX 往返 ≤50ms。"
        ),
    ),
    StrategyMeta(
        id="triangular_same_exchange",
        name_zh="同所三角套利",
        name_en="Triangular Arbitrage (Same Exchange)",
        status=StrategyStatus.DETECT_ONLY,
        short_zh="单交易所 A/B × B/C × C/A 三腿循环价差",
        description_zh=(
            "在同一家交易所内寻找三腿循环定价错配：例如 USDT → BTC → ETH → USDT，"
            "三笔交易的乘积 >1 即存在理论套利空间。\n"
            "优点：无需跨所余额与提币，三腿在同一撮合引擎内，失败回滚更可控。\n"
            "缺点：三腿意味着 3× 的手续费和滑点，净 edge 要求更高；同时"
            "单一交易所的定价往往已被做市商抹平，机会窗口极短。"
        ),
        caveat_zh=(
            "当前版本：扫描器开启后会持续检测并将机会写入 /opportunities 表（带 strategy=triangular 标签），"
            "但执行层暂未接通（需要 3 腿状态机 + 单边失败处理，V1.1 引入）。"
        ),
    ),
    StrategyMeta(
        id="cross_exchange_market",
        name_zh="跨所快速市价搬砖",
        name_en="Cross-Exchange Market-Order Arbitrage",
        status=StrategyStatus.PLANNED,
        short_zh="cross_exchange_spot 的变体：两腿都用市价单抢成交",
        description_zh=(
            "与主策略逻辑一致，但下单类型强制 market（而非 ioc_limit）。"
            "用于极短套利窗口（数百毫秒级）——不接受部分成交，宁可多付滑点也要两腿都 FILLED。\n"
            "风险更高：市价单可能深吃盘口、实际成本超出预估 slippage。"
        ),
        caveat_zh="启用前建议先用 paper-trade 验证滑点模型足够保守。当前版本未启用。",
    ),
    StrategyMeta(
        id="funding_rate_spot_perp",
        name_zh="资金费率套利",
        name_en="Funding Rate Arbitrage (Spot × Perpetual)",
        status=StrategyStatus.PLANNED,
        short_zh="现货多头 + 永续合约空头（反之亦然）赚资金费",
        description_zh=(
            "当永续合约资金费率显著为正（多头支付空头）时，做空永续 + 等额买入现货，"
            "市场中性，每 8 小时收取一次资金费。反之资金费为负时操作反转。\n"
            "年化收益通常 10-30%，波动较低，是加密量化的基础策略之一。"
        ),
        caveat_zh=(
            "需要衍生品（perp）adapter，当前系统只接现货；资金费结算周期内若现货/合约基差"
            "扩大会出现浮亏。V2 规划中。"
        ),
    ),
    StrategyMeta(
        id="stat_arb_pair",
        name_zh="统计配对套利",
        name_en="Statistical Pair Arbitrage",
        status=StrategyStatus.PLANNED,
        short_zh="基于协整关系的 z-score 均值回归策略",
        description_zh=(
            "对历史上高度相关（协整）的两个币对（如 BTC/USDT 与 ETH/USDT）建模其价差，"
            "当价差偏离均值超过阈值（典型 2σ）时反向建仓，等回归时平仓。\n"
            "不需要跨所价差，在单一交易所即可运行，方向性风险低。"
        ),
        caveat_zh=(
            "需要历史数据存储 + rolling 回归 + 协整检验（Engle-Granger / Johansen）。"
            "当前系统没有历史行情持久化管线。V2 规划中。"
        ),
    ),
]


_BY_ID = {s.id: s for s in STRATEGIES}


def get(strategy_id: str) -> StrategyMeta | None:
    return _BY_ID.get(strategy_id)


def all_strategies() -> list[StrategyMeta]:
    return list(STRATEGIES)


def is_ready(strategy_id: str) -> bool:
    m = get(strategy_id)
    return m is not None and m.status == StrategyStatus.READY


def is_detect_capable(strategy_id: str) -> bool:
    m = get(strategy_id)
    return m is not None and m.status in (StrategyStatus.READY, StrategyStatus.DETECT_ONLY)

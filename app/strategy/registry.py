"""Strategy registry.

A single place that lists every arbitrage strategy the system knows about,
its implementation status, a human-readable description, AND the strict
account-side requirements + simulation notes so the operator can see at a
glance exactly what preconditions a strategy needs.

Keep the catalog definition in a single place (this file). Anything that
needs strategy metadata (UI, API, scanner plumbing, metrics) reads from
here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class StrategyStatus:
    READY = "ready"  # implemented and safe to enable
    DETECT_ONLY = "detect_only"  # scanner runs but execution is disabled
    PLANNED = "planned"  # not implemented; enabling is rejected


@dataclass(frozen=True)
class AccountRequirement:
    """One row in the account-requirements block.

    Example:
      AccountRequirement(
        exchange="Binance",
        account_type="现货 Spot",
        required_assets=["USDT", "BTC", "ETH", "SOL"],
        min_balance_hint="每个资产建议 ≥ 2× max_notional_per_trade",
        permissions="仅交易（禁止提币）",
        ip_whitelist_required=True,
      )
    """

    exchange: str
    account_type: str
    required_assets: list[str]
    min_balance_hint: str = ""
    permissions: str = "仅交易（禁止提币）"
    ip_whitelist_required: bool = True


@dataclass(frozen=True)
class SimulationNote:
    """How each non-live mode simulates this strategy."""

    dry_run: str  # what happens in dry-run (no exchange calls)
    paper_trade: str  # what happens in paper-trade (simulated fills)
    not_simulated: list[str] = field(default_factory=list)  # things the simulator explicitly does NOT model


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    name_zh: str
    name_en: str
    status: str
    short_zh: str  # one-line gist
    description_zh: str  # multi-line explanation of the logic
    caveat_zh: str  # operator-level risks / preconditions
    accounts: list[AccountRequirement]  # strict requirements
    simulation: SimulationNote  # strict simulation description
    # How many exchanges the operator must pick before the strategy can run.
    # (min, max) inclusive. For cross-exchange strategies this is (2, 2+).
    # For single-exchange strategies (1, 1). PLANNED strategies can leave
    # this at (0, 0) — they are ungated here because registry-status rejects
    # the enable call before exchange validation runs anyway.
    min_exchanges: int = 1
    max_exchanges: int = 1


STRATEGIES: list[StrategyMeta] = [
    StrategyMeta(
        id="cross_exchange_spot",
        min_exchanges=2,
        max_exchanges=10,  # open-ended for future multi-venue routing
        name_zh="跨交易所现货套利",
        name_en="Cross-Exchange Spot Arbitrage",
        status=StrategyStatus.READY,
        short_zh="多家交易所同币对价差套利（系统主策略，当前支持 9 家现货）",
        description_zh=(
            "同一交易对在任意两家已绑定交易所间存在正价差且大于手续费+滑点时，"
            "在低价所限价买入、在高价所限价卖出，两腿同时下单、同时完成。\n"
            "扫描矩阵：勾选 N 家参与后，自动做 C(N,2) 两两配对扫描（例如勾 4 家 → 12 个有向对）。\n"
            "净 edge = 毛价差 − 双边手续费 − VWAP 滑点 − 安全保护偏移；"
            "只有超过 min_net_edge_bps 且预期净利润 ≥ min_profit_quote 才会放行。\n"
            "执行路径二选一：\n"
            "  · 双吃单（默认）：两腿 asyncio.gather 并发提交 IOC 限价吃单；确定性高、付 2× taker 费。\n"
            "  · 挂单-吃单（opt-in）：低价所挂 maker、成交后对侧立即 taker 对冲；费用减半但增加超时/漂移/单边风险。\n"
            "任一腿失败都会走 rollback/repair 反向回撤。"
        ),
        caveat_zh=(
            "本地延迟越低越好。主流币（BTC/ETH）价差常年 < 手续费，建议同时勾选小币种（SHIB/PEPE/DOGE 等）。"
            "需要所选每家都有对应基础资产与 USDT 的底仓，否则对应方向会被余额不足拒绝。"
        ),
        accounts=[
            AccountRequirement(
                exchange="Binance",
                account_type="现货 Spot",
                required_assets=["USDT", "BTC", "ETH", "SOL"],
                min_balance_hint=(
                    "每个基础资产与 USDT 均需 ≥ 2× max_notional_per_trade（default 100+ USDT），"
                    "否则某一方向的机会会被余额不足拒绝。"
                ),
                permissions="仅交易 + 读取（禁提币、禁合约、禁 Margin）",
                ip_whitelist_required=True,
            ),
            AccountRequirement(
                exchange="OKX",
                account_type="现货 Spot（Unified 账户下的现货子账户即可）",
                required_assets=["USDT", "BTC", "ETH", "SOL"],
                min_balance_hint="同 Binance 侧",
                permissions="Trade 权限开启；Withdraw 禁用；Passphrase 必须配置",
                ip_whitelist_required=True,
            ),
        ],
        simulation=SimulationNote(
            dry_run=(
                "完整走扫描 → 风控 → 执行规划；OrderRouter 在最后一步短路不调用 "
                "create_order；余额不变动。用于验证信号与风控逻辑。"
            ),
            paper_trade=(
                "用本地 OrderBookManager 的最新盘口做 VWAP 撮合：买单吃卖档、"
                "卖单吃买档，按下单量逐档消耗后得到均价；成交后调用 "
                "BalanceManager.adjust_virtual() 真实扣减虚拟余额（USDT/基础币）。"
                "部分成交概率按 PaperFillEngine 配置模拟。"
            ),
            not_simulated=[
                "maker 返佣与 taker 手续费的 tier 差异（统一按 settings 的 fee_bps 计）",
                "交易所级速率限制 / 撮合延迟（本地即时返回）",
                "极端行情下的 slippage 尾部风险",
                "交易所下单队列位置与公平性",
            ],
        ),
    ),
    StrategyMeta(
        id="triangular_same_exchange",
        min_exchanges=1,
        max_exchanges=1,
        name_zh="同所三角套利",
        name_en="Triangular Arbitrage (Same Exchange)",
        status=StrategyStatus.READY,
        short_zh="单交易所 A/B × B/C × C/A 三腿循环价差",
        description_zh=(
            "在同一家已绑定交易所内寻找三腿循环定价错配：例如 USDT → BTC → ETH → USDT，"
            "三笔交易的乘积 > 1（扣除 3× 手续费后）即存在理论套利空间。\n"
            "优点：无需跨所余额与提币，三腿在同一撮合引擎内原子性更高，失败回滚可控。\n"
            "缺点：三腿意味着 3× 的手续费和滑点，净 edge 要求更高；头部所的单一盘面定价通常已被做市商抹平，机会窗口极短。\n"
            "支持全部 9 家现货所中的任意一家——每家的三角路径自动由该所上架的交易对动态推导。"
        ),
        caveat_zh=(
            "V2 已接通：paper-trade 模式下每条入库机会自动 3 腿顺序撮合 + 任一腿失败则 LIFO "
            "反向回撤；live 模式走真实 ccxt create_order + fetchOrder 轮询到终态，"
            "部分成交时后一腿数量自动按前一腿实际成交缩放。"
        ),
        accounts=[
            AccountRequirement(
                exchange="Binance 或 OKX（任选其一即可）",
                account_type="现货 Spot",
                required_assets=["USDT", "BTC", "ETH"],
                min_balance_hint=(
                    "至少持有 USDT + 2 种基础币。循环起点资产（通常 USDT）建议 ≥ 3× max_notional_per_trade，"
                    "中间资产由三腿自动周转，但为避免首腿失败导致卡死，中间币也建议保留少量底仓。"
                ),
                permissions="仅交易",
                ip_whitelist_required=True,
            ),
        ],
        simulation=SimulationNote(
            dry_run=("scanner 会计算 A/B/C 三腿循环乘积，机会入库但执行侧短路。"),
            paper_trade=(
                "每条入库机会触发 TriangularExecutor.execute：3 腿依次 PaperFillEngine.simulate，"
                "腿 N 部分成交时腿 N+1 数量自动按实际 filled 缩放；任一腿 CANCELLED/REJECTED "
                "则 LIFO 反向回撤前面腿。执行历史写入 /opportunities/triangular/executions。"
            ),
            not_simulated=[
                "三腿并发 vs 顺序的成交概率差异",
                "单腿失败后撤回前腿的滑点",
                "交易所对短时间高频反向单的风控（部分交易所会触发异常登出）",
            ],
        ),
    ),
    StrategyMeta(
        id="cross_exchange_market",
        min_exchanges=2,
        max_exchanges=10,
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
        accounts=[
            AccountRequirement(
                exchange="Binance + OKX",
                account_type="现货 Spot",
                required_assets=["USDT", "BTC", "ETH", "SOL"],
                min_balance_hint="同 cross_exchange_spot，但建议倍数更高（市价单吃单深度不可预测）",
                permissions="仅交易",
                ip_whitelist_required=True,
            ),
        ],
        simulation=SimulationNote(
            dry_run="扫描 + 风控走完，不下单。",
            paper_trade=(
                "复用现有 PaperFillEngine，将 order_type 强制 market，按当前盘口直接"
                "从顶档起吃直到 amount 全部成交（无剩余），不会产生部分成交。"
            ),
            not_simulated=[
                "市价单在深度薄的币种上可能产生 10× 以上的额外滑点",
                "交易所 self-match 保护触发导致部分腿被拒",
            ],
        ),
    ),
    StrategyMeta(
        id="funding_rate_spot_perp",
        min_exchanges=1,
        max_exchanges=1,
        name_zh="资金费率套利",
        name_en="Funding Rate Arbitrage (Spot × Perpetual)",
        status=StrategyStatus.READY,
        short_zh="现货多头 + 永续合约空头（反之亦然）赚资金费",
        description_zh=(
            "当永续合约资金费率显著为正（多头支付空头）时，做空永续 + 等额买入现货，"
            "市场中性，每 8 小时收取一次资金费。资金费为负时操作反转（做多永续 + 做空现货）。\n"
            "年化收益通常 10-30%，波动较低，是加密量化的基础策略之一。\n"
            "扫描器调用 ccxt fetch_funding_rate 读取实时资金费率与下次结算时间，"
            "按 funding_rate_min_apr_bps 过滤（默认 2% APR）。paper-trade 下两腿走本地"
            "VWAP 撮合；live 模式下现货腿走 spot adapter、永续腿走 PerpAdapter，"
            "asyncio.gather 并发下单并在部分失败时反向回撤幸存腿。\n"
            "支持永续合约的所（7 家）：binance/okx/bybit/gate/kucoin/bitget/htx；"
            "Kraken、Coinbase 仅提供现货，本策略不适用。"
        ),
        caveat_zh=(
            "V2 已接入 PerpAdapter + FundingExecutor，但仅做开仓，不做定时平仓（下次资金费"
            "结算后手动或通过后续 maintenance loop 平仓）。保证金/强平仍由交易所风控兜底。"
        ),
        accounts=[
            AccountRequirement(
                exchange="Binance 或 OKX",
                account_type="现货 Spot",
                required_assets=["USDT", "BTC/ETH/SOL（作为现货腿持仓）"],
                min_balance_hint="约等于永续空单名义价值 + 手续费储备",
                permissions="仅交易",
                ip_whitelist_required=True,
            ),
            AccountRequirement(
                exchange="Binance（USDⓈ-M Futures）或 OKX（永续合约）",
                account_type="USDT 本位永续合约账户",
                required_assets=["USDT（作为保证金）"],
                min_balance_hint=(
                    "USDT 保证金 ≥ 2× 名义价值 / 杠杆倍数；建议用 3× 杠杆以下，避免基差扩大触发强平。"
                ),
                permissions="Futures Trade 权限（Binance 需单独开通 Futures 子账户；OKX 用 Unified）",
                ip_whitelist_required=True,
            ),
        ],
        simulation=SimulationNote(
            dry_run=(
                "扫描器每 funding_rate_poll_interval_sec（默认 300 秒）读一次 "
                "ccxt.binance/okx 的 fetch_funding_rate；命中条目入环形缓冲。不下单。"
            ),
            paper_trade=(
                "扫描到机会后立即由 FundingExecutor 并发下两条 paper 单：现货腿走"
                "PaperFillEngine 本地 VWAP 撮合 + 扣虚拟 USDT/基础币；永续腿在 fetch "
                "perp orderbook 后同样本地撮合，扣 `{exchange}-perp` 虚拟余额。"
                "不触碰真实交易所。"
            ),
            not_simulated=[
                "资金费结算时的实际现金流（需要 PerpAdapter）",
                "永续合约的 mark price 波动与强平逻辑",
                "基差扩大导致的保证金占用变化",
                "spot 与 perp 之间的数量对齐（合约乘数、最小张数）",
            ],
        ),
    ),
    StrategyMeta(
        id="stat_arb_pair",
        min_exchanges=1,
        max_exchanges=1,
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
            "当前系统没有历史行情持久化管线。"
        ),
        accounts=[
            AccountRequirement(
                exchange="Binance 或 OKX",
                account_type="现货 Spot",
                required_assets=["USDT", "两种配对币（如 BTC 与 ETH）"],
                min_balance_hint="两侧底仓均需 ≥ 2× max_notional_per_trade",
                permissions="仅交易",
                ip_whitelist_required=True,
            ),
        ],
        simulation=SimulationNote(
            dry_run="未实现。",
            paper_trade=(
                "未实现。需要先加历史 K 线数据管线（OHLCV 入库 + 定时回写）"
                "+ rolling z-score 计算器 + 双边持仓管理。"
            ),
            not_simulated=[
                "协整关系破裂（如某币发生分叉或监管事件）带来的尾部风险",
                "长期持仓的资金机会成本",
            ],
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

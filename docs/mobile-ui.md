# 移动端优先操作面板（Mobile-First UI）

> 入口路径：`/m`（桌面版仍在 `/`，互不影响）。
> 本设计严格按照如下约束输出：手机专属（320–430px）· 单列 · 卡片式 · 顶部极简 · 指标默认收起 · 拇指点击区 ≥44px · 仅点击/折叠/滑动 · 低密度。

---

## 1. 设计约束映射

| 约束 | 实现方式 |
|---|---|
| 仅适配 320–430px 单列 | `body { max-width: 430px; margin: 0 auto }`；所有容器 100% 宽度，无多列栅格 |
| 卡片式垂直结构 | `<section class="card">`，每卡 = 一个核心功能（机会/对冲/策略/配置/运维） |
| 顶部极简 | `.topbar` 只保留：标题 + 模式色点（含紧停/熔断聚合色） |
| 性能指标折叠 | `.dashboard-panel[data-open="false"]` 默认收起；展开后显示：各交易所延迟 · 滑点 · 进行中对冲 · 24h 盈亏 · 扫描状态 |
| 按钮 ≥44px，关键操作固定底部 | `.bottom-bar button { min-height: 48px }`，`position: fixed; bottom: 0` |
| 禁止 hover | 全部 `:active` / `[aria-expanded]` 状态；移除任何 `:hover` 规则 |
| 信息密度控制 | 每卡仅展示 1 个主指标 + 1 行副信息；其余点击进入 `.sheet`（全屏 slide-up 详情） |
| 快速决策 | 机会卡采用"即时可判断"信息：净 bps、方向、金额；其他字段折叠 |

---

## 2. 组件树（HTML/CSS）

```
body[data-theme="dark"]
├── header.topbar                        ← 固定顶部
│   ├── h1.title               "套利系统"
│   └── button.status-chip     → 聚合状态（运行中 / 暂停 / 紧停 / 熔断）
│
├── main.scroll                          ← 可滚动卡片流
│   │
│   ├── section.card.card--dashboard     ← 仪表盘面板（可折叠，默认收起）
│   │   ├── button.card-head[aria-controls="dash-body" aria-expanded="false"]
│   │   │   ├── span.card-title  "仪表盘"
│   │   │   ├── span.card-summary "延迟 · 滑点 · 盈亏"   ← 收起态副信息
│   │   │   └── span.caret "▾"
│   │   └── div#dash-body.card-body[hidden]
│   │       ├── .metric-row   (延迟)   ← 每交易所一行 chip
│   │       ├── .metric-row   (滑点)
│   │       ├── .metric-row   (24h 盈亏)
│   │       ├── .metric-row   (进行中对冲)
│   │       └── .metric-row   (扫描状态)
│   │
│   ├── section.card.card--opps          ← 当前机会（最多 3 条）
│   │   ├── .card-head "⚡ 当前机会"
│   │   └── ul.opps-list
│   │       └── li.opp-item(点击进入详情 sheet)
│   │           ├── .opp-main   "BTC/USDT  +18 bps"
│   │           └── .opp-sub    "binance → okx · 5,000 USDT"
│   │
│   ├── section.card.card--hedges        ← 进行中对冲
│   │   └── ul.hedge-list → .hedge-item
│   │
│   ├── section.card.card--strategies    ← 策略开关（可折叠）
│   │   └── ul.strategy-list → .strategy-row(开关)
│   │
│   ├── section.card.card--config        ← 运行时参数（点击进入详情页）
│   └── section.card.card--ops           ← 运维（账实核对 / 冷却）
│
├── nav.bottom-bar                       ← 固定底部操作栏
│   ├── button#btn-mode      "模式：仅扫描"   ← 循环切换
│   ├── button#btn-pause     "暂停"           ← 切换
│   └── button#btn-kill      "紧停"           ← 切换 (红色)
│
└── aside.sheet[data-open="false"]       ← 全屏详情页（slide-up）
    ├── header.sheet-head (返回 ×)
    └── div.sheet-body   (按需注入：机会详情 / 对冲详情 / 完整配置表单)
```

### CSS 关键规则

```css
:root { --tap-min: 48px; --pad: 16px; --radius: 12px; }

body {
  max-width: 430px; margin: 0 auto;
  padding-bottom: calc(72px + env(safe-area-inset-bottom));
  background: #0a0f1c; color: #e8eefb;
  font: 15px/1.5 system-ui, -apple-system, "PingFang SC", sans-serif;
  -webkit-tap-highlight-color: transparent;
}

.topbar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px var(--pad);
  background: #0e1627;
  border-bottom: 1px solid #1a2540;
}

.card {
  margin: 12px var(--pad); padding: 0;
  background: #111a2f; border: 1px solid #1d2a49;
  border-radius: var(--radius);
}
.card-head {
  width: 100%; min-height: var(--tap-min);
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  background: none; color: inherit; border: 0;
  font-size: 15px; font-weight: 600; text-align: left;
}
.card-head[aria-expanded="true"] .caret { transform: rotate(180deg); }
.card-body { padding: 0 16px 16px; }

.bottom-bar {
  position: fixed; left: 50%; transform: translateX(-50%);
  bottom: 0; width: 100%; max-width: 430px;
  display: grid; grid-template-columns: 1fr 1fr 1fr;
  gap: 1px; padding-bottom: env(safe-area-inset-bottom);
  background: #1a2540; border-top: 1px solid #243158;
}
.bottom-bar button {
  min-height: var(--tap-min);
  background: #111a2f; color: #e8eefb;
  border: 0; font-size: 13px; font-weight: 600;
}
.bottom-bar button[data-danger="true"] { color: #ff6b78; }
.bottom-bar button:active { background: #182445; }

/* 禁止所有 hover：全局不要 :hover 规则 */

.sheet {
  position: fixed; inset: 0; z-index: 50;
  transform: translateY(100%); transition: transform .25s ease-out;
  background: #0a0f1c;
}
.sheet[data-open="true"] { transform: translateY(0); }
```

### 交互规范

| 行为 | 交互方式 | 备注 |
|---|---|---|
| 展开 / 折叠卡片 | 点击 `.card-head` | 切换 `aria-expanded` |
| 查看机会/对冲详情 | 点击列表项 | 打开 `.sheet`（slide-up） |
| 关闭详情页 | 点击 × 或 下滑手势 | `touchmove` 下滑 >80px 关闭 |
| 切换模式 | 点击底部"模式"按钮 | 循环：仅扫描 → 模拟 → 实盘 → 仅扫描 |
| 暂停 / 紧停 | 点击底部对应按钮 | 红色紧停需二次确认（`confirm()` 或 long-press） |
| 保存配置 | 详情页底部"保存"按钮 | 固定在 sheet 底部 |

> ❌ 完全不使用 `:hover` — 移动端点击后 hover 状态会"粘住"，已替换为 `:active` 反馈。

---

## 3. Flutter 组件结构参考（等价映射）

```dart
Scaffold(
  appBar: _MobileTopBar(),                 // 标题 + 状态 chip
  body: ListView(
    padding: EdgeInsets.only(bottom: 96),
    children: [
      DashboardCard(defaultExpanded: false),   // ExpansionTile
      OpportunitiesCard(max: 3),
      HedgesCard(),
      StrategiesCard(),
      ConfigCard(onTap: () => _pushSheet(ConfigDetailPage())),
      OpsCard(),
    ],
  ),
  bottomNavigationBar: BottomActionBar(
    children: [
      _ActionButton(label: '模式', onTap: _cycleMode),
      _ActionButton(label: '暂停', onTap: _togglePause),
      _ActionButton(label: '紧停', danger: true, onTap: _confirmKill),
    ],
  ),
);

// 详情页：showModalBottomSheet(isScrollControlled: true, useSafeArea: true)
// → 达到与本设计 .sheet 等价的 slide-up 全屏效果
```

要点：
- `ExpansionTile` 的 `initiallyExpanded: false` 对应"仪表盘默认收起"
- `BottomAppBar` + `SizedBox(height: 48)` 对应 ≥44px 点击区
- `Material(inkwell: false)` + `GestureDetector` 代替 hover 反馈

---

## 4. 信息裁剪决策（为什么隐藏了什么）

| 桌面版有 | 移动端处理 | 理由 |
|---|---|---|
| 顶部 4 枚徽章（模式/暂停/紧停/熔断） | 合并为 1 个 `status-chip` + 底部操作栏 | 手机顶部空间有限；写操作集中到底部拇指区 |
| 顶部 `.latency-widget`（各交易所 RTT） | 移入仪表盘面板 · 默认收起 | 非高频决策信息；满足用户明确要求"延迟放到仪表盘面板" |
| 管理员令牌输入框 | 移入"运维"卡 | 偶发输入，非主流程 |
| 回显 7 个子 tab（机会/对冲/订单/行情/余额/报表/事件） | 主页只保留 机会 + 对冲；其余入口放 `.card--ops` 的列表中 | 减少认知负担，符合"快速决策场景" |
| 完整配置表单（10+ 字段） | 折叠为 `.card--config` 单卡，点击进入详情 sheet | 单页信息密度控制 |

---

## 5. 开发对接

- 后端数据源保持不变，全部复用现有 API：`/control`, `/health/latency`, `/opportunities/cross`, `/hedges/active`, `/config`, `/strategies` 等。
- 资源加载：`<link rel="stylesheet" href="/ui/mobile.css">` · `<script src="/ui/mobile.js" defer>`
- Viewport：`<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`（配合 `env(safe-area-inset-*)` 适配刘海屏）
- 建议在 `app/main.py` 中挂载 `/m` 路由返回 `mobile.html`（已在本 PR 中实现）。

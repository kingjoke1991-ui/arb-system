// 套利系统 · 操作面板 —— 原生 JS，无打包工具。
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// 管理员令牌持久化到 localStorage
(function loadToken() {
  const saved = localStorage.getItem("arb-admin-token");
  if (saved && $("#admin-token")) $("#admin-token").value = saved;
})();
$("#admin-token")?.addEventListener("change", (e) => {
  localStorage.setItem("arb-admin-token", e.target.value.trim());
});

// ---- 配置字段定义 --------------------------------------------------------
const CONFIG_FIELDS = [
  { key: "mode", label: "运行模式", type: "select",
    options: ["dry-run", "paper-trade", "live"],
    optionLabels: ["仅扫描 (dry-run)", "模拟交易 (paper-trade)", "实盘交易 (live)"],
    tip: "【仅扫描】走完整风控与执行链路但不发真实订单，用于验证风控逻辑；【模拟交易】用本地盘口撮合并扣虚拟余额，无需交易所密钥；【实盘交易】真实下单，必须先配置密钥。" },
  { key: "enabled_symbols", label: "启用的交易对（留空=使用下方分层）", type: "text",
    tip: "遗留字段。留空时系统使用下方的 tier1/tier2/tier3 并集。填入非空会覆盖分层（向后兼容）。" },
  { key: "symbol_tier1_enabled", label: "启用分层1 · 蓝筹", type: "bool",
    tip: "蓝筹币（BTC/ETH/SOL）。默认启用；但价差极窄，扣完手续费难有机会。" },
  { key: "symbol_tier1", label: "分层1 · 交易对（逗号分隔）", type: "text",
    tip: "蓝筹币白名单，逗号分隔。默认 BTC/USDT,ETH/USDT,SOL/USDT。" },
  { key: "symbol_tier2_enabled", label: "启用分层2 · 主流山寨", type: "bool",
    tip: "中盘币（DOGE/ARB/OP/SUI/WIF 等）。重点层，默认启用。" },
  { key: "symbol_tier2", label: "分层2 · 交易对（逗号分隔）", type: "text",
    tip: "中盘山寨币，逗号分隔。" },
  { key: "symbol_tier3_enabled", label: "启用分层3 · 小币/迷因", type: "bool",
    tip: "小币/迷因币（PEPE/SHIB/BONK 等）。波动大，但部分交易所可能未上线这些币。" },
  { key: "symbol_tier3", label: "分层3 · 交易对（逗号分隔）", type: "text",
    tip: "小币/迷因币，逗号分隔。注意：部分交易所可能没有这些币。" },
  { key: "min_net_edge_bps", label: "最小净价差（基点 / bps）", type: "number",
    tip: "低于此阈值的机会不会执行。1 基点 = 0.01%。该阈值作用在扣除双边手续费 + 滑点 + 保护偏移之后的净值上。" },
  { key: "min_profit_quote", label: "单笔最小净利润（USDT）", type: "number",
    tip: "预期净利润低于此值的机会被拒。" },
  { key: "fee_override_bps", label: "⚙️ 手续费覆盖（基点 / bps，留空=使用真实）", type: "number",
    tip: "【验证模式专用】填入后所有交易所的 taker 手续费都强制按此值计算，用于测试执行链路（否则 BTC/ETH 在两家头部所之间的毛价差 < 1bps，永远凑不够 25bps 真实手续费）。填 0 即免费扫描；跑通后请改回留空（即恢复为使用真实费率）。" },
  { key: "scan_buffer_bps", label: "安全保护偏移（基点 / bps）", type: "number",
    tip: "从毛价差扣除手续费和滑点之后，再减去此缓冲作为最终净值。防止决策瞬间到下单瞬间盘口微移导致亏损。降低此值让扫描更激进（更容易触发），升高更保守。真实跑建议 2-5。" },
  { key: "min_liquidity_usdt", label: "最小流动性门槛（USDT）", type: "number",
    tip: "可实际 VWAP 成交的名义金额下限。避免“幽灵套利”——价差看起来大但顶档只有几 USDT 深度，真下单会穿到深层导致滑点吃掉所有利润。默认 20 USDT。" },
  { key: "triangular_min_net_edge_bps", label: "三角套利 · 最小净价差（基点）", type: "number",
    tip: "同所三角套利的净值阈值。三腿都吃 taker，默认 2 基点。" },
  { key: "funding_rate_min_apr_bps", label: "资金费率 · 最小年化（基点）", type: "number",
    tip: "年化费率低于此阈值的永续合约不入库。200 基点 = 2% 年化。" },
  { key: "min_order_size_quote", label: "单腿最小金额（USDT）", type: "number",
    tip: "低于此值会被拒；多数交易所本身也有最小额度（约 10 USDT）。" },
  { key: "max_notional_per_trade", label: "⚠️ 单笔最大金额（USDT）", type: "number",
    tip: "实盘交易首次上线建议先压到 10–50 USDT，验证稳定后再放大。" },
  { key: "cooldown_seconds", label: "冷却时间（秒）", type: "number",
    tip: "同一交易对触发后进入冷却期，防止连续重复触发。" },
  { key: "scan_interval_ms", label: "扫描周期（毫秒）", type: "number",
    tip: "扫描周期。默认 200 毫秒；越小越灵敏也越消耗交易所接口配额。" },
  { key: "max_exposure_per_exchange", label: "单交易所风险敞口上限（USDT）", type: "number",
    tip: "在途 + 候选敞口超过此值会拒绝新机会。" },
  { key: "max_total_open_hedges", label: "同时进行中对冲组上限", type: "number",
    tip: "系统同时处理的对冲组最大数量。" },
  { key: "max_repair_attempts", label: "最多修复次数", type: "number",
    tip: "单个对冲组的残余敞口修复尝试次数上限；超出则放弃并告警。" },
  { key: "max_consecutive_failures", label: "连续失败熔断阈值", type: "number",
    tip: "达到此次数后熔断器自动触发。" },
  { key: "max_marketdata_staleness_ms", label: "行情最大陈旧时间（毫秒）", type: "number",
    tip: "盘口超过此时间视为过期，扫描会跳过；实盘交易下会阻止下单。" },
  { key: "max_balance_staleness_sec", label: "余额最大陈旧时间（秒）", type: "number",
    tip: "余额快照最大过期时间。实盘交易模式必须满足。" },
  { key: "kill_switch", label: "紧停", type: "bool",
    tip: "一键紧停：风控会拒绝任何新机会（等价于顶部的紧停按钮）。" },
  { key: "paused", label: "暂停扫描", type: "bool",
    tip: "等价于顶部暂停按钮：保留当前模式但不扫描/下单。" },
  { key: "order_type_policy", label: "下单类型", type: "select",
    options: ["limit", "market", "ioc_limit", "fok_limit"],
    optionLabels: ["普通限价单", "市价单", "激进限价单（成交即止）", "全部或取消限价单"],
    tip: "默认【激进限价单】：带保护价、只吃现有盘口流动性、剩余自动取消。【市价单】滑点风险大；【全部或取消】要求全量成交否则撤单。" },
  { key: "ioc_price_buffer_bps", label: "保护价偏移（基点 / bps）", type: "number",
    tip: "相对最优盘口价的偏移，买单往上加、卖单往下减，确保能吃到单。" },
  { key: "execution_mode", label: "🔀 执行模式", type: "select",
    options: ["taker_taker", "maker_taker"],
    optionLabels: ["双吃单（taker-taker，默认稳健）", "挂单-吃单（maker-taker，降费实验）"],
    tip: "【双吃单】两腿同时吃单，确定性高但付两侧 taker 费（典型 25 bps）。【挂单-吃单】便宜所挂 maker 买单，成交后对侧立即吃单对冲；理论成本减半（maker 约 0 bps），但会出现挂单排队不上 / 价格跑掉 / 单腿成交对冲失败的新情况，下方 5 个参数控制这些情况。" },
  { key: "maker_offset_bps", label: "Maker 挂单偏移（基点）", type: "number",
    tip: "挂单价相对 best_bid 的偏移。例如 best_bid=100, offset=1bps → 挂 100.01。太低会排不到、太高会变成 taker 吃单，反而没省费。默认 1.0。" },
  { key: "min_fill_ratio", label: "Maker 最小成交比例", type: "number",
    tip: "挂单在等待期内必须至少达到此成交比例，否则视为未成交。1.0 = 全量；0.5 = 至少成交一半。默认 0.5。" },
  { key: "max_wait_ms", label: "Maker 最大等待时长（毫秒）", type: "number",
    tip: "挂单超过此时长未达到成交比例就撤单。默认 5000（5 秒）。" },
  { key: "hedge_timeout_ms", label: "对冲腿超时（毫秒）", type: "number",
    tip: "Maker 已成交后，对侧吃单对冲必须在此时长内完成，否则强制反向平仓保存留的头寸。默认 2000。" },
  { key: "max_price_deviation_bps", label: "价格漂移容忍（基点）", type: "number",
    tip: "挂单等待期间若中间价漂移超过此基点，视为套利窗口消失、撤单。默认 3。" },
  { key: "maker_poll_interval_ms", label: "Maker 轮询周期（毫秒）", type: "number",
    tip: "挂单状态检查频率。越低反应越快、越高对交易所 API 压力越小。默认 250。" },
  { key: "alert_min_severity", label: "告警最小级别", type: "select",
    options: ["info", "warning", "error", "critical"],
    tip: "低于此级别的事件不会推到告警 webhook。" },
];

// ---- HTTP 辅助 -----------------------------------------------------------
function headers(withAuth = false) {
  const h = { "Content-Type": "application/json" };
  if (withAuth) {
    const t = $("#admin-token").value.trim();
    if (t) h["X-Admin-Token"] = t;
  }
  return h;
}

async function apiGet(path, auth = false) {
  const r = await fetch(path, { headers: auth ? headers(true) : undefined });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

async function apiPost(path, body, auth = true) {
  const r = await fetch(path, {
    method: "POST",
    headers: headers(auth),
    body: body === undefined ? "" : JSON.stringify(body || {}),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status} ${text}`);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtNum(v, dp = 4) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { maximumFractionDigits: dp });
}

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return String(ts);
  }
}

// ---- 主 tab / sub tab 切换 ----------------------------------------------
$$(".tabs > button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tabs > button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $$(".tab-pane").forEach((p) => p.classList.remove("active"));
    $("#tab-" + tab).classList.add("active");
    if (tab === "console") {
      renderCredentials();
      renderStrategies();
      renderConfig();
    } else if (tab === "monitor") {
      refreshSubTab(currentSubtab());
    }
  });
});

function currentSubtab() {
  const active = document.querySelector(".subtabs button.active");
  return active ? active.dataset.subtab : "dashboard";
}
$$(".subtabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".subtabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const name = btn.dataset.subtab;
    $$(".sub-pane").forEach((p) => p.classList.remove("active"));
    $("#sub-" + name).classList.add("active");
    refreshSubTab(name);
  });
});

async function refreshSubTab(name) {
  if (name === "dashboard") return refreshDashboard();
  if (name === "opportunities") return refreshOpportunities();
  if (name === "hedges") return refreshHedges();
  if (name === "orders") return refreshOrders();
  if (name === "marketdata") return refreshMarketData();
  if (name === "balances") return refreshBalances();
  if (name === "reports") return refreshReports();
  if (name === "events") return refreshEvents();
}

// ---- 顶部徽标 ------------------------------------------------------------
const MODE_ZH = { "dry-run": "仅扫描", "paper-trade": "模拟交易", "live": "实盘交易" };
function modeLabel(m) { return MODE_ZH[m] || m; }

async function refreshBadges() {
  try {
    const [h, cfg] = await Promise.all([apiGet("/health/exchanges"), apiGet("/config")]);
    const modeBadge = $("#mode-badge");
    modeBadge.textContent = "模式：" + modeLabel(cfg.mode) + " ▸";
    modeBadge.className = "badge badge-btn " + (cfg.mode === "live" ? "bad" : cfg.mode === "paper-trade" ? "warn" : "good");

    const pauseBadge = $("#pause-badge");
    if (pauseBadge) {
      pauseBadge.textContent = cfg.paused ? "⏸ 已暂停（点击恢复）" : "▶ 运行中（点击暂停）";
      pauseBadge.className = "badge badge-btn " + (cfg.paused ? "warn" : "good");
    }

    const ks = h.kill_switch;
    const ksBadge = $("#kill-badge");
    ksBadge.textContent = ks.on ? "⛔ 紧停 已开启（点击解除）" : "紧停：关闭";
    ksBadge.className = "badge badge-btn " + (ks.on ? "bad" : "good");

    const cb = h.circuit_breaker;
    const cbBadge = $("#cb-badge");
    if (cb.tripped) {
      cbBadge.textContent = "🛑 熔断已触发（点击重置）";
      cbBadge.className = "badge badge-btn bad";
    } else {
      cbBadge.textContent = "熔断：正常";
      cbBadge.className = "badge badge-btn good";
    }

    // cache for click handlers
    _lastState = {
      mode: cfg.mode,
      paused: !!cfg.paused,
      kill_on: !!ks.on,
      cb_tripped: !!cb.tripped,
    };
  } catch (e) { /* ignore */ }
}

// ====== 交易所凭据 ========================================================
function toggleCreds() {
  const body = document.getElementById("creds-body");
  const caret = document.getElementById("creds-caret");
  if (!body) return;
  const open = body.style.display !== "none";
  body.style.display = open ? "none" : "";
  if (caret) caret.textContent = open ? "▸" : "▾";
}
window.toggleCreds = toggleCreds;

function toggleStrategies() {
  const body = document.getElementById("strategies-body");
  const caret = document.getElementById("strategies-caret");
  if (!body) return;
  const open = body.style.display !== "none";
  body.style.display = open ? "none" : "";
  if (caret) caret.textContent = open ? "▸" : "▾";
}
window.toggleStrategies = toggleStrategies;

async function renderCredentials() {
  const container = $("#creds-grid");
  if (!container) return;
  container.innerHTML = '<div class="hint">加载中…</div>';
  try {
    const r = await apiGet("/exchanges/credentials");
    container.innerHTML = "";
    // Group spot + perp into two labelled blocks so 9+ cards are scannable.
    const spot = r.exchanges.filter((e) => (e.kind || "spot") === "spot");
    const perp = r.exchanges.filter((e) => e.kind === "perp");
    if (spot.length) {
      const h = document.createElement("div");
      h.className = "creds-group-header";
      h.innerHTML = `<span class="lbl">🏦 现货交易所（${spot.length} 家）</span>
        <span class="hint-small">未填 Key 时仅能读取公开行情，不能实盘下单</span>`;
      container.appendChild(h);
    }
    spot.forEach((e) => container.appendChild(credsCard(e)));
    if (perp.length) {
      const h = document.createElement("div");
      h.className = "creds-group-header";
      h.innerHTML = `<span class="lbl">💎 永续合约（${perp.length} 家）</span>
        <span class="hint-small">Kraken / Coinbase 当前不支持永续，故无对应卡片</span>`;
      container.appendChild(h);
    }
    perp.forEach((e) => container.appendChild(credsCard(e)));
  } catch (err) {
    container.innerHTML = `<div class="hint">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function credsCard(e) {
  const card = document.createElement("div");
  card.className = "creds-card";
  card.dataset.name = e.name;
  const icon = (e.name.replace("-perp", "")).slice(0, 3).toUpperCase();
  const configured = e.configured;
  const title = e.display_name || e.name.toUpperCase();
  const feeBadge = e.default_taker_bps
    ? `<span class="fee-badge" title="该所默认 taker 手续费">taker ${e.default_taker_bps} bps</span>`
    : "";
  const notesHtml = e.notes
    ? `<div class="creds-notes">⚠️ ${escapeHtml(e.notes)}</div>`
    : "";
  card.innerHTML = `
    <div class="header">
      <div class="icon">${icon}</div>
      <div class="title">${escapeHtml(title)}</div>
      ${feeBadge}
      <span class="status-chip ${configured ? "ok" : "missing"}">
        ${configured ? "● 已配置" : "○ 未配置"}
      </span>
    </div>
    ${notesHtml}
    <div class="fields">
      <div class="field-row">
        <label>API Key</label>
        <div class="value" data-role="key-display">${escapeHtml(e.key_masked || "（未设置）")}</div>
        <button class="mini-btn" data-action="reveal-key" title="明文显示 5 秒">显示</button>
      </div>
      <div class="field-row">
        <label>Secret</label>
        <div class="value" data-role="secret-display">${e.secret_masked ? "•".repeat(16) : "（未设置）"}</div>
        <button class="mini-btn" data-action="reveal-secret" title="明文显示 5 秒">显示</button>
      </div>
      ${e.has_passphrase_slot ? `
      <div class="field-row">
        <label>Passphrase</label>
        <div class="value" data-role="pass-display">${e.passphrase_masked ? "•".repeat(12) : "（未设置）"}</div>
        <button class="mini-btn" data-action="reveal-pass" title="明文显示 5 秒">显示</button>
      </div>` : ""}
      <div class="field-row">
        <label>Sandbox</label>
        <div class="value">${e.sandbox ? "是（测试网）" : "否（主网）"}</div>
        <span></span>
      </div>
    </div>

    <!-- Edit form (hidden by default) -->
    <div class="edit-form" style="display:none; margin-top:12px; padding-top:10px; border-top: 1px dashed var(--border-soft);">
      <div class="fields">
        <div class="field-row">
          <label>新 Key</label>
          <input type="text" data-field="key" placeholder="留空 = 保持不变" autocomplete="off" />
          <span></span>
        </div>
        <div class="field-row">
          <label>新 Secret</label>
          <input type="password" data-field="secret" placeholder="留空 = 保持不变" autocomplete="new-password" />
          <span></span>
        </div>
        ${e.has_passphrase_slot ? `
        <div class="field-row">
          <label>新 Passphrase</label>
          <input type="password" data-field="passphrase" placeholder="留空 = 保持不变" autocomplete="new-password" />
          <span></span>
        </div>` : ""}
        <div class="field-row">
          <label>Sandbox</label>
          <select data-field="sandbox">
            <option value="">（保持不变）</option>
            <option value="false">否（主网）</option>
            <option value="true">是（测试网）</option>
          </select>
          <span></span>
        </div>
      </div>
      <div class="actions-row">
        <button data-action="save" class="primary">保存</button>
        <button data-action="cancel">取消</button>
      </div>
    </div>

    <div class="actions-row">
      <button data-action="edit">编辑</button>
      <button data-action="test">连通性测试</button>
    </div>
    <div class="test-result" style="display:none"></div>
  `;

  // --- handlers ---
  const $c = (sel) => card.querySelector(sel);
  const $$c = (sel) => Array.from(card.querySelectorAll(sel));
  const editForm = $c(".edit-form");
  const actionsRow = card.querySelector(".actions-row:last-of-type");
  const testResult = $c(".test-result");

  card.querySelector('[data-action="edit"]').addEventListener("click", () => {
    editForm.style.display = editForm.style.display === "none" ? "block" : "none";
  });
  card.querySelector('[data-action="cancel"]')?.addEventListener("click", () => {
    editForm.style.display = "none";
    $$c("input[data-field]").forEach((i) => (i.value = ""));
  });
  card.querySelector('[data-action="save"]')?.addEventListener("click", async () => {
    const payload = {};
    $$c("input[data-field], select[data-field]").forEach((el) => {
      const name = el.dataset.field;
      const v = el.value.trim();
      if (!v) return;
      if (name === "sandbox") payload.sandbox = (v === "true");
      else payload[name] = v;
    });
    if (Object.keys(payload).length === 0) { log("未填写任何字段", "err"); return; }
    try {
      const r = await apiPost(`/exchanges/${e.name}/credentials`, payload);
      log(`${e.name} 凭据已更新：${r.updated_env_keys.join(", ")}`, "ok");
      editForm.style.display = "none";
      $$c("input[data-field]").forEach((i) => (i.value = ""));
      renderCredentials();
    } catch (err) {
      log(`${e.name} 凭据更新失败：${err.message}`, "err");
    }
  });
  card.querySelector('[data-action="test"]').addEventListener("click", async () => {
    testResult.style.display = "block";
    testResult.textContent = "测试中…";
    testResult.className = "test-result";
    try {
      const r = await apiPost(`/exchanges/${e.name}/credentials/test`, { mode: "balances" });
      if (r.ok) {
        testResult.className = "test-result ok";
        testResult.textContent = `✓ 连通成功 · 耗时 ${r.latency_ms}ms · 资产样本：${(r.assets_sample || []).join(", ") || "（全部为 0）"}`;
      } else {
        testResult.className = "test-result err";
        testResult.textContent = `✗ 失败 · 耗时 ${r.latency_ms}ms · ${r.error}`;
      }
    } catch (err) {
      testResult.className = "test-result err";
      testResult.textContent = "✗ " + err.message;
    }
  });

  // reveal buttons
  async function revealField(fieldRole, mapKey) {
    try {
      const r = await apiGet(`/exchanges/${e.name}/credentials/reveal`, true);
      const el = card.querySelector(`[data-role="${fieldRole}"]`);
      if (!el) return;
      const original = el.textContent;
      el.textContent = r[mapKey] || "（空）";
      el.style.color = "var(--warn)";
      setTimeout(() => { el.textContent = original; el.style.color = ""; }, 5000);
    } catch (err) {
      log(`${e.name} 明文获取失败：${err.message}（需要管理员令牌）`, "err");
    }
  }
  card.querySelector('[data-action="reveal-key"]').addEventListener("click", () => revealField("key-display", "key"));
  card.querySelector('[data-action="reveal-secret"]').addEventListener("click", () => revealField("secret-display", "secret"));
  card.querySelector('[data-action="reveal-pass"]')?.addEventListener("click", () => revealField("pass-display", "passphrase"));

  return card;
}

// ====== 策略卡片 ==========================================================
async function renderStrategies() {
  const container = $("#strategy-grid");
  if (!container) return;
  container.innerHTML = '<div class="hint">加载中…</div>';
  try {
    const [r, cfg] = await Promise.all([
      apiGet("/strategies"),
      apiGet("/config").catch(() => ({ mode: "paper-trade" })),
    ]);
    container.innerHTML = "";
    r.strategies.forEach((s) => container.appendChild(strategyCard(s, cfg.mode)));
  } catch (err) {
    container.innerHTML = `<div class="hint">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function strategyCard(s, currentMode) {
  const card = document.createElement("div");
  card.className = "strategy-card status-" + s.status;

  const canToggle = s.status !== "planned";
  const enabled = !!s.enabled;

  const sim = s.simulation || {};
  const simHtml = `
    <div class="sim-row">
      <span class="sim-label dry">仅扫描</span>
      <span class="sim-body">${escapeHtml(sim.dry_run || "—")}</span>
    </div>
    <div class="sim-row">
      <span class="sim-label paper">模拟交易</span>
      <span class="sim-body">${escapeHtml(sim.paper_trade || "—")}</span>
    </div>
    ${(sim.not_simulated && sim.not_simulated.length) ? `
      <div class="sim-row" style="margin-top:8px">
        <span class="sim-label ns">不模拟</span>
        <ul class="ns-list">
          ${sim.not_simulated.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}
        </ul>
      </div>
    ` : ""}
  `;

  // 交易所绑定（多选 / 单选，由 min_exchanges/max_exchanges 决定）
  const avail = s.available_exchanges || [];
  const sel = new Set(s.selected_exchanges || []);
  const isSingle = (s.min_exchanges === 1 && s.max_exchanges === 1);
  const bindCtrl = avail.length === 0
    ? '<span class="hint mono">尚无已配置的交易所</span>'
    : avail.map((ex) => {
        const checked = sel.has(ex) ? "checked" : "";
        const type = isSingle ? "radio" : "checkbox";
        return `
          <label class="ex-pick">
            <input type="${type}" name="bind-${s.id}" data-sid="${s.id}" data-ex="${ex}" ${checked} />
            <span>${escapeHtml(ex)}</span>
          </label>`;
      }).join("");
  const bindRangeZh = isSingle
    ? "需要选 1 家"
    : (s.max_exchanges >= 10
        ? `至少 ${s.min_exchanges} 家，上不封顶`
        : `${s.min_exchanges} — ${s.max_exchanges} 家`);

  // 动态账户警告：仅在实盘交易模式 + 所选交易所缺少凭据时显示（否则整块隐藏）
  const selList = s.selected_exchanges || [];
  const missingCreds = currentMode === "live"
    ? selList.filter((ex) => !avail.includes(ex))
    : [];
  const dynamicWarningHtml = (currentMode === "live" && (enabled || selList.length > 0) && missingCreds.length > 0)
    ? `<div class="dynamic-warn">
         ⚠️ <b>实盘交易模式下，以下已绑定账户缺少 API 凭据：</b>
         ${missingCreds.map((x) => `<span class="pill bad">${escapeHtml(x)}</span>`).join(" ")}
         <div class="hint">请到「一、交易所 API 配置」填入密钥并测试通过；或切换到模拟交易模式以继续使用。</div>
       </div>`
    : "";

  card.innerHTML = `
    <div class="header">
      <div>
        <span class="title">${escapeHtml(s.name_zh)}</span>
        <span class="en">${escapeHtml(s.name_en)}</span>
      </div>
      <span class="status-pill ${s.status}">${escapeHtml(s.status_zh)}</span>
    </div>
    <div class="short">${escapeHtml(s.short_zh)}</div>
    <div class="desc">${escapeHtml(s.description_zh)}</div>
    <div class="caveat">⚠️ ${escapeHtml(s.caveat_zh)}</div>
    ${dynamicWarningHtml}

    <div class="strategy-section sim">
      <div class="header-row">🧪 模拟方式（Simulation Notes）</div>
      ${simHtml}
    </div>

    <div class="strategy-section binding">
      <div class="header-row">
        🎯 启用 + 账户选择（一步完成；${escapeHtml(bindRangeZh)}）
      </div>
      <div class="enable-row">
        <label class="toggle-switch">
          <input type="checkbox" data-sid="${s.id}" data-role="enable-toggle"
                 ${enabled ? "checked" : ""} ${!canToggle ? "disabled" : ""} />
          <span>启用该策略（会自动使用下方勾选的账户）</span>
        </label>
      </div>
      <div class="ex-pick-row" data-bind-for="${s.id}">${bindCtrl}</div>
      <div class="binding-actions">
        <button class="primary" data-sid="${s.id}" data-action="configure"
                ${!canToggle ? "disabled" : ""}
                title="${canToggle ? "保存启用状态 + 已勾选的账户" : "该策略尚未实现，无法启用"}">
          💾 保存（启用 + 绑定）
        </button>
        <span class="binding-hint mono">
          当前：${enabled ? "● 已启用" : "○ 未启用"} · 绑定：${(s.selected_exchanges || []).join(", ") || "（未设置）"}
        </span>
      </div>
    </div>
  `;

  // 一键保存：启用 + 绑定
  card.querySelectorAll('button[data-action="configure"]').forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.sid;
      const toggle = card.querySelector(`input[data-role="enable-toggle"][data-sid="${sid}"]`);
      const enabled = !!(toggle && toggle.checked);
      const inputs = card.querySelectorAll(`input[name="bind-${sid}"]:checked`);
      const exchanges = Array.from(inputs).map((x) => x.dataset.ex);
      try {
        const resp = await apiPost(
          `/strategies/${sid}/configure`,
          { enabled, exchanges },
          true,
        );
        log(
          `策略 ${sid} 已保存：enabled=${resp.enabled}，绑定=${(resp.selected_exchanges || []).join(", ") || "（空）"}`,
          "ok",
        );
        renderStrategies();
      } catch (err) {
        log(`策略 ${sid} 保存失败：${err.message}`, "err");
      }
    });
  });
  return card;
}

// ====== 当前运行策略（回显页顶部）=========================================
async function renderActiveStrategies(cfg, opps) {
  const container = $("#active-strategies");
  if (!container) return;
  let strategies;
  try {
    const r = await apiGet("/strategies");
    strategies = r.strategies || [];
  } catch {
    container.innerHTML = '<div class="hint">策略列表加载失败</div>';
    return;
  }

  // Count opportunities per strategy (based on decision/reason fields we have)
  // Current API doesn't tag opps by strategy; only cross_exchange_spot produces
  // them today. We still show the full list with per-strategy counts placeholder.
  const runningGlobally = !cfg.paused && !cfg.kill_switch;

  container.innerHTML = "";
  strategies.forEach((s) => {
    const enabled = !!s.enabled;
    // Determine live state
    let liveLabel = "停用";
    let liveClass = "pill-muted";
    if (!enabled) {
      liveLabel = "已停用";
      liveClass = "pill-muted";
    } else if (s.status === "ready") {
      if (runningGlobally) {
        liveLabel = "执行中";
        liveClass = "pill-accent";
      } else {
        liveLabel = "已启用但暂停";
        liveClass = "pill-warn";
      }
    } else if (s.status === "detect_only") {
      if (runningGlobally) {
        liveLabel = "扫描中（不执行）";
        liveClass = "pill-ok";
      } else {
        liveLabel = "已启用但暂停";
        liveClass = "pill-warn";
      }
    } else {
      liveLabel = "未实现（占位）";
      liveClass = "pill-muted";
    }

    // For cross_exchange_spot we surface the in-process session counter
    // returned by the API (NOT the bounded recent-list length, which would
    // saturate at the limit value). Future: triangular/funding strategies
    // also expose ``session_count``; we'll wire each from its own endpoint.
    let oppsCount = 0;
    if (s.id === "cross_exchange_spot") {
      oppsCount = opps?.session_count ?? (opps?.opportunities || []).length;
    }

    const el = document.createElement("div");
    el.className = `active-strat ${enabled ? "on" : "off"} status-${s.status}`;
    el.innerHTML = `
      <div class="as-head">
        <div class="as-name">
          <b>${escapeHtml(s.name_zh)}</b>
          <span class="as-id mono">${escapeHtml(s.id)}</span>
        </div>
        <div class="as-pills">
          <span class="pill ${liveClass}">${liveLabel}</span>
          <span class="pill pill-muted">${s.status_zh || s.status}</span>
        </div>
      </div>
      <div class="as-body">
        <div class="as-col">
          <div class="k">本次会话机会数</div>
          <div class="v mono">${oppsCount}</div>
        </div>
        <div class="as-col">
          <div class="k">可执行</div>
          <div class="v">${s.status === "ready" ? "是" : s.status === "detect_only" ? "仅检测" : "否"}</div>
        </div>
        <div class="as-col">
          <div class="k">参与账户数</div>
          <div class="v mono">${(s.selected_exchanges || []).length}${(s.selected_exchanges || []).length ? ` · ${(s.selected_exchanges || []).join('/')}` : ''}</div>
        </div>
        <div class="as-col flex">
          <div class="k">描述</div>
          <div class="v small">${escapeHtml(s.short_zh || "")}</div>
        </div>
      </div>
    `;
    container.appendChild(el);
  });
}

// ====== Dashboard ========================================================
async function refreshDashboard() {
  try {
    const [h, opps, active, recent, cfg, report] = await Promise.all([
      apiGet("/health/exchanges"),
      apiGet("/opportunities/recent?limit=10").catch(() => ({ opportunities: [] })),
      apiGet("/hedges/active").catch(() => ({ hedges: [] })),
      apiGet("/hedges/recent").catch(() => ({ hedges: [] })),
      apiGet("/config"),
      apiGet("/reports/summary?hours=24").catch(() => null),
    ]);

    // Hero tiles
    const mode = cfg.mode;
    $("#hero-mode").textContent = modeLabel(mode);
    $("#hero-mode").className = "v " + (mode === "live" ? "neg" : mode === "paper-trade" ? "" : "pos");
    $("#hero-mode-sub").textContent = mode;

    $("#hero-pause").textContent = cfg.paused ? "已暂停" : "运行中";
    $("#hero-pause").className = "v " + (cfg.paused ? "neg" : "pos");
    $("#hero-pause-sub").textContent = cfg.paused ? "扫描器等待恢复" : "扫描器正在扫描";

    const kills = h.kill_switch.on;
    const cb = h.circuit_breaker.tripped;
    $("#hero-safety").textContent = kills ? "紧停 ON" : cb ? "熔断 ON" : "安全";
    $("#hero-safety").className = "v " + ((kills || cb) ? "neg" : "pos");
    $("#hero-safety-sub").textContent = `连续失败 ${h.circuit_breaker.consecutive_failures || 0} 次`;

    $("#hero-active-hedges").textContent = active.hedges.length;
    const finished1h = (recent.hedges || []).filter((x) =>
      x.state === "completed" && x.updated_at && (Date.now() - new Date(x.updated_at)) < 3600e3
    ).length;
    $("#hero-finished-1h").textContent = finished1h;

    let pnl = 0;
    (recent.hedges || []).forEach((x) => {
      const p = x.realized_pnl_quote ?? x.realized_profit_quote;
      if (p != null) pnl += Number(p) || 0;
    });
    const pnlEl = $("#hero-pnl");
    pnlEl.textContent = (pnl >= 0 ? "+" : "") + fmtNum(pnl, 4) + " USDT";
    pnlEl.className = "v mono " + (pnl > 0 ? "pos" : pnl < 0 ? "neg" : "");
    $("#hero-opps-24h").textContent = report?.opportunities ?? (opps.opportunities?.length || 0);

    $("#hero-config").textContent = `${cfg.min_net_edge_bps} bps / ${cfg.max_notional_per_trade} USDT`;

    // Active strategies panel (NEW)
    renderActiveStrategies(cfg, opps).catch(() => {});

    // Exchange rail
    const rail = $("#exchange-rail");
    rail.innerHTML = "";
    h.exchanges.forEach((ex) => {
      const el = document.createElement("div");
      el.className = "exchange-card";
      el.innerHTML = `
        <div class="rail-icon">${ex.name.slice(0, 3).toUpperCase()}</div>
        <div>
          <div class="rail-name">${escapeHtml(ex.name.toUpperCase())}</div>
          <div class="rail-sub">${ex.reason ? escapeHtml(ex.reason) : "连接正常"}</div>
        </div>
        <div class="rail-status">
          <span title="行情" class="dot ${ex.marketdata_ok ? "ok" : "bad"}"></span>
          <span title="余额" class="dot ${ex.balance_ok ? "ok" : "bad"}"></span>
        </div>
      `;
      rail.appendChild(el);
    });

    // Live opps feed (left)
    const feed = $("#live-opps");
    feed.innerHTML = "";
    (opps.opportunities || []).slice(0, 10).forEach((o) => feed.appendChild(oppCard(o, true)));
    if (!(opps.opportunities || []).length) feed.innerHTML = '<div class="hint">等待机会…</div>';

    // Live hedges (right)
    const hfeed = $("#live-hedges");
    hfeed.innerHTML = "";
    const bothHedges = (active.hedges || []).concat(recent.hedges || []).slice(0, 8);
    bothHedges.forEach((hg) => hfeed.appendChild(hedgeCard(hg)));
    if (!bothHedges.length) hfeed.innerHTML = '<div class="hint">尚无对冲组</div>';
  } catch (e) {
    console.error(e);
  }
}

// ====== 套利机会 ==========================================================
let _lastOppIds = new Set();
async function refreshOpportunities() {
  try {
    const r = await apiGet("/opportunities/recent?limit=50");
    const status = $("#cross-status");
    if (status) {
      const flag = r.enabled ? '<span style="color:var(--ok)">● 已启用</span>' : '<span style="color:var(--muted)">○ 未启用</span>';
      const runFlag = r.running ? '<span style="color:var(--ok)">扫描中</span>' : '<span style="color:var(--muted)">停止</span>';
      const lastScan = r.last_scan_at ? fmtTime(r.last_scan_at) : '—';
      const sess = r.session_count ?? 0;
      const acc = r.accepted_count ?? 0;
      const rej = sess - acc;
      status.innerHTML = `策略状态：${flag} · 扫描器：${runFlag} · <b>已扫描 ${r.scan_count || 0} 次</b> · 最近一次 ${lastScan} · 累计检测 <b>${sess}</b> 条 · 通过 <b style="color:var(--ok)">${acc}</b> · 被拒 <b style="color:var(--warn)">${rej}</b>`;
    }
    // Reject-reason distribution panel: lists each reason and how many times
    // it fired this session. This is what the user asked for as a "柱状图"
    // even though we render it as a horizontal bar list (simpler and more
    // information-dense than an actual chart at this scale).
    const reasonsEl = $("#cross-reject-reasons");
    if (reasonsEl) {
      const reasons = r.reject_counts || {};
      const total = Object.values(reasons).reduce((a, b) => a + b, 0);
      const labels = {
        kill_switch: "紧停开关已开",
        circuit_breaker: "熔断器跳闸",
        market_data_stale: "行情数据过期",
        below_min_edge: "净价差不达标 (<min_net_edge_bps)",
        below_min_profit: "单笔利润太小 (<min_profit_quote)",
        below_min_size: "可成交量太小 (<min_order_size_quote / 流动性不足)",
        insufficient_balance: "余额不足",
        max_exposure: "单所敞口超限",
        too_many_open_hedges: "未平对冲组数超限",
        cooldown_active: "冷却中",
        mode_disallowed: "模式不允许下单",
        symbol_not_whitelisted: "币对不在白名单",
        exchange_unhealthy: "交易所健康检查失败",
        other: "其他",
        unknown: "未分类",
      };
      if (total === 0) {
        reasonsEl.innerHTML = '<div class="hint">本次会话尚未拒绝任何机会（要么没扫到机会、要么全都通过了）。</div>';
      } else {
        const sorted = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
        reasonsEl.innerHTML = sorted
          .map(([k, v]) => {
            const pct = total > 0 ? Math.round((v / total) * 100) : 0;
            const label = labels[k] || k;
            return `
              <div class="reason-row">
                <div class="reason-bar"><div class="reason-fill" style="width:${pct}%"></div></div>
                <div class="reason-label"><b>${escapeHtml(label)}</b> <span class="mono">${k}</span></div>
                <div class="reason-stats mono">${v} 次 · ${pct}%</div>
              </div>`;
          })
          .join("");
      }
    }
    const container = $("#opp-cards");
    container.innerHTML = "";
    const newIds = new Set();
    (r.opportunities || []).forEach((o, i) => {
      const id = `${o.detected_at}-${o.symbol}-${i}`;
      const isNew = !_lastOppIds.has(id);
      const card = oppCard(o, false, isNew);
      newIds.add(id);
      container.appendChild(card);
    });
    _lastOppIds = newIds;
    if (!r.opportunities?.length) container.innerHTML = '<div class="hint">暂无机会数据（扫描器在跑但价差未超过 min_net_edge_bps）</div>';
  } catch (e) {
    $("#opp-cards").innerHTML = '<div class="hint">加载失败</div>';
  }
  // 三角套利机会
  try {
    const t = await apiGet("/opportunities/triangular/recent?limit=50");
    const status = $("#triangular-status");
    const tc = $("#triangular-cards");
    if (status) {
      const flag = t.enabled ? '<span style="color:var(--ok)">● 已启用</span>' : '<span style="color:var(--muted)">○ 未启用</span>';
      const runFlag = t.running ? '<span style="color:var(--ok)">扫描中</span>' : '<span style="color:var(--muted)">停止</span>';
      const lastScan = t.last_scan_at ? fmtTime(t.last_scan_at) : '—';
      status.innerHTML = `策略状态：${flag} · 扫描器：${runFlag} · <b>已扫描 ${t.scan_count || 0} 次</b> · 最近一次 ${lastScan} · 累计检测 <b>${t.session_count || 0}</b> 条机会`;
    }
    tc.innerHTML = "";
    (t.opportunities || []).forEach((o) => tc.appendChild(triangularCard(o)));
    if (!t.opportunities?.length) {
      const tip = t.enabled
        ? "扫描器已开启但尚未检测到满足阈值的机会（triangular_min_net_edge_bps 默认 5）。"
        : "请在『控制台 → 策略开关』启用『同所三角套利』后再查看。";
      tc.innerHTML = `<div class="hint">${tip}</div>`;
    }
  } catch (e) {
    // silent — don't clobber cross-ex opps
  }
  // 三角套利 paper 执行历史
  try {
    const r = await apiGet("/opportunities/triangular/executions?limit=50");
    const el = $("#triangular-exec-cards");
    if (el) {
      el.innerHTML = "";
      (r.executions || []).forEach((e) => el.appendChild(triangularExecCard(e)));
      if (!r.executions?.length) {
        el.innerHTML = '<div class="hint">尚无执行历史。【模拟交易】模式 + 启用三角策略后，每条入库机会会自动触发 3 腿撮合。</div>';
      }
    }
  } catch (e) {
    // silent
  }
  // 资金费率机会
  try {
    const r = await apiGet("/opportunities/funding/recent?limit=50");
    const status = $("#funding-status");
    const el = $("#funding-cards");
    if (status) {
      const flag = r.enabled ? '<span style="color:var(--ok)">● 已启用</span>' : '<span style="color:var(--muted)">○ 未启用</span>';
      const runFlag = r.running ? '<span style="color:var(--ok)">扫描中</span>' : '<span style="color:var(--muted)">停止</span>';
      const lastPoll = r.last_poll_at ? fmtTime(r.last_poll_at) : '—';
      const err = r.last_error ? ` · 上次错误: <span style="color:var(--warn)">${escapeHtml(r.last_error)}</span>` : '';
      status.innerHTML =
        `策略状态：${flag} · 扫描器：${runFlag} · <b>已轮询 ${r.scan_count || 0} 次</b> · 最近一次 ${lastPoll} · 阈值：${escapeHtml(r.min_apr_bps || '-')}bps APR · 累计 <b>${r.session_count || 0}</b> 条${err}`;
    }
    el.innerHTML = "";
    (r.opportunities || []).forEach((o) => el.appendChild(fundingCard(o)));
    if (!r.opportunities?.length) {
      const tip = r.enabled
        ? "扫描器已开启，但最近一次轮询没有发现超过 APR 阈值的资金费率。"
        : "请先在『控制台 → 策略开关』启用『资金费率套利』，扫描器将每 5 分钟轮询一次资金费率。";
      el.innerHTML = `<div class="hint">${tip}</div>`;
    }
  } catch (e) {
    // silent
  }
  // 资金费率执行历史
  try {
    const r = await apiGet("/opportunities/funding/executions?limit=50");
    const el = $("#funding-exec-cards");
    if (el) {
      el.innerHTML = "";
      (r.executions || []).forEach((h) => el.appendChild(fundingExecCard(h)));
      if (!r.executions?.length) {
        el.innerHTML = '<div class="hint">尚无执行历史。【模拟交易】或【实盘交易】模式 + 启用资金费率策略 + 永续合约密钥已配置后，扫描到机会会自动开仓（现货买 + 永续卖）。</div>';
      }
    }
  } catch (e) {
    // silent
  }
  // Maker-taker 执行历史（仅在 execution_mode=maker_taker 时有内容）
  try {
    const r = await apiGet("/opportunities/maker_taker/executions?limit=50");
    const el = $("#makertaker-exec-cards");
    if (el) {
      el.innerHTML = "";
      (r.executions || []).forEach((h) => el.appendChild(makerTakerExecCard(h)));
      if (!r.executions?.length) {
        el.innerHTML = '<div class="hint">尚无执行历史。切换到【挂单-吃单】执行模式后（控制台 → 运行时参数 → 执行模式），跨所现货扫到机会时会走此路径：便宜所挂 maker，成交后对侧 taker 对冲。默认 taker-taker 模式不会产生此历史。</div>';
      }
    }
  } catch (e) {
    // silent
  }
}

function makerTakerExecCard(h) {
  const el = document.createElement("div");
  const stateClass = {
    DONE: "accepted",
    CANCELED: "rejected",
    FLAT: "rejected",
    PANIC_CLOSING: "rejected",
  }[h.state] || "rejected";
  const stateZh = {
    DONE: "✓ 完成对冲",
    CANCELED: "⊘ 已撤单",
    FLAT: "↻ 已平仓",
    PANIC_CLOSING: "⚠ 强平中",
    POSTED: "○ 挂单中",
    PARTIAL_FILLED: "… 部分成交",
    FULL_FILLED: "→ 对冲中",
  }[h.state] || h.state;
  el.className = `opp-card makertaker ${stateClass}`;
  const legsHtml = (h.legs || [])
    .map(
      (l) => {
        const legName = { maker_buy: "挂单买", hedge_sell: "对冲卖", panic_close: "强平" }[l.leg] || l.leg;
        const bg = l.result === "FULL_FILLED" || l.result === "filled" ? "rgba(124,222,141,0.12)" : (l.result === "TIMEOUT" || l.result === "CANCELED" ? "rgba(239,83,80,0.12)" : "rgba(139,122,255,0.12)");
        return `<span class="pill" style="margin-right:4px;background:${bg}">${legName} @ ${escapeHtml(l.exchange)}${l.price ? ' ' + fmtNum(l.price, 4) : ''} · ${escapeHtml(l.result)} (${fmtNum(l.filled_amount, 6)})</span>`;
      }
    )
    .join("");
  const pnl = h.realized_profit_quote ? Number(h.realized_profit_quote) : null;
  const pnlHtml = pnl !== null
    ? `<span class="profit ${pnl >= 0 ? "pos" : "neg"}">${pnl >= 0 ? "+" : ""}${fmtNum(pnl, 4)} USDT</span>`
    : `<span class="profit">—</span>`;
  el.innerHTML = `
    <span class="ts">${fmtTime(h.posted_at)}</span>
    <span class="symbol">${escapeHtml(h.symbol)}</span>
    <span class="route">
      <span class="venue">${escapeHtml(h.buy_exchange)}</span>
      <span class="arrow">挂 → 冲</span>
      <span class="venue">${escapeHtml(h.sell_exchange)}</span>
    </span>
    <span class="edge-bar"><span class="val mono">@${fmtNum(h.maker_price, 4)} · 成交 ${fmtNum(h.filled_amount, 6)}</span></span>
    ${pnlHtml}
    <span class="decision ${stateClass}">${stateZh}</span>
    <span class="reason" style="grid-column:1/-1">
      <div style="margin-bottom:4px">${legsHtml}</div>
      ${h.reason && h.reason !== 'ok' ? `<div class="hint">原因：${escapeHtml(h.reason)}</div>` : ''}
    </span>
  `;
  return el;
}

function fundingExecCard(h) {
  const el = document.createElement("div");
  const cls = h.outcome === "opened" ? "accepted" : (h.outcome === "repaired" ? "rejected" : "rejected");
  el.className = `opp-card funding ${cls}`;
  const spot = h.spot_state;
  const perp = h.perp_state;
  const spotPill = spot
    ? `<span class="pill">现货 ${escapeHtml(spot.side)} ${fmtNum(spot.filled, 6)} · ${spot.status}</span>`
    : `<span class="pill" style="opacity:.5">现货 —</span>`;
  const perpPill = perp
    ? `<span class="pill">永续 ${escapeHtml(perp.side)} ${fmtNum(perp.filled, 6)} · ${perp.status}</span>`
    : `<span class="pill" style="opacity:.5">永续 —</span>`;
  const outcomeZh = { opened: '✓ 已开仓', repaired: '↻ 已回撤', failed: '✗ 失败' }[h.outcome] || h.outcome;
  el.innerHTML = `
    <span class="ts">${fmtTime(h.opened_at)}</span>
    <span class="symbol">${escapeHtml((h.exchange || '?').toUpperCase())}</span>
    <span class="route">
      <span class="venue">${escapeHtml(h.symbol || '')}</span>
      <span class="arrow">${h.direction === 'short-perp-long-spot' ? '做多现货 + 做空永续' : '做空现货 + 做多永续'}</span>
    </span>
    <span class="edge-bar"><span class="val mono">${fmtNum(h.apr_bps, 0)} bps APR</span></span>
    <span class="profit"></span>
    <span class="decision ${h.outcome === 'opened' ? 'accepted' : 'rejected'}">${outcomeZh}</span>
    <span class="reason" style="grid-column:1/-1">
      <div style="margin-bottom:4px">${spotPill} ${perpPill} <span class="pill" style="background:rgba(139,122,255,0.12);border-color:rgba(139,122,255,0.3)">${escapeHtml(h.mode)}</span></div>
      ${h.reason ? `<div class="hint">原因：${escapeHtml(h.reason)}</div>` : ''}
    </span>
  `;
  return el;
}

function triangularExecCard(e) {
  const el = document.createElement("div");
  const pnl = Number(e.realized_pnl_quote || 0);
  const cls = e.outcome === "completed" ? "accepted" : "rejected";
  el.className = `opp-card tri ${cls}`;
  const legsHtml = (e.legs || [])
    .map(
      (l) =>
        `<span class="pill" style="margin-right:4px">${l.leg_index}. ${escapeHtml(l.pair)} ${l.side} · ${l.status}</span>`
    )
    .join("");
  const rbHtml = (e.rollback_legs || [])
    .map(
      (l) =>
        `<span class="pill" style="margin-right:4px;background:rgba(255,167,38,0.12);border-color:rgba(255,167,38,0.3)">回撤 ${l.leg_index}. ${escapeHtml(l.pair)} ${l.side}</span>`
    )
    .join("");
  const outcomeZh = { completed: '✓ 完成', rolled_back: '↻ 已回撤', aborted: '✗ 中止' }[e.outcome] || e.outcome;
  el.innerHTML = `
    <span class="ts">${fmtTime(e.started_at)}</span>
    <span class="symbol">${escapeHtml(e.exchange?.toUpperCase() || '?')}</span>
    <span class="route">
      <span class="venue">${escapeHtml(e.direction || '')}</span>
    </span>
    <span class="edge-bar"><span class="val mono">${escapeHtml(e.probe_quote)} A</span></span>
    <span class="profit ${pnl < 0 ? 'neg' : ''}">${fmtNum(pnl, 4)}</span>
    <span class="decision ${e.outcome === 'completed' ? 'accepted' : 'rejected'}">${outcomeZh}</span>
    <span class="reason" style="grid-column:1/-1">
      <div style="margin-bottom:4px">${legsHtml}</div>
      ${rbHtml ? `<div>${rbHtml}</div>` : ''}
      ${e.reason ? `<div class="hint">原因：${escapeHtml(e.reason)}</div>` : ''}
    </span>
  `;
  return el;
}

function fundingCard(o) {
  const el = document.createElement("div");
  el.className = "opp-card funding accepted";
  const apr = Number(o.apr_bps || 0);
  const pct = Math.max(0, Math.min(100, (apr / 5000) * 100)); // scale 0-5000 bps -> 0-100%
  const dirZh = o.direction === 'short-perp-long-spot' ? '做空永续 + 做多现货' : '做多永续 + 做空现货';
  const rate = Number(o.funding_rate || 0);
  el.innerHTML = `
    <span class="ts">${fmtTime(o.detected_at)}</span>
    <span class="symbol">${escapeHtml((o.exchange || '?').toUpperCase())} · ${escapeHtml(o.symbol)}</span>
    <span class="route">
      <span class="venue">${escapeHtml(dirZh)}</span>
      <span class="hint" style="margin-left:8px">每期费率 ${fmtNum(rate * 100, 4)}%</span>
    </span>
    <span class="edge-bar">
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="val">${fmtNum(apr, 0)}bps APR</span>
    </span>
    <span class="profit">${fmtNum(apr / 100, 2)}%</span>
    <span class="decision accepted">仅检测 V1</span>
    <span class="reason" style="grid-column:1/-1">
      mark: ${escapeHtml(o.perp_mark_price ?? '—')} ·
      现货 ref: ${escapeHtml(o.spot_ref_price ?? '—')} ·
      下次结算: ${o.next_funding_time ? fmtTime(o.next_funding_time) : '—'}
    </span>
  `;
  return el;
}

function triangularCard(o) {
  const el = document.createElement("div");
  const net = Number(o.net_edge_bps || 0);
  const cls = net > 0 ? "accepted" : "rejected";
  el.className = `opp-card tri ${cls}`;
  const pct = Math.max(0, Math.min(100, (net / 50) * 100));
  const tri = (o.triangle || []).join(" → ");
  el.innerHTML = `
    <span class="ts">${fmtTime(o.detected_at)}</span>
    <span class="symbol">${escapeHtml(o.exchange.toUpperCase())}</span>
    <span class="route">
      <span class="venue">${escapeHtml(tri)}</span>
      <span class="arrow">⤴</span>
      <span class="hint" style="margin-left:6px">${escapeHtml(o.direction)}</span>
      <span class="hint" style="margin-left:10px">${escapeHtml(o.pair_ba)} · ${escapeHtml(o.pair_cb)} · ${escapeHtml(o.pair_ca)}</span>
    </span>
    <span class="edge-bar">
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="val">${fmtNum(net, 2)}bps</span>
    </span>
    <span class="profit ${net < 0 ? "neg" : ""}">${fmtNum(Number(o.end_quote) - Number(o.probe_quote), 4)}</span>
    <span class="decision accepted">仅检测 V1</span>
    <span class="reason" style="grid-column:1/-1">起始 ${fmtNum(o.probe_quote, 4)} · 终态 ${fmtNum(o.end_quote, 6)} · 毛 edge ${fmtNum(o.gross_edge_bps, 2)}bps · 费用 ${fmtNum(o.fee_bps_total, 2)}bps</span>
  `;
  return el;
}

function oppCard(o, compact = false, isNew = false) {
  const el = document.createElement("div");
  const cls = o.decision === "accepted" ? "accepted" : "rejected";
  el.className = `opp-card ${cls}${isNew ? " new" : ""}`;
  const edge = Number(o.net_edge_bps || 0);
  const pct = Math.max(0, Math.min(100, (edge / 50) * 100)); // scale: 0-50bps -> 0-100%
  const profit = Number(o.expected_profit_quote || 0);
  el.innerHTML = `
    <span class="ts">${fmtTime(o.detected_at)}</span>
    <span class="symbol">${escapeHtml(o.symbol)}</span>
    <span class="route">
      <span class="venue">${escapeHtml(o.buy_exchange || "?")}</span>
      <span class="arrow">→</span>
      <span class="venue">${escapeHtml(o.sell_exchange || "?")}</span>
      <span class="hint" style="margin-left:8px">${fmtNum(o.buy_price, 6)} / ${fmtNum(o.sell_price, 6)}</span>
    </span>
    <span class="edge-bar">
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="val">${fmtNum(edge, 2)}bps</span>
    </span>
    <span class="profit ${profit < 0 ? "neg" : ""}">${profit >= 0 ? "+" : ""}${fmtNum(profit, 4)}</span>
    <span class="decision ${o.decision}">${o.decision === "accepted" ? "✓ 接受" : "✗ 拒绝"}</span>
    ${!compact && o.decision_reason ? `<span class="reason" style="grid-column:1/-1">原因：${escapeHtml(o.decision_reason)}</span>` : ""}
  `;
  return el;
}

// ====== 对冲组 ============================================================
async function refreshHedges() {
  try {
    const [active, recent] = await Promise.all([apiGet("/hedges/active"), apiGet("/hedges/recent")]);
    const a = $("#hedge-active-cards");
    a.innerHTML = "";
    (active.hedges || []).forEach((h) => a.appendChild(hedgeCard(h)));
    if (!active.hedges?.length) a.innerHTML = '<div class="hint">没有进行中的对冲组</div>';

    const r = $("#hedge-recent-cards");
    r.innerHTML = "";
    (recent.hedges || []).slice(0, 30).forEach((h) => r.appendChild(hedgeCard(h)));
    if (!recent.hedges?.length) r.innerHTML = '<div class="hint">暂无历史对冲组</div>';
  } catch (e) {
    $("#hedge-active-cards").innerHTML = '<div class="hint">加载失败</div>';
  }
}

function hedgeCard(h) {
  const el = document.createElement("div");
  const state = (h.state || "pending").toLowerCase();
  el.className = `hedge-card state-${state}`;
  // hedges/recent returns realized_profit_quote (from DB) or realized_pnl_quote (from memory)
  const pnl = Number(h.realized_pnl_quote ?? h.realized_profit_quote ?? 0);
  const pnlCls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "zero";
  const target = Number(h.target_amount ?? h.planned_amount ?? 0);
  const eb = Number(h.executed_buy_amount || 0);
  const es = Number(h.executed_sell_amount || 0);
  const denom = target > 0 ? target : Math.max(eb, es, 1e-9);
  const buyFill = Math.min(100, (eb / denom) * 100);
  const sellFill = Math.min(100, (es / denom) * 100);
  const netPos = Number(h.net_position_base || 0);

  el.innerHTML = `
    <div class="header">
      <span class="hid">#${escapeHtml(String(h.id || "—").slice(-10))}</span>
      <div class="title">${escapeHtml(h.symbol || "?")}</div>
      <span class="pnl ${pnlCls}">${pnl >= 0 ? "+" : ""}${fmtNum(pnl, 4)} USDT</span>
    </div>
    <div class="legs">
      <div class="leg">
        <div class="ltitle">
          <span class="side buy">BUY</span>
          <span class="venue">${escapeHtml(h.buy_exchange || "?")}</span>
        </div>
        <div class="fill-bar"><div class="fill ${buyFill < 100 ? "partial" : ""}" style="width:${buyFill}%"></div></div>
        <div class="fill-meta"><span>成交 ${fmtNum(h.executed_buy_amount, 6)}</span><span>${buyFill.toFixed(0)}%</span></div>
      </div>
      <div class="leg">
        <div class="ltitle">
          <span class="side sell">SELL</span>
          <span class="venue">${escapeHtml(h.sell_exchange || "?")}</span>
        </div>
        <div class="fill-bar"><div class="fill ${sellFill < 100 ? "partial" : ""}" style="width:${sellFill}%"></div></div>
        <div class="fill-meta"><span>成交 ${fmtNum(h.executed_sell_amount, 6)}</span><span>${sellFill.toFixed(0)}%</span></div>
      </div>
    </div>
    <div class="footer">
      <span class="state-pill ${state}">${state.toUpperCase()}</span>
      <span><span class="k">净头寸:</span> ${fmtNum(netPos, 6)}</span>
      <span><span class="k">修复:</span> ${h.repair_attempts || 0}</span>
      <span style="margin-left:auto"><span class="k">时间:</span> ${fmtTime(h.updated_at || h.created_at)}</span>
    </div>
  `;
  return el;
}

// ====== 订单（按 hedge group 聚合） ======================================
async function refreshOrders() {
  try {
    const r = await apiGet("/orders/recent?limit=200");
    const container = $("#order-groups");
    container.innerHTML = "";
    const orders = r.orders || [];
    // group by hedge_group_id
    const groups = new Map();
    orders.forEach((o) => {
      const k = o.hedge_group_id || "(未归组)";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(o);
    });
    const sorted = Array.from(groups.entries()).sort((a, b) => {
      const ta = Math.max(...a[1].map((o) => new Date(o.created_at || 0).getTime()));
      const tb = Math.max(...b[1].map((o) => new Date(o.created_at || 0).getTime()));
      return tb - ta;
    });
    sorted.forEach(([gid, rows]) => container.appendChild(orderGroup(gid, rows)));
    if (!orders.length) container.innerHTML = '<div class="hint">暂无订单</div>';
  } catch (e) {
    $("#order-groups").innerHTML = '<div class="hint">加载失败</div>';
  }
}

function orderGroup(gid, rows) {
  const el = document.createElement("div");
  el.className = "order-group";
  const sym = rows[0]?.symbol || "?";
  el.innerHTML = `
    <div class="og-header">
      <span class="og-id">#${escapeHtml(String(gid).slice(-10))}</span>
      <span class="og-sym">${escapeHtml(sym)}</span>
      <span class="hint" style="margin-left:auto">${rows.length} 笔订单</span>
    </div>
    <div class="og-list">
      ${rows.map((o) => orderRowHtml(o)).join("")}
    </div>
  `;
  return el;
}

function orderRowHtml(o) {
  const amt = Number(o.amount || 0);
  const filled = Number(o.filled || 0);
  const pct = amt > 0 ? Math.min(100, (filled / amt) * 100) : 0;
  const sideCls = (o.side || "").toLowerCase();
  const status = (o.status || "open").toLowerCase();
  return `
    <div class="order-row">
      <span class="venue">${escapeHtml(o.exchange || "?")}</span>
      <span class="side ${sideCls}">${escapeHtml((o.side || "").toUpperCase())}</span>
      <span>${fmtNum(amt, 6)}</span>
      <span>
        <div class="fill-bar" style="max-width:220px"><div class="fill" style="width:${pct}%"></div></div>
        <div style="font-size:10.5px; color:var(--muted); margin-top:2px">${fmtNum(filled, 6)} / ${fmtNum(amt, 6)} (${pct.toFixed(0)}%)</div>
      </span>
      <span>${fmtNum(o.avg_fill_price, 6)}</span>
      <span class="status ${status}">${escapeHtml(status)}</span>
      <span class="ts">${fmtTime(o.created_at)}${o.is_repair ? " · 修复" : ""}</span>
    </div>
  `;
}

// ====== 行情 ==============================================================
async function refreshMarketData() {
  try {
    const r = await apiGet("/marketdata/books");
    const container = $("#market-cards");
    container.innerHTML = "";
    (r.books || []).forEach((b) => container.appendChild(mdCard(b)));
    if (!r.books?.length) container.innerHTML = '<div class="hint">暂无行情数据</div>';
  } catch (e) {
    $("#market-cards").innerHTML = '<div class="hint">加载失败</div>';
  }
}

function mdCard(b) {
  const el = document.createElement("div");
  el.className = "md-card";
  el.innerHTML = `
    <div class="mh">
      <div class="title">${escapeHtml(b.symbol || "?")}</div>
      <div class="venue">${escapeHtml((b.exchange || "?").toUpperCase())}</div>
    </div>
    <div class="prices">
      <div class="price-side bid">
        <div class="lbl">买一 BID</div>
        <div class="v">${fmtNum(b.best_bid, 6)}</div>
        <div class="sz">size: ${fmtNum(b.top_bid_size, 4)}</div>
      </div>
      <div class="price-side ask">
        <div class="lbl">卖一 ASK</div>
        <div class="v">${fmtNum(b.best_ask, 6)}</div>
        <div class="sz">size: ${fmtNum(b.top_ask_size, 4)}</div>
      </div>
    </div>
    <div class="meta-row">
      <span>中间价: ${fmtNum(b.mid_price, 6)}</span>
      <span>延迟: ${fmtNum(b.latency_ms, 0)}ms ${b.stale ? "<span style='color:var(--warn)'>· 过期</span>" : ""}</span>
    </div>
  `;
  return el;
}

// ====== 余额 ==============================================================
async function refreshBalances() {
  try {
    const r = await apiGet("/balances");
    const container = $("#balance-cards");
    container.innerHTML = "";
    // group by exchange
    const byEx = new Map();
    (r.balances || []).forEach((b) => {
      if (!byEx.has(b.exchange)) byEx.set(b.exchange, []);
      byEx.get(b.exchange).push(b);
    });
    byEx.forEach((rows, ex) => container.appendChild(balCard(ex, rows)));
    if (!byEx.size) container.innerHTML = '<div class="hint">暂无余额数据</div>';
  } catch (e) {
    $("#balance-cards").innerHTML = '<div class="hint">加载失败</div>';
  }
}

function balCard(ex, rows) {
  const el = document.createElement("div");
  el.className = "balance-card";
  const maxTotal = Math.max(...rows.map((r) => Number(r.total || 0)), 1);
  const sorted = rows.slice().sort((a, b) => Number(b.total || 0) - Number(a.total || 0));
  el.innerHTML = `
    <div class="bh">
      <span class="ex">${escapeHtml(ex.toUpperCase())}</span>
      <span class="hint">${rows.length} 项资产</span>
    </div>
    ${sorted.filter((b) => Number(b.total || 0) > 0).map((b) => {
      const tot = Number(b.total || 0);
      const locked = Number(b.locked || 0);
      const free = Number(b.free || 0);
      const totPct = (tot / maxTotal) * 100;
      const lockedPct = tot > 0 ? (locked / tot) * 100 : 0;
      return `
        <div class="balance-row">
          <span class="asset">${escapeHtml(b.asset)}</span>
          <span>
            <div class="meter">
              <div class="free" style="width:${totPct}%;background:linear-gradient(90deg,var(--accent),var(--accent-2) ${100 - lockedPct}%,var(--warn) ${100 - lockedPct}%,var(--warn));"></div>
            </div>
            <div class="amounts">
              <span class="free-val">${fmtNum(free, 4)}</span>
              ${locked > 0 ? ` <span class="locked">+${fmtNum(locked, 4)} 冻结</span>` : ""}
            </div>
          </span>
          <span class="amounts mono">${fmtNum(tot, 4)}</span>
        </div>
      `;
    }).join("") || '<div class="hint">全部为 0</div>'}
  `;
  return el;
}

// ====== 报表 ==============================================================
async function refreshReports() {
  const hours = parseInt($("#report-hours")?.value || "24", 10);
  try {
    const r = await apiGet(`/reports/summary?hours=${hours}`);
    const cards = $("#report-cards");
    cards.innerHTML = "";
    const items = [
      ["时间窗口", `${hours} 小时`],
      ["总机会数", r.opportunities ?? "—"],
      ["已接受", r.opportunities_accepted ?? "—"],
      ["已完成对冲", r.hedges_completed ?? "—"],
      ["中止对冲", r.hedges_aborted ?? "—"],
      ["订单数", r.orders ?? "—"],
      ["累计 PnL", (r.realized_pnl_quote !== undefined ? fmtNum(r.realized_pnl_quote, 4) + " USDT" : "—")],
    ];
    items.forEach(([k, v]) => {
      const t = document.createElement("div");
      t.className = "hero-tile";
      t.innerHTML = `<div class="k">${escapeHtml(k)}</div><div class="v mono small">${escapeHtml(String(v))}</div>`;
      cards.appendChild(t);
    });
    $("#report-body").textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    $("#report-body").textContent = "错误：" + e.message;
  }
}
$("#report-refresh")?.addEventListener("click", refreshReports);

// ====== 事件审计 ==========================================================
async function refreshEvents() {
  try {
    const r = await fetch("/config/audit", { headers: headers(true) });
    if (!r.ok) throw new Error(`${r.status}`);
    const body = await r.json();
    $("#audit-body").textContent = JSON.stringify(body, null, 2);
  } catch (e) {
    $("#audit-body").textContent = "需要填入管理员令牌（右上角）";
  }
}

// ====== 配置 + 操作 =======================================================
async function renderConfig() {
  const cfg = await apiGet("/config");
  const form = $("#config-form");
  form.innerHTML = "";
  CONFIG_FIELDS.forEach((f) => {
    const div = document.createElement("div");
    div.className = "field";
    const cur = cfg[f.key];
    let input = "";
    if (f.type === "select") {
      input = `<select name="${f.key}">${f.options.map((o, i) => {
        const lbl = (f.optionLabels && f.optionLabels[i]) || o;
        return `<option value="${o}" ${String(cur) === o ? "selected" : ""}>${lbl}</option>`;
      }).join("")}</select>`;
    } else if (f.type === "bool") {
      input = `<select name="${f.key}"><option value="true" ${cur ? "selected" : ""}>是（true）</option><option value="false" ${!cur ? "selected" : ""}>否（false）</option></select>`;
    } else {
      const t = f.type === "number" ? "number" : "text";
      input = `<input name="${f.key}" type="${t}" step="any" value="${cur ?? ""}" />`;
    }
    div.innerHTML = `<label>${f.label}<br><span style="font-family:var(--mono);color:var(--dim);font-size:10px">${f.key}</span></label>${input}<span class="tooltip">${f.tip}</span>`;
    form.appendChild(div);
  });
}

$("#save-config")?.addEventListener("click", async (e) => {
  e.preventDefault();
  const form = $("#config-form");
  const changes = {};
  CONFIG_FIELDS.forEach((f) => {
    const el = form.elements[f.key];
    if (!el) return;
    let v = el.value;
    if (f.type === "bool") v = v === "true";
    else if (f.type === "number") v = v === "" ? null : Number(v);
    // Nullable fields: empty string → explicit null so backend can clear them.
    const nullableKeys = new Set(["fee_override_bps"]);
    if (v !== null) changes[f.key] = v;
    else if (nullableKeys.has(f.key)) changes[f.key] = null;
  });
  try {
    const r = await apiPost("/config", changes, true);
    $("#config-result").textContent = JSON.stringify(r, null, 2);
    refreshBadges();
  } catch (err) {
    $("#config-result").textContent = "错误：" + err.message;
  }
});

$("#reload-config")?.addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const r = await apiPost("/control/reload-config", {}, true);
    $("#config-result").textContent = JSON.stringify(r, null, 2);
    await renderConfig();
  } catch (err) {
    $("#config-result").textContent = "错误：" + err.message;
  }
});

function log(msg, cls = "") {
  const el = $("#control-log");
  if (!el) return;
  const row = document.createElement("div");
  if (cls) row.className = cls;
  row.textContent = `[${new Date().toLocaleString("zh-CN")}] ${msg}`;
  el.prepend(row);
}

// ---- 顶栏徽章可点击控件 ------------------------------------------------
const MODE_CYCLE = ["dry-run", "paper-trade", "live"];
// Store last known state so click handlers know what to do
let _lastState = { mode: "dry-run", paused: false, kill_on: false, cb_tripped: false };

$("#mode-badge")?.addEventListener("click", async () => {
  const idx = MODE_CYCLE.indexOf(_lastState.mode);
  const next = MODE_CYCLE[(idx + 1) % MODE_CYCLE.length];
  // Confirm before switching to live
  if (next === "live" && !confirm("即将切换到【实盘交易】模式，会发出真实订单并扣减真实资产。请确认已配置好密钥、IP 白名单与资金上限。继续？")) return;
  try {
    await apiPost("/control/mode", { mode: next });
    log(`模式切换 → ${modeLabel(next)}`, "ok");
    refreshBadges();
  } catch (e) { log("切换模式失败：" + e.message, "err"); }
});

$("#pause-badge")?.addEventListener("click", async () => {
  try {
    if (_lastState.paused) {
      await apiPost("/control/resume", {});
      log("已恢复扫描", "ok");
    } else {
      await apiPost("/control/pause");
      log("已暂停扫描", "ok");
    }
    refreshBadges();
  } catch (e) { log("切换暂停失败：" + e.message, "err"); }
});

$("#kill-badge")?.addEventListener("click", async () => {
  try {
    if (_lastState.kill_on) {
      await apiPost("/control/kill-switch/off");
      log("紧停已解除", "ok");
    } else {
      if (!confirm("开启紧停？将立即拒绝一切新机会。")) return;
      await apiPost("/control/kill-switch/on");
      log("紧停已开启", "ok");
    }
    refreshBadges();
  } catch (e) { log("切换紧停失败：" + e.message, "err"); }
});

$("#cb-badge")?.addEventListener("click", async () => {
  if (!_lastState.cb_tripped) {
    log("熔断器当前未触发，无需重置", "");
    return;
  }
  try {
    await apiPost("/control/circuit-breaker/reset");
    log("熔断已重置", "ok");
    refreshBadges();
  } catch (e) { log("重置熔断失败：" + e.message, "err"); }
});
$("#reconcile")?.addEventListener("click", async () => {
  try {
    const r = await apiPost("/control/reconcile/balances");
    $("#reconcile-result").textContent = JSON.stringify(r, null, 2);
  } catch (e) { $("#reconcile-result").textContent = "错误：" + e.message; }
});
$("#cooldown-set")?.addEventListener("click", async () => {
  const sym = $("#cooldown-symbol").value.trim();
  const sec = parseInt($("#cooldown-seconds").value || "0", 10);
  if (!sym) { log("请输入交易对", "err"); return; }
  try {
    const r = await apiPost(`/control/cooldown/${encodeURIComponent(sym)}?seconds=${sec}`);
    log("设置冷却期：" + JSON.stringify(r), "ok");
  } catch (e) { log("设置冷却期失败：" + e.message, "err"); }
});

// ---- 顶栏延迟徽章（每 10s 轮询 /health/latency）---------------------------
async function refreshLatency() {
  const host = $("#latency-widget");
  if (!host) return;
  let data;
  try { data = await apiGet("/health/latency"); }
  catch { return; }  // silent — widget keeps last render
  const exs = data.exchanges || [];
  if (!exs.length) {
    host.innerHTML = '<span class="lat-dim mono">无交易所</span>';
    return;
  }
  host.innerHTML = exs.map((e) => {
    const ms = e.latency_ms;
    const avg = e.avg_ms;
    let cls, text;
    if (ms == null) { cls = "dead"; text = "超时"; }
    else if (ms < 100) { cls = "good"; text = ms + "ms"; }
    else if (ms < 300) { cls = "warn"; text = ms + "ms"; }
    else               { cls = "bad";  text = ms + "ms"; }
    const title = `${e.name} · 本次 ${ms == null ? "超时/失败" : ms + "ms"}` +
                  (avg != null ? ` · 最近 ${e.samples} 次均值 ${avg}ms` : "");
    const nm = e.name.slice(0, 3).toUpperCase();
    return `<span class="lat-pill ${cls}" title="${title}">
              <span class="dot"></span>
              <span class="lat-name">${nm}</span>
              <span class="lat-val">${text}</span>
            </span>`;
  }).join("");
}

// ---- 初始化 + 轮询 -------------------------------------------------------
refreshBadges();
refreshLatency();
renderCredentials();
renderStrategies();
renderConfig();
setInterval(refreshBadges, 3000);
setInterval(refreshLatency, 10000);
setInterval(() => {
  const tab = document.querySelector(".tabs > button.active");
  if (!tab) return;
  if (tab.dataset.tab === "monitor") refreshSubTab(currentSubtab());
}, 3000);

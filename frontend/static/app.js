// 套利系统 · 操作面板 —— 原生 JS，无打包工具。
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// 管理员令牌持久化到 localStorage，避免每次刷新都要重填
(function loadToken() {
  const saved = localStorage.getItem("arb-admin-token");
  if (saved && $("#admin-token")) $("#admin-token").value = saved;
})();
$("#admin-token")?.addEventListener("change", (e) => {
  localStorage.setItem("arb-admin-token", e.target.value.trim());
});

// ---- 配置字段定义（含中文说明） ----------------------------------------
const CONFIG_FIELDS = [
  { key: "mode", label: "运行模式", type: "select",
    options: ["dry-run", "paper-trade", "live"],
    tip: "dry-run 走完整风控与执行链路但不发真实订单；paper-trade 用本地盘口模拟撮合并扣虚拟余额；live 下真实单。" },
  { key: "enabled_symbols", label: "启用的交易对（逗号分隔）", type: "text",
    tip: "交易对白名单，例如 BTC/USDT,ETH/USDT,SOL/USDT。" },
  { key: "min_net_edge_bps", label: "最小净 edge (bps)", type: "number",
    tip: "低于此阈值的机会不会执行。1bps = 0.01%。已扣除双边手续费 + VWAP 滑点 + 保护偏移。" },
  { key: "min_profit_quote", label: "单笔最小净利润 (USDT)", type: "number",
    tip: "预期净利润低于此值的机会被拒。" },
  { key: "min_order_size_quote", label: "单腿最小名义金额 (USDT)", type: "number",
    tip: "低于此值会被拒；多数交易所本身也有最小额度（~10 USDT）。" },
  { key: "max_notional_per_trade", label: "⚠️ 单笔最大名义金额 (USDT)", type: "number",
    tip: "live 模式下 MVP 建议先压到 10–50 USDT，验证稳定后再放大。" },
  { key: "cooldown_seconds", label: "冷却期（秒）", type: "number",
    tip: "同一交易对触发后进入冷却期，防止连续重复触发。" },
  { key: "scan_interval_ms", label: "扫描周期（毫秒）", type: "number",
    tip: "扫描 scheduler 的周期。默认 200ms；越小越灵敏也越消耗 API 配额。最小值 50ms。" },
  { key: "max_exposure_per_exchange", label: "单交易所敞口上限 (USDT)", type: "number",
    tip: "在途 + 候选敞口超过此值会拒绝新机会。" },
  { key: "max_total_open_hedges", label: "同时在途 hedge 组上限", type: "number",
    tip: "系统同时处理的对冲组最大数量。" },
  { key: "max_repair_attempts", label: "最多修复次数", type: "number",
    tip: "单个 hedge 组的残余敞口修复尝试次数上限；超出则 abort。" },
  { key: "max_consecutive_failures", label: "连续失败熔断阈值", type: "number",
    tip: "达到此次数后熔断器自动触发。" },
  { key: "max_marketdata_staleness_ms", label: "行情最大陈旧度（毫秒）", type: "number",
    tip: "盘口超过此新鲜度视为 stale，扫描会跳过；live 下会阻止下单。" },
  { key: "max_balance_staleness_sec", label: "余额最大陈旧度（秒）", type: "number",
    tip: "余额快照最大过期时间。live 模式必须满足。" },
  { key: "kill_switch", label: "紧急停机", type: "bool",
    tip: "一键停机：风控会拒绝任何新机会（等价于顶部的紧停按钮）。" },
  { key: "paused", label: "暂停扫描", type: "bool",
    tip: "等价于顶部暂停按钮：保留 mode 但不扫描/下单。" },
  { key: "order_type_policy", label: "下单类型策略", type: "select",
    options: ["limit", "market", "ioc_limit", "fok_limit"],
    tip: "默认 ioc_limit（带保护价的激进限价，不吃单薄之外，成交即止）。market 风险大；fok 要求全量成交不然取消。" },
  { key: "ioc_price_buffer_bps", label: "IOC 保护价偏移 (bps)", type: "number",
    tip: "相对最优价的偏移，买单 +buffer、卖单 -buffer，确保能吃到单。" },
  { key: "alert_min_severity", label: "告警最小级别", type: "select",
    options: ["info", "warning", "error", "critical"],
    tip: "低于此级别的事件不会推到告警 webhook。" },
];

function headers(withAuth = false) {
  const h = { "Content-Type": "application/json" };
  if (withAuth) {
    const t = $("#admin-token").value.trim();
    if (t) h["X-Admin-Token"] = t;
  }
  return h;
}

async function apiGet(path) {
  const r = await fetch(path);
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

// ---- 顶部标签 -------------------------------------------------------------
$$(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $$(".tab-pane").forEach((p) => p.classList.remove("active"));
    $("#tab-" + tab).classList.add("active");
    if (tab === "console") {
      renderStrategies();
      renderConfig();
    } else if (tab === "monitor") {
      refreshSubTab(currentSubtab());
    }
  });
});

// ---- 子标签（回显页） -----------------------------------------------------
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
  if (name === "events") return refreshEvents();
}

// ---- 顶部徽标 -------------------------------------------------------------
async function refreshBadges() {
  try {
    const [h, cfg] = await Promise.all([apiGet("/health/exchanges"), apiGet("/config")]);
    const modeBadge = $("#mode-badge");
    modeBadge.textContent = "模式：" + cfg.mode;
    modeBadge.className = "badge " + (cfg.mode === "live" ? "bad" : cfg.mode === "paper-trade" ? "warn" : "good");
    $("#mode-current") && ($("#mode-current").textContent = cfg.mode);

    const pauseBadge = $("#pause-badge");
    if (pauseBadge) {
      pauseBadge.textContent = cfg.paused ? "已暂停" : "运行中";
      pauseBadge.className = "badge " + (cfg.paused ? "warn" : "good");
    }

    const ks = h.kill_switch;
    const ksBadge = $("#kill-badge");
    ksBadge.textContent = "紧停：" + (ks.on ? "ON" : "OFF");
    ksBadge.className = "badge " + (ks.on ? "bad" : "good");

    const cb = h.circuit_breaker;
    const cbBadge = $("#cb-badge");
    cbBadge.textContent = "熔断：" + (cb.tripped ? "已触发" : "正常");
    cbBadge.className = "badge " + (cb.tripped ? "bad" : "good");
  } catch (e) { /* ignore */ }
}

// ---- 策略开关 -------------------------------------------------------------
async function renderStrategies() {
  const container = $("#strategy-grid");
  if (!container) return;
  container.innerHTML = '<p class="hint">加载中…</p>';
  try {
    const r = await apiGet("/strategies");
    container.innerHTML = "";
    r.strategies.forEach((s) => {
      const card = document.createElement("div");
      card.className = "strategy-card";

      const statusClass =
        s.status === "ready" ? "status-ready" :
        s.status === "detect_only" ? "status-detect" : "status-planned";

      const canToggle = s.status !== "planned";
      const enabled = !!s.enabled;

      card.innerHTML = `
        <div class="header">
          <div>
            <span class="title">${s.name_zh}</span>
            <span class="en">${s.name_en}</span>
          </div>
          <span class="status-pill ${statusClass}">${s.status_zh}</span>
        </div>
        <div class="short">${s.short_zh}</div>
        <div class="desc">${s.description_zh}</div>
        <div class="caveat">⚠️ ${s.caveat_zh}</div>
        <div class="toggle-row">
          <span class="state ${enabled ? "on" : ""}">${enabled ? "● 已启用" : "○ 未启用"}</span>
          <div class="btn-row" style="margin-left:auto">
            <button data-sid="${s.id}" data-action="enable" class="primary" ${!canToggle ? "disabled" : ""} title="${canToggle ? "" : "该策略尚未实现，无法启用"}">启用</button>
            <button data-sid="${s.id}" data-action="disable">停用</button>
          </div>
        </div>
      `;
      container.appendChild(card);
    });

    container.querySelectorAll("button[data-sid]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const sid = btn.dataset.sid;
        const action = btn.dataset.action;
        try {
          const resp = await apiPost(`/strategies/${sid}/${action}`, {}, true);
          log(`策略 ${sid} → ${action}：${JSON.stringify(resp)}`, "ok");
          renderStrategies();
        } catch (e) {
          log(`策略 ${sid} ${action} 失败：${e.message}`, "err");
        }
      });
    });
  } catch (e) {
    container.innerHTML = `<p class="hint">加载策略失败：${e.message}</p>`;
  }
}

// ---- 仪表盘 ---------------------------------------------------------------
async function refreshDashboard() {
  try {
    const [h, opps, hedges, cfg] = await Promise.all([
      apiGet("/health/exchanges"),
      apiGet("/opportunities/recent?limit=10").catch(() => ({ opportunities: [] })),
      apiGet("/hedges/active"),
      apiGet("/config"),
    ]);
    const grid = $("#dashboard-grid");
    grid.innerHTML = "";
    tile(grid, "运行模式", cfg.mode);
    tile(grid, "扫描状态", cfg.paused ? "已暂停" : "运行中");
    tile(grid, "紧急停机", h.kill_switch.on ? "ON" : "OFF");
    tile(grid, "熔断器", h.circuit_breaker.tripped ? "已触发" : "正常");
    tile(grid, "进行中对冲组", hedges.hedges.length);
    tile(grid, "最小净 edge (bps)", cfg.min_net_edge_bps);
    tile(grid, "单笔上限 (USDT)", cfg.max_notional_per_trade);
    h.exchanges.forEach((ex) => {
      tile(grid, ex.name + " 行情", ex.marketdata_ok ? "正常" : "过期");
      tile(grid, ex.name + " 余额", ex.balance_ok ? "正常" : "过期");
    });

    renderTable($("#dashboard-recent"), opps.opportunities, [
      ["detected_at", "检测时间"],
      ["symbol", "交易对"],
      ["buy_exchange", "买方"],
      ["sell_exchange", "卖方"],
      ["net_edge_bps", "净 edge(bps)"],
      ["expected_profit_quote", "预期利润"],
      ["decision", "判定"],
      ["decision_reason", "原因"],
    ]);
  } catch (e) {
    $("#dashboard-grid").innerHTML = `<div class="tile"><div class="k">错误</div><div class="v">${e.message}</div></div>`;
  }
}

function tile(parent, k, v) {
  const d = document.createElement("div");
  d.className = "tile";
  d.innerHTML = `<div class="k">${k}</div><div class="v">${v}</div>`;
  parent.appendChild(d);
}

// ---- 配置表单 -------------------------------------------------------------
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
      input = `<select name="${f.key}">${f.options.map((o) => `<option value="${o}" ${String(cur) === o ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    } else if (f.type === "bool") {
      input = `<select name="${f.key}"><option value="true" ${cur ? "selected" : ""}>true（是）</option><option value="false" ${!cur ? "selected" : ""}>false（否）</option></select>`;
    } else {
      const t = f.type === "number" ? "number" : "text";
      input = `<input name="${f.key}" type="${t}" step="any" value="${cur ?? ""}" />`;
    }
    div.innerHTML = `<label>${f.label}<br><span style="font-family:monospace;color:#6e7681;font-size:10px">${f.key}</span></label>${input}<span class="tooltip">${f.tip}</span>`;
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
    if (v !== null) changes[f.key] = v;
  });
  try {
    const r = await apiPost("/config", changes, true);
    $("#config-result").textContent = JSON.stringify(r, null, 2);
    refreshBadges();
  } catch (e) {
    $("#config-result").textContent = "错误：" + e.message;
  }
});

$("#reload-config")?.addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const r = await apiPost("/control/reload-config", {}, true);
    $("#config-result").textContent = JSON.stringify(r, null, 2);
    await renderConfig();
  } catch (e) {
    $("#config-result").textContent = "错误：" + e.message;
  }
});

// ---- 操作（模式/紧停/暂停/熔断/核对/冷却） --------------------------------
function log(msg, cls = "") {
  const el = $("#control-log");
  if (!el) return;
  const row = document.createElement("div");
  if (cls) row.className = cls;
  row.textContent = `[${new Date().toLocaleString("zh-CN")}] ${msg}`;
  el.prepend(row);
}

$$("#tab-console [data-mode]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    try {
      const r = await apiPost("/control/mode", { mode: btn.dataset.mode });
      log("切换模式 → " + JSON.stringify(r), "ok");
      refreshBadges();
    } catch (e) {
      log("切换模式失败：" + e.message, "err");
    }
  });
});

$("#ks-on")?.addEventListener("click", async () => {
  try { log("紧停 ON：" + JSON.stringify(await apiPost("/control/kill-switch/on")), "ok"); refreshBadges(); }
  catch (e) { log("紧停开启失败：" + e.message, "err"); }
});
$("#ks-off")?.addEventListener("click", async () => {
  try { log("紧停 OFF：" + JSON.stringify(await apiPost("/control/kill-switch/off")), "ok"); refreshBadges(); }
  catch (e) { log("紧停解除失败：" + e.message, "err"); }
});
$("#pause-on")?.addEventListener("click", async () => {
  try { log("暂停：" + JSON.stringify(await apiPost("/control/pause")), "ok"); refreshBadges(); }
  catch (e) { log("暂停失败：" + e.message, "err"); }
});
$("#pause-off")?.addEventListener("click", async () => {
  try { log("恢复：" + JSON.stringify(await apiPost("/control/resume", {})), "ok"); refreshBadges(); }
  catch (e) { log("恢复失败：" + e.message, "err"); }
});
$("#cb-reset")?.addEventListener("click", async () => {
  try { log("重置熔断：" + JSON.stringify(await apiPost("/control/circuit-breaker/reset")), "ok"); refreshBadges(); }
  catch (e) { log("重置熔断失败：" + e.message, "err"); }
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

// ---- 各回显表 -------------------------------------------------------------
async function refreshOpportunities() {
  const r = await apiGet("/opportunities/recent?limit=100").catch(() => ({ opportunities: [] }));
  renderTable($("#opp-table"), r.opportunities, [
    ["detected_at", "检测时间"],
    ["symbol", "交易对"],
    ["buy_exchange", "买方"],
    ["sell_exchange", "卖方"],
    ["buy_price", "买价"],
    ["sell_price", "卖价"],
    ["net_edge_bps", "净 edge(bps)"],
    ["expected_profit_quote", "预期利润"],
    ["decision", "判定"],
    ["decision_reason", "原因"],
  ]);
}

async function refreshHedges() {
  const [active, recent] = await Promise.all([apiGet("/hedges/active"), apiGet("/hedges/recent")]);
  const cols = [
    ["id", "ID"],
    ["symbol", "交易对"],
    ["state", "状态"],
    ["buy_exchange", "买方"],
    ["sell_exchange", "卖方"],
    ["executed_buy_amount", "买成交量"],
    ["executed_sell_amount", "卖成交量"],
    ["net_position_base", "净头寸"],
    ["realized_pnl_quote", "实现盈亏"],
    ["repair_attempts", "修复次数"],
    ["updated_at", "更新时间"],
  ];
  renderTable($("#hedge-active"), active.hedges, cols);
  renderTable($("#hedge-recent"), recent.hedges, cols);
}

async function refreshOrders() {
  const r = await apiGet("/orders/recent?limit=100");
  renderTable($("#order-table"), r.orders, [
    ["created_at", "创建时间"],
    ["exchange", "交易所"],
    ["symbol", "交易对"],
    ["side", "方向"],
    ["amount", "数量"],
    ["filled", "已成交"],
    ["avg_fill_price", "均价"],
    ["status", "状态"],
    ["is_repair", "修复单"],
    ["hedge_group_id", "对冲组 ID"],
  ]);
}

async function refreshMarketData() {
  const r = await apiGet("/marketdata/books");
  renderTable($("#market-table"), r.books, [
    ["exchange", "交易所"],
    ["symbol", "交易对"],
    ["best_bid", "最优买价"],
    ["best_ask", "最优卖价"],
    ["mid_price", "中间价"],
    ["top_bid_size", "买一量"],
    ["top_ask_size", "卖一量"],
    ["latency_ms", "延迟(ms)"],
    ["stale", "是否过期"],
    ["ts_local", "本地时间"],
  ]);
}

async function refreshBalances() {
  const r = await apiGet("/balances");
  renderTable($("#balance-table"), r.balances, [
    ["exchange", "交易所"],
    ["asset", "资产"],
    ["free", "可用"],
    ["locked", "冻结"],
    ["total", "总额"],
    ["ts_local", "本地时间"],
  ]);
}

// ---- 报表 & 事件 ---------------------------------------------------------
$("#report-refresh")?.addEventListener("click", async () => {
  const hours = parseInt($("#report-hours").value || "24", 10);
  try {
    const r = await apiGet(`/reports/summary?hours=${hours}`);
    $("#report-body").textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    $("#report-body").textContent = "错误：" + e.message;
  }
});

async function refreshEvents() {
  try {
    // 审计需要 token
    const r = await fetch("/config/audit", { headers: headers(true) });
    if (!r.ok) throw new Error(`${r.status}`);
    const body = await r.json();
    $("#audit-body").textContent = JSON.stringify(body, null, 2);
  } catch (e) {
    $("#audit-body").textContent = "需要填入管理员令牌（右上角）";
  }
}

// ---- 工具函数 -------------------------------------------------------------
function renderTable(container, rows, cols) {
  if (!container) return;
  if (!rows || !rows.length) { container.innerHTML = "<p class='hint'>暂无数据</p>"; return; }
  const normalize = cols.map((c) => Array.isArray(c) ? c : [c, c]);
  const t = document.createElement("table");
  const thead = "<thead><tr>" + normalize.map(([, label]) => `<th>${label}</th>`).join("") + "</tr></thead>";
  const tbody = "<tbody>" + rows.map((r) => "<tr>" + normalize.map(([k]) => `<td>${format(r[k])}</td>`).join("") + "</tr>").join("") + "</tbody>";
  t.innerHTML = thead + tbody;
  container.innerHTML = "";
  container.appendChild(t);
}

function format(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}

// ---- 初始化 & 轮询 --------------------------------------------------------
refreshBadges();
renderConfig();
renderStrategies();
setInterval(refreshBadges, 5000);
setInterval(() => {
  const tab = document.querySelector(".tabs button.active");
  if (!tab) return;
  if (tab.dataset.tab === "monitor") {
    refreshSubTab(currentSubtab());
  }
}, 5000);

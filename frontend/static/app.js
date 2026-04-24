// arb-system operations panel — vanilla JS, no bundler.
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// --- Config field definitions (with tooltips) ---
const CONFIG_FIELDS = [
  { key: "mode", type: "select", options: ["dry-run", "paper-trade", "live"],
    tip: "dry-run: 完整经过风控和执行计划，但不调用交易所；paper-trade: 用本地盘口模拟成交；live: 真实下单。" },
  { key: "enabled_symbols", type: "text",
    tip: "白名单交易对（逗号分隔），例如 BTC/USDT,ETH/USDT,SOL/USDT。" },
  { key: "min_net_edge_bps", type: "number",
    tip: "最小净 edge (bps)。低于此阈值的机会不会执行。1bps=0.01%。" },
  { key: "min_profit_quote", type: "number",
    tip: "单笔最小预期净利润（quote 资产，默认 USDT）。" },
  { key: "min_order_size_quote", type: "number",
    tip: "单腿最小名义价值（quote）。低于此值将被拒（多数交易所也有最小额度要求）。" },
  { key: "max_notional_per_trade", type: "number",
    tip: "⚠️ 单笔最大名义价值（quote）。live 模式下 MVP 建议 ≤50 USDT。" },
  { key: "cooldown_seconds", type: "number",
    tip: "同一交易对触发后进入冷却期；防止同一瞬间反复触发。" },
  { key: "scan_interval_ms", type: "number",
    tip: "扫描周期（毫秒）。默认 200ms，越小越灵敏也越吃 API 配额。" },
  { key: "max_exposure_per_exchange", type: "number",
    tip: "单个交易所在途敞口上限（quote）。in-flight+候选 > 此值将拒绝。" },
  { key: "max_total_open_hedges", type: "number",
    tip: "同一时刻并行的 hedge group 最大数量。" },
  { key: "max_repair_attempts", type: "number",
    tip: "单个 hedge group 最多尝试几次修复，超出则 abort。" },
  { key: "max_consecutive_failures", type: "number",
    tip: "连续失败次数达到此值将触发熔断，暂停交易。" },
  { key: "max_marketdata_staleness_ms", type: "number",
    tip: "行情超过此新鲜度即视为 stale，scanner 会跳过；live 下还会阻止下单。" },
  { key: "max_balance_staleness_sec", type: "number",
    tip: "余额快照最大过期时间（秒），live 模式下必须满足。" },
  { key: "kill_switch", type: "bool",
    tip: "一键停机。开启后 risk engine 会拒绝任何新机会。" },
  { key: "order_type_policy", type: "select", options: ["limit", "market", "ioc_limit", "fok_limit"],
    tip: "默认 ioc_limit（带保护价的激进限价，成交即止，不吃单薄之外）。" },
  { key: "ioc_price_buffer_bps", type: "number",
    tip: "IOC 限价相对最优价的保护偏移（bps）。买单 +buffer，卖单 -buffer，确保能吃单。" },
  { key: "alert_min_severity", type: "select", options: ["info", "warning", "error", "critical"],
    tip: "告警最小严重级别（低于此级别不会发到 webhook）。" },
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

// --- Tabs ---
$$(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const tab = btn.dataset.tab;
    $$(".tab-pane").forEach((p) => p.classList.remove("active"));
    $("#tab-" + tab).classList.add("active");
    refreshTab(tab);
  });
});

// --- Badges (top bar) ---
async function refreshBadges() {
  try {
    const h = await apiGet("/health/exchanges");
    const cfg = await apiGet("/config");
    const modeBadge = $("#mode-badge");
    modeBadge.textContent = `mode: ${cfg.mode}`;
    modeBadge.className = "badge " + (cfg.mode === "live" ? "bad" : cfg.mode === "paper-trade" ? "warn" : "good");

    const ks = h.kill_switch;
    const ksBadge = $("#kill-badge");
    ksBadge.textContent = "kill: " + (ks.on ? "ON" : "OFF");
    ksBadge.className = "badge " + (ks.on ? "bad" : "good");

    const cb = h.circuit_breaker;
    const cbBadge = $("#cb-badge");
    cbBadge.textContent = "cb: " + (cb.tripped ? "TRIPPED" : "ok");
    cbBadge.className = "badge " + (cb.tripped ? "bad" : "good");
  } catch (e) { /* ignore */ }
}

// --- Dashboard ---
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
    tile(grid, "Mode", cfg.mode);
    tile(grid, "Kill switch", h.kill_switch.on ? "ON" : "OFF");
    tile(grid, "Circuit breaker", h.circuit_breaker.tripped ? "TRIPPED" : "ok");
    tile(grid, "Active hedges", hedges.hedges.length);
    tile(grid, "Min edge (bps)", cfg.min_net_edge_bps);
    tile(grid, "Max notional", cfg.max_notional_per_trade);
    h.exchanges.forEach((ex) => {
      tile(grid, ex.name + " md", ex.marketdata_ok ? "ok" : "stale");
      tile(grid, ex.name + " bal", ex.balance_ok ? "ok" : "stale");
    });

    const recent = $("#dashboard-recent");
    renderTable(recent, opps.opportunities, [
      "detected_at", "symbol", "buy_exchange", "sell_exchange",
      "net_edge_bps", "expected_profit_quote", "decision", "decision_reason",
    ]);
  } catch (e) {
    $("#dashboard-grid").innerHTML = `<div class="tile"><div class="k">error</div><div class="v">${e.message}</div></div>`;
  }
}

function tile(parent, k, v) {
  const d = document.createElement("div");
  d.className = "tile";
  d.innerHTML = `<div class="k">${k}</div><div class="v">${v}</div>`;
  parent.appendChild(d);
}

// --- Config ---
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
      input = `<select name="${f.key}"><option value="true" ${cur ? "selected" : ""}>true</option><option value="false" ${!cur ? "selected" : ""}>false</option></select>`;
    } else {
      const t = f.type === "number" ? "number" : "text";
      input = `<input name="${f.key}" type="${t}" step="any" value="${cur ?? ""}" />`;
    }
    div.innerHTML = `<label>${f.key}</label>${input}<span class="tooltip">${f.tip}</span>`;
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
    $("#config-result").textContent = "ERROR: " + e.message;
  }
});

$("#reload-config")?.addEventListener("click", async (e) => {
  e.preventDefault();
  try {
    const r = await apiPost("/control/reload-config", {}, true);
    $("#config-result").textContent = JSON.stringify(r, null, 2);
    await renderConfig();
  } catch (e) {
    $("#config-result").textContent = "ERROR: " + e.message;
  }
});

// --- Control ---
function log(msg, cls = "") {
  const el = $("#control-log");
  if (!el) return;
  const row = document.createElement("div");
  if (cls) row.className = cls;
  row.textContent = `[${new Date().toISOString()}] ${msg}`;
  el.prepend(row);
}

$$("#tab-control [data-mode]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    try {
      const r = await apiPost("/control/mode", { mode: btn.dataset.mode });
      log("mode -> " + JSON.stringify(r), "ok");
      refreshBadges();
    } catch (e) {
      log("mode ERR " + e.message, "err");
    }
  });
});

$("#ks-on")?.addEventListener("click", async () => {
  try { log(JSON.stringify(await apiPost("/control/kill-switch/on")), "ok"); refreshBadges(); }
  catch (e) { log("kill-switch ON ERR " + e.message, "err"); }
});
$("#ks-off")?.addEventListener("click", async () => {
  try { log(JSON.stringify(await apiPost("/control/kill-switch/off")), "ok"); refreshBadges(); }
  catch (e) { log("kill-switch OFF ERR " + e.message, "err"); }
});
$("#pause-on")?.addEventListener("click", async () => {
  try { log(JSON.stringify(await apiPost("/control/pause")), "ok"); refreshBadges(); }
  catch (e) { log("pause ERR " + e.message, "err"); }
});
$("#pause-off")?.addEventListener("click", async () => {
  try { log(JSON.stringify(await apiPost("/control/resume", {})), "ok"); refreshBadges(); }
  catch (e) { log("resume ERR " + e.message, "err"); }
});
$("#cb-reset")?.addEventListener("click", async () => {
  try { log(JSON.stringify(await apiPost("/control/circuit-breaker/reset")), "ok"); refreshBadges(); }
  catch (e) { log("cb reset ERR " + e.message, "err"); }
});
$("#reconcile")?.addEventListener("click", async () => {
  try {
    const r = await apiPost("/control/reconcile/balances");
    $("#reconcile-result").textContent = JSON.stringify(r, null, 2);
  } catch (e) { $("#reconcile-result").textContent = "ERROR: " + e.message; }
});
$("#cooldown-set")?.addEventListener("click", async () => {
  const sym = $("#cooldown-symbol").value.trim();
  const sec = parseInt($("#cooldown-seconds").value || "0", 10);
  try {
    const r = await apiPost(`/control/cooldown/${encodeURIComponent(sym)}?seconds=${sec}`);
    log("cooldown: " + JSON.stringify(r), "ok");
  } catch (e) { log("cooldown ERR " + e.message, "err"); }
});

// --- Opportunities / Hedges / Orders / MarketData / Balances ---
async function refreshOpportunities() {
  const r = await apiGet("/opportunities/recent?limit=100").catch(() => ({ opportunities: [] }));
  renderTable($("#opp-table"), r.opportunities, [
    "detected_at", "symbol", "buy_exchange", "sell_exchange",
    "buy_price", "sell_price", "net_edge_bps", "expected_profit_quote",
    "decision", "decision_reason",
  ]);
}

async function refreshHedges() {
  const [active, recent] = await Promise.all([
    apiGet("/hedges/active"),
    apiGet("/hedges/recent"),
  ]);
  renderTable($("#hedge-active"), active.hedges, [
    "id", "symbol", "state", "buy_exchange", "sell_exchange",
    "executed_buy_amount", "executed_sell_amount", "net_position_base",
    "realized_pnl_quote", "repair_attempts", "updated_at",
  ]);
  renderTable($("#hedge-recent"), recent.hedges, [
    "id", "symbol", "state", "buy_exchange", "sell_exchange",
    "executed_buy_amount", "executed_sell_amount", "net_position_base",
    "realized_pnl_quote", "repair_attempts", "created_at",
  ]);
}

async function refreshOrders() {
  const r = await apiGet("/orders/recent?limit=100");
  renderTable($("#order-table"), r.orders, [
    "created_at", "exchange", "symbol", "side", "amount", "filled",
    "avg_fill_price", "status", "is_repair", "hedge_group_id",
  ]);
}

async function refreshMarketData() {
  const r = await apiGet("/marketdata/books");
  renderTable($("#market-table"), r.books, [
    "exchange", "symbol", "best_bid", "best_ask", "mid_price",
    "top_bid_size", "top_ask_size", "latency_ms", "stale", "ts_local",
  ]);
}

async function refreshBalances() {
  const r = await apiGet("/balances");
  renderTable($("#balance-table"), r.balances, [
    "exchange", "asset", "free", "locked", "total", "ts_local",
  ]);
}

// --- Reports / Events ---
$("#report-refresh")?.addEventListener("click", async () => {
  const hours = parseInt($("#report-hours").value || "24", 10);
  const r = await apiGet(`/reports/summary?hours=${hours}`);
  $("#report-body").textContent = JSON.stringify(r, null, 2);
});

async function refreshEvents() {
  try {
    const r = await apiGet("/config/audit");
    $("#audit-body").textContent = JSON.stringify(r, null, 2);
  } catch (e) {
    $("#audit-body").textContent = "需要 admin token";
  }
}

// --- Helpers ---
function renderTable(container, rows, cols) {
  if (!container) return;
  if (!rows || !rows.length) { container.innerHTML = "<p class='hint'>无数据</p>"; return; }
  const t = document.createElement("table");
  const thead = "<thead><tr>" + cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead>";
  const tbody = "<tbody>" + rows.map((r) => "<tr>" + cols.map((c) => `<td>${format(r[c])}</td>`).join("") + "</tr>").join("") + "</tbody>";
  t.innerHTML = thead + tbody;
  container.innerHTML = "";
  container.appendChild(t);
}

function format(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return String(v);
  return String(v);
}

// --- Initial load + polling ---
async function refreshTab(tab) {
  if (tab === "dashboard") await refreshDashboard();
  if (tab === "config") await renderConfig();
  if (tab === "opportunities") await refreshOpportunities();
  if (tab === "hedges") await refreshHedges();
  if (tab === "orders") await refreshOrders();
  if (tab === "marketdata") await refreshMarketData();
  if (tab === "balances") await refreshBalances();
  if (tab === "events") await refreshEvents();
}

refreshBadges();
refreshDashboard();
setInterval(refreshBadges, 5000);
setInterval(() => {
  const activeTab = document.querySelector(".tabs button.active");
  if (activeTab) refreshTab(activeTab.dataset.tab);
}, 4000);

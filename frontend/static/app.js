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
    tip: "扫描周期。默认 200ms；越小越灵敏也越消耗 API 配额。" },
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
async function refreshBadges() {
  try {
    const [h, cfg] = await Promise.all([apiGet("/health/exchanges"), apiGet("/config")]);
    const modeBadge = $("#mode-badge");
    modeBadge.textContent = "模式：" + cfg.mode + " ▸";
    modeBadge.className = "badge badge-btn " + (cfg.mode === "live" ? "bad" : cfg.mode === "paper-trade" ? "warn" : "good");

    const pauseBadge = $("#pause-badge");
    if (pauseBadge) {
      pauseBadge.textContent = cfg.paused ? "⏸ 已暂停（点击恢复）" : "▶ 运行中（点击暂停）";
      pauseBadge.className = "badge badge-btn " + (cfg.paused ? "warn" : "good");
    }

    const ks = h.kill_switch;
    const ksBadge = $("#kill-badge");
    ksBadge.textContent = ks.on ? "⛔ 紧停 ON（点击解除）" : "紧停：OFF";
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
async function renderCredentials() {
  const container = $("#creds-grid");
  if (!container) return;
  container.innerHTML = '<div class="hint">加载中…</div>';
  try {
    const r = await apiGet("/exchanges/credentials");
    container.innerHTML = "";
    r.exchanges.forEach((e) => container.appendChild(credsCard(e)));
  } catch (err) {
    container.innerHTML = `<div class="hint">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function credsCard(e) {
  const card = document.createElement("div");
  card.className = "creds-card";
  card.dataset.name = e.name;
  const icon = e.name.slice(0, 3).toUpperCase();
  const configured = e.configured;
  card.innerHTML = `
    <div class="header">
      <div class="icon">${icon}</div>
      <div class="title">${escapeHtml(e.name.toUpperCase())}</div>
      <span class="status-chip ${configured ? "ok" : "missing"}">
        ${configured ? "● 已配置" : "○ 未配置"}
      </span>
    </div>
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
    const r = await apiGet("/strategies");
    container.innerHTML = "";
    r.strategies.forEach((s) => container.appendChild(strategyCard(s)));
  } catch (err) {
    container.innerHTML = `<div class="hint">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function strategyCard(s) {
  const card = document.createElement("div");
  card.className = "strategy-card status-" + s.status;

  const canToggle = s.status !== "planned";
  const enabled = !!s.enabled;

  const accountsHtml = (s.accounts || []).map((a) => `
    <div class="account-row">
      <div class="exchange-name">${escapeHtml(a.exchange)}</div>
      <div class="account-meta">
        <div class="meta-line"><b>账户类型：</b>${escapeHtml(a.account_type)}</div>
        <div class="meta-line">
          <b>必需资产：</b>
          <div class="asset-pills">${(a.required_assets || []).map((x) => `<span class="pill">${escapeHtml(x)}</span>`).join("")}</div>
        </div>
        ${a.min_balance_hint ? `<div class="meta-line"><b>最低余额建议：</b>${escapeHtml(a.min_balance_hint)}</div>` : ""}
        <div class="meta-line"><b>权限：</b>${escapeHtml(a.permissions || "")}</div>
        <div class="meta-line"><b>IP 白名单：</b>${a.ip_whitelist_required ? "必须" : "可选"}</div>
      </div>
    </div>
  `).join("");

  const sim = s.simulation || {};
  const simHtml = `
    <div class="sim-row">
      <span class="sim-label dry">dry-run</span>
      <span class="sim-body">${escapeHtml(sim.dry_run || "—")}</span>
    </div>
    <div class="sim-row">
      <span class="sim-label paper">paper</span>
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

    <div class="strategy-section accounts">
      <div class="header-row">🔑 账户要求（Accounts Required）</div>
      ${accountsHtml || '<div class="hint">—</div>'}
    </div>

    <div class="strategy-section sim">
      <div class="header-row">🧪 模拟方式（Simulation Notes）</div>
      ${simHtml}
    </div>

    <div class="toggle-row">
      <span class="state ${enabled ? "on" : ""}">${enabled ? "● 已启用" : "○ 未启用"}</span>
      <div class="btn-row" style="margin-left:auto">
        <button data-sid="${s.id}" data-action="enable" class="primary" ${!canToggle ? "disabled" : ""} title="${canToggle ? "" : "该策略尚未实现，无法启用"}">启用</button>
        <button data-sid="${s.id}" data-action="disable">停用</button>
      </div>
    </div>
  `;
  card.querySelectorAll("button[data-sid]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.sid;
      const action = btn.dataset.action;
      try {
        const resp = await apiPost(`/strategies/${sid}/${action}`, {}, true);
        log(`策略 ${sid} → ${action}：${JSON.stringify(resp)}`, "ok");
        renderStrategies();
      } catch (err) {
        log(`策略 ${sid} ${action} 失败：${err.message}`, "err");
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

    const oppsCount = (opps?.opportunities || []).filter((o) => {
      // Current opp records don't carry a strategy field — attribute everything
      // to cross_exchange_spot for now. Future: add `strategy_id` to opp table.
      return s.id === "cross_exchange_spot";
    }).length;

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
          <div class="v mono">${(s.accounts || []).length}</div>
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
    $("#hero-mode").textContent = { "dry-run": "试运行", "paper-trade": "模拟盘", "live": "实盘" }[mode] || mode;
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
    if (!r.opportunities?.length) container.innerHTML = '<div class="hint">暂无机会数据</div>';
  } catch (e) {
    $("#opp-cards").innerHTML = '<div class="hint">加载失败</div>';
  }
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
      input = `<select name="${f.key}">${f.options.map((o) => `<option value="${o}" ${String(cur) === o ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    } else if (f.type === "bool") {
      input = `<select name="${f.key}"><option value="true" ${cur ? "selected" : ""}>true（是）</option><option value="false" ${!cur ? "selected" : ""}>false（否）</option></select>`;
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
    if (v !== null) changes[f.key] = v;
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
  if (next === "live" && !confirm("即将切换到【实盘 live】模式，会发出真实订单。继续？")) return;
  try {
    await apiPost("/control/mode", { mode: next });
    log(`模式切换 → ${next}`, "ok");
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
      if (!confirm("开启紧急停机？将立即拒绝一切新机会。")) return;
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

// ---- 初始化 + 轮询 -------------------------------------------------------
refreshBadges();
renderCredentials();
renderStrategies();
renderConfig();
setInterval(refreshBadges, 3000);
setInterval(() => {
  const tab = document.querySelector(".tabs > button.active");
  if (!tab) return;
  if (tab.dataset.tab === "monitor") refreshSubTab(currentSubtab());
}, 3000);

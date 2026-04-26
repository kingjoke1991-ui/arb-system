/* =========================================================
   arb-system · mobile panel (vanilla JS, no deps)
   Reuses the same JSON APIs as the desktop panel.
   ========================================================= */

(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = {
    mode: null,
    paused: null,
    killSwitch: null,
    circuitBreaker: null,
    latency: [],
  };

  // ---------- Admin token (localStorage) ----------
  const TOKEN_KEY = "arb_admin_token";
  const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
  const setToken = (v) => v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY);

  // ---------- Fetch helpers ----------
  async function apiGet(path) {
    const r = await fetch(path, { headers: { "Accept": "application/json" } });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }
  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Admin-Token": getToken(),
      },
      body: body ? JSON.stringify(body) : "{}",
    });
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { /* ignore */ }
    if (!r.ok) {
      const msg = (data && (data.detail || data.message)) || `${r.status} ${r.statusText}`;
      throw new Error(msg);
    }
    return data;
  }

  // ---------- Toast ----------
  let toastEl = null, toastTimer = null;
  function toast(msg, kind = "info") {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.dataset.kind = kind;
    toastEl.dataset.show = "true";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.dataset.show = "false"; }, 2200);
  }

  // ---------- Collapsible cards ----------
  function setupCollapsibles() {
    $$('[data-toggle]').forEach((btn) => {
      btn.addEventListener("click", () => {
        const expanded = btn.getAttribute("aria-expanded") === "true";
        const target = document.getElementById(btn.getAttribute("aria-controls"));
        btn.setAttribute("aria-expanded", String(!expanded));
        if (target) target.hidden = expanded;
      });
    });
  }

  // ---------- Sheet (slide-up detail page) ----------
  const sheet = $("#sheet");
  const sheetTitle = $("#sheet-title");
  const sheetBody = $("#sheet-body");
  const sheetFoot = $("#sheet-foot");

  function openSheet({ title, bodyHTML, footHTML, onMount }) {
    sheetTitle.textContent = title;
    sheetBody.innerHTML = bodyHTML || "";
    if (footHTML) { sheetFoot.innerHTML = footHTML; sheetFoot.hidden = false; }
    else          { sheetFoot.innerHTML = "";     sheetFoot.hidden = true; }
    sheet.dataset.open = "true";
    sheet.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    if (typeof onMount === "function") onMount();
  }
  function closeSheet() {
    sheet.dataset.open = "false";
    sheet.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }
  $(".sheet-close").addEventListener("click", closeSheet);

  // Swipe-down to dismiss
  (() => {
    let startY = null;
    sheet.addEventListener("touchstart", (e) => {
      if (sheet.scrollTop > 0) { startY = null; return; }
      startY = e.touches[0].clientY;
    }, { passive: true });
    sheet.addEventListener("touchmove", (e) => {
      if (startY == null) return;
      const dy = e.touches[0].clientY - startY;
      if (dy > 0) sheet.style.transform = `translateY(${Math.min(dy, 240)}px)`;
    }, { passive: true });
    sheet.addEventListener("touchend", (e) => {
      if (startY == null) return;
      const dy = (e.changedTouches[0].clientY) - startY;
      sheet.style.transform = "";
      startY = null;
      if (dy > 80) closeSheet();
    });
  })();

  // ---------- Sheet contents ----------
  const sheetHandlers = {
    "admin-token": () => openSheet({
      title: "管理员令牌",
      bodyHTML: `
        <div class="field">
          <label for="f-token">X-Admin-Token</label>
          <input id="f-token" type="password" autocomplete="off" placeholder="来自 .env 的 ADMIN_API_TOKEN" />
        </div>
        <p style="color:var(--muted); font-size:13px">保存后仅存于本浏览器 localStorage。所有写操作（模式切换 / 紧停 / 配置保存）都会带上此令牌。</p>
      `,
      footHTML: `<button class="secondary" data-act="clear">清除</button><button data-act="save">保存</button>`,
      onMount: () => {
        $("#f-token").value = getToken();
        sheetFoot.addEventListener("click", (e) => {
          const act = e.target.dataset.act;
          if (act === "save") { setToken($("#f-token").value.trim()); toast("已保存", "ok"); closeSheet(); }
          if (act === "clear") { setToken(""); toast("已清除", "ok"); closeSheet(); }
        }, { once: true });
      },
    }),
    config: async () => {
      openSheet({ title: "运行时参数", bodyHTML: `<p style="color:var(--muted)">加载中…</p>` });
      try {
        const cfg = await apiGet("/config");
        const fields = [
          ["min_net_edge_bps", "最小净 edge（基点）"],
          ["per_trade_notional_usdt", "单笔最大金额（USDT）"],
          ["max_notional_usdt", "总名义上限（USDT）"],
          ["cooldown_sec", "冷却时间（秒）"],
          ["max_consecutive_failures", "连续失败阈值"],
          ["poll_interval_ms", "盘口轮询间隔（ms）"],
        ];
        sheetBody.innerHTML = fields.map(([k, label]) => `
          <div class="field">
            <label for="f-${k}">${label}</label>
            <input id="f-${k}" name="${k}" value="${escapeHtml(String(cfg[k] ?? ""))}" inputmode="decimal" />
          </div>
        `).join("");
        sheetFoot.innerHTML = `<button class="secondary" data-act="reload">从 .env 重载</button><button data-act="save">保存</button>`;
        sheetFoot.hidden = false;
        sheetFoot.onclick = async (e) => {
          const act = e.target.dataset.act;
          if (act === "save") {
            const patch = {};
            fields.forEach(([k]) => {
              const v = $(`#f-${k}`).value.trim();
              if (v === "") return;
              const n = Number(v);
              patch[k] = Number.isFinite(n) && !/[^0-9.\-]/.test(v) ? n : v;
            });
            try { await apiPost("/config", patch); toast("已保存", "ok"); closeSheet(); }
            catch (err) { toast(err.message, "err"); }
          }
          if (act === "reload") {
            try { await apiPost("/config/reload"); toast("已从 .env 重载", "ok"); closeSheet(); }
            catch (err) { toast(err.message, "err"); }
          }
        };
      } catch (err) {
        sheetBody.innerHTML = `<p style="color:var(--danger)">加载失败：${escapeHtml(err.message)}</p>`;
      }
    },
    opps: async () => {
      openSheet({ title: "所有当前机会", bodyHTML: `<p style="color:var(--muted)">加载中…</p>` });
      try {
        const data = await apiGet("/opportunities/cross?limit=50");
        const items = (data.items || data || []);
        sheetBody.innerHTML = items.length
          ? `<ul class="list">${items.map(renderOppItem).join("")}</ul>`
          : `<p style="color:var(--muted)">暂无机会</p>`;
      } catch (err) {
        sheetBody.innerHTML = `<p style="color:var(--danger)">加载失败：${escapeHtml(err.message)}</p>`;
      }
    },
    hedges: async () => {
      openSheet({ title: "所有进行中对冲", bodyHTML: `<p style="color:var(--muted)">加载中…</p>` });
      try {
        const data = await apiGet("/hedges/active?limit=50");
        const items = (data.items || data || []);
        sheetBody.innerHTML = items.length
          ? `<ul class="list">${items.map(renderHedgeItem).join("")}</ul>`
          : `<p style="color:var(--muted)">暂无活跃对冲</p>`;
      } catch (err) {
        sheetBody.innerHTML = `<p style="color:var(--danger)">加载失败：${escapeHtml(err.message)}</p>`;
      }
    },
    reconcile: () => openSheet({
      title: "账实核对",
      bodyHTML: `<p style="color:var(--muted)">点击执行后会立刻拉取各交易所余额并与本地记录对比，结果显示在此处。</p><pre id="rec-out" class="mono" style="white-space:pre-wrap;color:var(--text);font-size:12px"></pre>`,
      footHTML: `<button data-act="run">执行核对</button>`,
      onMount: () => {
        sheetFoot.onclick = async (e) => {
          if (e.target.dataset.act !== "run") return;
          e.target.disabled = true;
          try {
            const r = await apiPost("/control/reconcile");
            $("#rec-out").textContent = JSON.stringify(r, null, 2);
            toast("核对完成", "ok");
          } catch (err) { toast(err.message, "err"); }
          finally { e.target.disabled = false; }
        };
      },
    }),
    credentials: () => openSheet({
      title: "API 密钥",
      bodyHTML: `<p style="color:var(--muted)">出于安全考虑，交易所 API 密钥的配置仍仅在桌面完整面板内进行。点击下方按钮跳转。</p>`,
      footHTML: `<button data-act="go">前往桌面面板</button>`,
      onMount: () => {
        sheetFoot.onclick = (e) => { if (e.target.dataset.act === "go") location.href = "/"; };
      },
    }),
    reports: async () => {
      openSheet({ title: "24h 报表", bodyHTML: `<p style="color:var(--muted)">加载中…</p>` });
      try {
        const r = await apiGet("/reports/summary?hours=24");
        sheetBody.innerHTML = `<pre class="mono" style="white-space:pre-wrap;font-size:12px;color:var(--text)">${escapeHtml(JSON.stringify(r, null, 2))}</pre>`;
      } catch (err) {
        sheetBody.innerHTML = `<p style="color:var(--danger)">加载失败：${escapeHtml(err.message)}</p>`;
      }
    },
  };

  $$('[data-sheet]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const h = sheetHandlers[btn.dataset.sheet];
      if (h) h();
    });
  });

  // ---------- Renderers ----------
  function renderOppItem(o) {
    const bps = Number(o.net_edge_bps ?? o.edge_bps ?? 0);
    const cls = bps >= 0 ? "pos" : "neg";
    const sym = o.symbol || o.pair || "—";
    const buy = o.buy_exchange || o.buy || "—";
    const sell = o.sell_exchange || o.sell || "—";
    const notional = fmtNum(o.notional_usdt ?? o.notional, 0);
    return `<li><div class="opp-item">
      <div class="opp-main"><span>${escapeHtml(sym)}</span><span class="opp-bps ${cls}">${bps >= 0 ? "+" : ""}${bps.toFixed(1)} bps</span></div>
      <div class="opp-sub">${escapeHtml(buy)} → ${escapeHtml(sell)}${notional ? " · " + notional + " USDT" : ""}</div>
    </div></li>`;
  }
  function renderHedgeItem(h) {
    const sym = h.symbol || h.pair || "—";
    const state_ = h.state || h.status || "—";
    return `<li><div class="hedge-item">
      <div class="hedge-main"><span>${escapeHtml(sym)}</span><span class="mono">${escapeHtml(state_)}</span></div>
      <div class="hedge-sub">${escapeHtml(h.buy_exchange || "—")} → ${escapeHtml(h.sell_exchange || "—")}</div>
    </div></li>`;
  }

  function fmtNum(n, digits = 2) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "—";
    return x.toLocaleString(undefined, { maximumFractionDigits: digits });
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  }

  // ---------- Top status aggregation ----------
  function updateTopStatus() {
    const chip = $("#status-chip");
    const text = $(".status-text", chip);
    let kind = "ok", msg = "运行中";
    if (state.killSwitch) { kind = "danger"; msg = "紧停"; }
    else if (state.circuitBreaker === "OPEN" || state.circuitBreaker === true) { kind = "danger"; msg = "熔断"; }
    else if (state.paused) { kind = "warn"; msg = "已暂停"; }
    else if (state.mode === "live") { kind = "warn"; msg = "实盘运行"; }
    chip.dataset.state = kind;
    text.textContent = msg;
  }

  function updateBottomBar() {
    const modeMap = { scan_only: "仅扫描", paper: "模拟", live: "实盘", dry_run: "仅扫描" };
    $("#btn-mode-v").textContent = modeMap[state.mode] || state.mode || "—";
    $("#btn-mode").dataset.active = String(state.mode === "live");
    $("#btn-pause-v").textContent = state.paused ? "已暂停" : "运行中";
    $("#btn-pause").dataset.active = String(!!state.paused);
    $("#btn-kill-v").textContent = state.killSwitch ? "已触发" : "待命";
    $("#btn-kill").dataset.active = String(!!state.killSwitch);
  }

  // ---------- Data fetch loops ----------
  async function refreshControl() {
    try {
      const c = await apiGet("/control");
      state.mode = c.mode ?? state.mode;
      state.paused = !!c.paused;
      state.killSwitch = !!c.kill_switch;
      state.circuitBreaker = c.circuit_breaker ?? c.cb_state ?? null;
      updateTopStatus();
      updateBottomBar();
    } catch (e) { /* transient */ }
  }

  async function refreshLatency() {
    try {
      const data = await apiGet("/health/latency");
      const items = data.exchanges || data.items || data || [];
      state.latency = items;
      const host = $("#m-latency");
      if (!items.length) { host.innerHTML = `<span class="chip chip--dim">无数据</span>`; return; }
      host.innerHTML = items.map((e) => {
        const ms = Number(e.latency_ms ?? e.rtt_ms);
        let cls = "chip--ok";
        if (!Number.isFinite(ms)) cls = "chip--dim";
        else if (ms >= 300) cls = "chip--danger";
        else if (ms >= 100) cls = "chip--warn";
        const label = e.exchange || e.name || "?";
        const val = Number.isFinite(ms) ? `${ms.toFixed(0)}ms` : "—";
        return `<span class="chip ${cls}">${escapeHtml(label)} ${val}</span>`;
      }).join("");
      // update dashboard summary with min/max latency
      const valid = items.map((e) => Number(e.latency_ms ?? e.rtt_ms)).filter(Number.isFinite);
      if (valid.length) {
        const mn = Math.min(...valid), mx = Math.max(...valid);
        $("#dash-summary").textContent = `延迟 ${mn.toFixed(0)}–${mx.toFixed(0)}ms`;
      }
    } catch (e) { /* ignore */ }
  }

  async function refreshDashboard() {
    // Active hedges count
    try {
      const h = await apiGet("/hedges/active?limit=1");
      const n = h.total ?? (h.items ? h.items.length : (Array.isArray(h) ? h.length : 0));
      $("#m-active-hedges").textContent = String(n);
      $("#hedges-count").textContent = `${n} 个活跃`;
    } catch { /* ignore */ }

    // Reports (pnl + slippage)
    try {
      const r = await apiGet("/reports/summary?hours=24");
      const pnl = r.realized_pnl_usdt ?? r.pnl_usdt ?? r.realized_pnl;
      const slip = r.avg_slippage_bps ?? r.slippage_bps;
      $("#m-pnl").textContent = Number.isFinite(Number(pnl)) ? `${Number(pnl) >= 0 ? "+" : ""}${fmtNum(pnl)} USDT` : "—";
      $("#m-slippage").textContent = Number.isFinite(Number(slip)) ? `${Number(slip).toFixed(1)} bps` : "—";
    } catch { /* ignore */ }

    // Scan status from control
    $("#m-scan").innerHTML = state.paused
      ? `<span class="chip chip--warn">已暂停</span>`
      : `<span class="chip chip--ok">运行中</span>`;
  }

  async function refreshOpps() {
    try {
      const data = await apiGet("/opportunities/cross?limit=3");
      const items = (data.items || data || []).slice(0, 3);
      const list = $("#opps-list");
      list.innerHTML = items.length ? items.map(renderOppItem).join("") : `<li class="empty">暂无</li>`;
      $("#opps-count").textContent = items.length ? `最新 ${items.length} 条` : "暂无";
    } catch { /* ignore */ }
  }

  async function refreshHedges() {
    try {
      const data = await apiGet("/hedges/active?limit=3");
      const items = (data.items || data || []).slice(0, 3);
      const list = $("#hedges-list");
      list.innerHTML = items.length ? items.map(renderHedgeItem).join("") : `<li class="empty">暂无</li>`;
    } catch { /* ignore */ }
  }

  async function refreshStrategies() {
    try {
      const data = await apiGet("/strategies");
      const items = data.items || data || [];
      const ul = $("#strat-body");
      if (!items.length) { ul.innerHTML = `<li class="empty">暂无策略</li>`; return; }
      ul.innerHTML = items.map((s) => `
        <li><button class="strategy-row" type="button" data-sid="${escapeHtml(s.id || s.name)}">
          <span class="strategy-name">${escapeHtml(s.display_name || s.name || s.id)}</span>
          <span class="strategy-state" data-on="${!!s.enabled}">${s.enabled ? "已启用" : "已停用"}</span>
        </button></li>
      `).join("");
      const on = items.filter((s) => s.enabled).length;
      $("#strat-summary").textContent = `${on}/${items.length} 启用`;
      ul.onclick = async (e) => {
        const btn = e.target.closest("[data-sid]");
        if (!btn) return;
        const id = btn.dataset.sid;
        const s = items.find((x) => (x.id || x.name) === id);
        if (!s) return;
        try {
          await apiPost(`/strategies/${encodeURIComponent(id)}/${s.enabled ? "disable" : "enable"}`);
          toast(`${s.display_name || id}：已${s.enabled ? "停用" : "启用"}`, "ok");
          refreshStrategies();
        } catch (err) { toast(err.message, "err"); }
      };
    } catch { /* ignore */ }
  }

  // ---------- Bottom bar actions ----------
  $("#btn-mode").addEventListener("click", async () => {
    if (!getToken()) { toast("请先设置管理员令牌", "err"); sheetHandlers["admin-token"](); return; }
    const order = ["scan_only", "paper", "live"];
    const cur = order.indexOf(state.mode);
    const next = order[(cur + 1 + order.length) % order.length];
    if (next === "live" && !confirm("切换到【实盘交易】模式？会进行真实下单。")) return;
    try { await apiPost("/control/mode", { mode: next }); toast(`已切到 ${next}`, "ok"); refreshControl(); }
    catch (err) { toast(err.message, "err"); }
  });

  $("#btn-pause").addEventListener("click", async () => {
    if (!getToken()) { toast("请先设置管理员令牌", "err"); sheetHandlers["admin-token"](); return; }
    try {
      await apiPost(state.paused ? "/control/resume" : "/control/pause");
      toast(state.paused ? "已恢复" : "已暂停", "ok");
      refreshControl();
    } catch (err) { toast(err.message, "err"); }
  });

  $("#btn-kill").addEventListener("click", async () => {
    if (!getToken()) { toast("请先设置管理员令牌", "err"); sheetHandlers["admin-token"](); return; }
    const turningOn = !state.killSwitch;
    if (turningOn && !confirm("确认触发【紧急停机】？所有新机会将被拒绝。")) return;
    try {
      await apiPost(turningOn ? "/control/kill" : "/control/unkill");
      toast(turningOn ? "已紧停" : "已解除紧停", "ok");
      refreshControl();
    } catch (err) { toast(err.message, "err"); }
  });

  $("#status-chip").addEventListener("click", () => {
    // Open dashboard if collapsed so user can inspect detail
    const head = $("#card-dashboard .card-head");
    if (head && head.getAttribute("aria-expanded") === "false") {
      head.click();
      $("#card-dashboard").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  // ---------- Init ----------
  function init() {
    setupCollapsibles();
    refreshControl();
    refreshLatency();
    refreshOpps();
    refreshHedges();
    refreshStrategies();
    refreshDashboard();

    setInterval(refreshControl,   5_000);
    setInterval(refreshLatency,  10_000);
    setInterval(refreshOpps,      3_000);
    setInterval(refreshHedges,    5_000);
    setInterval(refreshStrategies, 30_000);
    setInterval(refreshDashboard, 10_000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

/**
 * 成员银行管理视图。
 *
 * 所有操作都走同一个后端入口：
 *   bridge.apiPost("bank", { action, ... })
 *   -> POST /api/v1/plugins/extensions/astrbot_plugin_kahunasystem/bank
 */

const PLUGIN_PATH = "bank";

/** 上个月，形如 2026-10 —— 结算默认口径。 */
function lastMonth() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function msg(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = "msg" + (kind ? " " + kind : "");
  el.textContent = text || "";
}

/** bridge SDK 异步注入，缓存一次即可。 */
let bridgePromise = null;
function getBridge(timeoutMs = 8000) {
  if (!bridgePromise) {
    bridgePromise = new Promise((resolve, reject) => {
      const started = Date.now();
      (function check() {
        if (window.AstrBotPluginView) {
          resolve(window.AstrBotPluginView);
          return;
        }
        if (Date.now() - started > timeoutMs) {
          reject(new Error("AstrBot bridge 未就绪"));
          return;
        }
        setTimeout(check, 50);
      })();
    }).then(async (b) => {
      await b.ready();
      return b;
    });
  }
  return bridgePromise;
}

async function api(action, extra = {}) {
  const bridge = await getBridge();
  const result = await bridge.apiPost(PLUGIN_PATH, { action, ...extra });
  if (result && result.ok === false && result.message) {
    throw new Error(result.message);
  }
  return result || {};
}

/** 把 "15亿" / "1.5b" / "1500000000" 解析成 ISK 整数。 */
function parseIsk(raw) {
  if (raw === null || raw === undefined) return NaN;
  const text = String(raw).trim().toLowerCase().replace(/[,_\s]/g, "");
  if (!text) return NaN;
  const m = text.match(/^(\d+(?:\.\d+)?)([embk]?)$/);
  if (!m) return NaN;
  const base = { "": 1, k: 1e3, m: 1e6, e: 1e8, b: 1e9 }[m[2]];
  return Math.round(parseFloat(m[1]) * base);
}

// ---------------- 账户 ----------------

let accountsCache = [];

function renderAccounts() {
  const keyword = document.getElementById("accounts-filter").value.trim().toLowerCase();
  const rows = accountsCache.filter((a) =>
    !keyword ||
    String(a.character).toLowerCase().includes(keyword) ||
    String(a.qq_id).includes(keyword));

  const body = document.getElementById("accounts-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8" class="empty">${
      accountsCache.length ? "没有匹配的账户" : "还没有账户"}</td></tr>`;
  } else {
    body.innerHTML = rows.map((a) => `
      <tr>
        <td>${escapeHtml(a.character) || "<span class=muted>（未命名）</span>"}</td>
        <td class="muted">${escapeHtml(a.qq_id)}</td>
        <td class="num">${escapeHtml(a.balance_text)}</td>
        <td class="num">${escapeHtml(a.allowance_text)}</td>
        <td class="num">${escapeHtml(a.monthly_reward_text)}</td>
        <td class="num"><b>${escapeHtml(a.total_text)}</b></td>
        <td><span class="chip ${a.status === "unclaimed" ? "warn" : "ok"}">${
          a.status === "unclaimed" ? "待认领" : "正常"}</span></td>
        <td>
          <div class="row-actions">
            <button class="ghost" data-act="ledger" data-qq="${escapeHtml(a.qq_id)}"
                    data-name="${escapeHtml(a.character)}">流水</button>
            <button class="ghost" data-act="allowance" data-qq="${escapeHtml(a.qq_id)}"
                    data-name="${escapeHtml(a.character)}">发津贴</button>
            <button class="ghost" data-act="adjust" data-qq="${escapeHtml(a.qq_id)}"
                    data-name="${escapeHtml(a.character)}">调余额</button>
          </div>
        </td>
      </tr>`).join("");
  }

  const totalBalance = accountsCache.reduce((s, a) => s + a.balance, 0);
  const totalAllowance = accountsCache.reduce((s, a) => s + a.allowance, 0);
  const totalReward = accountsCache.reduce((s, a) => s + a.monthly_reward, 0);
  const unclaimed = accountsCache.filter((a) => a.status === "unclaimed").length;
  const fmt = (v) => {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "b";
    if (a >= 1e8) return (v / 1e8).toFixed(2) + "e";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "m";
    return String(v);
  };
  document.getElementById("accounts-cards").innerHTML = `
    <div class="card"><div class="k">余额合计</div><div class="v">${fmt(totalBalance)}</div></div>
    <div class="card"><div class="k">津贴合计</div><div class="v">${fmt(totalAllowance)}</div></div>
    <div class="card"><div class="k">当月奖励待结转</div><div class="v">${fmt(totalReward)}</div></div>
    <div class="card"><div class="k">账户数 / 待认领</div><div class="v">${accountsCache.length} <span class="muted" style="font-size:12px">/ ${unclaimed}</span></div></div>`;
  document.getElementById("accounts-summary").textContent = `共 ${accountsCache.length} 个账户`;
}

async function loadAccounts() {
  msg("accounts-msg", "加载中…");
  try {
    const data = await api("accounts");
    accountsCache = data.accounts || [];
    renderAccounts();
    msg("accounts-msg", "");
  } catch (err) {
    msg("accounts-msg", "加载失败：" + err.message, "err");
  }
}

async function onAccountAction(event) {
  const btn = event.target.closest("button[data-act]");
  if (!btn) return;
  const { act, qq, name } = btn.dataset;
  if (act === "ledger") {
    switchTab("ledger");
    document.getElementById("ledger-keyword").value = qq;
    document.getElementById("ledger-period").value = "";
    document.getElementById("ledger-kind").value = "";
    loadLedger();
    return;
  }
  if (act === "allowance") {
    const raw = prompt(`给 ${name}（${qq}）发放额外津贴，额度：\n支持 1.5b / 5e / 1500000000`);
    const amount = parseIsk(raw);
    if (!Number.isFinite(amount) || amount <= 0) {
      if (raw !== null) msg("accounts-msg", "额度格式不对", "err");
      return;
    }
    try {
      const res = await api("allowance", { qq_id: qq, amount });
      msg("accounts-msg", res.message || "已发放津贴", "ok");
      await loadAccounts();
    } catch (err) {
      msg("accounts-msg", "发放失败：" + err.message, "err");
    }
    return;
  }
  if (act === "adjust") {
    const raw = prompt(`调整 ${name}（${qq}）的余额，正数增加、负数减少：\n支持 -1b / 5e / 1500000000`);
    const amount = parseIsk(raw);
    if (!Number.isFinite(amount) || amount === 0) {
      if (raw !== null) msg("accounts-msg", "额度格式不对（不能为 0）", "err");
      return;
    }
    try {
      const res = await api("adjust", { qq_id: qq, amount });
      msg("accounts-msg", res.message || "已调整", "ok");
      await loadAccounts();
    } catch (err) {
      msg("accounts-msg", "调整失败：" + err.message, "err");
    }
  }
}

// ---------------- 订单 ----------------

async function loadOrders() {
  const body = document.getElementById("orders-body");
  msg("orders-msg", "加载中…");
  try {
    const data = await api("orders", { status: "pending" });
    const list = data.orders || [];
    body.innerHTML = list.length
      ? list.map((o) => `
        <tr>
          <td>#${o.id}</td>
          <td>${escapeHtml(o.character) || "<span class=muted>—</span>"}</td>
          <td class="muted">${escapeHtml(o.qq_id)}</td>
          <td>${escapeHtml(o.item)}</td>
          <td class="num">${o.quantity}</td>
          <td class="muted">${escapeHtml(o.created_at)}</td>
          <td>
            <div class="row-actions">
              <button class="ghost" data-confirm="${o.id}">确认</button>
              <button class="ghost danger" data-reject="${o.id}">驳回</button>
            </div>
          </td>
        </tr>`).join("")
      : `<tr><td colspan="7" class="empty">没有待处理的订单</td></tr>`;
    msg("orders-msg", `共 ${list.length} 条待处理`);
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7" class="empty">加载失败</td></tr>`;
    msg("orders-msg", "加载失败：" + err.message, "err");
  }
}

async function onOrderAction(event) {
  const confirmBtn = event.target.closest("button[data-confirm]");
  const rejectBtn = event.target.closest("button[data-reject]");
  if (confirmBtn) {
    const id = Number(confirmBtn.dataset.confirm);
    const raw = prompt(`订单 #${id} 的消费额度：\n支持 15e / 1.5b / 1500000000`);
    if (raw === null) return;
    const amount = parseIsk(raw);
    if (!Number.isFinite(amount) || amount <= 0) {
      msg("orders-msg", "额度格式不对", "err");
      return;
    }
    try {
      const res = await api("order_confirm", { order_id: id, amount });
      msg("orders-msg",
        `订单 #${id} 已扣款 ${res.amount_text}（奖励 ${res.breakdown_text?.monthly_reward || 0} / ` +
        `津贴 ${res.breakdown_text?.allowance || 0} / 余额 ${res.breakdown_text?.balance || 0}），` +
        `剩余合计 ${res.total_text}`, "ok");
      await loadOrders();
    } catch (err) {
      msg("orders-msg", "确认失败：" + err.message, "err");
    }
    return;
  }
  if (rejectBtn) {
    const id = Number(rejectBtn.dataset.reject);
    if (!confirm(`确定驳回订单 #${id}？驳回后不扣款。`)) return;
    try {
      const res = await api("order_reject", { order_id: id });
      msg("orders-msg", res.message || "已驳回", "ok");
      await loadOrders();
    } catch (err) {
      msg("orders-msg", "驳回失败：" + err.message, "err");
    }
  }
}

// ---------------- 历史导入 ----------------

function renderImportPreview(data) {
  const parts = [];
  parts.push(`<div class="muted">解析 ${data.row_count} 行；余额合计 ${escapeHtml(data.total_balance_text)}，`
    + `津贴合计 ${escapeHtml(data.total_allowance_text)}，作废 ${escapeHtml(data.total_wasted_text)}</div>`);
  if (data.capped && data.capped.length) {
    parts.push(`<details class="result" open><summary>压降清单（${data.capped.length} 人超 2b）</summary><ul class="list">`
      + data.capped.map((c) => `<li>${escapeHtml(c.character)}：声明 ${escapeHtml(c.declared_text)} → 入账 ${escapeHtml(c.applied_text)}，作废 <b>${escapeHtml(c.wasted_text)}</b></li>`).join("")
      + `</ul></details>`);
  }
  if (data.mismatched && data.mismatched.length) {
    parts.push(`<details class="result"><summary>末列「结余」与推算不符（${data.mismatched.length} 行，仅提示）</summary><ul class="list">`
      + data.mismatched.map((m) => `<li>${escapeHtml(m.character)}：表内 ${escapeHtml(m.declared_text)}，推算 ${escapeHtml(m.expected_text)}</li>`).join("")
      + `</ul></details>`);
  }
  parts.push(`<details class="result"><summary>逐行明细（${data.accounts.length}）</summary><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>角色</th><th class="num">余额</th><th class="num">津贴</th><th class="num">当月奖励</th><th class="num">合计</th></tr></thead><tbody>`
    + data.accounts.map((a) => `<tr><td>${escapeHtml(a.character)}</td><td class="num">${escapeHtml(a.balance_text)}</td><td class="num">${escapeHtml(a.allowance_text)}</td><td class="num">${escapeHtml(a.monthly_reward_text)}</td><td class="num">${escapeHtml(a.total_text)}</td></tr>`).join("")
    + `</tbody></table></div></details>`);
  document.getElementById("import-result").innerHTML = parts.join("");
}

async function onImportPreview() {
  const text = document.getElementById("import-text").value;
  if (!text.trim()) {
    msg("import-msg", "先粘贴表格内容", "err");
    return;
  }
  msg("import-msg", "解析中…");
  document.getElementById("import-apply").disabled = true;
  try {
    const data = await api("import_preview", { text });
    renderImportPreview(data);
    msg("import-msg", "解析完成，核对无误后再点「确认导入」", "ok");
    document.getElementById("import-apply").disabled = false;
  } catch (err) {
    msg("import-msg", "解析失败：" + err.message, "err");
  }
}

async function onImportApply() {
  const text = document.getElementById("import-text").value;
  const period = document.getElementById("import-period").value.trim();
  if (!period) {
    msg("import-msg", "请填写结算期间（YYYY-MM）", "err");
    return;
  }
  if (!confirm(`确定把这份台账导入？期间 ${period}。\n重复导入不会重复记账。`)) return;
  msg("import-msg", "导入中…");
  try {
    const res = await api("import_apply", { text, period });
    msg("import-msg", res.message || "导入完成", "ok");
    document.getElementById("import-apply").disabled = true;
    await loadAccounts();
  } catch (err) {
    msg("import-msg", "导入失败：" + err.message, "err");
  }
}

// ---------------- 结算 ----------------

function renderSettle(data, applied) {
  const parts = [];
  const alreadyCount = data.already_reward_count ?? 0;
  parts.push(`<div class="cards">
    <div class="card"><div class="k">${applied ? "已发放" : "将发放"}人数</div><div class="v">${data.granted_count}</div></div>
    <div class="card"><div class="k">${applied ? "已发放" : "将发放"}合计</div><div class="v">${escapeHtml(data.total_reward_text)}</div></div>
    <div class="card"><div class="k">作废（超 2b）</div><div class="v">${escapeHtml(data.total_wasted_text)}</div></div>
    <div class="card"><div class="k">跳过</div><div class="v">${data.skipped_count ?? "—"}</div></div>
    <div class="card"><div class="k">流水已发放</div><div class="v">${alreadyCount} <span class="muted" style="font-size:12px">人</span></div><div class="k">${escapeHtml(data.already_reward_text || "0")}</div></div>
  </div>`);
  if (!applied && alreadyCount > 0) {
    parts.push(`<div class="msg" style="color:var(--warn)">该期间流水里已有 ${alreadyCount} 条发放记录（${escapeHtml(data.already_reward_text)}）。重复执行只会补齐缺失成员、不会重复发钱；若 PAP 数据被修正，需先冲正再重算。</div>`);
  }
  if (data.granted && data.granted.length) {
    parts.push(`<details class="result" open><summary>发放明细（${data.granted.length}）</summary><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>角色</th><th class="num">Start PAP</th><th class="num">奖励</th></tr></thead><tbody>`
      + data.granted.map((g) => `<tr><td>${escapeHtml(g.character)}</td><td class="num">${g.strat_pap}</td><td class="num">${escapeHtml(g.reward_text)}</td></tr>`).join("")
      + `</tbody></table></div></details>`);
  }
  if (data.notes && data.notes.length) {
    parts.push(`<div class="muted" style="margin-top:8px">${data.notes.map(escapeHtml).join("；")}</div>`);
  }
  document.getElementById("settle-result").innerHTML = parts.join("");
}

async function onSettlePreview() {
  const period = document.getElementById("settle-period").value.trim();
  if (!period) { msg("settle-msg", "请填写 PAP 月份（YYYY-MM）", "err"); return; }
  msg("settle-msg", "拉取 PAP 并计算中…");
  try {
    const data = await api("settle_preview", { period });
    renderSettle(data, false);
    msg("settle-msg", "预览完成，未动账。核对无误后再执行发放。", "ok");
  } catch (err) {
    msg("settle-msg", "预览失败：" + err.message, "err");
  }
}

async function onSettleApply() {
  const period = document.getElementById("settle-period").value.trim();
  if (!period) { msg("settle-msg", "请填写 PAP 月份（YYYY-MM）", "err"); return; }
  if (!confirm(`确定按 ${period} 的 PAP 执行发放？\n会先把上月奖励结转入余额，再发本月奖励。`)) return;
  msg("settle-msg", "执行中…");
  try {
    const data = await api("settle_apply", { period });
    renderSettle(data, true);
    msg("settle-msg", "发放完成", "ok");
    await loadAccounts();
  } catch (err) {
    msg("settle-msg", "执行失败：" + err.message, "err");
  }
}

// ---------------- 流水 ----------------

function fmtIskShort(v) {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e9) return sign + (a / 1e9).toFixed(2) + "b";
  if (a >= 1e8) return sign + (a / 1e8).toFixed(2) + "e";
  if (a >= 1e6) return sign + (a / 1e6).toFixed(2) + "m";
  return sign + a;
}

function renderLedger(data) {
  const list = data.entries || [];
  const body = document.getElementById("ledger-body");
  if (!list.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">没有匹配的流水</td></tr>`;
  } else {
    body.innerHTML = list.map((e) => `
      <tr>
        <td class="muted">${escapeHtml(e.created_at)}</td>
        <td>${escapeHtml(e.character) || "<span class='muted'>—</span>"}</td>
        <td class="muted">${escapeHtml(e.qq_id)}</td>
        <td><span class="chip">${escapeHtml(e.kind_label)}</span></td>
        <td class="num" style="color:${e.amount < 0 ? "var(--err)" : "var(--ok)"}">${escapeHtml(e.amount_text)}</td>
        <td class="num">${escapeHtml(e.balance_text)}</td>
        <td class="num">${escapeHtml(e.allowance_text)}</td>
        <td>${escapeHtml(e.note)}${e.order_id ? ` <span class="muted">#${e.order_id}</span>` : ""}</td>
        <td class="muted">${escapeHtml(e.operator)}</td>
      </tr>`).join("");
  }
  const income = list.filter((e) => e.amount > 0).reduce((s, e) => s + e.amount, 0);
  const expense = list.filter((e) => e.amount < 0).reduce((s, e) => s - e.amount, 0);
  document.getElementById("ledger-summary").textContent =
    `共 ${list.length} 条 ｜ 收入 ${fmtIskShort(income)} ｜ 支出 ${fmtIskShort(expense)}`;
}

async function loadLedger() {
  msg("ledger-msg", "加载中…");
  try {
    const data = await api("ledger", {
      keyword: document.getElementById("ledger-keyword").value.trim(),
      period: document.getElementById("ledger-period").value.trim(),
      kind: document.getElementById("ledger-kind").value,
    });
    renderLedger(data);
    msg("ledger-msg", "");
  } catch (err) {
    msg("ledger-msg", "加载失败：" + err.message, "err");
  }
}

function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "accounts") loadAccounts();
  if (name === "ledger") loadLedger();
  if (name === "orders") loadOrders();
}

// ---------------- 初始化 ----------------

function initTabs() {
  document.querySelectorAll("#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function init() {
  initTabs();

  document.getElementById("accounts-refresh").addEventListener("click", loadAccounts);
  document.getElementById("accounts-filter").addEventListener("input", renderAccounts);
  document.getElementById("accounts-body").addEventListener("click", onAccountAction);

  document.getElementById("orders-refresh").addEventListener("click", loadOrders);
  document.getElementById("orders-body").addEventListener("click", onOrderAction);

  document.getElementById("ledger-refresh").addEventListener("click", loadLedger);
  document.getElementById("ledger-kind").addEventListener("change", loadLedger);
  document.getElementById("ledger-period").addEventListener("change", loadLedger);
  document.getElementById("ledger-keyword").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadLedger();
  });
  document.getElementById("ledger-clear").addEventListener("click", () => {
    document.getElementById("ledger-keyword").value = "";
    document.getElementById("ledger-period").value = "";
    document.getElementById("ledger-kind").value = "";
    loadLedger();
  });

  document.getElementById("import-preview").addEventListener("click", onImportPreview);
  document.getElementById("import-apply").addEventListener("click", onImportApply);

  document.getElementById("settle-preview").addEventListener("click", onSettlePreview);
  document.getElementById("settle-apply").addEventListener("click", onSettleApply);

  const period = lastMonth();
  document.getElementById("settle-period").value = period;
  document.getElementById("import-period").value = period;

  loadAccounts();
}

init();

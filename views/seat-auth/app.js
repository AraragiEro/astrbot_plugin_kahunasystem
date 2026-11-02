/**
 * SeAT 授权视图的交互逻辑。
 *
 * 用户在两个输入框里分别粘贴 seat_session 的值与完整的 remember_web_* 条目，
 * 前端拼成 `name=value; name2=value2` 后用 bridge 交给插件后端校验。
 *
 * bridge.apiPost("seat/validate", { cookie }) 会被 Dashboard 转发到
 *   POST /api/v1/plugins/extensions/<插件名>/seat/validate
 */

const STATUS_LABELS = {
  idle: "未验证",
  pending: "正在验证…",
  ok: "验证通过",
  expired: "Cookie 已失效",
  blocked: "被 Cloudflare 拦截",
  error: "验证失败",
};

const sessionInput = document.getElementById("session");
const rememberInput = document.getElementById("remember");
const button = document.getElementById("btn");
const statusEl = document.getElementById("status");

/** 等待 bridge SDK 就绪（SDK 注入是异步的，这里给它最多 5 秒）。 */
function waitBridge(timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
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
  });
}

function setStatus(kind, text) {
  statusEl.className = "status" + (kind && kind !== "idle" ? " " + kind : "");
  statusEl.textContent = text || STATUS_LABELS[kind] || "";
}

/**
 * 把两个输入框拼成 Cookie 字符串。
 *
 * - seat_session：允许只填值（自动补前缀），也允许贴完整的 `seat_session=...`；
 * - remember_web_*：名字带随机 hash，无法替用户补全，必须粘贴完整的 `名字=值`。
 */
function buildCookie(sessionRaw, rememberRaw) {
  const parts = [];
  const session = (sessionRaw || "").trim();
  const remember = (rememberRaw || "").trim();

  if (session) {
    parts.push(session.includes("=") ? session : `seat_session=${session}`);
  }

  if (remember) {
    if (!remember.includes("=")) {
      return {
        error: "「remember_web_*」要粘贴完整的 名字=值（例如 remember_web_xxx=xxxx）",
      };
    }
    parts.push(remember);
  }

  return { cookie: parts.join("; ") };
}

async function onSubmit() {
  const { cookie, error } = buildCookie(sessionInput.value, rememberInput.value);
  if (error) {
    setStatus("error", error);
    return;
  }
  if (!cookie) {
    setStatus("error", "至少填写一项");
    return;
  }

  button.disabled = true;
  setStatus("pending", "正在验证…");

  try {
    const bridge = await waitBridge();
    await bridge.ready();
    const result = await bridge.apiPost("seat/validate", { cookie });
    const kind = result?.status || "error";
    setStatus(kind, result?.message || STATUS_LABELS[kind] || "未知结果");
  } catch (err) {
    setStatus("error", "请求失败：" + (err?.message || err));
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", onSubmit);

// 提前连上 bridge，让主题（明/暗）尽快同步
waitBridge()
  .then((bridge) => bridge.ready())
  .catch(() => {});

/**
 * SeAT 授权视图的交互逻辑。
 *
 * AstrBot 会在页面里注入 bridge SDK（`/api/plugin/page/bridge-sdk.js`），
 * 通过 `window.AstrBotPluginView` 与本插件后端通信：
 *   bridge.apiPost("seat/validate", { cookie })
 * 会被 Dashboard 转发到
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

const cookieInput = document.getElementById("cookie");
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

async function onSubmit() {
  const cookie = cookieInput.value.trim();
  if (!cookie) {
    setStatus("error", "请先粘贴 Cookie");
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
  } catch (error) {
    setStatus("error", "请求失败：" + (error?.message || error));
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", onSubmit);

// 提前连上 bridge，让主题（明/暗）尽快同步
waitBridge()
  .then((bridge) => bridge.ready())
  .catch(() => {});

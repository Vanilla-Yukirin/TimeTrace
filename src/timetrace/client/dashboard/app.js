const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="timetrace-csrf"]').content;
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "尚无";
const bytes = (n) => n < 1024 ? n + " B" : n < 1048576
  ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
const text = (id, value) => { $(id).textContent = value; };
const form = $("capture-form");
const feed = $("feed");
let settings = null;
let savedTheme = "system";
let newest = null;
let oldest = null;
let hasOlder = false;
let loadingOlder = false;
let refreshing = false;
let endpointSignature = "";
let connection = null;
let originalUrls = new Set();
let pendingCount = 0;
const connectionForm = $("connection-form");

async function json(url, method = "GET", body = {}) {
  const options = {method, credentials: "same-origin", cache: "no-store"};
  if (method !== "GET") {
    options.headers = {"Content-Type": "application/json", "X-TimeTrace-CSRF": csrf};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new Error(detail || "请求失败（HTTP " + response.status + "）");
  }
  return data;
}

document.querySelectorAll(".nav").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll(".nav").forEach((nav) => nav.removeAttribute("aria-current"));
    button.setAttribute("aria-current", "page");
    document.querySelectorAll(".page").forEach((page) => { page.hidden = page.id !== button.dataset.page; });
    if (button.dataset.page === "software") void loadConnection();
  };
});

$("open-connection").onclick = () => {
  document.querySelector('.nav[data-page="software"]').click();
  $("connection-heading").focus();
  connectionForm.scrollIntoView({block: "start"});
};

function newPath(name) {
  return {name, url: "", enabled: true, keep_index: null};
}

function updateSameServer() {
  if (!connection) return;
  const changesAddress = connection.paths.some((path) =>
    path.url.trim() && !originalUrls.has(path.url.trim().replace(/\/+$/, ""))
  );
  $("same-server-field").hidden = connection.address_managed
    || !changesAddress || (!originalUrls.size && !pendingCount);
}

function tokenChange() {
  if ($("clear-token").checked) return "";
  return $("connection-token").value.trim() || null;
}

function makePathButton(label, action, disabled = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "subtle";
  button.textContent = label;
  button.disabled = disabled;
  button.onclick = action;
  return button;
}

function renderConnectionPaths() {
  const rows = connection.paths.map((path, index) => {
    const row = document.createElement("div");
    row.className = "connection-path";
    const managed = connection.address_managed;
    const locked = path.keep_index !== null;
    const name = document.createElement("input");
    name.value = path.name;
    name.maxLength = 80;
    name.required = true;
    name.disabled = managed || locked;
    name.oninput = () => { path.name = name.value; };
    const url = document.createElement("input");
    url.type = "url";
    url.placeholder = "https://example.com";
    url.value = path.url;
    url.maxLength = 2048;
    url.required = true;
    url.disabled = managed || locked;
    url.oninput = () => {
      path.url = url.value;
      $("same-server").checked = false;
      updateSameServer();
    };
    const nameLabel = document.createElement("label");
    nameLabel.append("路径名称", name);
    const urlLabel = document.createElement("label");
    urlLabel.append("服务端地址", url);
    const actions = document.createElement("div");
    actions.className = "connection-path-actions";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = path.enabled;
    enabled.disabled = managed || locked;
    enabled.onchange = () => { path.enabled = enabled.checked; };
    const enabledLabel = document.createElement("label");
    enabledLabel.append(enabled, locked ? "由现有配置管理" : "启用");
    actions.append(enabledLabel);

    const test = makePathButton("测试连接", async () => {
      if (!url.reportValidity()) return;
      test.disabled = true;
      text("connection-result", "正在测试…");
      try {
        await json("/api/connection/test", "POST", {
          url: path.url, token: tokenChange(), same_server: $("same-server").checked,
        });
        text("connection-result", "服务端可访问，上传状态见状态页");
      } catch (error) {
        text("connection-result", error.message);
      } finally {
        test.disabled = false;
      }
    }, locked);
    actions.append(test);
    if (!managed) {
      const move = (offset) => {
        const target = index + offset;
        [connection.paths[index], connection.paths[target]] =
          [connection.paths[target], connection.paths[index]];
        renderConnectionPaths();
      };
      actions.append(
        makePathButton("上移", () => move(-1), index === 0),
        makePathButton("下移", () => move(1), index === connection.paths.length - 1),
        makePathButton("移除", () => {
          connection.paths.splice(index, 1);
          renderConnectionPaths();
        }, locked || connection.paths.length === 1),
      );
    }
    row.append(nameLabel, urlLabel, actions);
    return row;
  });
  $("connection-paths").replaceChildren(...rows);
  $("add-path").disabled = connection.address_managed || connection.paths.length >= 32;
  updateSameServer();
}

function fillConnection(value) {
  originalUrls = new Set(value.paths.map((path) => path.url));
  connection = value;
  if (!connection.paths.length) connection.paths.push(newPath("主连接"));
  $("connection-fields").disabled = false;
  $("connection-token").value = "";
  $("connection-token").disabled = value.token_managed;
  $("clear-token").checked = false;
  $("clear-token-field").hidden = value.token_managed || !value.token_present;
  $("same-server").checked = false;
  text("token-hint", value.token_managed ? "由环境变量管理"
    : value.token_present ? "已设置，留空保留当前 Token" : "填写服务端签发的设备 Token");
  text("connection-hint", value.address_managed ? "服务端地址由环境变量管理" : "按列表顺序尝试可用地址。");
  renderConnectionPaths();
}

async function loadConnection() {
  $("connection-fields").disabled = true;
  $("add-path").disabled = true;
  text("connection-result", "");
  try {
    fillConnection(await json("/api/connection"));
  } catch (error) {
    text("connection-result", error.message);
  }
}

$("add-path").onclick = () => {
  connection.paths.push(newPath(""));
  renderConnectionPaths();
  $("connection-paths").lastElementChild.querySelector("input").focus();
};
$("clear-token").onchange = () => { $("connection-token").disabled = $("clear-token").checked; };
connectionForm.onsubmit = async (event) => {
  event.preventDefault();
  $("connection-fields").disabled = true;
  $("add-path").disabled = true;
  text("connection-result", "正在保存并重连…");
  try {
    fillConnection(await json("/api/connection", "PUT", {
      paths: connection.paths, token: tokenChange(), revision: connection.revision,
      same_server: $("same-server").checked,
    }));
    text("connection-result", "连接设置已保存");
    await refresh();
  } catch (error) {
    text("connection-result", error.message);
  } finally {
    $("connection-fields").disabled = false;
    $("add-path").disabled = connection.address_managed || connection.paths.length >= 32;
  }
};

function updateFields() {
  const smart = $("strategy").value === "smart";
  $("smart-fields").hidden = !smart;
  $("interval-fields").hidden = smart;
  $("quality-field").hidden = $("image-format").value === "png";
}

function fillCapture(value) {
  settings = value;
  Object.entries(value).forEach(([name, field]) => {
    const input = form.elements.namedItem(name);
    if (input?.type === "checkbox") input.checked = field;
    else if (input) input.value = field;
  });
  updateFields();
}

async function loadConfig() {
  const config = await json("/api/config");
  fillCapture(config.capture);
  text("version", "v" + config.version);
  savedTheme = config.theme;
  document.documentElement.dataset.theme = savedTheme;
  $("theme").value = savedTheme;
  $("theme").disabled = false;
  $("autostart").disabled = config.autostart === null;
  $("autostart").checked = config.autostart === true;
  if (config.autostart === null) text("autostart-hint", "此选项适用于 Windows 安装包");
  $("images-disabled").hidden = config.images_enabled;
  $("save-capture").disabled = false;
}

$("strategy").onchange = updateFields;
$("image-format").onchange = updateFields;
form.onsubmit = async (event) => {
  event.preventDefault();
  const updated = {...settings};
  for (const name of Object.keys(updated)) {
    const input = form.elements.namedItem(name);
    if (input) updated[name] = input.type === "checkbox" ? input.checked
      : input.type === "number" ? input.valueAsNumber : input.value;
  }
  $("save-capture").disabled = true;
  text("capture-message", "正在保存…");
  try {
    fillCapture(await json("/api/capture/settings", "PATCH", updated));
    text("capture-message", "已保存并应用");
    await refresh();
  } catch (error) {
    text("capture-message", error.message);
  } finally {
    $("save-capture").disabled = false;
  }
};

$("theme").onchange = async () => {
  const selector = $("theme");
  selector.disabled = true;
  try {
    const result = await json("/api/settings/theme", "PATCH", {theme: selector.value});
    savedTheme = result.theme;
    document.documentElement.dataset.theme = savedTheme;
    text("software-message", "外观已保存");
  } catch (error) {
    selector.value = savedTheme;
    text("software-message", error.message);
  } finally {
    selector.disabled = false;
  }
};

$("autostart").onchange = async () => {
  const checkbox = $("autostart");
  const enabled = checkbox.checked;
  checkbox.disabled = true;
  try {
    checkbox.checked = (await json("/api/settings/autostart", "PATCH", {enabled})).enabled;
    text("software-message", checkbox.checked ? "已开启登录启动" : "已关闭登录启动");
  } catch (error) {
    checkbox.checked = !enabled;
    text("software-message", error.message);
  } finally {
    checkbox.disabled = false;
  }
};

$("check-update").onclick = async () => {
  $("check-update").disabled = true;
  $("update-link").hidden = true;
  text("update-result", "正在查询 GitHub…");
  try {
    const update = await json("/api/update/check", "POST");
    text("update-result", update.available ? "发现新版 v" + update.latest : "当前版本 v" + update.current + " 已是最新");
    $("update-link").href = update.url;
    $("update-link").hidden = !update.available;
  } catch (error) {
    text("update-result", error.message);
  } finally {
    $("check-update").disabled = false;
  }
};

const labels = {
  switch: "窗口切换", capture: "截图已入队", send: "尝试上传",
  success: "上传成功", error: "处理失败", skip: "跳过截图", info: "客户端",
};
function eventNode(item) {
  const row = document.createElement("li");
  row.classList.add("event", Object.hasOwn(labels, item.kind) ? item.kind : "info");
  const time = document.createElement("time");
  time.textContent = new Date(item.ts * 1000).toLocaleTimeString();
  time.title = when(item.ts);
  const label = document.createElement("strong");
  label.textContent = labels[item.kind] || labels.info;
  const detail = document.createElement("span");
  detail.textContent = item.detail;
  row.append(time, label, detail);
  return row;
}

async function refreshEvents() {
  const initial = newest === null;
  const suffix = initial ? "?limit=30" : "?limit=100&after=" + newest;
  const page = await json("/api/events" + suffix);
  if (page.items.length) {
    const position = feed.scrollTop;
    const height = feed.scrollHeight;
    const items = initial ? page.items : page.items.slice().reverse();
    $("events").prepend(...items.map(eventNode));
    newest = items[0].id;
    if (oldest === null) oldest = items.at(-1).id;
    if (position > 8) feed.scrollTop = position + feed.scrollHeight - height;
  }
  if (initial) hasOlder = page.more;
  $("empty-events").hidden = newest !== null;
  $("older").hidden = !hasOlder;
}

async function loadOlder() {
  if (!hasOlder || loadingOlder || oldest === null) return;
  loadingOlder = true;
  $("older").disabled = true;
  try {
    const page = await json("/api/events?before=" + oldest);
    $("events").append(...page.items.map(eventNode));
    if (page.items.length) oldest = page.items.at(-1).id;
    hasOlder = page.more;
    $("older").hidden = !hasOlder;
  } catch (error) {
    text("older", "加载失败，点击重试");
  } finally {
    loadingOlder = false;
    $("older").disabled = false;
  }
}
$("older").onclick = loadOlder;
feed.addEventListener("scroll", () => {
  if (feed.scrollTop > 0 && feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 28) {
    void loadOlder();
  }
}, {passive: true});

function renderEndpoints(endpoints) {
  const signature = JSON.stringify(endpoints);
  if (signature === endpointSignature) return;
  endpointSignature = signature;
  $("endpoints").replaceChildren(...endpoints.map((ep) => {
    const row = document.createElement("div");
    row.className = "endpoint";
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = ep.name;
    const meta = document.createElement("small");
    const state = ep.active ? "当前连接" : ep.healthy === true ? "正常"
      : ep.healthy === false ? "不可用" : "未探测";
    meta.textContent = state + " · " + ep.url;
    info.append(name, meta);
    const button = document.createElement("button");
    button.className = "subtle";
    button.textContent = ep.enabled ? "停用" : "启用";
    button.onclick = async () => {
      button.disabled = true;
      try {
        await json("/api/endpoints/" + ep.index, "PATCH", {enabled: !ep.enabled, expected_name: ep.name});
        await refresh();
      } catch (error) { alert(error.message); }
      finally { button.disabled = false; }
    };
    row.append(info, button);
    return row;
  }));
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const status = await json("/api/status");
    pendingCount = status.outbox_pending;
    updateSameServer();
    text("live", "本机运行中");
    $("live").className = "badge ok";
    text("capture", !status.connection_configured ? "等待连接设置" : status.paused ? "已暂停" : "正在采集");
    text("toggle", status.paused ? "继续采集" : "暂停采集");
    $("toggle").dataset.paused = status.paused;
    $("toggle").disabled = !status.connection_configured;
    text("active", status.connection_configured ? (status.active_endpoint || "未连接") : "尚未设置");
    const active = status.endpoints.find((ep) => ep.active);
    const connectionHint = active?.healthy === true ? "探测正常 · " : "";
    text("endpoint-health", connectionHint + "点击设置");
    text("mode", status.capture_strategy === "interval" ? "定时截图" : "智能截图");
    text("pending", status.outbox_images + " 张");
    text("backlog", bytes(status.outbox_bytes) + " · " + status.outbox_pending + " 项队列");
    text("last-capture", when(status.last_capture_at));
    text("last-upload", when(status.last_upload_at));
    $("error-box").hidden = !status.last_error;
    text("error", status.last_error ? status.last_error + " · " + when(status.last_error_at) : "");
    renderEndpoints(status.endpoints);
    if (!settings) await loadConfig();
    if (!$("status").hidden) await refreshEvents();
  } catch (_) {
    text("live", "本机连接中断");
    $("live").className = "badge bad";
    $("toggle").disabled = true;
  } finally {
    refreshing = false;
  }
}

$("toggle").onclick = async () => {
  $("toggle").disabled = true;
  try {
    const action = $("toggle").dataset.paused === "true" ? "resume" : "pause";
    const result = await json("/api/capture/" + action, "POST");
    if (result.warning) alert(result.warning);
  } catch (error) { alert(error.message); }
  finally { await refresh(); }
};
$("shutdown").onclick = async () => {
  if (!confirm("退出 TimeTrace 客户端？")) return;
  try {
    await json("/api/shutdown", "POST");
    text("software-message", "客户端正在退出");
  } catch (error) { text("software-message", error.message); }
};

void refresh();
setInterval(refresh, 1400);

export const $ = id => document.getElementById(id);
export const config = JSON.parse($("demo-config").textContent);
export const roles = {guest: "游客", member: "普通成员（买家 / 卖家）", admin: "管理员", support: "客服"};
export const image = new URL("item.svg", import.meta.url).pathname;
export const member = config.role === "member";
export const admin = config.role === "admin";
export const staff = admin || config.role === "support";
export const yuan = amount => `¥${amount}`;
const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium", hourCycle: "h23",
});
export const date = value => value ? formatter.format(new Date(value)) : "—";
export const uid = () => crypto.randomUUID();

export function component(name) {
  const root = $(`lab2-${name}`).content.firstElementChild.cloneNode(true);
  const slots = Object.fromEntries([...root.querySelectorAll("[data-slot]")].map(part => [part.dataset.slot, part]));
  return {root, slots};
}

export function node(tag, text, css) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (css) item.className = css;
  return item;
}

export function button(text, action, css) {
  const element = node("button", text, css || "btn");
  if (css === "primary") element.classList.add("btn");
  element.type = "button";
  element.addEventListener("click", async () => {
    element.disabled = true;
    try { await action(); }
    catch (error) { status(error.message, true); }
    finally { element.disabled = false; }
  });
  return element;
}

export function status(text = "", error = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", error);
}

export async function api(url, body, method = body === undefined ? "GET" : "POST") {
  const headers = {Accept: "application/json"};
  let data = body;
  if (method !== "GET") {
    const cookie = document.cookie.split("; ").find(item => item.startsWith("csrftoken="));
    headers["X-CSRFToken"] = cookie?.slice(10) || "";
    if (!(body instanceof URLSearchParams) && !(body instanceof FormData) && body !== undefined) {
      headers["Content-Type"] = "application/json";
      data = JSON.stringify(body);
    }
  }
  const result = await fetch(url, {method, headers, body: data, mode: "same-origin", cache: "no-store"});
  const payload = result.headers.get("Content-Type")?.includes("application/json") ? await result.json() : null;
  if (!result.ok) throw new Error(payload?.error || `请求未完成（HTTP ${result.status}），请刷新后检查。`);
  return payload;
}

export function field(name, label, options = {}) {
  const {type = "text", values, value, required = false, ...attributes} = options;
  const wrapper = node("label", null, type === "checkbox" ? "agree check" : "field");
  const input = node(values ? "select" : type === "textarea" ? "textarea" : "input");
  if (values) {
    for (const [key, title] of values) {
      const option = node("option", title);
      option.value = key;
      input.append(option);
    }
  } else if (type !== "textarea") input.type = type;
  input.name = name;
  if (type !== "checkbox") input.className = values ? "sel" : "input";
  input.required = required;
  for (const [key, content] of Object.entries(attributes)) input.setAttribute(key, content);
  if (type === "checkbox") { input.checked = Boolean(value); input.value = "1"; }
  else if (value != null) input.value = value;
  if (type === "checkbox") wrapper.append(input, node("span", label));
  else wrapper.append(node("span", label, "label"), input);
  return wrapper;
}

export function modal(title, intro, fields, submit, after = () => {}, label = "确认") {
  const dialog = $("dialog"), form = $("dialog-form"), send = $("dialog-submit");
  if (dialog.open) dialog.close();
  $("dialog-title").textContent = title;
  $("dialog-intro").textContent = intro;
  $("dialog-error").textContent = "";
  $("dialog-fields").replaceChildren(...fields);
  send.textContent = label;
  send.disabled = false;
  form.onsubmit = async event => {
    event.preventDefault();
    $("dialog-error").textContent = "";
    send.disabled = true;
    try {
      const result = await submit(new FormData(form));
      dialog.close();
      await after(result);
    } catch (error) {
      if (dialog.open) $("dialog-error").textContent = error.message;
      else status(error.message, true);
    } finally { send.disabled = false; }
  };
  dialog.showModal();
  $("dialog-cancel").onclick = () => { if (!send.disabled) dialog.close(); };
  dialog.oncancel = event => { if (send.disabled) event.preventDefault(); };
}

export function card(title, badge, ...children) {
  const item = node("article", null, "panel card");
  if (badge) item.append(node("span", badge, "badge"));
  item.append(node("h3", title), ...children.filter(Boolean));
  return item;
}

export function actions(...buttons) {
  const group = node("div", null, "actions");
  group.append(...buttons.filter(Boolean));
  return group;
}

export function grid(rows, render, empty = "暂无记录。") {
  if (!rows.length) return node("p", empty, "empty");
  const group = node("div", null, "grid");
  group.append(...rows.map(render));
  return group;
}

export const meta = text => node("p", text, "meta");
export const reason = (label = "说明", limit = 200) => field("reason", label, {
  type: "textarea", required: true, maxlength: limit,
});

export const imageAccept = "image/jpeg,image/png,image/webp,image/gif";

export function picture(source, alt = "商品缩略图", css = "thumbnail", placeholder = true) {
  const local = typeof source === "string" && /^\/(?:demo\/media\/|static\/demo\/)/.test(source);
  if (!local && !placeholder) return null;
  const item = node("img", null, css);
  item.src = local ? source : image;
  item.alt = alt;
  if (css !== "chat-image") item.loading = "lazy";
  if (local && placeholder) item.addEventListener("error", () => { item.src = image; }, {once: true});
  return item;
}

export function pair(label, value) {
  const row = node("div", null, "row data-pair");
  row.append(node("span", label), node("b", value));
  return row;
}

export function badge(label) {
  const tone = {"已完成": "ok", "已发布": "ok", "待付款": "info", "待收货": "info", "已预售": "info",
    "待付定金": "info", "待付尾款": "info", "已驳回": "danger", "违规下架": "danger", "纠纷中": "danger",
    "待发货": "warn", "待审核": "warn", "待确认": "warn", "待到货": "warn"};
  return node("span", label, `badge ${tone[label] || "plain"}`);
}

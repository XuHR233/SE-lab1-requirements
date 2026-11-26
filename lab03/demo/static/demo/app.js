import {$, actions, admin, api, button, card, config, date, field, member, meta, modal, node, roles, staff, status} from "./ui.js";
import {inventory, market, productDetail} from "./products.js";
import {appeals, disputes, orders, reservations} from "./trade.js";
import {chats} from "./chats.js?v=chat-scroll-2";
import {logs} from "./logs.js";
import {stats} from "./stats.js";

const tabs = {market: "首页", product: "商品详情", inventory: admin ? "商品审核" : "我的商品", orders: "我的订单",
  reservations: "我的预约", chats: "消息", disputes: "纠纷工单", appeals: "评价申诉", stats: "卖家统计",
  logs: "操作日志", clock: "模拟时间", help: "验收指引"};
const visible = [...(staff ? [] : ["market"]), ...(member || admin ? ["inventory"] : []),
  ...(member ? ["orders", "reservations", "chats", "disputes"] : staff ? ["disputes"] : []),
  ...(admin ? ["appeals"] : []), ...(config.user ? ["stats", "logs"] : []),
  ...(admin && config.clock ? ["clock"] : []), "help"];
const state = {tab: visible[0], focus: null, filters: new URLSearchParams(), generation: 0};

function auth(register) {
  const label = register ? "注册" : "登录";
  const fields = [field("phone", "手机号（请用虚构号码）", {
    required: true, type: "tel", pattern: "1[3-9][0-9]{9}", autocomplete: "username",
  }), field("password", "密码", {type: "password", required: true, autocomplete: register ? "new-password" : "current-password"})];
  if (register && config.smsCode) fields.push(field("code", "开发验证码", {inputmode: "numeric"}));
  if (register && config.smsSkip) fields.push(field("skip_sms", "跳过短信验证（仅本内测环境）", {type: "checkbox"}));
  const dialog = $("auth-dialog"), form = $("auth-form"), submit = $("auth-submit");
  $("auth-title").textContent = register ? "注册账号" : "欢迎回来";
  $("auth-login-tab").classList.toggle("on", !register);
  $("auth-register-tab").classList.toggle("on", register);
  $("auth-fields").replaceChildren(...fields);
  const phone = form.elements.phone;
  phone.addEventListener("input", () => {
    const value = phone.value.replace(/\s+/g, "");
    if (phone.value !== value) phone.value = value;
  });
  $("auth-error").textContent = "";
  submit.textContent = label;
  form.onsubmit = async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    try {
      $("auth-error").textContent = "";
      const data = new URLSearchParams(new FormData(form));
      data.set("phone", phone.value.replace(/\s+/g, ""));
      await api(`/accounts/${register ? "register" : "login"}`, data);
      location.assign("/");
    } catch (error) { $("auth-error").textContent = error.message; }
    finally { submit.disabled = false; }
  };
  if (!dialog.open) dialog.showModal();
}

async function open(tab, focus = null, message = "") {
  if (!visible.includes(tab) && !(staff && tab === "chats" && focus)
    && !(!staff && tab === "product" && focus)) return;
  state.tab = tab;
  state.focus = focus;
  await reload(message);
}

function help() {
  const wrapper = node("div", null, "grid");
  wrapper.append(
    card("01 · 准备账号", null, meta("普通窗口、无痕窗口或独立浏览器配置可以分别登录。也可退出再换账号，数据不会丢失。测试账号由 atlas 的验收账号文件发放。")),
    card("02 · 现货成交", null, meta("卖家新建商品 → 上传图片 → 提交审核 → 管理员审核通过 → 买家下单、模拟支付 → 卖家模拟发货 → 买家确认收货 → 双方互评。")),
    card("03 · 预售成交", null, meta("卖家发布预售 → 买家预约，模拟付定金 → 卖家确认并登记到货 → 管理员快进到次日零点 → 买家模拟付尾款 → 打开关联订单继续履约。")),
    card("04 · 售后验收", null, meta("另建订单测试未发货退款，或先在订单沟通留言，再请求客服介入。客服在纠纷工单接单并处理；评价申诉在管理员工作台处理。")),
  );
  return wrapper;
}

function clock(desk) {
  const choices = [["midnight", "快进到次日 00:00"], ["minute", "快进 1 分钟"], ["hour", "快进 1 小时"],
    ["day", "快进 24 小时"], ["review", "快进 16 天（评价）"], ["sweep", "不快进，只跑定时任务"]];
  return card("业务时钟（北京时间）", date(desk.now),
    meta("快进是这个隔离库的全局操作，不能回退；会运行到期任务并可能关掉其他未完成的单。请先处理其他待验收交易。不会改动 atlas 或浏览器的时钟。"),
    actions(...choices.map(([step, label]) => button(label, () => modal(label, "确认后运行预约、订单、评价的到期处理。", [],
      () => api("/demo/clock/", {step}), result => reload(`${date(result.now)} · ${Object.entries(result.counts).map(([key, value]) => `${key} ${value}`).join(" · ")}`))))));
}

async function render(tab, focus, desk) {
  if (tab === "market") {
    const data = await api(`/api/products/?${state.filters}`);
    return market(desk, data.items, state.filters, filters => { state.filters = filters; return reload(); }, open);
  }
  if (tab === "product") return productDetail(desk, await api(`/api/products/${focus}/`), open);
  if (tab === "inventory") return inventory(desk, reload);
  if (tab === "orders") return orders(desk, focus, open, reload);
  if (tab === "reservations") return reservations(desk, focus, open, reload);
  if (tab === "disputes") return disputes(desk, open);
  if (tab === "appeals") return appeals(await api(`/api/staff/review-appeals/?page=${focus || 1}`), open, reload);
  if (tab === "chats") return chats(desk, focus, open, reload);
  if (tab === "stats") return stats(focus, open);
  if (tab === "logs") return logs(focus, open);
  if (tab === "clock") return clock(desk);
  return help();
}

async function reload(message = "") {
  const {tab, focus} = state, generation = ++state.generation;
  $("section-title").textContent = tabs[tab];
  $("crumb").textContent = `${staff ? "后台" : ["market", "product"].includes(tab) ? "二手集市" : "我的"} / ${tabs[tab]}`;
  $("panel").replaceChildren(meta("正在加载…"));
  $("nav").replaceChildren(...visible.map(key => {
    const entry = button(tabs[key], () => open(key), "nav-link");
    if (key === tab || (tab === "product" && key === "market")) {
      entry.setAttribute("aria-current", "page");
      entry.classList.add("active");
    }
    return entry;
  }));
  status();
  try {
    const desk = await api("/demo/desk/");
    const panel = await render(tab, focus, desk);
    if (generation !== state.generation) return;
    $("panel").replaceChildren(panel);
    $("clock-label").textContent = config.clock ? ` 业务时间（北京时间）：${date(desk.now)}。` : "";
    status(message);
  } catch (error) {
    if (generation !== state.generation) return;
    $("panel").replaceChildren(meta("无法加载此分区，请刷新或返回商品广场。"));
    status(error.message, true);
  }
}

if ($("role")) $("role").textContent = roles[config.role];
if ($("login")) {
  $("login").onclick = $("auth-login-tab").onclick = () => auth(false);
  $("register").onclick = $("auth-register-tab").onclick = () => auth(true);
  $("auth-dismiss").onclick = () => $("auth-dialog").close();
}
if ($("quick-search")) $("quick-search").onsubmit = event => {
  event.preventDefault();
  state.filters = new URLSearchParams(new FormData(event.currentTarget));
  open("market");
};
if ($("logout")) $("logout").onclick = async () => {
  try { await api("/accounts/logout", {}); location.assign("/"); }
  catch (error) { status(error.message, true); }
};
$("refresh").onclick = () => reload();
reload();

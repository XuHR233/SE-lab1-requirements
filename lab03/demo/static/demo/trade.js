import {actions, api, badge, button, card, component, config, date, field, grid, member, meta, modal, node, pair, picture, reason, staff, uid, yuan} from "./ui.js";
import {claimTicket, ticketState} from "./disputes.js";

function operation(name, intro, url, reload, {fields = [], body = () => ({}), method = "POST"} = {}) {
  return button(name, () => modal(name, intro, fields, data => api(url, body(data), method),
    () => reload(`${name}已完成。`), name));
}

function reviewBox(order, url, reload) {
  const own = order.reviews.find(entry => entry.author === config.user);
  const review = operation(own ? "修改评价" : "提交评价", "订单双方均可评价。", url, reload, {
    fields: [field("rating", "评分", {value: own?.rating || 5, values: [5, 4, 3, 2, 1].map(n => [n, `${n} 星`])}),
      field("content", "评价内容", {type: "textarea", value: own?.content, maxlength: 1000})],
    body: data => ({rating: Number(data.get("rating")), content: data.get("content")}),
  });
  const del = operation("删除我的评价", "评价将不再公开展示。", url, reload, {method: "DELETE"});
  const wrapper = node("div");
  for (const entry of order.reviews) {
    wrapper.append(meta(`${entry.author === order.buyer_id ? "买家" : "卖家"} · ${entry.rating} 星${entry.automatic ? "（系统补评）" : ""} · ${entry.content || "无文字"}`));
    if (member && entry.target === config.user && !entry.automatic && !entry.appealed) wrapper.append(operation(
      "申诉这条评价", "申诉提交后由管理员处理。", `/api/reviews/${entry.id}/appeal/`, reload,
      {fields: [reason("申诉原因", 500)], body: data => ({reason: data.get("reason")})},
    ));
  }
  if (member && !own?.automatic) wrapper.append(actions(review, own && del));
  return wrapper;
}

export function orders(desk, focus, open, reload) {
  const layout = node("div", null, "split");
  const main = node("div", null, "main"), stack = node("div");
  const hour = 3600000;
  const within = (item, hours) => item.status === "待发货" && item.paid_at
    && new Date(desk.now) - new Date(item.paid_at) < hours * hour;
  const groups = [["全部", []], ["待付款", ["待付款"]], ["待发货", ["待发货"]],
    ["待收货", ["待收货"]], ["已完成", ["已完成"]], ["退款 / 纠纷", ["已退款", "纠纷中"]], ["已取消", ["已取消"]]];
  function orderRow(item) {
    const base = `/api/orders/${item.id}/`, buyer = member && item.buyer_id === config.user;
    const seller = member && item.seller_id === config.user;
    const refundable = item.can_self_refund ?? within(item, 48);
    const shippable = item.can_ship ?? within(item, 15 * 24);
    const active = focus === item.id ? " · 当前" : "";
    const list = [], op = (name, suffix, note, fields) => operation(name, note, base + suffix + "/", reload, fields);
    if ((buyer || seller) && item.status === "待付款") list.push(op("取消未付款订单", "cancel", "取消将释放锁定的商品。"));
    if (buyer && item.status === "待付款" && config.payments) list.push(op("模拟支付", "pay", `记账 ${yuan(item.amount)}，不扣款。`, {body: () => ({txn_no: uid()})}));
    if (buyer && refundable) list.push(op("全额退款", "refund", "付款后 48 小时内且未发货，可模拟全额退款。", {
      fields: [reason("退款理由")], body: data => ({reason: data.get("reason")}),
    }));
    if (seller && shippable) list.push(op("模拟发货", "ship", "请输入虚构的运单号。", {
      fields: [field("tracking_no", "运单号", {required: true, pattern: "[A-Za-z0-9]{6,60}", placeholder: "TEST123456"})],
      body: data => ({tracking_no: data.get("tracking_no")}),
    }));
    if (buyer && item.status === "待收货") list.push(op("确认收货", "receive", "确认后结算，买卖双方可互评。"));
    if (item.tracking_no) list.push(button("查看物流", () => modal("物流信息", "仅显示已登记的模拟运单，不查询物流平台。",
      [pair("运单号", item.tracking_no), pair("发货时间", date(item.shipped_at))], () => {}, () => {}, "关闭")));
    if (buyer || seller) {
      list.push(button("订单沟通", async () => {
        const row = await api("/api/chats/", {product_id: item.product_id, order_id: item.id});
        await open("chats", {conversationId: row.id, orderId: item.id});
      }));
      if (item.can_raise) list.push(operation("请求客服介入", "订单会进入纠纷流程。", "/api/chats/disputes/",
        () => open("disputes", null, "工单已提交。"), {
          fields: [reason("介入原因")], body: data => ({order_id: item.id, reason: data.get("reason")}),
        }));
    }
    list.push(button("处理记录", () => open("logs", item.id)));
    const {root, slots} = component("order");
    slots.party.textContent = buyer ? `卖家账号 #${item.seller_id}` : `买家账号 #${item.buyer_id}`;
    slots.number.textContent = `订单号 ${item.id}${active}`;
    slots.status.append(badge(item.status));
    slots.image.append(item.thumbnail ? picture(item.thumbnail) : "商品图");
    slots.title.textContent = item.title;
    slots.spec.textContent = `分类：${item.category} ｜ ${item.is_presale ? "预售" : "现货"}${item.description ? ` ｜ ${item.description}` : ""}`;
    slots.note.textContent = [`下单时间 ${date(item.created_at)}`, item.paid_at && `模拟付款 ${date(item.paid_at)}`,
      item.shipped_at && `发货 ${date(item.shipped_at)} · 运单 ${item.tracking_no}`].filter(Boolean).join(" ｜ ");
    slots.amount.textContent = yuan(item.amount);
    const notes = {
      "待付款": `支付截止 ${date(item.expires_at)}，超时释放商品。`,
      "待发货": refundable ? "付款后 48 小时内且未发货，可自助全额退款。"
        : "自助退款窗口已关闭；满 15 天未发货将自动全额退款。",
      "待收货": `自动确认时间 ${date(item.auto_confirm_at)}；如有问题可请求客服介入。`,
      "纠纷中": "工单处理中，暂停自动确认与结算。",
    };
    slots.tip.textContent = item.close_reason || notes[item.status] || (item.received_at ? `收货于 ${date(item.received_at)}` : "交易处理记录可查。");
    for (const control of list) {
      if (["模拟支付", "模拟发货", "确认收货"].includes(control.textContent)) control.classList.add("primary");
      else if (control.textContent !== "查看物流") control.classList.add("ghost");
    }
    slots.foot.append(...list);
    if (item.settlement || item.status === "已完成") {
      const review = node("div", null, "review-summary");
      if (item.settlement) review.append(meta(`已结算：手续费 ${yuan(item.settlement.fee)} · 卖家净额 ${yuan(item.settlement.net)}（模拟）`));
      if (item.status === "已完成") review.append(reviewBox(item, base + "review/", reload));
      root.append(review);
    }
    return root;
  }
  main.append(filterTabs(desk.orders, groups, stack, orderRow, "此状态暂无订单。"), stack);
  const aside = node("aside", null, "side");
  aside.append(card("担保交易", null, meta("内测仅模拟货款托管与结算，不扣款。确认收货后生成结算记录。"),
    ...["待付款", "待发货", "待收货"].map(name => pair(name, `${desk.orders.filter(row => row.status === name).length} 笔`)),
    pair("托管中货款（模拟）", yuan((desk.orders.filter(row => ["待发货", "待收货", "纠纷中"].includes(row.status))
      .reduce((cents, row) => cents + Math.round(Number(row.amount) * 100), 0) / 100).toFixed(2)))),
    card("状态说明", null, pair("待付款", "可取消订单"), pair("待发货", "可申请退款"),
      pair("待收货", "可请求客服介入"), meta("请保留商品照片、检测凭证和聊天记录。")));
  layout.append(main, aside);
  return layout;
}

function filterTabs(items, groups, stack, render, empty) {
  const tabs = node("nav", null, "tabs");
  tabs.setAttribute("aria-label", "按状态筛选");
  for (const [label, states] of groups) {
    const rows = items.filter(item => !states.length || states.includes(item.status));
    const control = button(label, () => select(control, rows), "tab");
    if (states.length && rows.length) control.append(node("em", rows.length));
    tabs.append(control);
  }
  function select(current, rows) {
    for (const entry of tabs.children) {
      entry.classList.toggle("on", entry === current);
      entry.setAttribute("aria-pressed", String(entry === current));
    }
    stack.replaceChildren(...(rows.length ? rows.map(render) : [node("p", empty, "empty")]));
  }
  select(tabs.firstChild, items);
  return tabs;
}

export function reservations(desk, focus, open, reload) {
  const layout = node("div", null, "split lab2-reservation"), main = node("div", null, "main");
  const stack = node("div"), aside = node("aside", null, "side");
  function render(item) {
    const base = `/api/reservations/${item.id}/`, buyer = member && item.buyer_id === config.user;
    const seller = member && item.seller_id === config.user;
    const list = [], op = (name, tail, note, fields) => operation(name, note, base + tail + "/", reload, fields);
    if (buyer && item.status === "待付定金" && config.payments) list.push(op("模拟付定金", "pay-deposit", `记账 ${yuan(item.deposit)}，不扣款。`, {body: () => ({txn_no: uid()})}));
    if (seller && item.status === "待确认") list.push(op("确认预约", "confirm", "确认后等待卖家登记到货。"), op("拒绝预约", "reject", "拒绝后按规则退回定金。", {
      fields: [reason("拒绝原因")], body: data => ({reason: data.get("reason")}),
    }));
    if (seller && item.status === "待到货") list.push(op("确认到货", "arrive", "确认后次日北京时间 00:00 开始支付尾款。"));
    if (buyer && item.status === "待付尾款" && config.payments) list.push(operation("模拟付尾款", `开放时间：${date(item.final_start)}；未到时间会被拒绝。`, base + "pay-final/",
      () => reload("模拟尾款成功，订单已生成。"), {body: () => ({txn_no: uid()})}));
    if ((buyer || seller) && ["待付定金", "待确认", "待到货", "待付尾款"].includes(item.status)) list.push(op("取消预约", "cancel", "定金按预约状态及取消方处理。", {
      fields: [reason("取消原因")], body: data => ({reason: data.get("reason")}),
    }));
    if (item.order_id) list.push(button("打开关联订单", () => open("orders", item.order_id)));
    const {root, slots} = component("reservation");
    slots.party.textContent = buyer ? `卖家账号 #${item.seller_id}` : `买家账号 #${item.buyer_id}`;
    slots.number.textContent = `预约号 ${item.id}${focus === item.id ? " · 当前" : ""}`;
    slots.created.textContent = `提交于 ${date(item.created_at)}`;
    slots.status.append(badge(item.status));
    if (item.thumbnail) slots.image.append(picture(item.thumbnail));
    slots.title.textContent = item.title;
    slots.note.textContent = item.order_id ? `尾款已支付 · 订单 #${item.order_id}` : item.description;
    for (const [label, value] of [["定金", yuan(item.deposit)], ["尾款", yuan(item.final_amount)], ["预约有效期至", date(item.expires_at)]]) {
      const entry = node("span", label);
      entry.append(node("b", value));
      slots.money.append(entry);
    }
    slots.tip.textContent = item.close_reason ? `${item.close_reason} · 应退 ${yuan(item.refund_due)}` : ({
      "待付定金": `定金截止 ${date(item.deposit_due_at)}`,
      "待确认": "请在有效期内确认；拒绝或超时全额退回定金。",
      "待到货": "有效期内未确认到货，定金全退、商品下架；按时确认后次日开放尾款。",
      "待付尾款": `尾款窗口 ${date(item.final_start)} 至 ${date(item.final_due_at)}`,
    }[item.status] || "尾款到账后进入订单履约流程。");
    slots.foot.append(...list);
    return root;
  }
  const groups = [["全部", []], ["待付定金", ["待付定金"]], ["待确认", ["待确认"]],
    ["已确认", ["待到货", "待付尾款"]], ["已完成", ["已完成"]], ["已取消", ["已取消"]], ["已拒绝", ["已拒绝"]]];
  main.append(filterTabs(desk.reservations, groups, stack, render, "此状态暂无预约。"), stack);
  aside.append(card("定金退款规则", null, pair("卖家确认前取消", "全额退"), pair("卖家拒绝 / 超时", "全额退"),
    pair("15 分钟未付定金", "预约关闭"), pair("确认后买家取消", "不退"),
    pair("确认后尾款逾期", "不退"), pair("商品违规下架", "全额退")));
  layout.append(main, aside);
  return layout;
}

export function disputes(desk, open) {
  return grid(desk.disputes, row => {
    const list = [member && button("关联订单", () => open("orders", row.order_id)),
      staff && button("订单处理记录", () => open("logs", row.order_id))];
    if (row.conversation_id) list.push(button("查看关联会话", () => open("chats",
      staff ? row.id : {conversationId: row.conversation_id, orderId: row.order_id})));
    list.push(claimTicket(row, () => open("chats", row.id, "已接单。")));
    return card(`工单 #${row.id} · 订单 #${row.order_id}`, ticketState(row), meta(row.reason),
      row.handler_id && meta(`处理人 #${row.handler_id}`), row.verdict && meta(`结果：${row.verdict} · ${row.note}`), actions(...list));
  });
}

export function appeals({pending, page = 1, has_next}, open, reload) {
  const root = node("div");
  root.append(grid(pending, row => card(`申诉 #${row.id} · 评价 #${row.review_id}`, row.reminded_at ? "已超期提醒" : "待处理",
    meta(row.reason), meta(`期限 ${date(row.due_at)}`), actions(operation("处理申诉", "申诉成立将屏蔽评价。",
      `/api/staff/review-appeals/${row.id}/resolve/`, reload, {
        fields: [field("upheld", "结论", {values: [["true", "成立 · 屏蔽评价"], ["false", "不成立"]]}),
          field("note", "说明", {required: true, maxlength: 500, type: "textarea"})],
        body: data => ({upheld: data.get("upheld") === "true", note: data.get("note")}),
      })))));
  root.append(actions(page > 1 && button("上一页", () => open("appeals", page - 1)),
    has_next && button("下一页", () => open("appeals", page + 1))));
  return root;
}

import {actions, api, button, card, component, config, date, field, imageAccept, member, meta, modal, node, pair, picture, reason, yuan} from "./ui.js";
import {claimTicket, dispositionPanel, ownsDispute, ticketState} from "./disputes.js";

function message(item, chat) {
  const mine = item.sender_id === config.user;
  const role = item.staff_role === "admin" ? "管理员" : item.staff_role === "support" ? "客服"
    : item.sender_id === chat.seller_id ? "卖家" : "买家";
  const row = node("div", null, `msg${mine ? " me from-me" : ""}`);
  const bubble = node("div", null, "bubble chat-bubble");
  bubble.append(node("span", `${role} #${item.sender_id}${mine ? "（我）" : ""}`, "when"));
  if (item.text) bubble.append(node("p", item.text));
  if (item.image) bubble.append(picture(item.image, "会话图片", "chat-image", false) || meta("旧图片地址未在内测页加载。"));
  bubble.append(node("span", date(item.created_at), "when"));
  row.append(node("span", mine ? "我" : role[0], mine ? "who s" : "who"), bubble);
  return row;
}

function requestDispute(chat, reload) {
  return button("请求客服介入", () => modal("请求客服介入", "提交后订单进入纠纷流程。", [reason("介入原因")],
    form => api("/api/chats/disputes/", {order_id: chat.order_id, conversation_id: chat.id, reason: form.get("reason")}),
    () => reload("工单已提交。"), "提交工单"), "primary");
}

function composer(chat, refreshThread, ticket) {
  const form = node("form", null, "chat-compose");
  const text = field("text", "消息", {placeholder: "输入消息，请勿填写手机号等敏感信息", maxlength: 1000});
  const attach = field("file", "图片（选填）", {type: "file", accept: imageAccept});
  const send = node("button", "发送", "btn primary"), notice = node("p", "", "error");
  const textInput = text.querySelector("input"), fileInput = attach.querySelector("input");
  notice.setAttribute("role", "alert");
  form.append(text, attach, send, notice);
  form.onsubmit = async event => {
    event.preventDefault();
    if (send.disabled) return;
    const data = new FormData(form);
    const restoreFocus = form.contains(document.activeElement);
    send.disabled = fileInput.disabled = textInput.readOnly = true;
    try {
      notice.textContent = "";
      const body = data.get("text").trim(), image = data.get("file");
      const message = new URLSearchParams({text: body});
      if (ticket) {
        data.set("dispute_id", ticket.id);
        message.set("dispute_id", ticket.id);
      }
      if (image.size) await api(`/demo/chats/${chat.id}/image/`, data);
      else if (body) await api(`/api/chats/${chat.id}/messages/`, message);
      else throw new Error("请输入文字或选一张图片。");
      form.reset();
      try {
        await refreshThread();
      } catch {
        notice.textContent = "消息已发送，但聊天记录未能更新。请刷新查看，不必重发。";
      }
    } catch (error) { notice.textContent = error.message; }
    finally {
      send.disabled = fileInput.disabled = textInput.readOnly = false;
      if (restoreFocus && form.isConnected && (document.activeElement === document.body
        || form.contains(document.activeElement))) textInput.focus({preventScroll: true});
    }
  };
  return form;
}

function orderLabel(chat) {
  if (!chat.order_id) return "商品咨询";
  return `订单 #${chat.order_id} · ${chat.order_status}${chat.order_is_historical ? " · 历史订单" : ""}`;
}

function ticketPanel(ticket, chat, open, reload) {
  if (!ticket) return card("纠纷工单", null, pair("当前查看", orderLabel(chat)),
    meta(chat.order_id ? "此订单暂无工单。" : "暂无工单。"));
  return card(`纠纷工单 #${ticket.id}`, null,
    !member && picture(chat.thumbnail, chat.title, "chat-image", true),
    !member && actions(claimTicket(ticket, () => reload("已接单。"))),
    ...(!member ? dispositionPanel(ticket, reload) : []),
    pair("工单状态", ticketState(ticket)),
    pair("关联订单", orderLabel(chat)), pair("发起时间", date(ticket.created_at)),
    pair("发起方式", ticket.raised_source),
    pair("订单与资金", ticket.order__status === "纠纷中" ? "暂停自动履约及结算" : ticket.order__status),
    ticket.handler_id && pair(ticket.closed_at ? "裁定人" : "处理人", `工作人员 #${ticket.handler_id}`),
    ticket.escalation_reason && pair("转交原因", ticket.escalation_reason),
    ticket.verdict && pair("处理结果", ticket.verdict), ticket.note && meta(ticket.note),
    ticket.closed_at && pair("结案时间", date(ticket.closed_at)),
    button("前往纠纷工单", () => open("disputes", ticket.id)));
}

export async function chats(desk, focus, open, reload) {
  const root = node("div", null, "split lab2-chat");
  const main = node("div", null, "main"), aside = node("aside", null, "side");
  const {items} = member ? await api("/api/chats/") : {items: []};
  const staffTicket = !member && focus ? await api(`/api/chats/disputes/${focus}/`) : null;
  const current = member ? (focus?.conversationId ?? focus ?? items[0]?.id) : staffTicket?.conversation_id;
  root.append(main, aside);
  if (items.length) {
    const chooser = field("conversation", "我的会话", {
      value: current, values: items.map(row => [row.id, `${row.title} · 会话 #${row.id}`]),
    });
    chooser.querySelector("select").onchange = event => open("chats", Number(event.target.value));
    main.append(chooser);
  }
  if (!current) {
    main.append(node("p", member ? "暂无会话。可以从商品广场联系卖家，或在订单中开启沟通。" : "请从有权查看的纠纷工单打开关联会话。", "empty"));
    return root;
  }
  const scope = new URLSearchParams();
  if (member && focus?.orderId) scope.set("order_id", focus.orderId);
  if (staffTicket) scope.set("dispute_id", staffTicket.id);
  const query = scope.toString();
  const detailUrl = `/api/chats/${current}/${query ? `?${query}` : ""}`;
  const data = await api(detailUrl), chat = data.conversation;
  const ticket = data.dispute !== undefined ? data.dispute : (staffTicket || desk.disputes.find(row =>
    row.conversation_id === chat.id && row.order_id === chat.order_id));
  if ((focus?.orderId && chat.order_id !== focus.orderId)
    || (staffTicket && (ticket?.id !== staffTicket.id || chat.order_id !== staffTicket.order_id))) {
    throw new Error("未能加载指定订单或工单，请刷新后重试。");
  }
  const {root: channel, slots} = component("chat");
  const party = config.user === chat.seller_id ? `买家账号 #${chat.buyer_id}` : `卖家账号 #${chat.seller_id}`;
  slots.title.textContent = chat.title;
  slots.price.textContent = yuan(chat.price);
  slots.order.textContent = orderLabel(chat);
  if (member && chat.thumbnail) slots.image.append(picture(chat.thumbnail));
  const thread = slots.thread;
  const bar = actions();
  let renderedMessages = new Map();
  function renderThread(snapshot, activeTicket) {
    const context = snapshot.conversation;
    slots.note.textContent = `会话 #${context.id} ｜ ${snapshot.items.length} 条可见消息 ｜ 建立于 ${date(context.created_at)}`;
    const rows = snapshot.items.map(item => [item.id, renderedMessages.get(item.id) || message(item, context)]);
    renderedMessages = new Map(rows);
    const content = rows.length ? rows.map(([, row]) => row) : [meta("暂无消息。")];
    const canRaise = member && !activeTicket && ["待发货", "待收货", "纠纷中", "已完成"].includes(context.order_status);
    const signals = snapshot.signals.filter(entry => !entry.ignored);
    for (const signal of signals) {
      const strip = node("div", null, "alert");
      strip.append(node("span", `${signal.reason}。系统仅提示，不判定责任或自动退款。`), node("div", null, "grow"));
      if (canRaise) strip.append(requestDispute(context, reload));
      if (member) strip.append(button("忽略提示", async () => {
        await api(`/api/chats/${current}/ignore-signal/`, {kind: signal.kind});
        await reload("已忽略此提示。");
      }));
      content.push(strip);
    }
    if (activeTicket) {
      const system = node("p", null, "sys");
      system.append(node("span", `客服工单 #${activeTicket.id} · ${ticketState(activeTicket)}${activeTicket.verdict ? ` · ${activeTicket.verdict}` : ""}`));
      content.push(system);
    }
    const retained = new Set(content);
    for (const child of [...thread.children]) {
      if (!retained.has(child)) child.remove();
    }
    let cursor = thread.firstElementChild;
    for (const child of content) {
      if (child === cursor) cursor = cursor.nextElementSibling;
      else thread.insertBefore(child, cursor);
    }
    bar.replaceChildren(...(canRaise && !signals.length ? [requestDispute(context, reload)] : []));
  }
  async function refreshThread() {
    if (!thread.isConnected) return;
    const snapshot = await api(detailUrl);
    if (!thread.isConnected) return;
    if (snapshot.conversation.order_id !== chat.order_id || snapshot.dispute?.id !== ticket?.id
      || (!member && !ownsDispute(snapshot.dispute))) {
      throw new Error("会话上下文已变化，请刷新后查看。");
    }
    const previousTop = thread.scrollTop;
    const nearEnd = thread.scrollHeight - thread.clientHeight - previousTop <= 32;
    renderThread(snapshot, snapshot.dispute);
    thread.scrollTop = nearEnd ? thread.scrollHeight : previousTop;
  }
  renderThread(data, ticket);
  if (member) slots.composer.append(composer(chat, refreshThread));
  else if (ownsDispute(ticket)) slots.composer.append(composer(chat, refreshThread, ticket));
  else slots.composer.append(meta("只有当前处理人可以参与交流。"));
  main.append(node("h3", member ? `与${party}的会话` : "工单关联会话"), channel, bar);
  aside.append(card("会话规则", null, meta("聊天记录仅双方和有权处理该工单的工作人员可见。页面不提供隐藏或删除消息。")),
    ticketPanel(ticket, chat, open, reload), card("处理权限", null, pair("客服退款", "须记录裁定及说明"), pair("封号 / 下架", "仅管理员"),
      meta("请保留商品照片、检测凭证与聊天记录。")));
  const scrollToEnd = () => { if (thread.isConnected) thread.scrollTop = thread.scrollHeight; };
  requestAnimationFrame(scrollToEnd);
  return root;
}

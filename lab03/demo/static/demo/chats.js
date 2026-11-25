import {actions, api, button, card, component, config, date, field, imageAccept, member, meta, modal, node, pair, picture, reason, yuan} from "./ui.js";
import {claimTicket, dispositionPanel, ownsDispute, ticketState} from "./disputes.js";

function message(item, chat, reload) {
  const mine = item.sender_id === config.user;
  const role = item.staff_role === "admin" ? "管理员" : item.staff_role === "support" ? "客服"
    : item.sender_id === chat.seller_id ? "卖家" : "买家";
  const row = node("div", null, `msg${mine ? " me from-me" : ""}`);
  const bubble = node("div", null, "bubble chat-bubble");
  bubble.append(node("span", `${role} #${item.sender_id}${mine ? "（我）" : ""}`, "when"));
  if (item.text) bubble.append(node("p", item.text));
  if (item.image) bubble.append(picture(item.image, "会话图片", "chat-image", false) || meta("旧图片地址未在内测页加载。"));
  bubble.append(node("span", date(item.created_at), "when"));
  if (member) bubble.append(button("仅对我隐藏", async () => {
    await api(`/api/chats/${chat.id}/hide/`, {message_id: item.id});
    await reload("已在我的会话中隐藏。");
  }, "nav-link chat-hide"));
  row.append(node("span", mine ? "我" : role[0], mine ? "who s" : "who"), bubble);
  return row;
}

function requestDispute(chat, reload) {
  return button("请求客服介入", () => modal("请求客服介入", "提交后订单进入纠纷流程。", [reason("介入原因")],
    form => api("/api/chats/disputes/", {order_id: chat.order_id, conversation_id: chat.id, reason: form.get("reason")}),
    () => reload("工单已提交。"), "提交工单"), "primary");
}

function composer(chat, reload, ticket) {
  const form = node("form", null, "chat-compose");
  const text = field("text", "消息", {placeholder: "输入消息，请勿填写手机号等敏感信息", maxlength: 1000});
  const attach = field("file", "图片（选填）", {type: "file", accept: imageAccept});
  const send = node("button", "发送", "btn primary"), notice = node("p", "", "error");
  notice.setAttribute("role", "alert");
  form.append(text, attach, send, notice);
  form.onsubmit = async event => {
    event.preventDefault();
    send.disabled = true;
    const data = new FormData(form);
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
      await reload("消息已发送。");
    } catch (error) { notice.textContent = error.message; }
    finally { send.disabled = false; }
  };
  return form;
}

function ticketPanel(ticket, chat, open, reload) {
  if (!ticket) return card("纠纷工单", null, meta("当前会话暂无关联工单。"));
  return card(`纠纷工单 #${ticket.id}`, null,
    !member && picture(chat.thumbnail, chat.title, "chat-image", true),
    !member && actions(claimTicket(ticket, () => reload("已接单。"))),
    ...(!member ? dispositionPanel(ticket, reload) : []),
    pair("工单状态", ticketState(ticket)),
    pair("关联订单", `#${ticket.order_id}`), pair("发起时间", date(ticket.created_at)),
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
  const current = member ? focus || items[0]?.id : staffTicket?.conversation_id;
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
  const data = await api(`/api/chats/${current}/`), chat = data.conversation;
  const linked = desk.disputes.filter(row => row.conversation_id === chat.id);
  const ticket = staffTicket || linked.find(row => row.status === "待处理") || linked[0];
  const {root: channel, slots} = component("chat");
  const party = config.user === chat.seller_id ? `买家账号 #${chat.buyer_id}` : `卖家账号 #${chat.seller_id}`;
  slots.title.textContent = chat.title;
  slots.note.textContent = `会话 #${chat.id} ｜ ${data.items.length} 条可见消息 ｜ 建立于 ${date(chat.created_at)}`;
  slots.price.textContent = yuan(chat.price);
  slots.order.textContent = ticket ? `订单 #${ticket.order_id} · ${ticket.order__status}`
    : chat.order_id ? `订单 #${chat.order_id} · ${chat.order_status}` : "商品咨询";
  if (member && chat.thumbnail) slots.image.append(picture(chat.thumbnail));
  const thread = slots.thread;
  thread.append(...data.items.map(item => message(item, chat, reload)));
  if (!data.items.length) thread.append(meta("暂无消息。"));
  const canRaise = member && !ticket && ["待发货", "待收货", "纠纷中", "已完成"].includes(chat.order_status);
  for (const signal of data.signals.filter(entry => !entry.ignored)) {
    const strip = node("div", null, "alert");
    strip.append(node("span", `${signal.reason}。系统仅提示，不判定责任或自动退款。`), node("div", null, "grow"));
    if (canRaise) strip.append(requestDispute(chat, reload));
    if (member) strip.append(button("忽略提示", async () => {
      await api(`/api/chats/${current}/ignore-signal/`, {kind: signal.kind});
      await reload("已忽略此提示。");
    }));
    thread.append(strip);
  }
  if (ticket) {
    const system = node("p", null, "sys");
    system.append(node("span", `客服工单 #${ticket.id} · ${ticketState(ticket)}${ticket.verdict ? ` · ${ticket.verdict}` : ""}`));
    thread.append(system);
  }
  if (member) slots.composer.append(composer(chat, reload));
  else if (ownsDispute(ticket)) slots.composer.append(composer(chat, reload, ticket));
  else slots.composer.append(meta("只有当前处理人可以参与交流。"));
  const bar = actions(canRaise && !data.signals.some(entry => !entry.ignored) && requestDispute(chat, reload));
  main.append(node("h3", member ? `与${party}的会话` : "工单关联会话"), channel, bar);
  aside.append(card("会话规则", null, meta("聊天记录与商品绑定，仅双方和有权处理该工单的工作人员可见。消息不能单方面删除，可以仅对自己隐藏。")),
    ticketPanel(ticket, chat, open, reload), card("处理权限", null, pair("客服退款", "须记录裁定及说明"), pair("封号 / 下架", "仅管理员"),
      meta("请保留商品照片、检测凭证与聊天记录。")));
  const scrollToEnd = () => { if (thread.isConnected) thread.scrollTop = thread.scrollHeight; };
  requestAnimationFrame(scrollToEnd);
  return root;
}

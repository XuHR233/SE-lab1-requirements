import {actions, admin, api, button, config, field, meta, modal, reason, staff} from "./ui.js";

export function ownsDispute(ticket) {
  return staff && ticket?.status === "待处理" && ticket.handler_id === config.user
    && (!ticket.escalated_at || admin);
}

export function ticketState(ticket) {
  if (ticket.status !== "待处理") return ticket.status;
  if (ticket.escalated_at) return ticket.handler_id ? "管理员处理中" : "待管理员接管";
  return ticket.handler_id ? "处理中" : "待接单";
}

export function claimTicket(ticket, onClaim) {
  if (!staff || ticket.status !== "待处理" || ticket.handler_id || (ticket.escalated_at && !admin)) return null;
  const label = ticket.escalated_at ? "接管工单" : "接单";
  return button(label, () => modal(label, "确认后，你将成为该订单唯一的处理人。", [],
    () => api(`/api/chats/disputes/${ticket.id}/accept/`, {}), onClaim, label), "primary");
}

export function disposition(ticket, reload) {
  if (!ownsDispute(ticket)) return null;
  const url = `/api/chats/disputes/${ticket.id}/`;
  const action = (label, intro, name, fields, body) => button(label, () => modal(label, intro,
    fields, form => api(url + name + "/", body(form)), () => reload(`${label}已完成。`), label));
  const note = () => reason("处理说明");
  const verdict = () => field("verdict", "裁定结果", {required: true,
    values: [["", "请选择"], ["退款", "退款"], ["终止", "终止"]]});
  const submitNote = form => ({note: form.get("reason")});
  return [actions(
    action("裁定纠纷", "未发货可退款；已发货可裁定终止或退款，商品转为已下架。", "resolve",
      [verdict(), note()], form => ({...submitNote(form), verdict: form.get("verdict")})),
    action("驳回介入", "撤销本次介入，恢复订单履约。", "reject", [note()], submitNote),
  ), !admin && actions(action("转交管理员", "转交后你不再是处理人。管理员必须接管才能裁定。",
    "escalate", [reason("转交原因")], submitNote))].filter(Boolean);
}

export function claimHint(ticket) {
  if (ticket.status !== "待处理") return "工单已结案，处理记录只读。";
  if (ticket.handler_id) return "只有当前处理人可以参与聊天和处置。";
  return ticket.escalated_at ? "管理员接管后才能发言和处置。" : "接单后才能发言和处置。";
}

export function dispositionPanel(ticket, reload) {
  return disposition(ticket, reload) || [meta(claimHint(ticket))];
}

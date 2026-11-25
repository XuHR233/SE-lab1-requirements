import {api, button, date, field, meta, node, staff} from "./ui.js";

const names = {
  "order.create": "创建订单并锁货", "order.pay": "模拟支付入账", "order.ship": "登记发货",
  "order.receive": "确认收货并结算", "order.cancel": "取消订单并释放商品", "order.refund": "记录退款",
  "order.refund_request": "申请退款", "order.resume": "恢复订单处理", "order.settle": "结算货款",
  "order.dispute": "暂停履约", "order.terminate": "裁定终止交易", "dispute.raise": "请求客服介入",
  "dispute.handle": "处理纠纷工单", "dispute.escalate": "转交管理员", "dispute.read": "查看纠纷工单",
  "review.submit": "提交评价",
  "review.delete": "删除评价", "review.appeal": "提交评价申诉", "review.resolve": "处理评价申诉",
  "review.auto": "系统补评", "product.create": "创建商品草稿", "product.edit": "维护商品资料",
  "product.submit": "提交商品审核", "product.audit": "审核商品", "product.revise": "修改并重新送审",
  "product.transit": "变更商品状态", "product.delete": "删除商品草稿", "product.takedown": "违规下架",
  "product.unpublish": "卖家下架", "product.withdraw": "退回草稿", "reservation.create": "提交预售预约",
  "reservation.pay": "模拟支付预约款项", "reservation.confirm": "确认预约或到货", "reservation.cancel": "关闭预约",
  "chat.view": "查看会话", "chat.send": "发送消息或创建会话", "chat.hide": "隐藏消息",
  "chat.ignore_signal": "忽略纠纷提示", "demo.clock": "推进内测时钟或运行定时任务",
};
const targets = {order: "订单", product: "商品", dispute: "工单", user: "账号", reservation: "预约", conversation: "会话", message: "消息"};
const outcomes = {allowed: "成功", denied: "拒绝", error: "失败", failed: "失败"};
const disputeActions = {accept: "接单", takeover: "接管工单", resolve: "裁定纠纷", reject: "驳回介入"};
const details = {reason: "原因", to: "变更后状态", from: "变更前状态", amount: "金额", fee: "手续费",
  seller_net: "卖家净额", tracking_no: "运单号", kind: "款项类型", refund: "退款金额", verdict: "裁定",
  note: "处理说明", version: "版本", images: "图片数量", before: "修改前", after: "修改后",
  approved: "审核通过", uphold: "申诉成立", upheld: "申诉成立", business_now: "业务时间"};

function describe(row) {
  if (typeof row.detail === "string") return row.detail;
  const values = Object.entries(row.detail || {}).filter(([key, value]) => details[key] && value != null && typeof value !== "object");
  return values.map(([key, value]) => `${details[key]}：${typeof value === "boolean" ? (value ? "是" : "否") : value}`).join(" · ") || "—";
}

function table(rows) {
  const frame = node("div", null, "card audit-table"), element = node("table");
  const head = element.createTHead().insertRow();
  for (const label of ["时间", "操作", "结果", ...(staff ? ["操作人 / 目标", "业务变化"] : [])]) {
    head.append(node("th", label));
  }
  const body = element.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    const label = row.action === "dispute.handle" ? disputeActions[row.detail?.action] : null;
    const cells = [date(row.at), label || names[row.action] || "其他业务操作", outcomes[row.result] || "已记录"];
    if (staff) cells.push(`${row.actor_id ? `账号 #${row.actor_id}` : "系统"}${row.target_id ? ` · ${targets[row.target_type] || "记录"} #${row.target_id}` : ""}`, describe(row));
    for (const value of cells) line.append(node("td", value));
  }
  frame.append(element);
  return frame;
}

export async function logs(focus, open) {
  const root = node("div"), form = node("form", null, "filters"), content = node("div");
  const orderId = typeof focus === "object" && focus ? focus.id : focus;
  const page = typeof focus === "object" && focus ? focus.page : 1;
  form.append(field("order_id", "订单号", {type: "number", min: 1, required: !staff, value: orderId}),
    node("button", staff ? "查询（留空看近 7 日）" : "查询我的订单处理记录", "btn primary"));
  form.onsubmit = event => { event.preventDefault(); open("logs", Number(new FormData(form).get("order_id")) || null); };
  root.append(form, content);
  if (!orderId && !staff) return root;
  const query = new URLSearchParams({page, ...(orderId ? {order_id: orderId} : {confirm: 1})});
  const data = await api(`/api/logs/?${query}`);
  content.append(data.rows.length ? table(data.rows) : node("p", "暂无处理记录。", "empty"));
  if (page > 1) content.append(button("上一页", () => open("logs", {id: orderId, page: page - 1})));
  if (data.has_next) content.append(button("下一页", () => open("logs", {id: orderId, page: page + 1})));
  return root;
}

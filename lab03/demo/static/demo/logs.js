import {api, button, date, field, meta, modal, node, pair, staff} from "./ui.js";

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
Object.assign(details, {status: "状态", price: "价格", title: "名称", brand: "品牌", description: "描述",
  category_id: "分类编号", order_id: "关联订单", product_id: "商品编号", product_status: "商品状态",
  deposit: "定金", final_amount: "尾款", paid_at: "付款时间", shipped_at: "发货时间",
  refunded_at: "退款时间", auto_confirm_at: "自动收货时间", final_due_at: "尾款截止时间",
  actor_role: "操作角色", client_type: "端类型", is_presale: "是否预售", presale_valid_days: "预售有效天数",
  received_at: "收货时间", version: "版本", expires_at: "截止时间", rating: "星级", review_id: "评价编号",
  appeal_id: "申诉编号", action: "操作", result: "结果", event: "事件", message_id: "消息编号",
  txn_no: "支付流水号"});

function detailRows(value, prefix = "") {
  if (!value || typeof value !== "object") return [];
  if (Array.isArray(value)) return [pair(prefix || "记录", `共 ${value.length} 项`)];
  return Object.entries(value).flatMap(([key, item]) => {
    const label = `${prefix ? `${prefix} · ` : ""}${details[key] || "其他业务字段"}`;
    if (item && typeof item === "object") return detailRows(item, label);
    if (item == null) return [];
    const text = (key === "action" ? disputeActions[item] || names[item] || "业务操作" : null)
      || (key === "result" ? outcomes[item] || "已记录" : null)
      || (typeof item === "boolean" ? (item ? "是" : "否") : String(item));
    return [pair(label, text)];
  });
}

async function showDetail(row) {
  const detail = await api(`/api/logs/${row.id}/`);
  const roles = {admin: "管理员", support: "客服", member: "普通账号", system: "系统", guest: "游客"};
  const extra = typeof detail.detail === "string" ? [pair("说明", detail.detail)] : detailRows(detail.detail);
  modal("操作详情", "查看本次操作记录的业务信息。", [
    pair("时间", date(detail.at)), pair("操作角色", roles[detail.actor_role] || "—"),
    pair("来源 IP", detail.ip || "—"), pair("端类型", detail.client_type || "—"), ...extra,
  ], () => {}, () => {}, "关闭");
}

function describe(row) {
  if (typeof row.detail === "string") return row.detail;
  const values = Object.entries(row.detail || {}).filter(([key, value]) => details[key] && value != null && typeof value !== "object");
  return values.map(([key, value]) => `${details[key]}：${typeof value === "boolean" ? (value ? "是" : "否") : value}`).join(" · ") || "—";
}

function table(rows) {
  const frame = node("div", null, "card audit-table"), element = node("table");
  const head = element.createTHead().insertRow();
  for (const label of ["时间", "操作", "结果", ...(staff ? ["操作人 / 目标", "业务变化", "详情"] : [])]) {
    head.append(node("th", label));
  }
  const body = element.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    const label = row.action === "dispute.handle" ? disputeActions[row.detail?.action] : null;
    const cells = [date(row.at), label || names[row.action] || "其他业务操作", outcomes[row.result] || "已记录"];
    if (staff) cells.push(`${row.actor_id ? `账号 #${row.actor_id}` : "系统"}${row.target_id ? ` · ${targets[row.target_type] || "记录"} #${row.target_id}` : ""}`, describe(row));
    for (const value of cells) line.append(node("td", value));
    if (staff) {
      const cell = node("td");
      cell.append(button("查看详情", () => showDetail(row)));
      line.append(cell);
    }
  }
  frame.append(element);
  return frame;
}

export async function logs(focus, open) {
  const root = node("div"), form = node("form", null, "filters"), content = node("div");
  const state = focus && typeof focus === "object" ? focus : {filters: focus ? {order_id: focus} : {}};
  const filters = state.filters || {}, page = state.page || 1;
  const numberField = (key, label) => field(key, label, {type: "number", min: 1, value: filters[key], required: !staff});
  form.append(numberField("order_id", "订单号"));
  if (staff) form.append(numberField("product_id", "商品编号"), numberField("actor_id", "操作人账号 ID"),
    field("action", "业务动作", {value: filters.action, values: [["", "全部"], ...Object.entries(names)]}),
    field("result", "操作结果", {value: filters.result, values: [["", "全部"], ["allowed", "成功"], ["denied", "拒绝 / 失败"]]}),
    field("start", "开始时间（北京时间）", {type: "datetime-local", value: filters.start}),
    field("end", "结束时间（北京时间，不含）", {type: "datetime-local", value: filters.end}));
  form.append(node("button", staff ? "查询" : "查询我的订单处理记录", "btn primary"));
  form.onsubmit = event => {
    event.preventDefault();
    const selected = Object.fromEntries([...new FormData(form)].filter(([, value]) => value));
    open("logs", {filters: selected});
  };
  root.append(form, content);
  if (!filters.order_id && !staff) return root;
  const query = new URLSearchParams({...filters, page, ...(state.confirmed ? {confirm: 1} : {})});
  const data = await api(`/api/logs/?${query}`);
  if (data.confirmation_required) {
    content.append(meta("未设置筛选条件。确认后查询最近 7 天。"),
      button("确认查询近 7 天", () => open("logs", {...state, confirmed: true}), "btn primary"));
    return root;
  }
  content.append(data.rows.length ? table(data.rows) : node("p", "暂无处理记录。", "empty"));
  if (page > 1) content.append(button("上一页", () => open("logs", {...state, page: page - 1})));
  if (data.has_next) content.append(button("下一页", () => open("logs", {...state, page: page + 1})));
  if (data.limit_reached) content.append(meta("已到查询上限，请缩小筛选范围。"));
  return root;
}

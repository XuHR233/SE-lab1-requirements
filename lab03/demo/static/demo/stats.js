import {api, button, card, config, date, field, member, meta, node, staff, yuan} from "./ui.js";

function charts(data) {
  const columns = node("div", null, "two"), trend = node("section", null, "panel2 card");
  const chart = node("div", null, "chart"), peak = Math.max(1, ...data.daily.map(row => row.count));
  trend.append(node("h3", "近 7 日成交量与成交额"), node("p", "仅统计已完成订单", "d"), chart);
  for (const row of data.daily) {
    const col = node("div", null, row.count ? "col" : "col off"), bar = node("i");
    bar.style.height = `${Math.max(6, Math.round(100 * row.count / peak))}px`;
    col.title = `${row.day} · ${row.count} 笔 · ${yuan(row.amount)}`;
    col.append(node("b", row.count), bar, node("span", row.day.slice(5)));
    chart.append(col);
  }
  const categories = node("section", null, "panel2 card");
  categories.append(node("h3", "成交构成"), node("p", "按商品分类汇总成交金额", "d"));
  for (const row of data.categories) {
    const group = node("div", null, "category-bar"), caption = node("div", null, "category-caption small");
    const bar = node("div", null, "bar"), fill = node("i");
    const share = Number(data.amount) > 0 ? 100 * Number(row.amount) / Number(data.amount) : 0;
    fill.style.width = `${share}%`;
    caption.append(node("span", row.product__category__name), node("b", `${yuan(row.amount)} · ${share.toFixed(1)}%`));
    bar.append(fill);
    group.append(caption, bar);
    categories.append(group);
  }
  if (!data.categories.length) categories.append(meta("该区间暂无成交。"));
  columns.append(trend, categories);
  return columns;
}

function recent(rows) {
  const section = node("section", null, "card table-scroll recent-sales"), table = node("table");
  const head = table.createTHead().insertRow(), body = table.createTBody();
  for (const label of ["商品", "成交时间", "成交金额", "手续费（1.5%）"]) head.append(node("th", label));
  for (const item of rows) {
    const row = body.insertRow();
    for (const value of [item.product__title, date(item.received_at), yuan(item.amount), item.settlement__fee == null ? "—" : yuan(item.settlement__fee)]) {
      row.append(node("td", value));
    }
  }
  section.append(table, node("p", rows.length ? "最近成交 · 手续费按成交额 1.5% 计算，单笔最高 200 元" : "该区间暂无成交。", "small muted"));
  return section;
}

export async function stats(focus, open) {
  const seller = focus?.seller || focus || (member ? config.user : null), period = focus?.period || "30";
  const root = node("div", null, "lab2-stats"), form = node("form", null, "filters");
  form.append(field("seller", "个人账号 ID", {type: "number", min: 1, required: true, value: seller}), node("button", "按账号查询", "btn primary"));
  if (member) form.append(button("查看我的统计", () => open("stats", config.user)));
  form.onsubmit = event => { event.preventDefault(); open("stats", Number(new FormData(form).get("seller"))); };
  root.append(form);
  if (!seller) return root;
  const endpoint = staff ? `/api/staff/sellers/${seller}/stats/` : `/api/sellers/${seller}/`;
  const own = staff || seller === config.user;
  const [data, extra] = await Promise.all([api(endpoint), own ? api(`/demo/sellers/${seller}/stats/?period=${period}`) : null]);
  const report = extra?.stats || data.stats, dashboard = extra?.dashboard;
  const rate = report.refund_rate === "—" ? "—" : `${(Number(report.refund_rate) * 100).toFixed(2)}%`;
  const scope = node("div", null, "toolbar");
  scope.append(node("span", `卖家账号 #${data.seller_id}`, "small muted"), node("div", null, "grow"));
  if (dashboard) {
    const segments = node("div", null, "seg");
    for (const [value, label] of [["30", "近 30 天"], ["7", "近 7 天"], ["month", "本月"]]) {
      const control = button(null, () => open("stats", {seller, period: value}), "nav-link");
      control.setAttribute("aria-pressed", String(period === value));
      control.append(node("span", label, period === value ? "on" : ""));
      segments.append(control);
    }
    scope.append(segments);
  }
  const metrics = node("div", null, "stat-grid");
  for (const [label, value, note] of [["成交笔数", report.completed, "仅统计已完成订单"],
    dashboard ? ["成交金额", yuan(dashboard.amount), `手续费 ${yuan(dashboard.fee)}`] : ["退款笔数", report.refunded, "已退款订单"],
    ["在架商品", report.on_sale, "已发布与已预售"], ["退款率", rate, `已完成 ${report.completed} 笔 · 已退款 ${report.refunded} 笔`]]) {
    const metric = node("div", null, "stat card");
    metric.append(node("div", label, "k"), node("div", value, "v"), node("div", note, "d"));
    metrics.append(metric);
  }
  root.append(scope, metrics);
  if (dashboard) root.append(charts(dashboard), recent(dashboard.latest));
  root.append(card("收到的评价", null, ...data.reviews.map(row => meta(`${row.rating} 星${row.automatic ? "（系统补评）" : ""} · ${row.content || "无文字"}`)),
    data.reviews.length === 0 && meta("暂无评价。")));
  return root;
}

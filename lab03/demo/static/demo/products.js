import {actions, admin, api, badge, button, card, component, config, date, field, grid, imageAccept, member, meta, modal, node, picture, reason, uid, yuan} from "./ui.js";

const publicStatus = ["已发布", "已预售"];
const categoryOptions = desk => desk.categories.map(item => [item.id, item.name]);
const categoryName = (desk, item) => desk.categories.find(category => category.id === item.category_id)?.name || "未分类";

function productModal(desk, item, reload) {
  const editing = Boolean(item);
  const base = editing ? `/api/products/${item.id}/` : "/api/products/";
  const label = editing ? "保存修改" : "保存草稿";
  const change = editing && publicStatus.includes(item.status) ? "revise/" : "update/";
  const fields = [
    field("title", "商品标题", {required: true, maxlength: 120, value: item?.title}),
    field("category_id", "分类", {values: categoryOptions(desk), required: true, value: item?.category_id}),
    field("brand", "品牌", {maxlength: 60, value: item?.brand}),
    field("price", "价格（元）", {type: "number", required: true, min: ".01", step: ".01", value: item?.price}),
    field("description", "商品描述", {type: "textarea", maxlength: 2000, value: item?.description}),
    field("is_presale", "设为预售", {type: "checkbox", value: item?.is_presale}),
    field("deposit", "定金（元）", {type: "number", min: "0", step: ".01", value: item?.deposit || "0"}),
    field("final_amount", "尾款（元）", {type: "number", min: "0", step: ".01", value: item?.final_amount || "0"}),
    field("presale_valid_days", "预约有效天数", {type: "number", min: "0", max: "180", value: item?.presale_valid_days || "7"}),
  ];
  const pre = fields[5].querySelector("input");
  const toggle = () => { for (const entry of fields.slice(6)) entry.hidden = !pre.checked; };
  pre.addEventListener("change", toggle);
  toggle();
  modal(editing ? `编辑 ${item.title}` : "创建商品草稿", "预售时定金与尾款之和须等于价格。保存草稿后可上传图片并送审。",
    fields, data => {
      const body = Object.fromEntries(data);
      body.is_presale = data.has("is_presale");
      if (!body.is_presale) Object.assign(body, {deposit: 0, final_amount: 0, presale_valid_days: 0});
      if (editing) body.version = item.version;
      return api(base + (editing ? change : ""), body);
    }, () => reload(editing ? "商品修改已保存。" : "草稿已创建，请上传图片并送审。"), label);
}

function summary(item, desk) {
  const identity = node("div", null, "item-summary"), copy = node("div");
  copy.append(node("h3", item.title), meta(`${categoryName(desk, item)} · ${yuan(item.price)} · ${item.status}`),
    meta(item.description || "未填写商品描述。"));
  if (item.is_presale) copy.append(meta(`定金 ${yuan(item.deposit)} · 尾款 ${yuan(item.final_amount)}`));
  identity.append(picture(item.thumbnail), copy);
  return identity;
}

function uploadButton(item, reload, live) {
  const label = live ? "上传图片并重审" : "上传图片";
  const formatHint = "支持 JPG、PNG、WebP、GIF，单张最多 5 MB。";
  return button(label, () => {
    const input = field("file", "选择商品图片", {type: "file", required: true, accept: imageAccept});
    modal(label, live ? `${formatHint} 上传后商品将转为待审核，审核通过后重新展示。` : formatHint, [input], data => {
      data.set("version", item.version);
      return api(`/demo/products/${item.id}/image/`, data);
    }, () => reload(live ? "图片已上传，商品已重新送审。" : "图片已上传，可在商品和送审预览中查看。"), live ? label : "上传");
  });
}

function productCard(item, desk, itemActions, cover = false) {
  return card(`#${item.id} · ${item.title}`, item.status,
    cover && picture(item.thumbnail, "商品主图", "cover"), node("p", yuan(item.price), "price"), meta(`${categoryName(desk, item)}${item.brand ? ` · ${item.brand}` : ""}`),
    item.description && node("p", item.description, "detail-text"),
    item.is_presale && meta(`定金 ${yuan(item.deposit)} · 尾款 ${yuan(item.final_amount)} · ${item.presale_valid_days} 天`),
    itemActions);
}

export function inventory(desk, reload) {
  const wrapper = node("div", null, "lab2-inventory");
  const head = node("div", null, "head");
  head.append(node("span", `全部商品共 ${desk.products.length} 件，公开售卖中 ${desk.products.filter(item => publicStatus.includes(item.status)).length} 件`, "muted small"), node("div", null, "grow"));
  if (member) head.append(button("新建商品", () => productModal(desk, null, reload), "primary"));
  const {root: table, slots} = component("inventory");
  function row(item) {
    const base = `/api/products/${item.id}/`;
    const change = (name, action, data = {}) => button(name, () => modal(name,
      `商品 #${item.id}`, [summary(item, desk)], () => api(base + action + "/", data), () => reload(`${name}成功。`), name));
    const withReason = (name, action, data) => button(name, () => modal(name, `商品 #${item.id}`,
      [summary(item, desk), reason("理由")], form => api(base + action + "/", {...data, reason: form.get("reason")}), () => reload(`${name}成功。`), name));
    const list = [];
    if (admin) {
      if (item.status === "待审核") list.push(change("审核通过", "audit", {approve: true}), withReason("审核驳回", "audit", {approve: false}));
      if ([...publicStatus, "待付款锁定", "已下架"].includes(item.status)) list.push(withReason("违规下架", "takedown", {}));
    } else {
      const live = publicStatus.includes(item.status);
      if (["草稿", "已驳回"].includes(item.status) || live) {
        list.push(button(live ? "修改并重审" : "编辑草稿", () => productModal(desk, item, reload)),
          uploadButton(item, reload, live));
      }
      if (item.status === "草稿") list.push(item.image_count > 0 ? change("提交审核", "submit") :
        button("提交审核", () => modal("缺少商品图片", "上传至少一张图片后才能送审。", [summary(item, desk)], () => {}, () => {}, "知道了")),
      change("删除草稿", "delete"));
      if (["待审核", "已驳回", "已下架", "违规下架"].includes(item.status)) list.push(change("回到草稿", "withdraw"));
      if (live) list.push(change("下架", "unpublish"));
    }
    const row = node("tr"), identity = node("div", null, "pcell"), imageBox = node("div", null, "pimg"), copy = node("div");
    if (item.thumbnail) imageBox.append(picture(item.thumbnail));
    copy.append(node("div", item.title, "ptitle"), node("p", `${categoryName(desk, item)} · 图片 ${item.image_count} 张 · 版本 ${item.version}`, "small muted"));
    if (item.description) copy.append(node("p", item.description, "small muted ptitle"));
    identity.append(imageBox, copy);
    const controls = node("div", null, "ops");
    controls.append(...list);
    for (const value of [identity, badge(item.status), yuan(item.price), item.view_count, date(item.updated_at), controls]) {
      const cell = node("td");
      cell.append(value == null ? "—" : value);
      row.append(cell);
    }
    return row;
  }
  const states = node("div", null, "state-tabs");
  function select(current, status) {
    for (const control of states.children) {
      control.firstChild.classList.toggle("on", control === current);
      control.setAttribute("aria-pressed", String(control === current));
    }
    const rows = desk.products.filter(item => !status || item.status === status);
    if (rows.length) slots.rows.replaceChildren(...rows.map(row));
    else {
      const empty = node("td", "暂无该状态的商品。", "muted");
      empty.colSpan = 6;
      const placeholder = node("tr");
      placeholder.append(empty);
      slots.rows.replaceChildren(placeholder);
    }
  }
  for (const status of [null, ...new Set(desk.products.map(item => item.status))]) {
    const control = button(null, () => select(control, status), "state-filter");
    const label = node("span", status || "全部");
    label.append(node("b", desk.products.filter(item => !status || item.status === status).length));
    control.append(label);
    states.append(control);
  }
  select(states.firstChild, null);
  wrapper.append(head, states, table);
  return wrapper;
}

function productActions(item, open) {
  const list = config.user ? [button("卖家主页", () => open("stats", item.seller_id))] : [];
  if (!member || config.user === item.seller_id || !publicStatus.includes(item.status)) return list;
  list.push(button("联系卖家", async () => {
    const chat = await api("/api/chats/", {product_id: item.id});
    await open("chats", chat.id);
  }));
  const label = item.is_presale ? "提交预约" : "创建订单";
  list.push(button(label, () => {
    const body = {version: item.version, request_key: uid()};
    const url = item.is_presale ? `/api/products/${item.id}/reservations/` : "/api/orders/";
    if (!item.is_presale) body.product_id = item.id;
    modal(label, `${item.title} · ${yuan(item.price)}。此操作将占用商品，不会扣款。`, [],
      () => api(url, body), result => open(item.is_presale ? "reservations" : "orders", result.id, `${label}成功。`));
  }, "primary"));
  return list;
}

export function productDetail(desk, item, open) {
  const root = node("div", null, "lab2-product"), detail = node("div", null, "detail");
  const gallery = node("div", null, "gallery"), big = node("div", null, "big"), info = node("div", null, "info");
  big.append(item.thumbnail ? picture(item.thumbnail, "商品主图") : "暂无商品主图");
  gallery.append(big);
  const tags = node("div", null, "tags"), pricebox = node("div", null, "pricebox"), line = node("div", null, "line1");
  tags.append(node("span", item.is_presale ? "预售" : "现货", "tag hot"),
    node("span", categoryName(desk, item), "tag"));
  if (item.brand) tags.append(node("span", item.brand, "tag"));
  line.append(node("b", yuan(item.price)), badge(item.status));
  pricebox.append(line);
  if (item.is_presale) {
    const amounts = node("div", null, "line2");
    for (const [label, value] of [["定金", yuan(item.deposit)], ["尾款", yuan(item.final_amount)], ["预约有效期", `${item.presale_valid_days} 天`]]) {
      const entry = node("span", label);
      entry.append(node("b", value));
      amounts.append(entry);
    }
    pricebox.append(amounts);
  }
  const seller = node("div", null, "seller"), caption = node("div");
  caption.append(node("div", `卖家账号 #${item.seller_id}`, "nm"), node("p", "二手集市 · 内测交易", "sub"));
  seller.append(node("div", "卖", "av"), caption);
  const controls = productActions(item, open);
  seller.append(node("div", null, "grow"), ...controls.filter(control => !control.classList.contains("primary")));
  info.append(node("h1", item.title), tags, pricebox, seller,
    card("商品描述", null, node("p", item.description || "卖家未填写商品描述。", "detail-text")));
  if (item.is_presale) {
    const flow = node("div", null, "stepsbox"), steps = node("div", null, "steps");
    for (const [index, title] of ["提交预约", "支付定金", "支付尾款"].entries()) {
      const step = node("div", null, index === 0 ? "step now" : "step");
      step.append(node("i"), node("b", title));
      steps.append(step);
    }
    flow.append(node("h3", "预售购买流程"), steps);
    info.append(flow, node("p", "确认到货后次日零点开放尾款，窗口为 24 小时。", "notice"));
  }
  const final = controls.find(control => control.classList.contains("primary"));
  if (final) info.append(actions(final));
  detail.append(gallery, info);
  root.append(button("返回商品列表", () => open("market"), "btn ghost"), detail);
  return root;
}

export function market(desk, rows, filters, apply, open) {
  const wrapper = node("div"), form = node("form", null, "filters market-filters");
  const value = name => filters.get(name) || "";
  form.append(
    field("keyword", "搜索商品", {value: value("keyword"), placeholder: "标题 / 品牌 / 描述"}),
    field("category", "分类", {values: [["", "全部"], ...categoryOptions(desk)], value: value("category")}),
    field("tag", "标签", {values: [["", "全部"], ...desk.tags.map(name => [name, name])], value: value("tag")}),
    field("status", "类型", {values: [["", "全部"], ...publicStatus.map(name => [name, name])], value: value("status")}),
    field("sort", "排序", {values: [["newest", "最新"], ["price_asc", "价格升序"], ["price_desc", "价格降序"], ["oldest", "最早"]], value: value("sort") || "newest"}),
    field("price_min", "最低价", {type: "number", min: 0, step: ".01", value: value("price_min")}),
    field("price_max", "最高价", {type: "number", min: 0, step: ".01", value: value("price_max")}),
    field("start", "发布起点（北京时间）", {type: "datetime-local", value: value("start")}),
    field("end", "发布终点（不含）", {type: "datetime-local", value: value("end")}),
  );
  const submit = node("button", "搜索", "btn primary");
  form.append(submit);
  form.onsubmit = event => {
    event.preventDefault();
    apply(new URLSearchParams(new FormData(form)));
  };
  wrapper.append(form, grid(rows, item => {
    const list = [button("查看详情", () => open("product", item.id)), ...productActions(item, open)];
    return productCard(item, desk, actions(...list), true);
  }, "暂无符合条件的在售商品。卖家提交审核、管理员通过后，商品就会出现在这里。"));
  const page = Number(value("page") || 1);
  const move = target => { const next = new URLSearchParams(filters); next.set("page", target); return apply(next); };
  wrapper.append(actions(page > 1 && button("上一页", () => move(page - 1)),
    meta(`第 ${page} 页`), rows.length === 12 && button("下一页", () => move(page + 1))));
  return wrapper;
}

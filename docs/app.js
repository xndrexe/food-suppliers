"use strict";
// Прототип без сервера: база в data.js, мой список и заметки — в localStorage.
// Тексты с чужих сайтов вставляются только через textContent.

const DATA = window.SUPPLIERS || { suppliers: [], cities: [], categories: [] };
const STATUSES = ["не связывались", "написал / позвонил", "жду прайс", "договорились о пробной партии", "не подходит"];
const KEY = "food-suppliers:list:v1";
const $ = (s, root = document) => root.querySelector(s);

const state = { cat: DATA.categories[0] || "", city: DATA.cities[0] || "", q: "", sort: "score", flags: new Set() };
let list = load();

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function save() { localStorage.setItem(KEY, JSON.stringify(list)); }
const idOf = (s) => `${s.domain}|${s.category}|${s.search_city}`;
const byId = new Map(DATA.suppliers.map((s) => [idOf(s), s]));
const val = (s, f) => (s[f] && s[f].value) || "";

function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}
function safeUrl(u) {
  try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; }
}
function phoneText(p) {
  const m = p.match(/^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/);
  return m ? `+7 ${m[1]} ${m[2]}-${m[3]}-${m[4]}` : p;
}

// ---------- фильтры ----------
function renderChips() {
  const box = $("#cats");
  box.replaceChildren();
  for (const c of DATA.categories) {
    const n = DATA.suppliers.filter((s) => s.category === c && s.search_city === state.city).length;
    const b = el("button", { class: "chip", role: "radio", "aria-checked": String(c === state.cat) }, c);
    b.append(el("span", { class: "n" }, n));
    b.onclick = () => { state.cat = c; renderChips(); renderList(); };
    box.append(b);
  }
}
function initFilters() {
  const city = $("#city");
  for (const c of DATA.cities) city.append(el("option", { value: c }, c));
  city.onchange = () => { state.city = city.value; renderChips(); renderList(); };
  $("#q").oninput = (e) => { state.q = e.target.value.trim().toLowerCase(); renderList(); };
  $("#sort").onchange = (e) => { state.sort = e.target.value; renderList(); };
  document.querySelectorAll("[data-f]").forEach((cb) => {
    cb.onchange = () => { cb.checked ? state.flags.add(cb.dataset.f) : state.flags.delete(cb.dataset.f); renderList(); };
  });
}

function visible() {
  const f = state.flags;
  let out = DATA.suppliers.filter((s) => {
    if (f.has("shortlist")) return list[idOf(s)];  // мой список — по всем категориям и городам
    if (s.category !== state.cat || s.search_city !== state.city) return false;
    if (f.has("price") && !val(s, "price")) return false;
    if (f.has("min_order") && !val(s, "min_order")) return false;
    if (f.has("documents") && !val(s, "documents") && !(s.doc_mentions || []).length) return false;
    if (f.has("producer") && s.kind !== "производитель") return false;
    if (state.q) {
      const hay = [s.name, s.domain, s.summary, ...(s.products || [])].join(" ").toLowerCase();
      if (!hay.includes(state.q)) return false;
    }
    return true;
  });
  const by = {
    score: (a, b) => b.score - a.score || b.completeness - a.completeness,
    completeness: (a, b) => b.completeness - a.completeness || b.score - a.score,
    name: (a, b) => (a.name || a.domain).localeCompare(b.name || b.domain, "ru"),
  }[state.sort];
  return out.sort(by);
}

// ---------- карточки ----------
const FACTS = [
  ["min_order", "Минимальный заказ"], ["price", "Цена"], ["delivery", "Доставка"],
  ["regions", "Регион работы"], ["documents", "Документы"],
];

function fact(s, field, label) {
  const box = el("div", { class: "fact" });
  box.append(el("dt", {}, label));
  const v = val(s, field);
  if (field === "documents" && !v && (s.doc_mentions || []).length) {
    box.append(el("dd", {}, "на сайте упоминаются: " + s.doc_mentions.join(", ")));
    return box;
  }
  if (!v) { box.classList.add("none"); box.append(el("dd", {}, "на сайте нет — спросить")); return box; }
  box.append(el("dd", {}, v));
  if (s[field].quote) {
    const d = el("details");
    d.append(el("summary", {}, "цитата с сайта"), el("blockquote", {}, "«" + s[field].quote + "»"));
    box.append(d);
  }
  return box;
}

function card(s) {
  const node = $("#cardTpl").content.firstElementChild.cloneNode(true);
  const id = idOf(s);
  const sc = $(".score", node);
  $("b", sc).textContent = s.score;
  sc.classList.add(s.score >= 55 ? "hi" : s.score >= 35 ? "mid" : "low");

  const a = $(".title a", node);
  a.textContent = s.name || s.domain;
  const url = safeUrl(s.site);
  if (url) a.href = url; else a.removeAttribute("href");
  const meta = $(".meta", node);
  if (s.kind) meta.append(el("span", { class: "tag" }, s.kind));
  meta.append(document.createTextNode([s.city, s.domain].filter(Boolean).join(" · ")));
  if (state.flags.has("shortlist")) meta.append(document.createTextNode(` · ${s.category}, ${s.search_city}`));

  $(".summary", node).textContent = s.summary || "";
  const prods = $(".products", node);
  for (const p of (s.products || []).slice(0, 8)) prods.append(el("span", {}, p));

  const facts = $(".facts", node);
  for (const [f, l] of FACTS) facts.append(fact(s, f, l));

  for (const r of s.reasons || []) $(".plus", node).append(el("li", {}, r));
  for (const r of s.minuses || []) $(".minus", node).append(el("li", {}, r));

  if ((s.ask || []).length) {
    const ask = $(".ask", node);
    ask.append(el("b", {}, "Что спросить при звонке"));
    const ol = el("ol");
    for (const q of s.ask) ol.append(el("li", {}, q));
    ask.append(ol);
  }

  const c = $(".contacts", node);
  for (const p of s.phones || []) c.append(el("a", { href: "tel:" + p }, "☎ " + phoneText(p)));
  for (const m of s.emails || []) c.append(el("a", { href: "mailto:" + m }, "✉ " + m));
  if (s.inn) c.append(el("a", { href: "https://egrul.nalog.ru/", target: "_blank", rel: "noopener", title: "Проверить в ЕГРЮЛ по ИНН" }, "ИНН " + s.inn));
  const src = (s.sources || []).map(safeUrl).filter(Boolean);
  if (src.length) {
    const d = el("details", { class: "small" });
    d.append(el("summary", {}, `источник: ${src.length} стр. сайта`));
    for (const u of src) { const l = el("a", { href: u, target: "_blank", rel: "noopener" }, u); d.append(el("div", {}, "")); d.lastChild.append(l); }
    c.append(d);
  }

  const star = $(".star", node);
  const on = Boolean(list[id]);
  star.setAttribute("aria-pressed", String(on));
  star.textContent = on ? "★" : "☆";
  star.title = on ? "Убрать из моего списка" : "В мой список";
  node.classList.toggle("picked", on);
  star.onclick = () => toggle(id);
  return node;
}

function renderList() {
  const items = visible();
  const box = $("#list");
  box.replaceChildren(...items.map(card));
  const where = state.flags.has("shortlist") ? "в моём списке" : `«${state.cat}», ${state.city}`;
  $("#count").textContent = items.length ? `${items.length} поставщ. — ${where}` : "";
  if (!items.length) {
    box.append(el("div", { class: "empty" },
      state.flags.has("shortlist") ? "В списке пока никого: отметьте поставщиков звёздочкой."
        : "Под эти условия никого нет — снимите часть галочек или смените город."));
  }
}

// ---------- мой список ----------
function toggle(id) {
  if (list[id]) delete list[id];
  else list[id] = { status: STATUSES[0], note: "", added: Date.now() };
  save(); renderList(); renderShortlist();
}

function renderShortlist() {
  const ids = Object.keys(list).filter((id) => byId.has(id)).sort((a, b) => list[a].added - list[b].added);
  const ol = $("#shortlist");
  ol.replaceChildren();
  $("#slEmpty").hidden = ids.length > 0;
  $(".side").classList.toggle("empty", !ids.length);  // на телефоне пустой список не занимает экран
  $("#slCount").textContent = ids.length ? `· ${ids.length}` : "";
  $("#compareBtn").disabled = ids.length < 2;
  $("#compareBtn").textContent = ids.length > 4 ? "Сравнить первых четырёх" : "Сравнить отмеченных";
  $("#csvBtn").disabled = !ids.length;
  for (const id of ids) {
    const s = byId.get(id), item = list[id];
    const li = el("li", { class: "sl-item" });
    const top = el("div", { class: "sl-top" });
    top.append(el("b", {}, s.name || s.domain));
    const x = el("button", { title: "Убрать", "aria-label": "Убрать из списка" }, "×");
    x.onclick = () => toggle(id);
    top.append(x);
    li.append(top, el("div", { class: "muted" }, `${s.category} · ${s.search_city} · ${(s.phones || [])[0] ? phoneText(s.phones[0]) : s.domain}`));
    const sel = el("select", { "aria-label": "Статус" });
    for (const st of STATUSES) sel.append(el("option", { value: st }, st));
    sel.value = item.status;
    sel.onchange = () => { item.status = sel.value; save(); };
    const ta = el("textarea", { rows: "2", placeholder: "Заметка: что сказали, цена, сроки…", "aria-label": "Заметка" });
    ta.value = item.note;
    ta.oninput = () => { item.note = ta.value; save(); };
    li.append(sel, ta);
    ol.append(li);
  }
}

// ---------- сравнение ----------
function compare() {
  const ids = Object.keys(list).filter((id) => byId.has(id)).slice(0, 4);
  const rows = [
    ["Оценка", (s) => s.score, "max"],
    ["Тип", (s) => s.kind || "—"],
    ["Город", (s) => s.city || "—"],
    ["Мин. заказ", (s) => val(s, "min_order") || "—"],
    ["Цена", (s) => val(s, "price") || "—"],
    ["Доставка", (s) => val(s, "delivery") || "—"],
    ["Регион работы", (s) => val(s, "regions") || "—"],
    ["Документы", (s) => val(s, "documents") || (s.doc_mentions || []).join(", ") || "—"],
    ["ИНН", (s) => s.inn || "—"],
    ["Телефон", (s) => (s.phones || []).map(phoneText).join(", ") || "—"],
    ["Почта", (s) => (s.emails || []).join(", ") || "—"],
    ["Данных на сайте", (s) => s.completeness + "%", "max"],
    ["Статус", (s) => list[idOf(s)].status],
    ["Заметка", (s) => list[idOf(s)].note || "—"],
  ];
  const sup = ids.map((id) => byId.get(id));
  const t = $("#cmpTable");
  t.replaceChildren();
  const head = el("tr");
  head.append(el("th", {}, ""));
  for (const s of sup) head.append(el("th", { scope: "col" }, s.name || s.domain));
  t.append(head);
  for (const [label, get, best] of rows) {
    const tr = el("tr");
    tr.append(el("th", { scope: "row" }, label));
    const vals = sup.map(get);
    const top = best ? Math.max(...vals.map((v) => parseFloat(v) || 0)) : null;
    vals.forEach((v) => tr.append(el("td", best && parseFloat(v) === top ? { class: "best" } : {}, String(v))));
    t.append(tr);
  }
  $("#compare").showModal();
}

// ---------- выгрузка ----------
function csv() {
  const cols = ["Поставщик", "Категория", "Город поиска", "Тип", "Оценка", "Статус", "Заметка",
    "Телефоны", "Почта", "Сайт", "ИНН", "Мин. заказ", "Цена", "Доставка", "Регион работы", "Документы"];
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(esc).join(";")];
  for (const id of Object.keys(list).filter((i) => byId.has(i))) {
    const s = byId.get(id), it = list[id];
    lines.push([s.name || s.domain, s.category, s.search_city, s.kind, s.score, it.status, it.note,
      (s.phones || []).map(phoneText).join(", "), (s.emails || []).join(", "), s.site, s.inn,
      val(s, "min_order"), val(s, "price"), val(s, "delivery"), val(s, "regions"),
      val(s, "documents") || (s.doc_mentions || []).join(", ")].map(esc).join(";"));
  }
  // BOM и «;» — чтобы Excel сразу открыл кириллицу по столбцам
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = el("a", { href: URL.createObjectURL(blob), download: "поставщики.csv" });
  document.body.append(a); a.click(); a.remove();
}

// ---------- старт ----------
document.querySelectorAll("[data-close]").forEach((b) => { b.onclick = () => b.closest("dialog").close(); });
document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
$("#howBtn").onclick = () => $("#how").showModal();
$("#compareBtn").onclick = compare;
$("#csvBtn").onclick = csv;
if (DATA.collected_at) {
  const d = new Date(DATA.collected_at);
  $("#stamp").textContent = `База собрана ${d.toLocaleDateString("ru-RU")}: ${DATA.suppliers.length} карточек поставщиков`;
}
initFilters();
renderChips();
renderList();
renderShortlist();

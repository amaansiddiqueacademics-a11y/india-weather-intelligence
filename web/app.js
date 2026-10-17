/* India Weather Intelligence: every number rendered here comes from /api (PostgreSQL warehouse). */
const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");
const PAL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#6e6e73", "#8c564b"];
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };
const S = { meta: null, city: store.get("city", "BOM") };

const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();
async function api(path, opts) {
  const r = await fetch("/api" + path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
  return j;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtn = (v, d = 0) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt = (v, d = 2) => (v === null || v === undefined ? "n/a" : typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toFixed(d)) : esc(v));
const cityName = (id) => S.meta.locations.find((l) => l.location_id === id)?.city ?? id;
const skeleton = () => `<div class="skeleton" role="status" aria-label="Loading"></div>`;
const err = (e) => `<div class="error" role="alert"><b>Could not load this section.</b> ${esc(e.message)}</div>`;
const maxBy = (a, f) => a.reduce((m, x) => (f(x) > f(m) ? x : m), a[0]);
const minBy = (a, f) => a.reduce((m, x) => (f(x) < f(m) ? x : m), a[0]);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = (a) => { const v = a.filter((x) => x !== null && x !== undefined && !Number.isNaN(+x)).map(Number); return v.length ? [Math.min(...v), Math.max(...v)] : [NaN, NaN]; };

/* ------------------------------------------------------------- UI helpers */
const ICONS = { trend: '<polyline points="3 17 9 11 13 15 21 6"/><polyline points="15 6 21 6 21 12"/>', drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
  db: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>', check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>', alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', pin: '<path d="M12 21s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/>',
  spark: '<path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>', wind: '<path d="M3 8h11a3 3 0 1 0-3-3M3 16h14a3 3 0 1 1-3 3M3 12h8"/>', target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>', shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  cloud: '<path d="M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.2 11 3.5 3.5 0 0 0 7 18z"/>', leaf: '<path d="M5 19c0-8 5-13 14-14 0 9-5 14-13 14z"/><path d="M5 19c3-5 6-8 10-10"/>',
  sunrise: '<path d="M3 19h18M7.5 15a4.5 4.5 0 0 1 9 0M12 5v4M12 5l-2.2 2.2M12 5l2.2 2.2"/>', humid: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/><path d="M9.5 14.5a2.5 2.5 0 0 0 2.5 2.5"/>' };
const ico = (n, c = "#0a6bd6", s = 22) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" fill="none" stroke="${c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n]}</svg>`;
const insights = (list) => `<div class="insights">${list.map(([ic, col, t, x]) => `<div class="ins reveal"><span class="ib" style="background:${col}1f">${ico(ic, col)}</span><div><b>${t}</b><p>${x}</p></div></div>`).join("")}</div>`;
const kpi = (v, label, o = {}) => `<div class="kpi reveal ${o.cls ?? ""}"><b data-count="${v}" data-dec="${o.dec ?? 0}" data-suffix="${esc(o.suffix ?? "")}">0</b><span>${label}</span></div>`;
function animateCounts(root = view) {
  root.querySelectorAll("[data-count]:not([data-done])").forEach((el) => {
    el.dataset.done = 1; const to = +el.dataset.count, dec = +(el.dataset.dec || 0), suf = el.dataset.suffix || "";
    if (REDUCED) { el.textContent = fmtn(to, dec) + suf; return; }
    const t0 = performance.now(), step = (t) => { const p = Math.min(1, (t - t0) / 1100), e = 1 - Math.pow(1 - p, 3); el.textContent = fmtn(to * e, dec) + suf; if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}
function finish(root = view) { root.querySelectorAll(".reveal:not([data-rv])").forEach((e, i) => { e.dataset.rv = 1; e.style.setProperty("--i", Math.min(i, 14)); }); animateCounts(root); }
function table(rows, cols, opts = {}) {
  if (!rows || !rows.length) return `<p class="muted">No rows returned.</p>`;
  cols = cols || Object.keys(rows[0]);
  const num = (v) => typeof v === "number";
  return `<div class="scroll"><table><thead><tr>${cols.map((c) => `<th scope="col" class="${num(rows[0][c]) ? "num" : ""}">${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr class="${opts.rowClass ? opts.rowClass(r) : ""}">${cols.map((c) => `<td class="${num(r[c]) ? "num" : ""}">${typeof r[c] === "object" && r[c] !== null ? esc(JSON.stringify(r[c])) : fmt(r[c], opts.dec ?? 2)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
const cityChips = () => `<div class="chips" role="group" aria-label="City">${S.meta.locations.map((l) => `<button class="pill" data-c="${l.location_id}" aria-pressed="${l.location_id === S.city}">${esc(l.city)}</button>`).join("")}</div>`;
function bindChips(root, cb) { root.querySelectorAll("[data-c]").forEach((b) => b.addEventListener("click", () => { S.city = b.dataset.c; store.set("city", S.city); root.querySelectorAll("[data-c]").forEach((x) => x.setAttribute("aria-pressed", x === b)); cb(); })); }
const rangeHtml = (id, min, max, a, b) => `<div class="tgroup"><span class="cap" id="${id}_c">Years: <span class="rangeval" id="${id}_v">${a} to ${b}</span></span><div class="range" role="group" aria-labelledby="${id}_c"><div class="rail2"></div><div class="fill" id="${id}_f"></div><input type="range" id="${id}_0" min="${min}" max="${max}" value="${a}" aria-label="From year"><input type="range" id="${id}_1" min="${min}" max="${max}" value="${b}" aria-label="To year"></div></div>`;
function bindRange(id, min, max, cb) {
  const sync = () => { const a = +$("#" + id + "_0").value, b = +$("#" + id + "_1").value, lo = Math.min(a, b), hi = Math.max(a, b); $("#" + id + "_v").textContent = `${lo} to ${hi}`; $("#" + id + "_f").style.left = `${((lo - min) / (max - min)) * 100}%`; $("#" + id + "_f").style.right = `${100 - ((hi - min) / (max - min)) * 100}%`; return [lo, hi]; };
  ["0", "1"].forEach((k) => { $("#" + id + "_" + k).addEventListener("input", sync); $("#" + id + "_" + k).addEventListener("change", () => cb(...sync())); }); sync();
}
function skillRows(items, fmtv) {
  const mx = Math.max(...items.map((i) => i.pct ?? i.v));
  return items.map((i) => `<div class="srow2 ${i.win ? "win" : ""}"><span class="nm">${esc(i.name)}</span><span class="tr"><i data-w="${((100 * (i.pct ?? i.v)) / mx).toFixed(1)}"></i></span><span class="v">${fmtv(i.v)}</span></div>`).join("");
}
const growBars = (root = view) => requestAnimationFrame(() => requestAnimationFrame(() => root.querySelectorAll(".tr i[data-w]").forEach((i) => (i.style.width = i.dataset.w + "%"))));

/* ------------------------------------------------------------------ charts */
const baseLayout = () => ({ margin: { l: 54, r: 18, t: 12, b: 50 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { family: "Inter, system-ui, sans-serif", size: 12.5, color: "#1d1d1f" },
  hoverlabel: { bgcolor: "#1d1d1f", bordercolor: "rgba(0,0,0,0)", font: { color: "#fff", family: "Inter, system-ui, sans-serif", size: 13 } }, legend: { orientation: "h", y: -0.2, font: { size: 12 } },
  xaxis: { gridcolor: "rgba(0,0,0,.06)", zeroline: false, linecolor: "rgba(0,0,0,.14)", tickfont: { color: "#6e6e73" } }, yaxis: { gridcolor: "rgba(0,0,0,.06)", zeroline: false, linecolor: "rgba(0,0,0,.0)", tickfont: { color: "#6e6e73" } }, colorway: PAL, bargap: 0.3 });
/* every chart: shared look, rounded bars, and a draw-in animation for simple bar/line charts */
function plot(el, data, layout = {}, summary = "", opt = {}) {
  if (typeof el === "string") el = $(el); if (!el) return;
  const base = baseLayout(), lay = { ...base, ...layout }; lay.xaxis = { ...base.xaxis, ...(layout.xaxis || {}) }; lay.yaxis = { ...base.yaxis, ...(layout.yaxis || {}) };
  const traces = data.map((t) => (t.type === "bar" ? { ...t, marker: { cornerradius: 7, ...(t.marker || {}) } } : t));
  const cfg = { displayModeBar: false, responsive: true };
  const simple = (t) => ((t.type === "bar" && t.orientation !== "h") || ((!t.type || t.type === "scatter") && /lines/.test(t.mode || "lines"))) && !t.yaxis && Array.isArray(t.y) && t.y.length > 0;
  const animate = opt.animate !== false && !REDUCED && traces.length && traces.every(simple) && !layout.yaxis2;
  if (animate) {
    const ys = traces.flatMap((t) => t.y.filter((v) => v !== null && v !== undefined && !Number.isNaN(+v)).map(Number)), isBar = traces.some((t) => t.type === "bar"), lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.12 || 1;
    if (!layout.yaxis?.range) { lay.yaxis.range = isBar ? [Math.min(0, lo), hi + pad] : [lo - pad, hi + pad]; lay.yaxis.autorange = false; }
    const startY = isBar ? 0 : lo - pad;
    Plotly.newPlot(el, traces.map((t) => ({ ...t, y: t.y.map(() => startY) })), lay, cfg).then(() => Plotly.animate(el, { data: traces.map((t) => ({ y: t.y })), traces: traces.map((_, i) => i) }, { transition: { duration: 950, easing: "cubic-out" }, frame: { duration: 950, redraw: false } }));
  } else Plotly.newPlot(el, traces, lay, cfg);
  el.setAttribute("role", "img"); el.setAttribute("aria-label", summary || "Chart");
  let cap = el.nextElementSibling; if (!cap || !cap.classList.contains("sr-only")) { cap = document.createElement("p"); cap.className = "sr-only"; el.after(cap); }
  cap.textContent = "Text summary: " + (summary || "chart without summary");
}
const chartCard = (id, takeaway, extra = "", cls = "") => `<section class="card reveal"><h3 class="takeaway" id="t_${id}">${takeaway}</h3>${extra}<div id="${id}" class="chart ${cls}"></div></section>`;
const warm = RAMPS.temp.map((c, i) => [i / (RAMPS.temp.length - 1), c]), wet = RAMPS.rain.map((c, i) => [i / (RAMPS.rain.length - 1), c]);

/* ------------------------------------------------------------------ router */
const routes = { overview: skyOverview, models: modelsPage, history, engineering, warehouse, olap, risk, method };
async function route() {
  const name = (location.hash.replace(/^#\//, "") || "overview").split("?")[0];
  document.querySelectorAll(".topbar nav a").forEach((a) => { if (a.getAttribute("href") === "#/" + name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  window.__skyCleanup?.(); window.__skyCleanup = null; document.querySelector(".shell").classList.remove("sky-mode", "dark-scene", "models-mode");
  view.innerHTML = skeleton();
  try {
    if (!S.meta) S.meta = await api("/meta");
    if (!S.meta.locations.some((l) => l.location_id === S.city)) S.city = S.meta.locations[0].location_id;
    await (routes[name] || skyOverview)();
    decorate(name);
    view.focus({ preventScroll: true });
  } catch (e) { view.innerHTML = err(e); }
}
window.addEventListener("hashchange", route);
route();

function resultView(f) {
  const r = f.result;
  if (r.rows) return table(r.rows) + (r.pct_hot_hours_flagged_outlier !== undefined ? `<p class="muted">Share of ≥40 °C hours flagged as outliers: ${r.pct_hot_hours_flagged_outlier}%</p>` : "");
  if (r.strongest) return table(r.strongest);
  return `<div class="scroll"><table><tbody>${Object.entries(r).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(JSON.stringify(v))}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------- the story that links the pages */
const JOURNEY = [["overview", "See today", "What is the weather like right now?"], ["history", "Look back", "How has it changed over 21 years?"], ["engineering", "Trust the data", "How raw files became clean records"], ["warehouse", "See where it lives", "How the data is organised for questions"],
  ["olap", "Ask questions", "Slice and compare the data yourself"], ["risk", "Look ahead", "Which hazards hurt, and can we see them coming?"], ["models", "Compare all models", "What each model takes in, gives out and scores"], ["method", "Know the limits", "What we did and what we cannot claim"]];
function decorate() { /* intentionally empty: pages stand on their own */ }

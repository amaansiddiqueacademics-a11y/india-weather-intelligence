/* SQL and OLAP page: compare cities, explore the data (EDA), build your own view, and read the SQL behind every answer. */
const OMEAS = { avg_temp_c: "Average temperature", avg_tmax_c: "Typical daily high", max_temp_c: "Hottest reading", min_temp_c: "Coldest reading", total_rain_mm: "Total rain", rainy_days: "Rainy days", heavy_rain_days: "Heavy-rain days", hot_days: "Days above 40 °C", avg_humidity_pct: "Average humidity", avg_hi_max_c: "Typical peak 'feels like'", days: "Days counted" };
const OTIME = [["all", "Whole period"], ["year", "Each year"], ["year_season", "Each year and season"], ["year_month", "Each year and month"], ["day", "Each day"]];
const OPLACE = [["all", "All cities"], ["region", "By region"], ["state", "By state"], ["city", "By city"]];
const anaBlock = (k, color, html) => `<button class="anbtn" data-an="${k}" aria-expanded="false" style="--ac:${color}">Analysis <span aria-hidden="true">›</span></button><div class="ana" id="an_${k}" hidden>${html}</div>`;
const anH = (h, p) => `<h5>${h}</h5><p>${p}</p>`;
function bindAna(root) { root.querySelectorAll(".anbtn").forEach((b) => b.addEventListener("click", () => { const p = $("#an_" + b.dataset.an), open = p.hidden; p.hidden = !open; b.setAttribute("aria-expanded", open); b.firstChild.textContent = open ? "Hide analysis " : "Analysis "; b.lastElementChild.textContent = open ? "‹" : "›"; })); }
const hlSql = (s) => esc(s).replace(/\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|JOIN|ON|USING|WITH|AS|AND|IN|BETWEEN|HAVING|LIMIT|FILTER|DISTINCT|DESC|ASC|ROLLUP|NULLS|OVER)\b/g, '<b class="sk">$1</b>').replace(/\b(count|sum|avg|min|max|round|stddev_samp|regr_slope|regr_r2|corr|coalesce|COALESCE)\(/g, '<i class="sf">$1</i>(');
const sqlBox = (id, sql) => `<div class="sqlwrap"><button class="ghost copybtn2" data-copy="${id}">Copy SQL</button><pre class="sqlbox" id="${id}">${hlSql(sql)}</pre></div>`;
function bindCopy(root, texts) { root.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => { try { await navigator.clipboard.writeText(texts[b.dataset.copy]); b.textContent = "Copied"; } catch { b.textContent = "Copy failed"; } setTimeout(() => (b.textContent = "Copy SQL"), 1500); })); }
const FRIENDLY = { tmean_c: "Mean temp", tmax_c: "Max temp", tmin_c: "Min temp", precip_mm: "Rain", rh_mean: "Humidity", wind_mean_kmh: "Wind", pressure_mean_hpa: "Pressure", soil_moisture: "Soil moisture", et0_mm: "Evaporation", hi_max_c: "Feels-like peak" };

function analysisCards(rows, valCol, labelFn, timeCol) {
  const v = rows.map((r) => +r[valCol]).filter((x) => !Number.isNaN(x)); if (v.length < 2) return "";
  const hi = maxBy(rows, (r) => +r[valCol]), lo = minBy(rows, (r) => +r[valCol]), mean = hmean(v), spread = +hi[valCol] - +lo[valCol];
  const cards = [["trend", "#d9480f", `Highest: ${esc(String(labelFn(hi)))}`, `${hi[valCol]}, ${mean ? ((100 * (hi[valCol] - mean)) / Math.abs(mean)).toFixed(0) : 0}% above the average of ${mean.toFixed(1)}.`], ["layers", "#0a6bd6", `Lowest: ${esc(String(labelFn(lo)))}`, `${lo[valCol]}, ${mean ? ((100 * (mean - lo[valCol])) / Math.abs(mean)).toFixed(0) : 0}% below the average.`], ["target", "#7048e8", `Spread: ${spread.toFixed(1)}`, `The gap between highest and lowest across ${rows.length} groups.`]];
  if (timeCol) { const sorted = [...rows].sort((p, q) => (p[timeCol] > q[timeCol] ? 1 : -1)), f = +sorted[0][valCol], l = +sorted.at(-1)[valCol]; cards[2] = ["clock", "#0a8f8f", `Change ${sorted[0][timeCol]} to ${sorted.at(-1)[timeCol]}: ${f ? (((l - f) / Math.abs(f)) * 100 >= 0 ? "+" : "−") + Math.abs(((l - f) / Math.abs(f)) * 100).toFixed(0) + "%" : "n/a"}`, `From ${f} to ${l}. Highest ${hi[timeCol]}, lowest ${lo[timeCol]}.`]; }
  return insights(cards);
}
const exportBar = (id) => `<div class="exp"><button class="ghost" id="${id}c">Download CSV</button><button class="ghost" id="${id}s">Copy SQL</button></div>`;
function bindExport(id, rows, sql) {
  $("#" + id + "c")?.addEventListener("click", () => { const cols = Object.keys(rows[0] ?? {}), csv = [cols.join(","), ...rows.map((r) => cols.map((c) => JSON.stringify(r[c] ?? "")).join(","))].join("\n"), a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "wx_analysis.csv"; a.click(); });
  $("#" + id + "s")?.addEventListener("click", async (e) => { try { await navigator.clipboard.writeText(sql); e.target.textContent = "Copied"; } catch { e.target.textContent = "Copy failed"; } setTimeout(() => (e.target.textContent = "Copy SQL"), 1500); });
}


async function olap() {
  const meta = await api("/olap/meta"), qlist = await api("/sql/questions");
  view.innerHTML = `<div class="page"><header class="phead reveal"><h1>SQL and OLAP</h1><p class="lede">Slice the warehouse by place, time and season. Every view is a real query.</p></header>
    <h2 class="first">1. Build a view</h2><p class="lede2"><b>Roll up</b> = fewer details, <b>Drill down</b> = more details, <b>Slice / Dice</b> = filter one / several things.</p><div id="p1"></div>
    <h2>2. Cross-tab</h2><p class="lede2"><b>Pivot</b> = one thing down the side, another across the top.</p><div id="p2"></div>
    <h2>3. SQL queries</h2><div id="p3"></div></div>`;
  finish();
  const run3 = () => { const texts = {}; $("#p3").innerHTML = `<div class="sqllist">${qlist.map((q, i) => `<section class="sqlitem reveal"><div class="sqlh"><span class="qn">${i + 1}</span><b>${esc(q.title)}</b><button class="ghost copybtn2" data-copy="sql_${q.id}">Copy SQL</button></div><pre class="sqlbox" id="sql_${q.id}">${hlSql(q.sql)}</pre><div class="qprev" id="prev_${q.id}"><span class="muted">Running…</span></div></section>`).join("")}</div>`; qlist.forEach((q) => (texts["sql_" + q.id] = q.sql)); bindCopy($("#p3"), texts); scrollReveal($("#p3")); qlist.forEach(async (q) => { const el = $("#prev_" + q.id); try { const r = await api("/sql/run?" + qs({ id: q.id })), cols = r.columns, rows = r.rows.slice(0, 3); el.innerHTML = `<div class="scroll"><table class="pg"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((x) => `<tr>${cols.map((c) => `<td class="${typeof x[c] === "number" ? "num" : ""}">${x[c] === null ? "<i>null</i>" : esc(x[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div><p class="pgf">Showing ${rows.length} of ${r.rows.length} rows · ${r.elapsed_ms} ms</p>`; } catch (e) { el.innerHTML = `<span class="muted">${esc(e.message)}</span>`; } }); };
  explorer(); pivotTab(); run3();
  function drawChart() {}
  function explorer() {
    const cur = { m: ["avg_temp_c"], t: "year", g: "region", season: "", cities: [], a: 2002, b: 2022 };
    $("#p1").innerHTML = `<div class="grid g3"><div class="stepbox reveal"><h3><span class="sn">1</span>Measure</h3><div class="pillrow" id="o_m" style="margin-top:10px">${Object.keys(meta.measures).filter((k) => OMEAS[k]).map((k) => `<button type="button" data-v="${k}" aria-pressed="${cur.m.includes(k)}">${OMEAS[k]}</button>`).join("")}</div></div>
      <div class="stepbox reveal"><h3><span class="sn">2</span>Level of detail</h3>${seg("o_t", "Time level", OTIME, cur.t)}<div style="margin-top:8px">${seg("o_g", "Place level", OPLACE, cur.g)}</div><div class="controls" style="margin:10px 0 0"><button class="ghost" id="o_out">Roll up ▲</button><button class="ghost" id="o_in">Drill down ▼</button></div></div>
      <div class="stepbox reveal"><h3><span class="sn">3</span>Filters</h3>${seg("o_s", "Slice: season", [["", "All year"], ...meta.seasons.map((s) => [s, s])], "")}<div style="margin:10px 0 6px"><span class="cap" style="font-size:11.5px;font-weight:700;color:var(--ink-2)">Dice: cities (none selected = all)</span><div class="pillrow" id="o_c" style="margin-top:6px">${S.meta.locations.map((l) => `<button type="button" data-v="${l.location_id}" aria-pressed="false">${esc(l.city)}</button>`).join("")}</div></div>${rangeHtml("oy", 2002, 2022, 2002, 2022)}</div></div>
      <p class="guide" id="o_sum" style="margin-top:14px"></p><div id="oout"></div>`;
    const summary = () => { $("#o_sum").innerHTML = `<b>Current view:</b> ${cur.m.map((k) => OMEAS[k].toLowerCase()).join(" and ")}, ${OPLACE.find((p) => p[0] === cur.g)[1].toLowerCase()}, ${OTIME.find((t) => t[0] === cur.t)[1].toLowerCase()}${cur.season ? `, ${cur.season} only` : ""}${cur.cities.length ? `, for ${cur.cities.map(cityName).join(", ")}` : ""}, ${cur.a} to ${cur.b}.`; };
    let timer; const later = () => { summary(); clearTimeout(timer); timer = setTimeout(run, 150); };
    bindSeg("o_t", (v) => { cur.t = v; later(); }); bindSeg("o_g", (v) => { cur.g = v; later(); }); bindSeg("o_s", (v) => { cur.season = v; later(); });
    $("#o_m").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (!b) return; const k = b.dataset.v; cur.m = cur.m.includes(k) ? (cur.m.length > 1 ? cur.m.filter((x) => x !== k) : cur.m) : [...cur.m, k]; $("#o_m").querySelectorAll("[data-v]").forEach((x) => x.setAttribute("aria-pressed", cur.m.includes(x.dataset.v))); later(); });
    $("#o_c").addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (!b) return; const k = b.dataset.v; cur.cities = cur.cities.includes(k) ? cur.cities.filter((x) => x !== k) : [...cur.cities, k]; b.setAttribute("aria-pressed", cur.cities.includes(k)); later(); });
    const step = (arr, key, segId, d) => { const i = arr.findIndex((x) => x[0] === cur[key]) + d; if (i >= 0 && i < arr.length) { cur[key] = arr[i][0]; $("#" + segId).querySelectorAll("[data-v]").forEach((x) => x.setAttribute("aria-pressed", x.dataset.v === cur[key])); later(); } };
    $("#o_out").onclick = () => { step(OTIME, "t", "o_t", -1); step(OPLACE, "g", "o_g", -1); }; $("#o_in").onclick = () => { step(OTIME, "t", "o_t", 1); step(OPLACE, "g", "o_g", 1); };
    bindRange("oy", 2002, 2022, (a, b) => { cur.a = a; cur.b = b; later(); });
    async function run() {
      const out = $("#oout"); out.innerHTML = skeleton();
      try { const r = await api("/olap/aggregate?" + qs({ time_level: cur.t, geo_level: cur.g, measures: cur.m.join(","), cities: cur.cities.join(","), seasons: cur.season, year_from: cur.a, year_to: cur.b }));
        const meas = Object.keys(r.measure_labels), dims = r.columns.filter((c) => !meas.includes(c)), m0 = meas[0], nm = (x) => dims.map((d) => (d === "month" ? MONTHS[x[d] - 1] : x[d])).join(" · ") || "everything";
        const hi = r.rows.length ? maxBy(r.rows, (x) => x[m0]) : null, lo = r.rows.length ? minBy(r.rows, (x) => x[m0]) : null;
        const tcol = ["full_date", "year"].find((d) => dims.includes(d));
        out.innerHTML = `${r.rows.length ? analysisCards(r.rows, m0, nm, tcol && dims.length === 1 ? tcol : null) : ""}<section class="card reveal"><h3 class="q">${OMEAS[m0] ?? m0}</h3><p class="ans">${r.rows.length ? `${r.rows.length} groups in ${r.elapsed_ms} ms.` : "No rows match these filters. Widen the selection."}</p>${r.rows.length ? exportBar("ox") : ""}<div id="ochart" class="chart tall"></div></section><details class="card more reveal" style="margin-top:12px"><summary>SQL and table</summary>${sqlBox("osql", r.sql)}${table(r.rows)}</details>`; finish(out); if (r.rows.length) { bindExport("ox", r.rows, r.sql); bindCopy(out, { osql: r.sql }); }
        if (r.rows.length) { const td = ["full_date", "year", "month"].find((d) => dims.includes(d)), others = dims.filter((d) => d !== td), lbl = OMEAS[m0] ?? m0;
          if (td && others.length && td !== "month") { const gs = [...new Set(r.rows.map((x) => others.map((d) => x[d]).join(" · ")))].slice(0, 12); plot("#ochart", gs.map((g, i) => ({ x: r.rows.filter((x) => others.map((d) => x[d]).join(" · ") === g).map((x) => x[td]), y: r.rows.filter((x) => others.map((d) => x[d]).join(" · ") === g).map((x) => x[m0]), mode: "lines+markers", name: g, line: { shape: "spline", width: 3, color: PAL[i % PAL.length] }, marker: { size: 5 } })), { yaxis: { title: lbl } }, `Lines of ${lbl} over ${td}.`); }
          else if (td === "year" || td === "full_date") plot("#ochart", [{ x: r.rows.map((x) => x[td]), y: r.rows.map((x) => x[m0]), mode: "lines+markers", line: { shape: "spline", width: 3.5, color: "#0a6bd6" }, fill: "tozeroy", fillcolor: "rgba(10,107,214,.12)" }], { yaxis: { title: lbl } }, `Line of ${lbl} over ${td}.`);
          else plot("#ochart", [{ type: "bar", x: r.rows.slice(0, 60).map(nm), y: r.rows.slice(0, 60).map((x) => x[m0]), marker: { color: "#0a6bd6" } }], { xaxis: { type: "category", tickangle: -35 }, yaxis: { title: lbl } }, `Bars of ${lbl} for ${r.rows.length} groups.`); }
      } catch (e) { out.innerHTML = err(e); }
    }
    summary(); run();
  }
  function pivotTab() {
    const dims = [["city", "City"], ["region", "Region"], ["year", "Year"], ["season", "Season"], ["month", "Month"]];
    $("#p2").innerHTML = `<div class="tb controls"><label>Rows<select id="pr">${dims.map(([k, l]) => `<option value="${k}" ${k === "city" ? "selected" : ""}>${l}</option>`).join("")}</select></label><label>Columns<select id="pc">${dims.map(([k, l]) => `<option value="${k}" ${k === "season" ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Cell value<select id="pm">${Object.keys(meta.measures).filter((k) => OMEAS[k]).map((k) => `<option value="${k}">${OMEAS[k]}</option>`).join("")}</select></label></div><div id="pout"></div>`;
    async function run() { const out = $("#pout"); out.innerHTML = skeleton(); try { if ($("#pr").value === $("#pc").value) throw new Error("Choose two different things for the side and the top."); const r = await api("/olap/pivot?" + qs({ row: $("#pr").value, col: $("#pc").value, measure: $("#pm").value }));
      const flat = r.rows.flatMap((x) => x.values.map((v, i) => ({ row: x.row, col: r.col_labels[i], v }))).filter((c) => c.v !== null), hi = maxBy(flat, (c) => c.v), lo = minBy(flat, (c) => c.v);
      out.innerHTML = `<section class="card reveal"><h3 class="q">${OMEAS[$("#pm").value]}: ${$("#pr").selectedOptions[0].text.toLowerCase()} by ${$("#pc").selectedOptions[0].text.toLowerCase()}</h3><p class="ans">Highest: <b>${esc(hi.row)} / ${esc(hi.col)}</b> (${hi.v.toFixed(1)}). Lowest: <b>${esc(lo.row)} / ${esc(lo.col)}</b> (${lo.v.toFixed(1)}). Spread ${(hi.v - lo.v).toFixed(1)}.</p><div id="pchart" class="chart tall"></div></section><details class="card more reveal" style="margin-top:12px"><summary>SQL</summary>${sqlBox("psql", r.sql)}</details>`; finish(out); bindCopy(out, { psql: r.sql });
      plot("#pchart", [{ type: "heatmap", x: r.col_labels, y: r.rows.map((x) => x.row), z: r.rows.map((x) => x.values), colorscale: warm, xgap: 3, ygap: 3, text: r.rows.map((x) => x.values.map((v) => (v === null ? "" : Math.abs(v) >= 100 ? Math.round(v) : v.toFixed(1)))), texttemplate: "%{text}", textfont: { size: 11, color: "rgba(0,0,0,.75)" }, hovertemplate: "%{y} / %{x}: %{z}<extra></extra>" }], { margin: { l: 110, t: 6, r: 10, b: 50 }, yaxis: { autorange: "reversed" } }, "Heatmap of the grid."); } catch (e) { out.innerHTML = `<div class="notice">${esc(e.message)}</div>`; } }
    ["pr", "pc", "pm"].forEach((i) => $("#" + i).addEventListener("change", run)); run();
  }
}

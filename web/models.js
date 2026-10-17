/* Models page: how each forecasting model reasons, drawn from the saved artifacts and the 2019-2022 test period. */
const FEAT = { temp: "Temperature now", rh: "Humidity now", dew: "Dew point now", pres: "Pressure now", cloud: "Cloud cover", wind: "Wind speed", gust: "Wind gusts", hi: "Heat index now", app: "Feels-like now", soil: "Soil moisture", rad: "Sunshine",
  temp_l1: "Temperature 1 h ago", temp_l3: "Temperature 3 h ago", temp_l6: "Temperature 6 h ago", temp_l12: "Temperature 12 h ago", temp_l24: "Temperature 24 h ago", temp_l48: "Temperature 48 h ago", rh_l24: "Humidity 24 h ago",
  pres_d3: "Pressure change, 3 h", pres_d24: "Pressure change, 24 h", rain_6h: "Rain, last 6 h", rain_24h: "Rain, last 24 h", rain_72h: "Rain, last 3 days", rain_168h: "Rain, last 7 days",
  temp_mean24: "Mean temperature, 24 h", temp_max24: "Highest temperature, 24 h", temp_min24: "Lowest temperature, 24 h", hi_max24: "Peak heat index, 24 h", sin_h: "Time of day (sine)", cos_h: "Time of day (cosine)",
  sin_d: "Season (sine)", cos_d: "Season (cosine)", wd_sin: "Wind direction (sine)", wd_cos: "Wind direction (cosine)", latitude: "Latitude", longitude: "Longitude", elevation_m: "Elevation",
  clim_temp: "Normal temperature for this hour", clim_hi: "Normal heat index for this hour", temp_anom: "Temperature vs normal", hi_anom: "Heat index vs normal" };
const fl = (f) => FEAT[f] ?? f.replace(/_/g, " ");
const TARGET_LABEL = { temp: "Temperature", hi: "Heat index", rain: "Heavy rain" };
const SLIDERS = {
  heat: [["temp", 8, 46, 0.5, "°C"], ["rh", 5, 100, 1, "%"], ["pres", 960, 1030, 0.5, " hPa"], ["wind", 0, 60, 1, " km/h"], ["rain_24h", 0, 150, 1, " mm"], ["soil", 0.05, 0.5, 0.01, ""]],
  rain: [["rain_24h", 0, 150, 1, " mm"], ["rain_72h", 0, 300, 1, " mm"], ["rh", 5, 100, 1, "%"], ["pres", 960, 1030, 0.5, " hPa"], ["soil", 0.05, 0.5, 0.01, ""], ["temp", 8, 46, 0.5, "°C"]],
};

const PRESETS = { heat: [["Hot, humid afternoon", { temp: 36, rh: 70, wind: 8, rain_24h: 0 }], ["After heavy rain", { temp: 27, rh: 92, rain_24h: 60, pres: 1000 }], ["Cool, dry night", { temp: 18, rh: 35, wind: 6, rain_24h: 0 }], ["Windy and clear", { temp: 30, rh: 45, wind: 35, rain_24h: 0 }]],
  rain: [["Soaked monsoon", { rain_24h: 80, rain_72h: 200, rh: 95, soil: 0.45 }], ["Dry spell", { rain_24h: 0, rain_72h: 0, rh: 40, soil: 0.12 }], ["Humid build-up", { rh: 90, pres: 1000, rain_24h: 10, rain_72h: 40 }]] };
const heatBand = (v) => (v >= 54 ? "extreme danger" : v >= 41 ? "danger" : v >= 32 ? "extreme caution" : v >= 27 ? "caution" : "low");
async function modelsInside(host) {
  const M = { target: "temp", h: 24, city: S.city, date: "2022-06-15", hour: 12, over: {}, sweep: "temp", base: null, ov: null, ex: null };
  const unit = () => (M.target === "rain" ? "%" : " °C");
  const val = (v) => (M.target === "rain" ? (100 * v).toFixed(1) : v.toFixed(1));
  const hOpts = () => (M.target === "rain" ? [[24, "24 h"], [48, "48 h"], [72, "72 h"]] : [[6, "6 h"], [24, "24 h"], [72, "72 h"]]);
  host.innerHTML = `<div class="mpage">
    <div class="toolbar" id="mtool"></div><div id="mnotice" aria-live="polite"></div>
    <section class="mcard"><div class="mhead"><span class="mnum">1</span><div><h2>The linear model</h2><p>It multiplies each input by a weight and adds them up. Simple, fast and easy to explain, but it can only draw straight lines.</p></div></div><div class="mgrid"><div><h3 class="takeaway" id="a_t"></h3><div id="a_plot" class="chart tall"></div></div><div><h3>Which inputs the straight line leans on</h3><div id="a_coef"></div></div></div></section>
    <section class="mcard"><div class="mhead"><span class="mnum">2</span><div><h2>The decision-tree model</h2><p>Hundreds of small trees, each asking yes/no questions about the inputs. Every tree nudges the answer a little. This is the first tree, with the path your chosen moment takes highlighted.</p></div></div><div class="diagram2" id="b_tree"></div><p class="sub" id="b_txt"></p><h3 class="takeaway" id="b_t"></h3><div id="b_plot" class="chart"></div></section>
    <section class="mcard"><div class="mhead"><span class="mnum">3</span><div><h2>Try a situation: change the weather, watch both models react</h2><p>Drag any input. Both models re-predict instantly from the real trained files. The curve shows how each one responds across the whole range of the highlighted input.</p></div></div><div class="mgrid"><div id="c_sliders" class="sliders"></div><div><div id="c_out" class="outs"></div><div id="c_plain" class="plainbox"></div></div></div><h3 class="takeaway" id="c_t"></h3><div id="c_plot" class="chart tall"></div></section>
    <section class="mcard"><div class="mhead"><span class="mnum">4</span><div><h2>What the models look at</h2><p>Importance is measured by shuffling one input at a time and seeing how much worse the boosted model gets.</p></div></div><div class="mgrid"><div id="d_imp"></div><div id="d_pipe"></div></div></section></div>`;

  const drawTool = () => {
    $("#mtool").innerHTML = `${seg("m_target", "Question", [["temp", "Temperature"], ["hi", "Heat index"], ["rain", "Heavy rain"]], M.target)}<span id="m_hw">${seg("m_h", "Look ahead", hOpts(), M.h)}</span>
      <div class="tgroup"><label class="cap" for="m_city">City</label><select id="m_city">${S.meta.locations.map((l) => `<option value="${l.location_id}" ${l.location_id === M.city ? "selected" : ""}>${esc(l.city)}</option>`).join("")}</select></div>
      <div class="tgroup"><label class="cap" for="m_date">Date</label><input type="date" id="m_date" value="${M.date}" min="2019-01-10" max="2022-12-25"></div>${seg("m_hour", "Time", [[0, "00:00"], [6, "06:00"], [12, "12:00"], [18, "18:00"]], M.hour)}<button class="go" id="m_go">Explain this moment</button>`;
    bindSeg("m_target", (v) => { M.target = v; M.sweep = v === "rain" ? "rain_72h" : "temp"; if (!hOpts().some(([h]) => h === M.h)) M.h = 24; M.over = {}; M.base = null; drawTool(); load(); });
    bindSeg("m_h", (v) => { M.h = +v; M.over = {}; M.base = null; load(); }); bindSeg("m_hour", (v) => (M.hour = +v));
    $("#m_city").onchange = (e) => { M.city = e.target.value; S.city = M.city; store.set("city", M.city); }; $("#m_date").onchange = (e) => (M.date = e.target.value);
    $("#m_go").onclick = () => { M.over = {}; M.base = null; load(); };
  };
  drawTool();

  const body = (over, sweep) => ({ location_id: M.city, issue_time: `${M.date}T${String(M.hour).padStart(2, "0")}:00:00`, target: M.target, horizon_h: M.h, overrides: over, sweep });
  async function load() {
    $("#mnotice").innerHTML = "";
    try {
      const [ov, ex] = await Promise.all([api(`/models/overview?${qs({ target: M.target, horizon_h: M.h })}`), api("/models/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body({}, M.sweep)) })]);
      M.ov = ov; M.ex = ex; M.base = { ridge: ex.ridge, hgb: ex.hgb }; drawA(); drawB(); drawC(true); drawD();
    } catch (e) { $("#mnotice").innerHTML = `<div class="error" role="alert"><b>Could not build the explanation.</b> ${esc(e.message)}</div>`; }
  }

  /* 1. linear model */
  function drawA() {
    const o = M.ov, e = M.ex, lin = o.linear, rain = M.target === "rain";
    const maxc = Math.max(...lin.coefficients.map((c) => Math.abs(c.value)));
    $("#a_coef").innerHTML = `<div class="coefs">${lin.coefficients.map((c) => `<div class="crow"><span>${esc(fl(c.feature))}</span><div class="cbar"><i class="${c.value < 0 ? "neg" : "pos"}" style="width:${(50 * Math.abs(c.value)) / maxc}%;${c.value < 0 ? "right:50%" : "left:50%"}"></i></div><b>${c.value > 0 ? "+" : ""}${c.value.toFixed(2)}</b></div>`).join("")}</div><p class="sub">${esc(lin.model)}. ${esc(lin.note)} Blue pushes the forecast up, orange pushes it down.</p>`;
    if (!rain) {
      const s = o.scatter, ols = s.ols, xs = s.points.map((p) => p.x), x0 = Math.min(...xs), x1 = Math.max(...xs);
      $("#a_t").textContent = `Each 1 °C warmer now means about ${ols.slope.toFixed(2)} °C warmer in ${M.h} h (one-input fit, R² ${ols.r2.toFixed(2)})`;
      plot("#a_plot", [{ x: xs, y: s.points.map((p) => p.y), mode: "markers", name: "Test-period cases", marker: { size: 5, color: "rgba(11,111,184,.35)" }, hovertemplate: "now %{x:.1f} → later %{y:.1f}<extra></extra>" },
        { x: [x0, x1], y: [x0, x1], mode: "lines", name: "No change", line: { dash: "dot", color: "#8e8e93", width: 2 }, hoverinfo: "skip" },
        { x: [x0, x1], y: [ols.intercept + ols.slope * x0, ols.intercept + ols.slope * x1], mode: "lines", name: `Best-fit line: y = ${ols.slope.toFixed(2)}x + ${ols.intercept.toFixed(1)}`, line: { color: "#d9480f", width: 4 } }],
        { xaxis: { title: "Temperature now (°C)" }, yaxis: { title: `${TARGET_LABEL[M.target]} in ${M.h} h (°C)` }, legend: { orientation: "h", y: -0.2 } }, `Scatter of ${xs.length} test cases with a best-fit line, slope ${ols.slope.toFixed(2)}, R squared ${ols.r2.toFixed(2)}.`);
    } else {
      const pts = M.ov.scatter.points, q = [0, 0.5, 0.75, 0.9, 0.95, 0.98, 1].map((p) => p), xs = pts.map((p) => p.x).sort((a, b) => a - b), edges = q.map((p) => xs[Math.min(xs.length - 1, Math.floor(p * (xs.length - 1)))]);
      const bins = []; for (let i = 0; i < edges.length - 1; i++) { const g = pts.filter((p) => p.x >= edges[i] && (p.x < edges[i + 1] || i === edges.length - 2)); if (g.length) bins.push({ x: g.reduce((a, b) => a + b.x, 0) / g.length, y: g.reduce((a, b) => a + b.y, 0) / g.length, n: g.length }); }
      const sw = e.sweep && e.sweep.feature === "rain_72h" ? e.sweep : null;
      $("#a_t").textContent = "More rain over the last 3 days raises the chance of a heavy-rain day, along an S-shaped (logistic) curve";
      plot("#a_plot", [{ x: bins.map((b) => b.x), y: bins.map((b) => b.y), mode: "markers", name: "Observed event rate in this slice of cases", marker: { size: bins.map((b) => 8 + Math.log10(b.n + 1) * 6), color: "rgba(11,111,184,.55)" }, text: bins.map((b) => `${b.n} cases`), hovertemplate: "%{x:.0f} mm → %{y:.0%} (%{text})<extra></extra>" },
        ...(sw ? [{ x: sw.x, y: sw.ridge, mode: "lines", name: "Linear model", line: { color: "#d9480f", width: 4 } }] : [])],
        { xaxis: { title: "Rain in the last 3 days (mm)" }, yaxis: { title: "Chance of heavy rain", tickformat: ".0%" }, legend: { orientation: "h", y: -0.2 } }, "Observed heavy-rain rate against recent rainfall with the logistic curve.");
    }
  }

  /* 2. decision tree */
  function drawB() {
    const e = M.ex, tree = e.tree, N = Object.fromEntries(tree.nodes.map((n) => [n.id, n])), onPath = new Set(e.path.filter((i) => N[i]));
    let lx = 0; const pos = {}, lay = (id) => { const n = N[id]; if (n.leaf) { pos[id] = { x: lx++, y: n.depth }; return; } lay(n.left); lay(n.right); pos[id] = { x: (pos[n.left].x + pos[n.right].x) / 2, y: n.depth }; }; lay(0);
    const BW = 152, BH = 58, GX = 14, GY = 62, W = lx * (BW + GX), maxD = Math.max(...tree.nodes.map((n) => n.depth)), H = (maxD + 1) * (BH + GY) - GY + 24;
    const cx = (id) => pos[id].x * (BW + GX) + BW / 2, cy = (id) => pos[id].y * (BH + GY) + 12;
    const edges = tree.nodes.filter((n) => !n.leaf).flatMap((n) => [[n.id, n.left, "yes"], [n.id, n.right, "no"]]).map(([a, b, t]) => { const hot = onPath.has(a) && onPath.has(b), x1 = cx(a), y1 = cy(a) + BH, x2 = cx(b), y2 = cy(b); return `<path d="M${x1} ${y1}C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2} ${x2} ${y2}" fill="none" stroke="${hot ? "#d9480f" : "#c7c7cc"}" stroke-width="${hot ? 3.5 : 1.8}"/><text x="${(x1 + x2) / 2 + (t === "yes" ? -14 : 14)}" y="${(y1 + y2) / 2 + 4}" font-size="11" text-anchor="middle" fill="${hot ? "#d9480f" : "#8e8e93"}" font-weight="600">${t}</text>`; }).join("");
    const unitStr = M.target === "rain" ? " (log-odds)" : " °C";
    const boxes = tree.nodes.map((n) => { const hot = onPath.has(n.id), x = cx(n.id) - BW / 2, y = cy(n.id), cut = n.leaf && n.feature;
      const fill = hot ? "#fff4e6" : n.leaf && !cut ? "#f2f7fc" : "#fff", stroke = hot ? "#d9480f" : "#d1d1d6";
      const t1 = n.leaf && !cut ? `${n.value >= 0 ? "+" : ""}${n.value.toFixed(2)}${unitStr}` : fl(n.feature), t2 = n.leaf && !cut ? "this tree adds" : `${cut ? "continues: " : ""}≤ ${n.threshold.toFixed(1)} ?`;
      return `<g><rect x="${x}" y="${y}" width="${BW}" height="${BH}" rx="14" fill="${fill}" stroke="${stroke}" stroke-width="${hot ? 3 : 1.5}" ${cut ? 'stroke-dasharray="5 4"' : ""}/><text x="${cx(n.id)}" y="${y + 22}" text-anchor="middle" font-size="12.5" font-weight="700" fill="#1d1d1f">${esc(t1.length > 24 ? t1.slice(0, 23) + "…" : t1)}</text><text x="${cx(n.id)}" y="${y + 38}" text-anchor="middle" font-size="12" fill="#6e6e73">${esc(t2)}</text><text x="${cx(n.id)}" y="${y + 52}" text-anchor="middle" font-size="10.5" fill="#8e8e93">${n.count.toLocaleString()} cases</text></g>`; }).join("");
    $("#b_tree").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:${Math.min(W, 1040)}px" role="img" aria-label="First decision tree of the boosted model, three levels deep, with this moment's path highlighted">${edges}${boxes}</svg>`;
    const leafId = [...onPath].filter((i) => N[i]).pop(), leaf = N[leafId];
    $("#b_txt").textContent = M.target === "rain"
      ? `Start from the model's baseline score ${tree.baseline.toFixed(2)} (log-odds). Tree 1 sends this moment down the orange path and adds ${leaf.value.toFixed(2)}; ${tree.n_trees - 1} more trees then refine it before the score is calibrated into a probability.`
      : `Start from the baseline ${tree.baseline.toFixed(1)} °C (the average in the training years). Tree 1 sends this moment down the orange path and adds ${leaf.value >= 0 ? "+" : ""}${leaf.value.toFixed(2)} °C; ${tree.n_trees - 1} more trees then refine it.`;
    const st = e.staged.values, xs = st.map((_, i) => 1 + i * e.staged.step), f = (v) => (M.target === "rain" ? 100 * v : v);
    $("#b_t").textContent = `Prediction as trees are added: it settles at ${f(st[st.length - 1]).toFixed(1)}${unit()} after ${tree.n_trees} trees`;
    plot("#b_plot", [{ x: xs, y: st.map(f), mode: "lines", line: { color: "#0b6fb8", width: 4, shape: "hv" }, name: "Boosted prediction" }], { xaxis: { title: "Number of trees" }, yaxis: { title: M.target === "rain" ? "Chance of heavy rain (%)" : "°C" }, margin: { l: 56, r: 14, t: 10, b: 50 } }, `Prediction converging as trees are added, ending at ${f(st[st.length - 1]).toFixed(1)}.`);
    $("#b_plot").style.minHeight = "240px";
  }

  /* 3. playground */
  function drawC(rebuild) {
    const e = M.ex, list = SLIDERS[M.target === "rain" ? "rain" : "heat"];
    if (rebuild) {
      $("#c_sliders").innerHTML = `<div class="presets"><span class="cap">Try a situation</span>${PRESETS[M.target === "rain" ? "rain" : "heat"].map(([l], i) => `<button type="button" class="pill" data-preset="${i}">${l}</button>`).join("")}</div>` + list.map(([f, lo, hi, st, u]) => { const v = e.features[f]; return `<div class="srow ${f === M.sweep ? "on" : ""}" data-f="${f}"><div class="slab"><button type="button" class="slink" data-pick="${f}" aria-pressed="${f === M.sweep}" title="Show the response curve for this input">${esc(fl(f))}</button><b id="sv_${f}">${(+v).toFixed(f === "soil" ? 2 : 1)}${u}</b></div><input type="range" min="${lo}" max="${hi}" step="${st}" value="${Math.min(hi, Math.max(lo, v))}" data-f="${f}" aria-label="${esc(fl(f))}"></div>`; }).join("") + `<button class="ghostbtn" id="c_reset" type="button">Reset to the real weather</button>`;
      $("#c_sliders").oninput = (ev) => { const r = ev.target.closest("input[type=range]"); if (!r) return; const f = r.dataset.f, u = list.find((x) => x[0] === f)[4]; $("#sv_" + f).textContent = (+r.value).toFixed(f === "soil" ? 2 : 1) + u; M.over[f] = +r.value; clearTimeout(M.t); M.t = setTimeout(update, 220); };
      $("#c_sliders").onclick = async (ev) => { const pb = ev.target.closest("[data-preset]"); if (pb) { M.over = { ...PRESETS[M.target === "rain" ? "rain" : "heat"][+pb.dataset.preset][1] }; await update(); drawC(true); return; } const b = ev.target.closest("[data-pick]"); if (b) { M.sweep = b.dataset.pick; $("#c_sliders").querySelectorAll(".srow").forEach((r) => { r.classList.toggle("on", r.dataset.f === M.sweep); r.querySelector(".slink").setAttribute("aria-pressed", r.dataset.f === M.sweep); }); update(); } };
      $("#c_reset").onclick = () => { M.over = {}; drawC(true); update(); };
    }
    const b = M.base, lin = e.linear_name === "ridge" ? "Linear model" : "Linear model", f = (v) => (M.target === "rain" ? 100 * v : v), d = (v, v0) => { const x = f(v) - f(v0); return Math.abs(x) < 0.05 ? "unchanged" : `${x > 0 ? "+" : ""}${x.toFixed(1)}${unit()} vs real`; };
    const rows = [[lin, e.ridge, b.ridge, "#8e8e93"], ["Decision-tree model", e.hgb, b.hgb, "#d9480f"]]; if (M.target !== "rain") rows.push(["No change (baseline)", e.baseline_persistence, e.baseline_persistence, "#c7c7cc"]);
    const mx = Math.max(...rows.map((r) => f(r[1])), 1) * 1.15;
    $("#c_out").innerHTML = `<p class="sub">Predicted ${TARGET_LABEL[M.target].toLowerCase()} in ${M.h} h for ${esc(cityName(M.city))}</p>` + rows.map(([n, v, v0, c]) => `<div class="orow"><span>${n}</span><div class="obar"><i style="width:${Math.max(2, (100 * f(v)) / mx)}%;background:${c}"></i></div><b>${val(v)}<small>${unit()}</small></b><em>${d(v, v0)}</em></div>`).join("");
    const diff = Math.abs(f(e.hgb) - f(e.ridge)), band = M.target === "hi" ? ` The decision-tree forecast falls in the "${heatBand(e.hgb)}" heat band.` : "";
    $("#c_plain").innerHTML = `<p><b>In plain words.</b> ${M.target === "rain" ? `The decision-tree model gives a ${val(e.hgb)}% chance of heavy rain within the next ${M.h} hours; the linear model says ${val(e.ridge)}%.` : `Both models expect about ${val(e.hgb)}${unit()} in ${M.h} hours.${band}`} ${diff < (M.target === "rain" ? 2 : 0.5) ? "They agree closely, so you can lean on this number." : `They differ by ${diff.toFixed(1)}${M.target === "rain" ? " points" : " °C"}, so treat the answer with care.`}</p>`;
    const sw = e.sweep; if (!sw) return;
    $("#c_t").textContent = `${fl(sw.feature)}: the linear model answers with a ${M.target === "rain" ? "smooth curve" : "straight line"}, the tree model with steps that follow the data`;
    plot("#c_plot", [{ x: sw.x, y: sw.ridge.map(f), mode: "lines", name: lin, line: { color: "#8e8e93", width: 4 } }, { x: sw.x, y: sw.hgb.map(f), mode: "lines", name: "Decision-tree model", line: { color: "#d9480f", width: 4, shape: "hv" } },
      { x: [sw.current], y: [f(e.hgb)], mode: "markers", name: "Right now", marker: { size: 14, color: "#fff", line: { color: "#d9480f", width: 4 } } }], { xaxis: { title: fl(sw.feature) }, yaxis: { title: M.target === "rain" ? "Chance (%)" : "Forecast (°C)" }, legend: { orientation: "h", y: -0.22 } },
      `Response of the linear and boosted models to ${fl(sw.feature)}.`);
  }
  async function update() {
    try { M.ex = await api("/models/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body(M.over, M.sweep)) }); drawC(false); drawB(); if (M.target === "rain") drawA(); } catch (e) { $("#mnotice").innerHTML = `<div class="error" role="alert">${esc(e.message)}</div>`; }
  }

  /* 4. importance + pipeline */
  function drawD() {
    const o = M.ov, rows = o.importance.rows, mx = Math.max(...rows.map((r) => r.value), 1e-9);
    $("#d_imp").innerHTML = `<h3 class="takeaway">${esc(fl(rows[0].feature))} matters most</h3><div class="coefs">${rows.map((r) => `<div class="crow"><span>${esc(fl(r.feature))}</span><div class="cbar one"><i class="pos" style="width:${Math.max(1.5, (100 * Math.max(r.value, 0)) / mx)}%"></i></div><b>${r.value.toFixed(3)}</b></div>`).join("")}</div><p class="sub">${esc(o.importance.metric)}, measured on ${o.n_rows.toLocaleString()} test cases.</p>`;
    const en = o.ensemble;
    $("#d_pipe").innerHTML = `<h3 class="takeaway">The full chain</h3><div class="chain"><div class="cnode"><b>${en.features} inputs</b><small>now, past 1-48 h, rain totals, season and hour cycles, place</small></div><div class="carrow">↓</div><div class="cnode"><b>${en.n_trees} small trees</b><small>each adds a small correction</small></div><div class="carrow">↓</div><div class="cnode"><b>${M.target === "rain" ? "Calibration" : "Safety margin"}</b><small>${M.target === "rain" ? "scores become honest probabilities (fitted on 2016-2018)" : `±${en.conformal_halfwidth ? en.conformal_halfwidth.toFixed(1) : "?"} °C for a 90% range (fitted on 2016-2018)`}</small></div><div class="carrow">↓</div><div class="cnode out"><b>Forecast</b><small>always compared with simple baselines</small></div></div>`;
  }
  load();
}

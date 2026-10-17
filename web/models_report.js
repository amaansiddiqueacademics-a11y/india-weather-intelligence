/* All models: same plain style as the other tabs. Short answer per question, one simple bar chart each, models explained, terms, full scorecard.
   Every number comes from /api/ml/summary (scored on the 2019-2022 test years). */
const AMN = { persistence: "No change", climatology: "Usual climate", ridge: "Linear model", hgb: "Decision-tree model", logistic_calibrated: "Linear model", hgb_calibrated: "Decision-tree model", last_year: "Same as last year", group_mean: "Long-run average", group_trend: "Trend line", ridge_weather_only: "Linear model, weather only" };
const AMBASE = new Set(["persistence", "climatology", "last_year", "group_mean", "group_trend"]);
const AMP = [
  { id: "temp", label: "Temperature", icon: "sun", color: "#d9480f", risk: "A", target: "temperature_c", hs: [6, 24, 72], primary: "mae", low: true, models: ["ridge", "hgb"], base: ["persistence", "climatology"], link: "#/risk?tab=heat", linkText: "See it in Early warnings",
    cols: [["mae", "MAE (average error)", "°C", 2], ["mse", "MSE", " °C²", 2], ["rmse", "RMSE", "°C", 2], ["r2", "R²", "", 3], ["interval_coverage_90", "Range was right", "%", 0]] },
  { id: "hi", label: "Heat index (feels-like)", icon: "alert", color: "#c2410c", risk: "A", target: "heat_index_c", hs: [6, 24, 72], primary: "mae", low: true, models: ["ridge", "hgb"], base: ["persistence", "climatology"], link: "#/risk?tab=heat", linkText: "See it in Early warnings",
    cols: [["mae", "MAE (average error)", "°C", 2], ["mse", "MSE", " °C²", 2], ["rmse", "RMSE", "°C", 2], ["r2", "R²", "", 3], ["event_precision", "Hot alerts that were right", "%", 0], ["event_recall", "Hot hours caught", "%", 0]] },
  { id: "rain", label: "Heavy rain", icon: "drop", color: "#0a6bd6", risk: "B", target: "rain", hs: [24, 48, 72], primary: "roc_auc", low: false, models: ["logistic_calibrated", "hgb_calibrated"], base: ["climatology", "persistence"], link: "#/risk?tab=rain", linkText: "See it in Early warnings",
    cols: [["accuracy", "Accuracy", "%", 1], ["precision", "Alerts that were right", "%", 0], ["recall", "Heavy days caught", "%", 0], ["f1", "F1 (balance of the two)", "", 2], ["roc_auc", "Ranking skill (ROC-AUC)", "", 2]] },
  { id: "crop", label: "Crop yield", icon: "leaf", color: "#2f9e44", risk: "C", target: "yield_t_ha", hs: [0], primary: "mae", low: true, models: ["ridge", "ridge_weather_only", "hgb"], base: ["last_year", "group_mean", "group_trend"], link: "#/risk?tab=agri", linkText: "See it in Early warnings",
    cols: [["mae", "MAE (average error)", " t/ha", 2], ["mse", "MSE", " (t/ha)²", 2], ["rmse", "RMSE", " t/ha", 2], ["r2", "R²", "", 3]] },
];
const MRIO = {
  temp: ["Weather now, the last 1 to 48 hours, time of year, city", "Temperature in °C, plus a 90% range"],
  hi: ["Weather now, the last 1 to 48 hours, time of year, city", "Feels-like heat index in °C"],
  rain: ["Recent rain, humidity, pressure, soil moisture, season, city", "Chance (0 to 100%) of a heavy-rain day, 64.5 mm or more"],
  crop: ["Weather for the growing season, district, crop", "Yield in tonnes per hectare (t/ha)"] };
const MRDEF = { mae: ["MAE", "Mean absolute error: the average size of the miss, in the same unit. Lower is better."], mse: ["MSE", "Mean squared error: misses are squared first, so big misses count extra. Lower is better."], rmse: ["RMSE", "Square root of MSE, back in the original unit. Close to MAE means no big surprises."], r2: ["R²", "Share of the ups and downs explained. 1 is perfect, 0 is no better than the average."],
  interval_coverage_90: ["Range was right", "How often the real value fell inside the 90% range. Ideal is 90%."], event_precision: ["Hot alerts that were right", "Of the hot alerts raised, the share that came true."], event_recall: ["Hot hours caught", "Of the real hot hours, the share the model caught."],
  accuracy: ["Accuracy", "Share of days called right. Misleading here because heavy rain is rare."], precision: ["Alerts that were right", "Of the alerts raised, the share that came true."], recall: ["Heavy days caught", "Of the real heavy-rain days, the share caught."], f1: ["F1", "Balance of precision and recall. 1 is perfect."], roc_auc: ["ROC-AUC", "Can it rank risky days above calm ones? 0.5 is a coin flip, 1 is perfect."] };
const MRCOL = { hgb: "#0072B2", hgb_calibrated: "#0072B2", ridge: "#CC79A7", ridge_weather_only: "#b07aa1", logistic_calibrated: "#7048e8", persistence: "#8e8e93", climatology: "#E69F00", last_year: "#009E73", group_mean: "#56B4E9", group_trend: "#c9a227" };
const mrHL = (p, h) => (p.id === "crop" ? "next season" : p.id === "rain" ? "day " + h / 24 : "+" + h + " h");

async function modelsPage() {
  const [ml, rc] = await Promise.all([api("/ml/summary"), api("/ml/cases?kind=rain")]);
  const find = (p, h, m) => ml.results.find((r) => r.risk.startsWith(p.risk) && (p.id === "rain" ? r.target.startsWith("rain") : r.target === p.target) && r.horizon_h === h && r.model === m);
  /* accuracy is not stored, so derive it from prevalence, precision and recall */
  const val = (r, k) => { const m = r.metrics; if (k === "mse") return m.rmse * m.rmse; if (k === "accuracy") return 1 - m.prevalence * m.recall * (1 / m.precision - 1) - m.prevalence * (1 - m.recall); return m[k]; };
  const fmtv = (v, [, , u, d]) => (v === undefined || v === null || Number.isNaN(v) ? "–" : u === "%" ? (100 * v).toFixed(d) + "%" : v.toFixed(d) + u);
  const better = (p, a, b) => (p.low ? a < b : a > b);
  const pickBest = (rows, p) => rows.reduce((a, b) => (better(p, val(b.r, p.primary), val(a.r, p.primary)) ? b : a));
  const rowsAt = (p, h, list) => list.map((m) => ({ m, r: find(p, h, m) })).filter((x) => x.r);
  const pc = (v, d = 0) => (100 * v).toFixed(d) + "%";
  const rs = rc.summary, alerts = rs.caught + rs.false_alarms, rr = (m, h = 24) => find(AMP[2], h, m).metrics;
  const T = AMP[0];
  const t = (h, m) => find(T, h, m).metrics.mae, tb24 = Math.min(t(24, "persistence"), t(24, "climatology"));

  /* ---- the short answer, one card per question ---- */
  const answer = (p) => {
    const h = p.id === "crop" ? 0 : 24, all = rowsAt(p, h, [...p.models, ...p.base]), best = pickBest(all, p), trained = pickBest(rowsAt(p, h, p.models), p), yard = pickBest(rowsAt(p, h, p.base), p);
    const pv = (x) => val(x.r, p.primary), tw = better(p, pv(trained), pv(yard)), g = Math.round((100 * Math.abs(pv(yard) - pv(trained))) / pv(yard));
    const big = p.id === "rain" ? pv(trained).toFixed(2) : pv(trained).toFixed(2), unit = p.id === "rain" ? "ranking skill (1 is perfect)" : (p.id === "crop" ? "t/ha average error" : "°C average error");
    const line = p.id === "rain" ? `Good at ranking risky days, but only ${pc(rr("hgb_calibrated").precision)} of alerts were right, so treat it as a watch-list.`
      : tw ? `${g}% smaller error than the best baseline (${AMN[yard.m].toLowerCase()}).` : `The baseline "${AMN[yard.m].toLowerCase()}" was better (${fmtv(pv(yard), p.cols[0])}). No trained model beat it.`;
    const wc = tw ? "#1f8a4c" : "#c62828", sub = p.id === "rain" ? "ranking skill, 1 is perfect" : unit;
    return `<div class="rec sa reveal" style="--rc:${p.color}"><span class="rt">${ico(p.icon, p.color, 18)}${p.label}, ${p.id === "rain" ? "day 1" : mrHL(p, h)}</span>
      <b>${big}</b><small class="sunit">${sub.replace(" (1 is perfect)", "")}</small><span class="chip" style="background:${wc}1a;color:${wc}">${tw ? "Model wins" : "Baseline wins"}</span><p>Best: <b>${AMN[tw ? trained.m : yard.m]}</b>. ${line}</p><a class="golink" href="${p.link}">${p.linkText} ›</a></div>`;
  };

  /* ---- terms ---- */
  const TERMS = [
    ["layers", "#0a6bd6", "Input", "What the model is shown", "The facts handed to the model before it answers: weather now, the last 1 to 48 hours, rain totals, hour of day, time of year and the city."],
    ["target", "#7048e8", "Output", "What the model gives back", "A temperature in °C, a heat index, a crop yield in t/ha, or the chance of heavy rain as a percentage."],
    ["trend", "#d9480f", "Average error", "How far off it is, on average", `Lower is better. Forecast 30 °C, real 31 °C: error is 1. Our best 24-hour temperature forecast is off by ${t(24, "hgb").toFixed(2)} °C.`],
    ["check", "#2f9e44", "Accuracy", "Share of answers that were right", `Easy to fool for rare events. Heavy rain falls on only ${pc(rr("hgb_calibrated").prevalence, 1)} of days, so always saying "no" is about 99.5% accurate and useless.`],
    ["alert", "#c62828", "Precision", "When it raises an alert, how often is it right?", `Of every 100 heavy-rain alerts the best model raises on day 1, about ${Math.round(100 * rr("hgb_calibrated").precision)} come true.`],
    ["drop", "#0a8f8f", "Recall", "Of the real events, how many did it catch?", `Of every 100 real heavy-rain days on day 1, the best model catches about ${Math.round(100 * rr("hgb_calibrated").recall)}. Raising recall usually lowers precision.`],
    ["spark", "#e67700", "R²", "Share of the ups and downs explained", "1 is perfect; 0 means no better than always guessing the average."],
    ["clock", "#0a6bd6", "ROC-AUC", "Can it rank risky days above calm ones?", "0.5 is a coin flip and 1 is perfect. Works even when events are rare."],
  ];

  /* ---- model cards ---- */
  const MODELS = [
    { id: "ridge", color: "#7048e8", icon: "trend", name: "Linear model", used: "Temperature, heat index, crop yield", input: "Numbers: current and past readings, time of year, place.", output: "One number, such as °C or t/ha.", how: "Multiplies each input by a weight and adds them up, like a formula. Fast and easy to explain.", limit: "Draws only straight lines, so it can miss sudden or combined effects.", own: ["ridge", "ridge_weather_only"] },
    { id: "logistic_calibrated", color: "#0a6bd6", icon: "drop", name: "Linear model (rain version)", used: "Heavy rain only", input: "Recent rain, humidity, pressure, soil moisture, time of year, place.", output: "A chance of heavy rain from 0% to 100%.", how: "Linear regression whose result is squeezed into a probability.", limit: "Straight-line logic again, so it flags more days but gets more of them wrong.", own: ["logistic_calibrated"] },
    { id: "hgb", color: "#d9480f", icon: "layers", name: "Decision-tree model", used: "All four questions", input: "The same numbers as the others.", output: "A number or a chance, plus a 90% range for temperatures.", how: "Hundreds of small yes/no decision trees, each correcting the last. Learns curves and combinations of inputs.", limit: "Harder to explain, and its edge shrinks the further ahead it looks.", own: ["hgb", "hgb_calibrated"] },
    { id: "yardsticks", color: "#6e6e73", icon: "shield", name: "Baselines", used: "Every question", input: "Almost nothing: now, or the usual value.", output: "The same kind of answer as the models.", how: "No change, usual climate, same as last year, long-run average, trend line. They cost nothing, so a model only counts if it beats them.", limit: "They show the bar a model has to clear, not what to use.", own: ["persistence", "climatology", "last_year", "group_mean", "group_trend"] },
  ];
  const mtable = (own) => {
    const rows = [];
    AMP.forEach((p) => p.hs.forEach((h) => p.models.concat(p.base).filter((m) => own.includes(m)).forEach((m) => { const r = find(p, h, m); if (!r) return; const yard = AMBASE.has(m) ? null : pickBest(rowsAt(p, h, p.base), p), pcol = p.cols.find((c) => c[0] === p.primary);
      rows.push([p.label, mrHL(p, h), fmtv(val(r, p.primary), pcol), yard ? fmtv(val(yard.r, p.primary), pcol) : "–", yard ? (better(p, val(r, p.primary), val(yard.r, p.primary)) ? "Yes" : "No") : AMN[m]]); })));
    const head = own.some((m) => AMBASE.has(m)) ? ["Question", "Look-ahead", "Score", "—", "Baseline"] : ["Question", "Look-ahead", "Score", "Best baseline", "Beats it?"];
    return `<table class="am-table"><thead><tr>${head.map((h, i) => `<th class="${i > 1 && i < 4 ? "num" : ""}">${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i > 1 && i < 4 ? "num" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  };
  const mcard = (m) => `<div class="ic reveal" id="m_${m.id}"><div class="ich"><span class="ib" style="background:${m.color}1f">${ico(m.icon, m.color, 20)}</span><span>${m.name}</span></div>
    <dl class="am-kv"><div><dt>Goes in</dt><dd>${m.input}</dd></div><div><dt>Comes out</dt><dd>${m.output}</dd></div><div><dt>How it works</dt><dd>${m.how}</dd></div><div><dt>Weakness</dt><dd>${m.limit}</dd></div></dl>
    <button class="anbtn" data-an="${m.id}" aria-expanded="false" style="--ac:${m.color}"><span>See its scores</span> <span aria-hidden="true">›</span></button><div class="ana" id="an_${m.id}" hidden><div class="scroll">${mtable(m.own)}</div><p>2019 to 2022 test years. "Score" is average error (lower is better), except heavy rain, which uses ROC-AUC (higher is better).</p></div></div>`;

  /* ---- one simple chart card per question ---- */
  const chartCardFor = (p) => `<section class="card reveal" id="am_${p.id}"><div class="mr-qh"><span class="ib" style="background:${p.color}1f">${ico(p.icon, p.color, 20)}</span><div><b>${p.label}</b><span><em>Input:</em> ${MRIO[p.id][0]}</span><span><em>Output:</em> ${MRIO[p.id][1]}</span></div></div><h3 class="takeaway" id="ch_${p.id}_t"></h3>
    ${p.hs.length > 1 ? `<div class="am-seg" role="group" aria-label="Look ahead">${p.hs.map((h) => `<button type="button" data-h="${h}" aria-pressed="${h === (p.id === "rain" ? p.hs[0] : p.hs[1])}">${p.id === "rain" ? "Day " + h / 24 : "+" + h + " hours"}</button>`).join("")}</div>` : ""}
    <div id="ch_${p.id}" class="chart"></div><p class="am-best"></p>
    <button class="anbtn" data-an="ev_${p.id}" data-l="Evaluation metrics" aria-expanded="false" style="--ac:${p.color}"><span>Evaluation metrics</span> <span aria-hidden="true">›</span></button>
    <div class="ana" id="an_ev_${p.id}" hidden><div class="scroll"><table class="am-table"><thead></thead><tbody></tbody></table></div><dl class="am-kv mr-defs">${p.cols.map((c) => `<div><dt>${MRDEF[c[0]][0]}</dt><dd>${MRDEF[c[0]][1]}</dd></div>`).join("")}</dl></div></section>`;

  view.innerHTML = `<div class="page am"><header class="phead reveal"><h1>All models</h1><p class="lede">Which models we use, what goes in, what comes out, and how well each one scored. Every model is tested on years it never saw: it learned from 2002 to 2015 and is scored on 2019 to 2022.</p></header>
    <h2>The short answer</h2><p class="sub">For each question: the best trained model against the best baseline.</p><div class="icgrid am-g4">${AMP.map(answer).join("")}</div>
    <h2>What is a baseline?</h2><div class="card reveal"><p class="mr-bp">A <b>baseline</b> is a simple guess that needs no machine learning. It sets the bar: a model is only useful if it does better than the baseline. If a baseline wins, the model is not worth using yet.</p>
    <dl class="am-kv"><div><dt>No change</dt><dd>Assume the future looks like right now.</dd></div><div><dt>Usual climate</dt><dd>Use the typical value for this place and time of year.</dd></div><div><dt>Same as last year</dt><dd>Repeat last season's crop yield.</dd></div><div><dt>Long-run average</dt><dd>Use the average of all past years.</dd></div><div><dt>Trend line</dt><dd>Extend the long-term up or down trend.</dd></div></dl></div>
    <h2>Score by model</h2><p class="sub">Shorter bars are better for errors; longer is better for heavy rain. Blue, pink and purple are trained models; grey, amber and green are baselines.</p><div class="icgrid am-g1">${AMP.map(chartCardFor).join("")}</div>
    <h2>The models</h2><div class="icgrid am-g2">${MODELS.map(mcard).join("")}</div>
    <h2>Four words to know</h2><div class="icgrid am-g4">${TERMS.filter((x) => ["Average error", "Precision", "Recall", "ROC-AUC"].includes(x[2])).map(([i, c, tt, h, x]) => `<div class="ic reveal"><div class="ich"><span class="ib" style="background:${c}1f">${ico(i, c, 20)}</span><span>${tt}</span></div><div class="icbig" style="font-size:19px;line-height:1.25">${h}</div><p>${x}</p></div>`).join("")}</div>
    <a class="nextcard reveal" href="#/method"><small>Next</small><b>Know the limits</b><span>What we did and what we cannot claim</span></a></div>`;

  AMP.forEach((p) => {
    const root = $("#am_" + p.id);
    const draw = (h) => {
      const rows = rowsAt(p, h, [...p.models, ...p.base]), best = pickBest(rows, p), pv = (x) => val(x.r, p.primary), unit = p.cols[0][2];
      const sorted = [...rows].sort((a, b) => (p.low ? pv(a) - pv(b) : pv(b) - pv(a)));
      const base = pickBest(rows.filter((x) => AMBASE.has(x.m)), p), isBest = (x) => x === best;
      let tk;
      if (p.id === "rain") tk = `${AMN[best.m]} ranks risky days best (${pv(best).toFixed(2)}), well above the usual climate (${pv(base).toFixed(2)}); 0.5 would be a coin flip`;
      else if (AMBASE.has(best.m)) tk = `No trained model beats "${AMN[best.m].toLowerCase()}" for ${p.label.toLowerCase()} ${mrHL(p, h)}`;
      else tk = `${AMN[best.m]} is off by ${fmtv(pv(best), p.cols[0])} on average, against ${fmtv(pv(base), p.cols[0])} for the best baseline`;
      $("#ch_" + p.id + "_t").textContent = `${p.label} ${mrHL(p, h)}: ${tk}`;
      plot("#ch_" + p.id, [{ type: "bar", orientation: "h", y: sorted.map((x) => AMN[x.m]), x: sorted.map((x) => +pv(x).toFixed(3)), marker: { color: sorted.map((x) => MRCOL[x.m]) }, text: sorted.map((x) => (p.id === "rain" ? pv(x).toFixed(2) : pv(x).toFixed(2) + (unit === "%" ? "" : " " + unit.trim()))), textposition: "outside", cliponaxis: false }],
        { margin: { l: 190, r: 70, t: 6, b: 44 }, yaxis: { autorange: "reversed" }, xaxis: { title: p.id === "rain" ? "Ranking skill (ROC-AUC), higher is better" : "Average error (" + unit.trim() + "), lower is better", range: p.id === "rain" ? [0.4, 1.05] : [0, Math.max(...rows.map(pv)) * 1.18] }, showlegend: false, height: 60 + 52 * rows.length },
        `Bar chart of ${p.cols[0][1].toLowerCase()} by model for ${p.label} ${mrHL(p, h)}.`, { animate: false });
      root.querySelector("thead").innerHTML = `<tr><th>Model</th>${p.cols.map((c) => `<th class="num">${c[1]}</th>`).join("")}</tr>`;
      root.querySelector("tbody").innerHTML = sorted.map((x) => `<tr class="${isBest(x) ? "best" : ""}"><td>${AMN[x.m]}${AMBASE.has(x.m) ? " <i>baseline</i>" : ""}${isBest(x) ? " <em>best</em>" : ""}</td>${p.cols.map((c) => `<td class="num">${fmtv(val(x.r, c[0]), c)}</td>`).join("")}</tr>`).join("");
      const line = root.querySelector(".am-best");
      if (p.id === "rain") { const m = best.r.metrics; line.innerHTML = `It caught ${pc(m.recall)} of heavy-rain days and ${pc(m.precision)} of its alerts were right. Only ${pc(m.prevalence, 1)} of days are heavy-rain days, so use it as a watch-list, not an alarm.`; }
      else if (AMBASE.has(best.m)) line.innerHTML = `A baseline is hard to beat here, so the baseline is the better choice for now. ${p.id === "crop" ? "Last year's harvest already carries irrigation, seeds and farm practice that weather cannot see." : ""}`;
      else { const g = Math.round((100 * (pv(base) - pv(best))) / pv(base)); line.innerHTML = `${g}% lower error than the best baseline.${g < 10 ? " The gain is small at this distance." : ""}`; }
    };
    draw(p.id === "rain" ? p.hs[0] : p.hs.length === 3 ? p.hs[1] : p.hs[0]);
    const sg = root.querySelector(".am-seg"); if (sg) sg.addEventListener("click", (e) => { const b = e.target.closest("[data-h]"); if (!b) return; sg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b)); draw(+b.dataset.h); });
  });
  view.querySelectorAll(".anbtn").forEach((b) => b.addEventListener("click", () => { const q = $("#an_" + b.dataset.an), open = q.hidden; q.hidden = !open; b.setAttribute("aria-expanded", open); b.firstElementChild.textContent = open ? "Hide " + (b.dataset.l || "scores").toLowerCase() : b.dataset.l || "See its scores"; b.lastElementChild.textContent = open ? "‹" : "›"; }));
  finish();
}

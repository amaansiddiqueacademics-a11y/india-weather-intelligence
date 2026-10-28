/* Overview page. Live conditions come from the external Open-Meteo forecast API (labelled as such);
   history, map metrics and model sessions are queried from the PostgreSQL warehouse. */
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ----------------------------------------------------------------- icons */
function wxKind(code) {
  if (code === 0 || code === 1) return { k: "clear", t: code === 0 ? "Clear" : "Mostly clear" };
  if (code === 2) return { k: "partly", t: "Partly cloudy" };
  if (code === 3) return { k: "cloud", t: "Overcast" };
  if (code === 45 || code === 48) return { k: "fog", t: "Fog" };
  if (code >= 51 && code <= 57) return { k: "rain", t: "Drizzle" };
  if (code >= 61 && code <= 67) return { k: "rain", t: "Rain" };
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return { k: "snow", t: "Snow" };
  if (code >= 80 && code <= 82) return { k: "rain", t: "Showers" };
  if (code >= 95) return { k: "storm", t: "Thunderstorm" };
  return { k: "cloud", t: "Cloudy" };
}
const CLOUD = (fill = "#fff", dx = 0, dy = 0) => `<path transform="translate(${dx} ${dy})" d="M13 35h21a8 8 0 0 0 1.2-15.9A11 11 0 0 0 13.6 21.5 6.8 6.8 0 0 0 13 35z" fill="${fill === "#b9c6d6" ? "url(#gCloudD)" : "url(#gCloud)"}"/>`;
const SUN = (cx = 24, cy = 24, r = 9) => `<circle cx="${cx}" cy="${cy}" r="${r + 7}" fill="rgba(255,201,51,.22)"/><g stroke="#FFB703" stroke-width="2.4" stroke-linecap="round">${[0, 45, 90, 135, 180, 225, 270, 315].map((a) => `<line x1="${cx + Math.cos((a * Math.PI) / 180) * (r + 4)}" y1="${cy + Math.sin((a * Math.PI) / 180) * (r + 4)}" x2="${cx + Math.cos((a * Math.PI) / 180) * (r + 8)}" y2="${cy + Math.sin((a * Math.PI) / 180) * (r + 8)}"/>`).join("")}</g><circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#gSun)"/>`;
function icon(kind, day = true, size = 40) {
  const body = {
    clear: day ? SUN() : `<path d="M31 8a16 16 0 1 0 10 28A14 14 0 0 1 31 8z" fill="url(#gMoon)"/>`,
    partly: (day ? SUN(17, 17, 7) : `<path d="M22 6a11 11 0 1 0 8 19 10 10 0 0 1-8-19z" fill="url(#gMoon)"/>`) + CLOUD("#fff", 3, 2),
    cloud: CLOUD("#eef4fb", 0, -2) + CLOUD("#fff", 4, 4),
    fog: CLOUD("#eef4fb", 0, -4) + `<g stroke="#9db4cc" stroke-width="2.4" stroke-linecap="round"><line x1="10" y1="34" x2="38" y2="34"/><line x1="14" y1="40" x2="34" y2="40"/></g>`,
    rain: CLOUD("#dfe9f4", 0, -4) + `<g stroke="#2f80ed" stroke-width="2.6" stroke-linecap="round"><line x1="16" y1="36" x2="13" y2="43"/><line x1="25" y1="36" x2="22" y2="43"/><line x1="34" y1="36" x2="31" y2="43"/></g>`,
    storm: CLOUD("#b9c6d6", 0, -4) + `<polygon points="26,30 19,41 25,41 22,48 33,35 27,35 30,30" fill="#FFC933" stroke="#e59f00" stroke-width="1"/>`,
    snow: CLOUD("#eef4fb", 0, -4) + `<g fill="#7fb4e8"><circle cx="16" cy="39" r="2.4"/><circle cx="25" cy="42" r="2.4"/><circle cx="34" cy="39" r="2.4"/></g>`,
  }[kind];
  return `<svg class="wxi" width="${size}" height="${size}" viewBox="0 0 48 48" aria-hidden="true" focusable="false">${body}</svg>`;
}

/* ---------------------------------------------------------- instruments */
const polar = (cx, cy, r, deg) => [cx + r * Math.cos((deg * Math.PI) / 180), cy - r * Math.sin((deg * Math.PI) / 180)];
const arcPath = (a, b, r = 56) => { const [x1, y1] = polar(70, 70, r, 180 - 180 * a), [x2, y2] = polar(70, 70, r, 180 - 180 * b); return `M${x1.toFixed(1)} ${y1.toFixed(1)}A${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`; };
/* thin gradient arc with a dot that sweeps to the value */
function gauge(value, max, bands, label, sub) {
  const id = "gg" + ++uid, frac = Math.max(0, Math.min(1, value / max)), deg = -90 + frac * 180;
  let prev = 0; const stops = bands.map(([to, col]) => { const mid = (prev + Math.min(to, max)) / 2 / max; prev = to; return `<stop offset="${(mid * 100).toFixed(0)}%" stop-color="${col}"/>`; }).join("");
  return `<div class="inst col"><svg class="gauge" viewBox="0 0 140 84" role="img" aria-label="${esc(label)}: ${value}"><defs><linearGradient id="${id}" x1="14" x2="126" gradientUnits="userSpaceOnUse">${stops}</linearGradient></defs><path d="${arcPath(0, 1)}" fill="none" stroke="var(--track)" stroke-width="9" stroke-linecap="round"/><path d="${arcPath(0, 1)}" fill="none" stroke="url(#${id})" stroke-width="9" stroke-linecap="round"/><g class="needle" data-deg="${deg}" style="transform:rotate(-90deg)"><circle cx="70" cy="14" r="8.5" fill="#fff" stroke="rgba(0,0,0,.18)" stroke-width="1"/><circle cx="70" cy="14" r="3.5" fill="var(--ink)"/></g></svg><div class="gnum">${sub ?? ""}</div></div>`;
}
/* ring progress, animated from empty */
function ring(pct, colour, centre, sub) {
  const c = 2 * Math.PI * 34;
  return `<div class="inst"><svg viewBox="0 0 100 100" width="104" height="104" role="img" aria-label="${esc(sub)} ${pct}%"><circle cx="50" cy="50" r="34" fill="none" stroke="var(--track)" stroke-width="7"/><circle class="ring" data-off="${(c * (1 - pct / 100)).toFixed(1)}" cx="50" cy="50" r="34" fill="none" stroke="${colour}" stroke-width="7" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${c.toFixed(1)}"/></svg><div class="num">${centre}<small>${sub}</small></div></div>`;
}
let uid = 0;
/* water-filled drop for rain probability */
function drop(pct) {
  const id = "dr" + ++uid, y = 94 - (86 * pct) / 100;
  return `<div class="inst"><svg viewBox="0 0 80 100" width="86" height="106" role="img" aria-label="Chance of rain ${pct}%"><defs><clipPath id="${id}"><path d="M40 6C40 6 10 44 10 64a30 30 0 0 0 60 0C70 44 40 6 40 6z"/></clipPath></defs><path d="M40 6C40 6 10 44 10 64a30 30 0 0 0 60 0C70 44 40 6 40 6z" fill="rgba(255,255,255,.35)" stroke="#2f80ed" stroke-width="3"/><g clip-path="url(#${id})"><g style="transform:translateY(${y}px)"><path class="wave b" d="M-60 6q15-9 30 0t30 0 30 0 30 0 30 0 30 0 30 0v120h-210z" fill="#7fb6f5"/><path class="wave" d="M-60 4q15 9 30 0t30 0 30 0 30 0 30 0 30 0 30 0v120h-210z" fill="#2f80ed"/></g></g></svg><div class="num" style="top:58%;color:${pct > 55 ? "#fff" : "var(--ink)"}">${pct}%</div></div>`;
}
/* compass: the arrow points where the wind is heading */
function compassSvg(dir, speed) {
  const ticks = [...Array(36).keys()].map((i) => { const a = i * 10, [x1, y1] = polar(55, 55, 48, a), [x2, y2] = polar(55, 55, i % 9 === 0 ? 40 : 44, a); return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="var(--ink2)" stroke-width="${i % 9 === 0 ? 2 : 1}" opacity=".7"/>`; }).join("");
  return `<svg viewBox="0 0 110 110" width="118" height="118" role="img" aria-label="Wind from ${compass(dir)} at ${Math.round(speed)} km/h"><circle cx="55" cy="55" r="50" fill="rgba(255,255,255,.2)" stroke="var(--track)" stroke-width="2"/>${ticks}<text x="55" y="17" text-anchor="middle" font-size="10" font-weight="700" fill="var(--ink)">N</text><text x="97" y="58" text-anchor="middle" font-size="10" font-weight="700" fill="var(--ink)">E</text><text x="55" y="100" text-anchor="middle" font-size="10" font-weight="700" fill="var(--ink)">S</text><text x="13" y="58" text-anchor="middle" font-size="10" font-weight="700" fill="var(--ink)">W</text><g class="arrow" data-deg="${(dir + 180) % 360}" style="transform:rotate(0deg)"><path d="M55 20 L63 55 L55 49 L47 55 Z" fill="#0b6fb8"/><line x1="55" y1="49" x2="55" y2="86" stroke="#0b6fb8" stroke-width="3" stroke-linecap="round"/></g></svg>`;
}
const beaufort = (k) => (k < 2 ? "Calm" : k < 12 ? "Light air" : k < 20 ? "Light breeze" : k < 29 ? "Gentle breeze" : k < 39 ? "Breezy" : k < 62 ? "Strong wind" : "Gale");
function sunArc(frac, up) {
  const R = 100, cx = 130, cy = 104, f = Math.max(0, Math.min(1, frac)), a = Math.PI * (1 - f), sx = cx + R * Math.cos(a), sy = cy - R * Math.sin(a);
  const path = `M${cx - R} ${cy}A${R} ${R} 0 0 1 ${cx + R} ${cy}`;
  return `<svg viewBox="0 0 260 120" width="100%" style="max-height:120px" role="img" aria-label="Sun position between sunrise and sunset"><defs><linearGradient id="sg" x1="0" x2="1"><stop offset="0" stop-color="#ff9f43"/><stop offset=".5" stop-color="#ffd23f"/><stop offset="1" stop-color="#ff7a45"/></linearGradient><radialGradient id="sgl"><stop offset="0" stop-color="#fff3a0" stop-opacity=".95"/><stop offset="1" stop-color="#fff3a0" stop-opacity="0"/></radialGradient></defs>
    <path d="${path}" fill="none" stroke="var(--track)" stroke-width="3" stroke-dasharray="2 6" stroke-linecap="round"/><path d="${path}" pathLength="1" fill="none" stroke="url(#sg)" stroke-width="6" stroke-linecap="round" stroke-dasharray="${up ? f : 0} 1"/>
    <line x1="14" y1="${cy}" x2="246" y2="${cy}" stroke="var(--ink2)" stroke-width="1.5" opacity=".6"/>${up ? `<circle class="sunglow" cx="${sx}" cy="${sy}" r="22" fill="url(#sgl)"/><circle cx="${sx}" cy="${sy}" r="9" fill="#FFC933" stroke="#fff" stroke-width="2.5"/>` : `<path d="M${cx - 8} ${cy - 2}a8 8 0 0 1 16 0z" fill="#9fb0c7"/>`}</svg>`;
}

/* clear hourly temperature chart: coloured line, value labels every 3 h, day/night bands, rain chance */
function tempChart(times, temps, rain, sunrise, sunset) {
  const W = 1000, H = 330, L = 64, R = 30, T = 56, B = 84, n = temps.length, lo0 = Math.min(...temps), hi0 = Math.max(...temps);
  const lo = Math.floor(lo0 / 5) * 5 - 3, hi = Math.ceil(hi0 / 5) * 5 + 3, X = (i) => L + ((W - L - R) * i) / (n - 1), Y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const col = (v) => ramp(RAMPS.temp, (v - 14) / 28), pts = temps.map((v, i) => [X(i), Y(v)]);
  let d = `M${pts[0][0]} ${pts[0][1]}`; for (let i = 0; i < n - 1; i++) { const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(n - 1, i + 2)]; d += `C${p1[0] + (p2[0] - p0[0]) / 6} ${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6} ${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]} ${p2[1]}`; }
  const id = "tc" + ++uid, hiI = temps.indexOf(hi0), loI = temps.indexOf(lo0);
  const grid = []; for (let v = Math.ceil(lo / 5) * 5; v <= hi; v += 5) grid.push(`<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--track)" stroke-width="1"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="12" fill="var(--ink2)">${v}°</text>`);
  const mins = (d) => d.getHours() * 60 + d.getMinutes(), dark = times.map((t, i) => { const m = mins(new Date(t)); return m < mins(sunrise) || m > mins(sunset) ? i : -1; }).filter((i) => i >= 0);
  const bands = dark.map((i) => `<rect x="${X(i) - (W - L - R) / (n - 1) / 2}" y="${T - 22}" width="${(W - L - R) / (n - 1)}" height="${H - T - B + 22}" fill="rgba(10,25,60,.09)"/>`).join("");
  const hr = (t) => new Date(t).toLocaleTimeString("en-IN", { hour: "numeric", hour12: true }).replace(" ", "").toLowerCase();
  const labels = temps.map((v, i) => (i % 3 === 0 ? `<g><circle cx="${X(i)}" cy="${Y(v)}" r="5.5" fill="#fff" stroke="${col(v)}" stroke-width="3"/><text x="${X(i)}" y="${Y(v) - 14}" text-anchor="middle" font-size="16" font-weight="700" fill="var(--ink)">${Math.round(v)}°</text><text x="${X(i)}" y="${H - B + 28}" text-anchor="middle" font-size="12.5" font-weight="${i === 0 ? 700 : 500}" fill="var(--ink)">${i === 0 ? "Now" : hr(times[i])}</text><text x="${X(i)}" y="${H - B + 47}" text-anchor="middle" font-size="12" fill="${(rain[i] ?? 0) >= 40 ? "#2f80ed" : "var(--ink2)"}" font-weight="${(rain[i] ?? 0) >= 40 ? 700 : 500}">${rain[i] ?? 0}% rain</text></g>` : "")).join("");
  const stops = temps.map((v, i) => `<stop offset="${((i / (n - 1)) * 100).toFixed(1)}%" stop-color="${col(v)}"/>`).join("");
  const tag = (i, txt, above) => `<g><rect x="${X(i) - 36}" y="${Y(temps[i]) + (above ? -52 : 20)}" width="72" height="24" rx="12" fill="var(--ink)"/><text x="${X(i)}" y="${Y(temps[i]) + (above ? -35.5 : 36.5)}" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--g3)">${txt}</text></g>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Hourly temperature for the next 24 hours, from ${Math.round(lo0)} to ${Math.round(hi0)} degrees Celsius" style="display:block"><defs><linearGradient id="${id}s" x1="${L}" x2="${W - R}" gradientUnits="userSpaceOnUse">${stops}</linearGradient><linearGradient id="${id}f" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>
    ${bands}${grid.join("")}<path d="${d} L${X(n - 1)} ${H - B} L${X(0)} ${H - B} Z" fill="url(#${id}f)"/><path d="${d}" fill="none" stroke="rgba(10,25,60,.12)" stroke-width="9" stroke-linecap="round"/><path d="${d}" fill="none" stroke="url(#${id}s)" stroke-width="5.5" stroke-linecap="round"/>
    <line x1="${X(0)}" x2="${X(0)}" y1="${T - 22}" y2="${H - B}" stroke="var(--ink2)" stroke-dasharray="3 5" opacity=".6"/>${labels}${tag(hiI, `High ${Math.round(hi0)}°`, true)}${tag(loI, `Low ${Math.round(lo0)}°`, false)}</svg>`;
}

/* ---------------------------------------------------------- helpers */
const aqiBand = (v) => (v <= 50 ? ["Good", "#1a9850"] : v <= 100 ? ["Moderate", "#c9a100"] : v <= 150 ? ["Unhealthy for some", "#f46d43"] : v <= 200 ? ["Unhealthy", "#d73027"] : v <= 300 ? ["Very unhealthy", "#8e3b8e"] : ["Hazardous", "#7a0019"]);
const uvBand = (v) => (v < 3 ? ["Low", "No protection needed"] : v < 6 ? ["Moderate", "Wear sunscreen"] : v < 8 ? ["High", "Cover up, use SPF 30+"] : v < 11 ? ["Very high", "Avoid midday sun"] : ["Extreme", "Stay indoors at midday"]);
const compass = (d) => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(d / 45) % 8];
const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
const dur = (ms) => { const m = Math.round(ms / 60000); return `${Math.floor(m / 60)} h ${m % 60} min`; };
function countUp(el, to, ms = 700, dec = 0) {
  const from = parseFloat(el.dataset.v ?? "0") || 0; el.dataset.v = to;
  if (REDUCED) { el.textContent = to.toFixed(dec); return; }
  const t0 = performance.now();
  const step = (t) => { const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 3); el.textContent = (from + (to - from) * e).toFixed(dec); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
const lerp = (a, b, t) => a + (b - a) * t;
function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t)); const n = stops.length - 1, i = Math.min(n - 1, Math.floor(t * n)), f = t * n - i;
  const h = (c) => [1, 3, 5].map((k) => parseInt(c.slice(k, k + 2), 16)); const A = h(stops[i]), B = h(stops[i + 1]);
  return "#" + A.map((v, k) => Math.round(lerp(v, B[k], f)).toString(16).padStart(2, "0")).join("");
}
const RAMPS = { temp: ["#5b78e0", "#4fa3d9", "#7fd3a6", "#f2e35a", "#f59b38", "#c8321f"], rain: ["#e8f3fb", "#8cc6ea", "#3f8fd1", "#1f4fa3", "#2a1f7a"], heat: ["#fff3b0", "#ffc247", "#f2762e", "#e0422b", "#b3173a"], storm: ["#efe6f7", "#c3a6e0", "#9a66d6", "#7a3ad0", "#5b21b6"] };
const METRICS = {
  avg_temp_c: { ic: "thermo", def: "Average of all 24 hourly readings: how warm a place is, day and night.", short: "Mean temp", label: "Mean temperature (°C)", measure: "avg_temp_c", ramp: "temp", unit: "°C", dec: 1 },
  avg_tmax_c: { ic: "trend", def: "The hottest hour of each day, averaged: a typical afternoon.", short: "Daily high", label: "Typical daily high (°C)", measure: "avg_tmax_c", ramp: "temp", unit: "°C", dec: 1 },
  avg_hi_max_c: { ic: "flame", def: "Daily peak of how hot it feels (temperature plus humidity).", short: "Heat index", label: "Typical peak heat index (°C)", measure: "avg_hi_max_c", ramp: "heat", unit: "°C", dec: 1 },
  rain_per_year: { ic: "drop", def: "Total rain in a year, in mm (1 mm = 1 litre per m²).", short: "Rain per year", label: "Rainfall per year (mm)", measure: "total_rain_mm", perYear: true, ramp: "rain", unit: " mm", dec: 0 },
  hot_days: { ic: "sun", def: "Days per year when the high reaches 40 °C or more.", short: "Hot days", label: "Days per year with a high ≥ 40 °C", measure: "hot_days", perYear: true, ramp: "heat", unit: " d", dec: 1 },
  heavy_days: { ic: "cloud", def: "Days per year with 35.5 mm or more of rain: street-flooding downpours.", short: "Heavy-rain days", label: "Days per year with rain ≥ 35.5 mm", measure: "heavy_rain_days", perYear: true, ramp: "storm", unit: " d", dec: 2 },
};
const STATE_OF = {}; // web/data/india_states.geojson uses current state boundaries (Telangana included)
const LANDMARK = { BOM: ["Gateway of India", "Gateway of India"], DEL: ["India Gate", "India Gate"], CCU: ["Victoria Memorial, Kolkata", "Victoria Memorial"], MAA: ["Kapaleeshwarar Temple", "Kapaleeshwarar Temple"], BLR: ["Vidhana Soudha", "Vidhana Soudha"], HYD: ["Charminar", "Charminar"], AMD: ["Hutheesing Jain Temple", "Hutheesing Jain Temple"], JAI: ["Hawa Mahal", "Hawa Mahal"], LKO: ["Rumi Darwaza", "Rumi Darwaza"], LDH: ["Gurdwara Mehdiana Sahib", "Gurdwara Mehdiana Sahib (Ludhiana district)"] }; // [Wikipedia page, caption]
/* Bundled fallback photos (web/img/landmarks, small Wikimedia Commons thumbnails) used when the Wikipedia API is rate-limited or offline: [caption, Commons file page] */
const LANDMARK_LOCAL = { BOM: ["Gateway of India", "https://commons.wikimedia.org/wiki/File:Mumbai_03-2016_30_Gateway_of_India.jpg"], DEL: ["India Gate", "https://commons.wikimedia.org/wiki/File:India_Gate_front.jpg"], CCU: ["Victoria Memorial", "https://commons.wikimedia.org/wiki/File:Victoria_Memorial_situated_in_Kolkata.jpg"], MAA: ["Kapaleeshwarar Temple", "https://commons.wikimedia.org/wiki/File:Kapaleeswarar1.jpg"], BLR: ["Vidhana Soudha", "https://commons.wikimedia.org/wiki/File:Lighting_of_Vidhana_Soudha.jpg"], HYD: ["Charminar", "https://commons.wikimedia.org/wiki/File:Charminar_Hyderabad_1.jpg"], AMD: ["Sidi Saiyyed Mosque", "https://commons.wikimedia.org/wiki/File:Sidi_Saiyyed_Mosque%2C_Ahmedabad.jpg"], JAI: ["Hawa Mahal", "https://commons.wikimedia.org/wiki/File:East_facade_Hawa_Mahal_Jaipur_from_ground_level_%28July_2022%29_-_img_01.jpg"], LKO: ["Rumi Darwaza", "https://commons.wikimedia.org/wiki/File:Rumi_Darwaza_-_DSC2797-01.jpg"], LDH: ["Mohinder Singh Randhawa Library", "https://commons.wikimedia.org/wiki/File:Mohinder_Singh_Randhawa_Library.jpg"] };
/* One lookup for both pages: browser cache -> Wikipedia (high-res) -> bundled photo. Returns {src, page, caption?, local?} or null. */
async function landmarkInfo(loc) {
  const key = "lmimg_v2_" + loc.location_id; let info = null; try { info = JSON.parse(store.get(key, "null")); } catch { /* ignore */ }
  if (info) return info;
  try {
    const r = await fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent((LANDMARK[loc.location_id] ?? [loc.city])[0]));
    if (r.ok) { const j = await r.json(); if (j.thumbnail?.source) { info = { src: j.thumbnail.source, page: j.content_urls?.desktop?.page }; store.set(key, JSON.stringify(info)); return info; } }
  } catch { /* offline: fall through */ }
  const lc = LANDMARK_LOCAL[loc.location_id]; // not cached, so Wikipedia is retried on the next visit
  return lc ? { src: `/img/landmarks/${loc.location_id}.jpg`, page: lc[1], caption: lc[0], local: true } : null;
}
const seg = (id, cap, opts, sel) => `<div class="tgroup"><span class="cap" id="${id}_c">${cap}</span><div class="seg" id="${id}" role="group" aria-labelledby="${id}_c">${opts.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${String(v) === String(sel)}">${l}</button>`).join("")}</div></div>`;
function bindSeg(id, cb) { $("#" + id).addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (!b) return; $("#" + id).querySelectorAll("[data-v]").forEach((x) => x.setAttribute("aria-pressed", x === b)); cb(b.dataset.v); }); }
const wait = (ms) => new Promise((r) => setTimeout(r, REDUCED ? 0 : ms));

/* --------------------------------------------------------- page */
async function skyOverview() {
  const MODELS = [["hgb", "Decision-tree model"], ["linear", "Linear model"], ["persistence", "No change"], ["climatology", "Usual climate"]]; // same names on every page
  const sk = { force: new URLSearchParams(location.hash.split("?")[1] || "").get("scene") || "", metric: store.get("sk_metric", "avg_tmax_c"), season: "", y0: 2002, y1: 2022, last: null };
  const sm = { risk: "heat", h: 24, hour: 12, model: "hgb" };
  const cities = S.meta.locations;
  const qCity = new URLSearchParams(location.hash.split("?")[1] || "").get("city"); if (qCity && cities.some((c) => c.location_id === qCity)) S.city = qCity;
  Object.assign(ICONS, { thermo: '<path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>', flame: '<path d="M12 3c1 4 5 5.5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-9z"/>' });
  view.innerHTML = `<div class="skypage" id="skypage" data-scene="clear"><div class="skybg" id="skybg"><div class="sun"></div><div class="cloud" style="top:9%;animation-delay:-10s"></div><div class="cloud" style="top:26%;animation-delay:-48s;transform:scale(.8)"></div><div class="cloud" style="top:52%;animation-delay:-70s;transform:scale(1.2);opacity:.6"></div><div class="mist"></div><canvas id="skyfx" aria-hidden="true"></canvas><div class="flash" id="skflash"></div></div>
  <div class="sk-wrap">
    <h1 class="sr-only">Weather overview</h1>
    <div class="toprow"><div class="chips" role="group" aria-label="Choose a city" id="skcities"></div></div>
    <div class="dash">
      <div class="dcol1"><section class="hero" aria-live="polite" id="skhero">${skel()}</section><section class="glass insight" id="skinsight">${skel()}</section><div class="stats" id="skstat" aria-label="Conditions at a glance"></div><figure class="citycard" id="skcity"></figure></div>
      <div class="dcol2"><div class="sk-tiles" id="sktiles"></div>
        <section class="glass" style="margin-top:14px"><h2>${ico("clock", "var(--ink2)", 15)} Next 24 hours</h2><p class="sub" id="sk24sub">Loading the hourly forecast from the external service.</p><div id="sk24"></div><div class="hours" id="skhours"></div></section></div>
      <div class="dcol3"><section class="glass"><h2>${ico("clock", "var(--ink2)", 15)} Next 7 days</h2><div class="days" id="skdays"></div>
        <div class="conflegend" aria-label="Forecast confidence"><span><span class="cfd"><i class="on"></i><i class="on"></i><i class="on"></i></span> sure</span><span><span class="cfd"><i class="on"></i><i class="on"></i><i></i></span> likely</span><span><span class="cfd"><i class="on"></i><i></i><i></i></span> a tendency</span></div>
        <details class="how"><summary>How is the rain predicted?</summary><p><b>Where it comes from.</b> These numbers are not from our models. They come from Open-Meteo, which blends the computer weather models of national forecast centres. Each model simulates the atmosphere, starting from today's measurements and stepping forward hour by hour.</p><p><b>What "71% rain" means.</b> In roughly 7 of every 10 comparable forecast runs, measurable rain fell at this place. It is a chance, not a promise.</p><p><b>Why later days are less sure.</b> Small errors grow with time. Today and tomorrow are usually reliable; for days 5 to 7, read "drizzle on Saturday" as a tendency toward unsettled weather.</p></details></section><section class="glass" id="skctx" style="margin-top:14px"></section></div>
    </div>
    <section class="glass darkcard" style="margin-top:22px" id="skcmp"><h2>Compare the ten cities, season by season and year by year</h2><p class="sub">Pick a measure, a season and years. Click a city to select it for the whole page.</p>
      <div class="toolbar">${seg("skmetric", "Measure", Object.entries(METRICS).map(([k, m]) => [k, `${ico(m.ic, "currentColor", 14)}${m.short}`]), sk.metric)}${seg("skseason", "Season", [["", "All year"], ["Winter", "Winter"], ["Pre-monsoon", "Pre-monsoon"], ["Monsoon", "Monsoon"], ["Post-monsoon", "Post-monsoon"]], "")}
        <div class="tgroup"><span class="cap" id="skyr_c">Years: <span class="rangeval" id="skyrv">2002 to 2022</span></span><div class="range" role="group" aria-labelledby="skyr_c"><div class="rail2"></div><div class="fill" id="skfill"></div><input type="range" id="sky0" min="2002" max="2022" value="2002" aria-label="From year"><input type="range" id="sky1" min="2002" max="2022" value="2022" aria-label="To year"></div></div>
        ${seg("skpreset", "Quick years", [["all", "All 21"], ["early", "2002-2011"], ["late", "2012-2022"], ["last5", "Last 5"]], "all")}</div>
      <div class="mdef" id="skdef"></div>
      <div class="maprow"><div><div id="skmap" role="img" aria-label="Map of India with the ten supported cities"></div><p class="sr-only" id="skmaptxt"></p></div>
      <div><h3 id="skranktitle"></h3><div class="rank" id="skrank"></div><div class="legend"><span id="sklo"></span><i id="skgrad"></i><span id="skhi"></span></div><details><summary>Show the executed SQL</summary><pre id="sksql"></pre></details></div></div>
      <div class="yrblock"><h3 id="skyeartitle">Year by year</h3><div id="skyeartext"></div><div id="skyearchart" style="height:310px"></div></div></section>
    <section class="glass darkcard" style="margin-top:22px" id="skmodel"><h2>Can the forecasts be trusted? Test them on a past day</h2><p class="sub">Three steps: see how the data was split, pick a day, then watch a model make its forecast and compare it with what really happened.</p><div class="splitline" id="sm_split"><h3 class="mhead" style="border:0;margin:6px 0 0;padding:0"><span class="stepn">1</span>How the 21 years were split</h3><small class="sub">The model learns from the first block, is tuned on the second, and is judged only on the third, which it has never seen.</small>
  <div class="tlbar" style="margin-top:34px"><div class="tls s0" style="flex:14"><b>Training · 2002–2015</b><span id="sp_tr">14 years · 67%</span></div><div class="tls s1" style="flex:3"><b>Tuning</b><span id="sp_va">3 yrs</span></div><div class="tls s2" style="flex:4"><b>Unseen test · 2019–22</b><span id="sp_te">4 years · 19%</span></div><div class="tlmark" id="sm_mark" style="left:0"></div></div>
  <div class="tlcap"><span style="flex:14">The model learns the weather patterns here: lags, seasons, rainfall history.</span><span style="flex:3">2016–2018: picks the settings (tree depth, learning rate) and the width of the 90% range.</span><span style="flex:4">Final exam: scored once, never used to learn or tune.</span></div>
  <p class="sub" style="margin:8px 0 0">Time order is kept (no shuffling), so the model never sees the future while learning. Ten cities, hourly data.</p></div><h3 class="mhead" style="margin-top:22px"><span class="stepn">2</span>Pick a day and a question</h3><div class="toolbar">${seg("sm_risk", "Question", [["heat", "How hot?"], ["rain", "Heavy rain?"]], "heat")}<div class="tgroup"><label for="sm_date" class="cap">Forecast made on</label><input type="date" id="sm_date" value="2021-06-15" min="2002-01-10" max="2022-12-25"></div>${seg("sm_hour", "At this time", [[0, "00:00"], [6, "06:00"], [12, "12:00"], [18, "18:00"]], 12)}<span id="sm_hwrap">${seg("sm_h", "Look ahead", [[6, "6 h"], [24, "24 h"], [72, "72 h"]], 24)}</span>${seg("sm_model", "Show the steps of", MODELS, "hgb")}<button class="go" id="sm_run">Run the forecast</button></div>
      <h3 class="mhead" style="margin-top:22px"><span class="stepn">3</span>Watch the forecast being made<span class="badge" id="sm_badge" data-k="2" style="margin-left:10px"></span></h3>
      <div class="flow" id="skflow"></div><p id="sm_note" class="sub" aria-live="polite"></p>
      <div id="skcase"></div>
      <h3 class="mhead">Result over the unseen test years, 2019-22<small id="skscoresub"></small></h3>
      <div id="skscore"></div>
      <div id="skproof"></div>
      </section>
  </div></div>`;
  const shell = document.querySelector(".shell"); shell.classList.add("sky-mode");

  /* city + scene chips */
  const drawChips = () => ($("#skcities").innerHTML = cities.map((c) => `<button class="pill" aria-pressed="${c.location_id === S.city}" data-c="${c.location_id}">${esc(c.city)}</button>`).join(""));
  const pick = (id) => { S.city = id; store.set("city", id); sk.last = null; drawChips(); loadCity(); drawMapOnly(); drawYears(); drawProof(); };
  $("#skcities").addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (b) pick(b.dataset.c); });
  drawChips();
  function setScene(scene) { $("#skypage").dataset.scene = scene; shell.classList.toggle("dark-scene", scene === "night" || scene === "storm"); startFx(scene); }

  /* one famous landmark photo per city (Wikipedia/Wikimedia Commons, cached in the browser) */
  async function loadCityImage(loc) {
    const fig = $("#skcity"), caption0 = (LANDMARK[loc.location_id] ?? [loc.city, loc.city])[1];
    fig.innerHTML = `<div class="cpic"><div class="cbg"></div></div><figcaption><b>${esc(caption0)}</b><span>${esc(loc.city)}, ${esc(loc.state)}</span><span class="meta">ERA5 grid cell ${(+loc.grid_latitude).toFixed(2)}°N, ${(+loc.grid_longitude).toFixed(2)}°E · ${Math.round(loc.elevation_m)} m above sea level</span></figcaption>`;
    const info = await landmarkInfo(loc);
    if (info && S.city === loc.location_id) {
      const caption = info.caption ?? caption0, img = new Image(); img.alt = `${caption}, ${loc.city}`; let tried = false;
      img.onload = () => { if (S.city !== loc.location_id) return; const pic = fig.querySelector(".cpic"), w = pic.clientWidth || 340, want = w / (img.naturalWidth / img.naturalHeight); pic.style.height = Math.round(Math.max(190, Math.min(270, want))) + "px"; img.style.objectFit = want >= 190 && want <= 270 ? "cover" : "contain"; img.className = "ready"; pic.append(img); fig.querySelector(".cbg").style.backgroundImage = `url("${img.src}")`; fig.querySelector("figcaption b").textContent = caption; fig.querySelector("figcaption").insertAdjacentHTML("beforeend", `<a href="${esc(info.page)}" target="_blank" rel="noopener">Photo: ${info.local ? "Wikimedia Commons" : "Wikipedia / Wikimedia Commons"}</a>`); };
      img.onerror = () => { if (!tried) { tried = true; img.src = info.src; } }; // large size unavailable: fall back to the standard thumbnail
      img.src = info.local ? info.src : info.src.replace(/\/\d+px-/, "/1280px-");
    }
  }

  /* ------------ live conditions */
  async function loadCity() {
    const loc = cities.find((c) => c.location_id === S.city); loadCityImage(loc);
    const [live, air, normal] = await Promise.allSettled([api("/live?" + qs({ city: S.city })), api("/live/air?" + qs({ city: S.city })), (async () => { const n = new Date(); return api("/normal?" + qs({ city: S.city, month: n.getMonth() + 1, day: n.getDate(), hour: n.getHours() })); })()]);
    if (S.city !== loc.location_id) return; // user switched city meanwhile
    if (!document.getElementById("skypage")) return; // user left the Overview page while the live lookup was pending
    if (live.status !== "fulfilled") { renderOffline(loc, live.reason, normal.value); return; }
    sk.last = [loc, live.value, air.value ?? null, normal.value ?? null]; render(...sk.last);
  }
  function renderOffline(loc, reason, normal) {
    setScene(sk.force || "clear");
    $("#skhero").innerHTML = `<div class="glass"><h2>${esc(loc.city)}</h2><p>Live conditions are unavailable right now (${esc(reason?.message ?? "network")}). The history, map and model sections still work because they read the warehouse.</p></div>`;
    $("#skinsight").innerHTML = `<div><b>Live forecast offline</b><p>Showing history only. ${normal?.daily ? `For this time of year ${esc(loc.city)} normally reaches ${normal.daily.tmax_mean} °C by day and ${normal.daily.tmin_mean} °C overnight (2002-2022).` : ""}</p></div>`;
    ["#sktiles", "#skhours", "#skdays"].forEach((s) => ($(s).innerHTML = "")); $("#sk24").innerHTML = `<p class="sub">No hourly forecast available.</p>`; $("#sk24sub").textContent = "External forecast service unreachable.";
  }
  function animateInstruments() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll(".needle").forEach((n) => (n.style.transform = `rotate(${n.dataset.deg}deg)`));
      document.querySelectorAll(".arrow").forEach((n) => (n.style.transform = `rotate(${n.dataset.deg}deg)`));
      document.querySelectorAll(".ring").forEach((n) => (n.style.strokeDashoffset = n.dataset.off));
    }));
  }
  function render(loc, L, A, N) {
    const c = L.current, H = L.hourly, D = L.daily, day = c.is_day === 1, wk = wxKind(c.weather_code);
    const i0 = Math.max(0, H.time.indexOf(c.time.slice(0, 13) + ":00"));
    const auto = c.weather_code >= 95 ? "storm" : !day ? "night" : wk.k === "rain" || wk.k === "snow" ? "rain" : wk.k === "cloud" || wk.k === "fog" ? "cloud" : "clear";
    setScene(sk.force || auto);
    const now = new Date(c.time);
    $("#skhero").innerHTML = `<div class="hcity">${esc(loc.city)}</div><div class="hsub">${esc(loc.state)} · ${now.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}, ${now.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true })}</div>
      <div class="bigtemp"><span id="skt" data-v="0">0</span><sup>°</sup></div>
      <div class="hcond">${icon(wk.k, day, 34)}<b>${wk.t}</b></div><div class="hhl">H ${Math.round(D.temperature_2m_max[0])}°  ·  L ${Math.round(D.temperature_2m_min[0])}°  ·  Feels like ${Math.round(c.apparent_temperature)}°</div>
      <p class="normal" id="sknormal"></p><p class="sub src">Live data: ${esc(L.source)}, retrieved ${new Date(L.retrieved_at).toLocaleTimeString("en-IN")}.</p>`;
    countUp($("#skt"), Math.round(c.temperature_2m));
    if (N?.hourly?.temp_mean != null) { const d = c.temperature_2m - N.hourly.temp_mean, z = d / (N.hourly.temp_sd || 1);
      const word = Math.abs(d) < 0.8 ? "About normal" : d > 0 ? `${d.toFixed(1)} °C warmer than normal` : `${(-d).toFixed(1)} °C cooler than normal`;
      $("#sknormal").innerHTML = `<span class="dot" style="background:${Math.abs(z) < 1 ? "#1a9850" : z > 0 ? "#d73027" : "#2f80ed"}"></span><b>${word}</b> for this date and hour (typical ${N.hourly.temp_mean} °C, from ${N.daily?.years ?? 21} years of history).`; }
    /* answer-first message */
    const rainIdx = [...Array(12).keys()].map((k) => i0 + k).find((i) => (H.precipitation_probability[i] ?? 0) >= 50);
    const why = []; let head, body, ic = "clear";
    if (c.weather_code >= 95) { head = "Thunderstorms nearby: stay indoors if you can"; body = "Lightning risk is highest in open areas."; ic = "storm"; why.push(`weather code ${c.weather_code} (thunderstorm)`); }
    else if (rainIdx !== undefined) { head = `Carry an umbrella: rain likely around ${hhmm(H.time[rainIdx])}`; body = `${H.precipitation_probability[rainIdx]}% chance in that hour.`; ic = "rain"; why.push(`hourly precipitation probability ${H.precipitation_probability[rainIdx]}% at ${H.time[rainIdx]}`); }
    else if (c.apparent_temperature >= 41) { head = "Dangerous heat: avoid the outdoors from noon to 4 PM"; body = `Feels like ${Math.round(c.apparent_temperature)} °C. Drink water, rest in shade.`; why.push(`apparent temperature ${c.apparent_temperature} °C ≥ 41 °C`); }
    else if (c.apparent_temperature >= 35) { head = "Hot and sticky: plan outdoor tasks for early or late in the day"; body = `Feels like ${Math.round(c.apparent_temperature)} °C.`; why.push(`apparent temperature ${c.apparent_temperature} °C ≥ 35 °C`); }
    else if (c.temperature_2m <= 12) { head = "Cool out there: bring a layer"; body = `${Math.round(c.temperature_2m)} °C now.`; ic = "cloud"; why.push(`temperature ${c.temperature_2m} °C ≤ 12 °C`); }
    else { head = "Comfortable for most outdoor plans"; body = `${Math.round(c.temperature_2m)} °C and no rain expected in the next 12 hours.`; ic = day ? "clear" : "partly"; why.push("no heat, cold, rain or storm rule triggered"); }
    if (A?.current?.us_aqi >= 150) { body += ` Air is poor (AQI ${A.current.us_aqi}): go easy on outdoor exercise.`; why.push(`US AQI ${A.current.us_aqi}`); }
    $("#skinsight").innerHTML = `<div class="badge-i">${icon(ic, day, 36)}</div><div><b>${head}</b><p>${body}</p><details><summary>Why am I seeing this?</summary>Rule triggered: ${esc(why.join("; "))}. Inputs come from the external forecast service${N ? " and warehouse history for the 'normal' comparison" : ""}. Guidance only, not an official warning.</details></div>`;

    /* instruments */
    const uv = H.uv_index[i0] ?? 0, [uvw, uvadv] = uvBand(uv), aq = A?.current?.us_aqi, [aqt, aqc] = aq != null ? aqiBand(aq) : ["Unavailable", "#888"];
    const sr = new Date(D.sunrise[0]), ss = new Date(D.sunset[0]), up = now >= sr && now <= ss, frac = (now - sr) / (ss - sr);
    const sunText = up ? `${dur(ss - now)} of daylight left` : now < sr ? `Sun rises in ${dur(sr - now)}` : `Sun is down · rises ${hhmm(D.sunrise[1])}`;
    const rainP = D.precipitation_probability_max[0] ?? 0, wind = c.wind_speed_10m;
    const streaks = [18, 38, 58, 78].map((t, i) => `<i style="top:${t}%;--d:${Math.max(0.9, 4.2 - wind / 8) + i * 0.35}s;animation-delay:-${i * 0.7}s"></i>`).join("");
    $("#sktiles").innerHTML = `
      <div class="tile"><div class="streaks">${streaks}</div><div class="k">${ico("wind", "var(--ink2)", 15)} Wind</div><div class="inst">${compassSvg(c.wind_direction_10m, wind)}</div><div class="stat">${Math.round(wind)} km/h from ${compass(c.wind_direction_10m)}<small>${beaufort(wind)} · gusts ${Math.round(c.wind_gusts_10m)}</small></div></div>
      <div class="tile"><div class="k">${ico("drop", "var(--ink2)", 15)} Chance of rain today</div>${drop(rainP)}<div class="stat">${rainP < 20 ? "Unlikely" : rainP < 50 ? "Possible" : "Likely"}<small>${(D.precipitation_sum[0] ?? 0).toFixed(1)} mm expected${N?.daily?.rainy_day_pct != null ? ` · usually rains on ${N.daily.rainy_day_pct}% of days now` : ""}</small></div></div>
      <div class="tile span2"><div class="k">${ico("sunrise", "var(--ink2)", 15)} Sun</div><div class="inst" style="padding-top:2px">${sunArc(frac, up)}</div><div class="sunlbl">${sunText} · day length ${dur(ss - sr)}</div><div class="sunrow"><b>Sunrise <span>${hhmm(D.sunrise[0])}</span></b><b>Sunset <span>${hhmm(D.sunset[0])}</span></b></div></div>
      <div class="tile"><div class="k">${ico("sun", "var(--ink2)", 15)} UV index</div>${gauge(uv, 12, [[3, "#2e9e4f"], [6, "#f2c200"], [8, "#f28c28"], [11, "#d6332b"], [12, "#8a3fb0"]], "UV index", `${uv.toFixed(0)}<small>${uvw}</small>`)}<div class="stat"><small>${uvadv}</small></div></div>
      <div class="tile"><div class="k">${ico("leaf", "var(--ink2)", 15)} Air quality</div>${aq != null ? gauge(aq, 300, [[50, "#2e9e4f"], [100, "#f2c200"], [150, "#f28c28"], [200, "#d6332b"], [300, "#8a3fb0"]], "Air quality index", `${aq}<small>AQI</small>`) : `<div class="inst"><span class="sub">Feed unavailable</span></div>`}<div class="stat"><span class="dot" style="background:${aqc}"></span>${aqt}<small>${A ? "PM2.5 " + A.current.pm2_5 + " µg/m³ (modelled)" : ""}</small></div></div>
      <div class="tile"><div class="k">${ico("humid", "var(--ink2)", 15)} Humidity</div>${ring(c.relative_humidity_2m, "#0a9396", `${c.relative_humidity_2m}%`, "")}<div class="stat">${c.relative_humidity_2m < 30 ? "Dry" : c.relative_humidity_2m < 60 ? "Comfortable" : c.relative_humidity_2m < 80 ? "Humid" : "Very humid"}</div></div>
      <div class="tile"><div class="k">${ico("cloud", "var(--ink2)", 15)} Cloud cover</div>${ring(c.cloud_cover, "#6c8fb5", `${c.cloud_cover}%`, "")}<div class="stat">${c.cloud_cover < 20 ? "Clear sky" : c.cloud_cover < 60 ? "Some clouds" : "Mostly cloudy"}<small>${Math.round(c.pressure_msl)} hPa</small></div></div>`;
    animateInstruments();
    const ap = c.apparent_temperature, uvc = uv < 3 ? "#12b76a" : uv < 6 ? "#eab308" : uv < 8 ? "#f79009" : "#d92d20";
    const heatS = ap >= 41 ? ["Dangerous heat", "#d92d20"] : ap >= 35 ? ["Hot", "#f79009"] : ap >= 28 ? ["Warm", "#eab308"] : ap > 12 ? ["Comfortable", "#12b76a"] : ["Cool", "#2e90fa"];
    const rainS = rainP >= 60 ? ["Rain likely", "#2e6fed"] : rainP >= 30 ? ["Rain possible", "#5aa9ff"] : ["Mostly dry", "#12b76a"];
    const chips = [["sun", heatS], ["drop", rainS], ...(aq != null ? [["leaf", [`Air: ${aqt.toLowerCase()}`, aqc]]] : []), ["sun", [`UV ${uvw.toLowerCase()}`, uvc]]];
    $("#skstat").innerHTML = chips.map(([ic, [t, col]]) => `<span class="sc"><i style="background:${col}"></i>${ico(ic, col, 15)}${esc(t)}</span>`).join("");
    if (N?.daily?.tmax_mean != null) {
      const mean = N.daily.tmax_mean, sd = N.daily.tmax_sd || 1, hiF = D.temperature_2m_max[0], z = (hiF - mean) / sd, pos = Math.max(2, Math.min(98, 50 + (z / 3) * 50));
      $("#skctx").innerHTML = `<h2>${ico("trend", "var(--ink2)", 15)} Is today normal?</h2><div class="ctxbig">${hiF - mean >= 0 ? "+" : "−"}${Math.abs(hiF - mean).toFixed(1)} °C<small> vs the normal high</small></div><div class="ctxbar" role="img" aria-label="Forecast high is ${z.toFixed(1)} standard deviations from normal"><span class="ctxzone"></span><i style="left:${pos}%"></i></div><div class="ctxax"><span>cooler</span><span>normal ${mean} °C</span><span>hotter</span></div>
        <p class="sub">The forecast high of ${Math.round(hiF)}° is ${Math.abs(z).toFixed(1)} standard deviations ${z >= 0 ? "above" : "below"} the ${N.daily.years}-year normal. ${Math.abs(z) < 1 ? "That is well within the usual range for this date." : Math.abs(z) < 2 ? "That is noticeably unusual for this date." : "That is rare: only a few years in twenty reach this."}</p>`;
    } else $("#skctx").innerHTML = "";

    /* next 24h */
    const idx = [...Array(24).keys()].map((k) => i0 + k).filter((i) => i < H.time.length), ys = idx.map((i) => H.temperature_2m[i]), xs = idx.map((i) => H.time[i]);
    const [lo, hi] = [Math.min(...ys), Math.max(...ys)];
    $("#sk24sub").textContent = `Peaks at ${hi.toFixed(0)} °C around ${hhmm(xs[ys.indexOf(hi)])} and bottoms out at ${lo.toFixed(0)} °C around ${hhmm(xs[ys.indexOf(lo)])}. Shaded hours are night. External forecast, not our model.`;
    $("#sk24").innerHTML = tempChart(xs, ys, idx.map((i) => H.precipitation_probability[i] ?? 0), sr, ss);
    $("#skhours").innerHTML = idx.filter((_, k) => k % 3 === 0).slice(0, 8).map((i) => { const w = wxKind(H.weather_code[i]), h = new Date(H.time[i]).getHours(); return `<div>${icon(w.k, h >= 6 && h < 19, 32)}<span>${w.t}</span></div>`; }).join("");
    $("#skdays").innerHTML = D.time.map((t, k) => { const w = wxKind(D.weather_code[k]), conf = k < 2 ? 3 : k < 4 ? 2 : 1; return `<div class="day"><b>${k === 0 ? "Today" : new Date(t).toLocaleDateString("en-IN", { weekday: "short" })}<span class="cfd" title="${["", "A tendency only", "Likely", "Sure"][conf]}">${"<i class=\"on\"></i>".repeat(conf)}${"<i></i>".repeat(3 - conf)}</span></b>${icon(w.k, true, 44)}<div class="t">${Math.round(D.temperature_2m_max[k])}° <small>${Math.round(D.temperature_2m_min[k])}°</small></div><div class="c">${w.t}</div><div class="r">${D.precipitation_probability_max[k] ?? 0}% rain</div></div>`; }).join("");
  }

  /* ------------ weather effects: layered rain, ripples, lightning, stars */
  let fx = null;
  function startFx(scene) {
    stopFx(); const cv = $("#skyfx"); if (!cv || REDUCED) return; const ctx = cv.getContext("2d"), flash = $("#skflash");
    const size = () => { cv.width = innerWidth; cv.height = innerHeight; }; size(); addEventListener("resize", size);
    const wet = scene === "rain" || scene === "storm", stars = scene === "night"; if (!wet && !stars) { fx = () => removeEventListener("resize", size); return; }
    const layers = [{ n: 70, len: 9, v: 9, a: 0.22, w: 1 }, { n: 60, len: 16, v: 14, a: 0.38, w: 1.3 }, { n: 34, len: 26, v: 22, a: 0.6, w: 1.8 }];
    const drops = layers.flatMap((L) => [...Array(L.n)].map(() => ({ L, x: Math.random() * cv.width, y: Math.random() * cv.height })));
    const ripples = [], star = [...Array(110)].map(() => ({ x: Math.random(), y: Math.random() * 0.6, s: Math.random() })); let bolt = null, nextBolt = performance.now() + 2500, raf, last = 0;
    const slant = scene === "storm" ? 0.32 : 0.14;
    function makeBolt() { const pts = []; let x = cv.width * (0.15 + Math.random() * 0.7), y = 0; pts.push([x, y]); while (y < cv.height * (0.45 + Math.random() * 0.3)) { y += 18 + Math.random() * 30; x += (Math.random() - 0.5) * 44; pts.push([x, y]); } const branch = pts.slice(Math.floor(pts.length / 2)).map(([bx, by], i) => [bx + i * 12, by + i * 10]); return { pts, branch, born: performance.now() }; }
    const tick = (t) => { raf = requestAnimationFrame(tick); if (t - last < 32) return; last = t; ctx.clearRect(0, 0, cv.width, cv.height);
      if (stars) { ctx.fillStyle = "#fff"; star.forEach((p) => { ctx.globalAlpha = 0.25 + 0.75 * Math.abs(Math.sin(t / 1100 + p.s * 9)); ctx.fillRect(p.x * cv.width, p.y * cv.height, 1.8, 1.8); }); ctx.globalAlpha = 1; return; }
      layers.forEach((L) => { ctx.strokeStyle = `rgba(214,232,255,${L.a})`; ctx.lineWidth = L.w; ctx.beginPath(); drops.forEach((d) => { if (d.L !== L) return; ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - L.len * slant, d.y + L.len); d.y += L.v; d.x -= L.v * slant; if (d.y > cv.height) { if (L.v > 20 && Math.random() < 0.5) ripples.push({ x: d.x, y: cv.height - 6 - Math.random() * 90, r: 1, a: 0.5 }); d.y = -30; d.x = Math.random() * (cv.width + 200); } }); ctx.stroke(); });
      ctx.lineWidth = 1.2; for (let i = ripples.length - 1; i >= 0; i--) { const r = ripples[i]; r.r += 0.9; r.a -= 0.03; if (r.a <= 0) { ripples.splice(i, 1); continue; } ctx.strokeStyle = `rgba(220,236,255,${r.a})`; ctx.beginPath(); ctx.ellipse(r.x, r.y, r.r * 2.2, r.r * 0.6, 0, 0, 7); ctx.stroke(); }
      if (scene === "storm") { if (t > nextBolt && !bolt) { bolt = makeBolt(); nextBolt = t + 3000 + Math.random() * 5000; }
        if (bolt) { const age = t - bolt.born; if (age > 520) { bolt = null; flash.style.opacity = 0; } else { const on = age < 120 || (age > 200 && age < 300); flash.style.opacity = on ? 0.45 : 0.08; if (on) { ctx.shadowColor = "#cfe0ff"; ctx.shadowBlur = 22; ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.beginPath(); bolt.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); ctx.lineWidth = 1.6; ctx.beginPath(); bolt.branch.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); ctx.shadowBlur = 0; } } } } };
    raf = requestAnimationFrame(tick); fx = () => { cancelAnimationFrame(raf); removeEventListener("resize", size); flash.style.opacity = 0; };
  }
  function stopFx() { if (fx) fx(); fx = null; }
  window.__skyCleanup = () => { stopFx(); const sh = document.querySelector(".shell"); sh?.classList.remove("sky-mode", "dark-scene"); };

  /* ------------ map + ranking (warehouse queries) */
  async function drawMap() {
    const M = METRICS[sk.metric]; store.set("sk_metric", sk.metric); drawDef(M); const years = Math.max(1, sk.y1 - sk.y0 + 1);
    let r; try { r = await api("/olap/aggregate?" + qs({ time_level: "all", geo_level: "city", measures: M.measure, seasons: sk.season, year_from: sk.y0, year_to: sk.y1 })); } catch (e) { $("#skrank").innerHTML = err(e); return; }
    if (!sk.geo) { try { const g = await fetch("/data/india_states.geojson"); sk.geo = g.ok ? await g.json() : { type: "FeatureCollection", features: [] }; } catch { sk.geo = { type: "FeatureCollection", features: [] }; } } /* no outline file: still draw city markers */
    sk.values = Object.fromEntries(r.rows.map((x) => [x.city, M.perYear ? x[M.measure] / years : x[M.measure]])); sk.M = M;
    $("#sksql").textContent = r.sql + "\n-- parameters: " + JSON.stringify(r.params); drawMapOnly(); loadYears();
  }
  function drawMapOnly() {
    const M = sk.M, vals = sk.values; if (!M || !vals) return;
    const list = cities.map((c) => ({ ...c, v: vals[c.city] })).filter((c) => c.v != null).sort((a, b) => b.v - a.v);
    if (!list.length) { $("#skrank").innerHTML = `<p class="sub">No data for this filter.</p>`; return; }
    const vmin = Math.min(...list.map((c) => c.v)), vmax = Math.max(...list.map((c) => c.v)), span = vmax - vmin || 1, colour = (v) => ramp(RAMPS[M.ramp], (v - vmin) / span), fmtv = (v) => v.toFixed(M.dec) + M.unit;
    $("#skranktitle").textContent = `${M.label}${sk.season ? " · " + sk.season : ""} · ${sk.y0}-${sk.y1}`;
    $("#skrank").innerHTML = list.map((c) => `<button data-c="${c.location_id}" aria-pressed="${c.location_id === S.city}"><span>${esc(c.city)}</span><span class="bar"><i style="--c:${colour(c.v)};width:${Math.max(6, ((c.v - vmin) / span) * 100)}%;display:block;height:100%"></i></span><span class="val">${fmtv(c.v)}</span></button>`).join("");
    $("#skgrad").style.background = `linear-gradient(90deg, ${RAMPS[M.ramp].join(",")})`; $("#sklo").textContent = fmtv(vmin); $("#skhi").textContent = fmtv(vmax);
    const sel = cities.find((c) => c.location_id === S.city), selState = STATE_OF[sel?.city] ?? sel?.state, names = sk.geo.features.map((f) => f.properties.name);
    const ink = "#eaf2fb", dark = true;
    plot("#skmap", [
      { type: "choropleth", geojson: sk.geo, featureidkey: "properties.name", locations: names, z: names.map((n) => (n === selState ? 1 : 0)), colorscale: [[0, dark ? "rgba(255,255,255,.12)" : "rgba(255,255,255,.55)"], [1, dark ? "rgba(255,255,255,.3)" : "rgba(255,255,255,.95)"]], showscale: false, marker: { line: { color: dark ? "rgba(255,255,255,.35)" : "rgba(16,36,58,.35)", width: 0.7 } }, hovertemplate: "%{location}<extra>state outline</extra>" },
      { type: "scattergeo", lat: list.map((c) => c.latitude), lon: list.map((c) => c.longitude), mode: "markers+text", text: list.map((c) => c.city), textposition: list.map((c) => ({ Mumbai: "middle left", Chennai: "middle right", Kolkata: "middle right", Bengaluru: "bottom center", Hyderabad: "middle right", Ahmedabad: "middle left" }[c.city] || "top center")), textfont: { size: 12, color: ink, family: "Public Sans" }, customdata: list.map((c) => c.location_id),
        marker: { size: list.map((c) => (c.location_id === S.city ? 30 : 22)), color: list.map((c) => colour(c.v)), line: { color: list.map((c) => (c.location_id === S.city ? "#ffd166" : "rgba(8,18,38,.9)")), width: list.map((c) => (c.location_id === S.city ? 4 : 2)) }, opacity: 0.95 },
        hovertext: list.map((c) => `<b>${c.city}</b><br>${M.label}: ${fmtv(c.v)}<br>grid cell ${(+c.grid_latitude).toFixed(2)}°N ${(+c.grid_longitude).toFixed(2)}°E`), hoverinfo: "text" }],
      { margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false, dragmode: false, geo: { projection: { type: "mercator" }, lonaxis: { range: [67.5, 98] }, lataxis: { range: [6.5, 37] }, visible: false, showframe: false, bgcolor: "rgba(0,0,0,0)" } },
      `Map of India. ${list.length} cities coloured by ${M.label}: highest ${list[0].city} (${fmtv(list[0].v)}), lowest ${list.at(-1).city} (${fmtv(list.at(-1).v)}).`);
    const el = $("#skmap"); el.removeAllListeners?.("plotly_click"); el.on?.("plotly_click", (ev) => { const id = ev.points?.[0]?.customdata; if (id) pick(id); });
    $("#skmaptxt").textContent = `Highest ${list[0].city} ${fmtv(list[0].v)}; lowest ${list.at(-1).city} ${fmtv(list.at(-1).v)}.`;
  }
  $("#skrank").addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (b) pick(b.dataset.c); });
  bindSeg("skmetric", (v) => { sk.metric = v; drawMap(); }); bindSeg("skseason", (v) => { sk.season = v; drawMap(); });
  const syncRange = () => { const a = +$("#sky0").value, b = +$("#sky1").value; sk.y0 = Math.min(a, b); sk.y1 = Math.max(a, b); $("#skyrv").textContent = `${sk.y0} to ${sk.y1}`; $("#skfill").style.left = `${((sk.y0 - 2002) / 20) * 100}%`; $("#skfill").style.right = `${100 - ((sk.y1 - 2002) / 20) * 100}%`; };
  bindSeg("skpreset", (v) => { const [a, b] = { all: [2002, 2022], early: [2002, 2011], late: [2012, 2022], last5: [2018, 2022] }[v]; $("#sky0").value = a; $("#sky1").value = b; syncRange(); drawMap(); drawYears(); });
  ["sky0", "sky1"].forEach((id) => { $("#" + id).addEventListener("input", syncRange); $("#" + id).addEventListener("change", () => { syncRange(); drawMap(); drawYears(); }); }); syncRange();

  /* ------------ year-by-year analysis for the selected measure */
  const dkAxis = { gridcolor: "rgba(255,255,255,.08)", linecolor: "rgba(255,255,255,.2)", tickfont: { color: "#a9bdd4" }, zeroline: false };
  const DK = (o = {}) => ({ hoverlabel: { bgcolor: "#0b1a33", bordercolor: "#ffb74d", font: { color: "#ffffff", size: 13 } }, font: { color: "#dbe7f5", family: "Inter, system-ui, sans-serif", size: 12.5 }, ...o, xaxis: { ...dkAxis, ...(o.xaxis || {}) }, yaxis: { ...dkAxis, ...(o.yaxis || {}) } });
  function drawDef(M) { $("#skdef").innerHTML = `${ico(M.ic, "#ffd166", 22)}<div><b>${M.short}:</b> ${M.def}</div>`; }
  async function loadYears() {
    const M = METRICS[sk.metric];
    try { sk.series = (await api("/olap/aggregate?" + qs({ time_level: "year", geo_level: "city", measures: M.measure, seasons: sk.season, year_from: 2002, year_to: 2022 }))).rows; } catch (e) { $("#skyearchart").innerHTML = err(e); return; }
    drawYears();
  }
  function drawYears() {
    const M = METRICS[sk.metric], rows = sk.series; if (!rows) return;
    const sel = cities.find((c) => c.location_id === S.city), by = {};
    rows.forEach((r) => (by[r.city] ??= []).push([r.year, +r[M.measure]]));
    const mine = by[sel.city] || [], f = (v) => v.toFixed(M.dec) + M.unit, avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    if (!mine.length) { $("#skyearchart").innerHTML = ""; return; }
    const hi = maxBy(mine, (p) => p[1]), lo = minBy(mine, (p) => p[1]), a = mine.slice(0, 5).map((p) => p[1]), b = mine.slice(-5).map((p) => p[1]), d = avg(b) - avg(a);
    const ranked = Object.entries(by).map(([c, v]) => [c, avg(v.map((p) => p[1]))]).sort((x, y) => y[1] - x[1]), rank = ranked.findIndex(([c]) => c === sel.city) + 1;
    const season = sk.season ? `${sk.season} ` : "";
    $("#skyeartitle").textContent = `${sel.city}: ${season}${M.short.toLowerCase()} ${Math.abs(d) < (M.dec === 0 ? 5 : 0.15) ? "has barely moved" : d > 0 ? "is higher" : "is lower"} in the last 5 years than in the first 5 (${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(M.dec === 0 ? 0 : 1)}${M.unit})`;
    $("#skyeartext").innerHTML = `<div class="kpis"><div class="kt"><b style="color:#ff9b7a">${hi[0]}</b><span>Peak year · ${f(hi[1])}</span></div><div class="kt"><b style="color:#7cc4f2">${lo[0]}</b><span>Lowest year · ${f(lo[1])}</span></div><div class="kt"><b>#${rank} <small>of ${ranked.length}</small></b><span>Rank over 21 years</span></div><div class="kt"><b style="color:#ffd166">${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(M.dec === 0 ? 0 : 1)}<small>${M.unit}</small></b><span>Last 5 vs first 5 years</span></div></div><p class="sub">Gold: ${sel.city}. Grey: other cities. Shaded: your years.</p>`;
    const traces = Object.entries(by).filter(([c]) => c !== sel.city).map(([c, v], i) => ({ x: v.map((p) => p[0]), y: v.map((p) => p[1]), type: "scatter", mode: "lines", line: { color: "rgba(169,189,212,.35)", width: 1.3 }, name: "Other cities", legendgroup: "o", showlegend: i === 0, hovertemplate: `${c} %{x}: %{y:.${M.dec}f}${M.unit}<extra></extra>` }));
    traces.push({ x: mine.map((p) => p[0]), y: mine.map((p) => p[1]), type: "scatter", mode: "lines+markers", line: { color: "#ffd166", width: 3, shape: "spline", smoothing: 0.4 }, marker: { size: 7, color: "#ffd166" }, name: sel.city, hovertemplate: `${sel.city} %{x}: %{y:.${M.dec}f}${M.unit}<extra></extra>` },
      { x: [hi[0], lo[0]], y: [hi[1], lo[1]], type: "scatter", mode: "markers+text", text: [`peak ${hi[0]}`, `low ${lo[0]}`], textposition: ["top center", "bottom center"], textfont: { color: "#fff", size: 12 }, marker: { size: 11, color: ["#ff7a59", "#56B4E9"], line: { color: "#fff", width: 2 } }, showlegend: false, hoverinfo: "skip" });
    plot("#skyearchart", traces, DK({ margin: { l: 56, r: 14, t: 14, b: 42 }, legend: { orientation: "h", y: -0.16, font: { color: "#dbe7f5" } }, xaxis: { dtick: 2, title: { text: "Year", font: { size: 12 } } }, yaxis: { title: { text: M.label, font: { size: 12 } } },
      shapes: [{ type: "rect", xref: "x", yref: "paper", x0: sk.y0 - 0.5, x1: sk.y1 + 0.5, y0: 0, y1: 1, fillcolor: "rgba(255,255,255,.09)", line: { width: 0 }, layer: "below" }] }),
      `${sel.city} ${M.label} by year: peak ${hi[0]} at ${f(hi[1])}, lowest ${lo[0]} at ${f(lo[1])}; ranks ${rank} of ${ranked.length} cities.`, { animate: false });
  }

  /* ------------ time-travel test of the models */
  const kt = (v, l, c) => `<div class="kt"><b${c ? ` style="color:${c}"` : ""}>${v}</b><span>${l}</span></div>`;
  const winner = (name, line, close) => `<div class="winner"><span>★</span><div><b>Best model: ${name}</b><small>${line}${close ? " Close call: the runner-up is nearly as good." : ""}</small></div></div>`;
  const sm2 = { year: 2021 }, pad2 = (n) => String(n).padStart(2, "0");
  const splitOf = (y) => (y <= 2015 ? 0 : y <= 2018 ? 1 : 2);
  const apiModel = (m, risk) => (m === "linear" ? (risk === "heat" ? "ridge" : "logistic") : m);
  const sumName = (m, risk) => (risk === "heat" ? apiModel(m, risk) : m === "hgb" ? "hgb_calibrated" : m === "linear" ? "logistic_calibrated" : m);
  const MCOL = { hgb: "#ffb74d", linear: "#4dd0e1", persistence: "#b39ddb", climatology: "#7ddc9a" };
  function syncDate() {
    const v = $("#sm_date").value; if (!v) return;
    const d = new Date(v + "T00:00:00"), y = d.getFullYear(), i = splitOf(y), frac = ((y - 2002) + (d - new Date(y, 0, 1)) / 31536000000) / 21;
    const b = $("#sm_badge"); b.dataset.k = i;
    b.textContent = i === 2 ? "Fair test: the model never saw this day" : i === 1 ? "Partly fair: this day was used to tune settings" : "Not fair: the model learned from this day";
    if (i === 2) sm2.year = y; const mk = $("#sm_mark"); if (mk) mk.style.left = `calc(${(100 * Math.min(1, Math.max(0, frac))).toFixed(1)}% - 1px)`;
  }
  $("#sm_date").addEventListener("change", () => { syncDate(); drawProof(); });
  api("/ml/summary").then((m) => { sk.ml ??= m; const r = m.registry.find((x) => x.model_name === "hgb" && x.horizon_h === 24) ?? m.registry[0], te = m.results.find((x) => x.n)?.n; if (!r) return; const f = (n) => n.toLocaleString("en-IN"), va = Math.round((r.n_train * 3) / 14), tot = r.n_train + va + (te || 0);
    $("#sp_tr").textContent = `14 years · ${f(r.n_train)} samples · ${Math.round((100 * r.n_train) / tot)}%`; $("#sp_va").textContent = `3 yrs · ≈${f(va)}`; $("#sp_te").textContent = `4 years · ${f(te)} samples · ${Math.round((100 * te) / tot)}%`; syncDate(); }).catch(() => {});
  const flowHtml = () => `<div class="fnode" id="f0"><b>1. Recent weather</b><small>What the model sees: the 8 days before the forecast</small><div class="ic" id="f0c"></div></div>
    <div class="fnode" id="f1"><b>2. Prepare the inputs</b><small>Turns that history into numbers: past values, rain totals, time of year</small><div class="big" id="f1n"></div></div>
    <div class="fnode" id="f2"><b>3. The model</b><small id="f2s">Learned from 2002-2015</small><div class="big" id="f2n"></div></div>
    <div class="fnode" id="f3"><b>4. Margin of error</b><small id="f3s">How far off it could be</small><div class="big" id="f3n"></div></div>
    <div class="fnode" id="f4"><b>5. Forecast vs reality</b><small>The model's answer, then what really happened</small><div class="big" id="f4n"></div></div>`;
  $("#skflow").innerHTML = flowHtml();
  const horizons = { heat: [[6, "6 h"], [24, "24 h"], [72, "72 h"]], rain: [[24, "24 h"], [48, "48 h"], [72, "72 h"]] };
  const bindSession = () => { bindSeg("sm_h", (v) => { sm.h = +v; drawScore(); drawProof(); }); };
  bindSeg("sm_risk", (v) => { sm.risk = v; if (!horizons[v].some(([h]) => h === sm.h)) sm.h = 24; $("#sm_hwrap").innerHTML = seg("sm_h", "Look ahead", horizons[v], sm.h); bindSession(); drawScore(); drawProof(); });
  bindSeg("sm_hour", (v) => (sm.hour = +v)); bindSeg("sm_model", (v) => (sm.model = v)); bindSession();
  const unit = "°C";
  function renderCase(results, risk, shown) {
    const ok = results.filter((r) => r.v); let head, rows, verdict;
    if (risk === "heat") {
      const err1 = (r) => { const x = r.v.results.heat_index_c; return x.observed_at_target == null ? null : Math.abs(x.predicted - x.observed_at_target); }, errs = ok.map(err1).filter((e) => e != null), best = errs.length ? Math.min(...errs) : null;
      head = "<th>Model</th><th class='num'>Predicted heat index</th><th>90% range</th><th class='num'>What really happened</th><th class='num'>Miss</th>";
      rows = ok.map((r) => { const x = r.v.results.heat_index_c, e = err1(r), name = MODELS.find(([m]) => m === r.m)[1]; return `<tr class="${e != null && e === best ? "best" : ""} ${r.m === shown ? "main" : ""}"><td>${name}</td><td class="num">${x.predicted.toFixed(1)} ${unit}</td><td>${x.lower_90 != null ? `${x.lower_90.toFixed(1)} to ${x.upper_90.toFixed(1)}` : "none"}</td><td class="num">${x.observed_at_target != null ? x.observed_at_target.toFixed(1) + " " + unit : "n/a"}</td><td class="num">${e != null ? e.toFixed(1) + " " + unit : "n/a"}</td></tr>`; }).join("");
      const m = ok.find((r) => r.m === "hgb"), b = ok.find((r) => r.m === "persistence"), mh = m && err1(m), bh = b && err1(b);
      verdict = mh != null && bh != null ? `The decision-tree model missed by <b>${mh.toFixed(1)} ${unit}</b>, the "no change" guess by <b>${bh.toFixed(1)} ${unit}</b>. ${mh < bh ? "The model won this day." : "The simple guess won this day: that happens, which is why the result over all test years, below, matters more than one case."}` : "No observation is stored for the target time, so the miss cannot be computed.";
    } else {
      const thr = ok[0]?.v.results.rain_threshold_mm_24h ?? 64.5, obs = ok[0]?.v.results.observed_24h_rain_mm, happened = obs != null ? obs >= thr : null;
      head = `<th>Model</th><th class='num'>Chance of ≥ ${thr} mm</th><th>Alert raised?</th><th>What really happened</th><th>Verdict</th>`;
      rows = ok.map((r) => { const x = r.v.results, name = MODELS.find(([m]) => m === r.m)[1], vt = happened == null ? "n/a" : x.flagged && happened ? "Correct alert" : !x.flagged && !happened ? "Correctly quiet" : x.flagged ? "False alarm" : "Missed it";
        return `<tr class="${r.m === shown ? "main" : ""}"><td>${name}</td><td class="num">${(100 * x.probability_heavy_rain).toFixed(1)}%</td><td>${x.flagged ? "Yes" : "No"}</td><td>${obs != null ? obs.toFixed(1) + " mm in 24 h" + (happened ? " (heavy)" : "") : "n/a"}</td><td>${vt}</td></tr>`; }).join("");
      verdict = happened == null ? "No observation is stored for this window." : happened ? `Heavy rain really did fall (${obs.toFixed(1)} mm). Check which models raised the alert.` : `No heavy rain fell (${obs.toFixed(1)} mm). Most days are like this, so a good model mostly stays quiet; a "Correctly quiet" row is a success, not a non-answer.`;
    }
    $("#skcase").innerHTML = `<h3 class="mhead">The same day, forecast by all four models</h3><div class="scroll"><table class="ctable"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div><div class="verdict">${verdict}</div>`;
  }
  $("#sm_run").onclick = async () => {
    const btn = $("#sm_run"); btn.disabled = true; $("#skflow").innerHTML = flowHtml(); $("#skcase").innerHTML = ""; $("#sm_note").textContent = "";
    const risk = sm.risk, issue = `${$("#sm_date").value}T${pad2(sm.hour)}:00:00`;
    const call = (m) => api("/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ location_id: S.city, issue_time: issue, risk, horizon_h: sm.h, model: apiModel(m, risk) }) }).then((v) => ({ m, v })).catch((e) => ({ m, e }));
    const all = Promise.all(MODELS.map(([m]) => call(m))), on = (i) => $("#f" + i).classList.add("on");
    on(0); await wait(500); const results = await all, res = results.find((r) => r.m === sm.model);
    if (res.e) { $("#f1").classList.add("bad"); $("#f4").classList.add("bad"); $("#f4n").innerHTML = `<small>No prediction: ${esc(res.e.message)}</small>`; $("#sm_note").textContent = "Try a day between 2002-01-10 and 2022-12-25 and one of the offered times."; btn.disabled = false; return; }
    const p = res.v;
    for (const [k, v] of Object.entries(p.inputs)) { $("#f0c").insertAdjacentHTML("beforeend", `<span>${esc(k.replace(/_/g, " "))}: ${esc(v)}</span>`); await wait(160); }
    on(1); $("#f1n").innerHTML = `${p.n_features} <small>input numbers</small>`; await wait(700);
    on(2); const mi = p.model_info; $("#f2n").innerHTML = `<small>${esc(MODELS.find(([m]) => m === sm.model)[1])}</small>`; if (mi.historical_test_metrics) $("#f2s").textContent = mi.historical_test_metrics.mae != null ? `Usually off by ${mi.historical_test_metrics.mae.toFixed(2)} °C on unseen years` : `Ranking score ${(mi.historical_test_metrics.pr_auc ?? 0).toFixed(2)} on unseen years (PR-AUC)`; await wait(800);
    on(3); const r = p.results; $("#f3s").textContent = risk === "heat" ? "The real value should land in this range 9 times out of 10" : "How the chance becomes an alert";
    $("#f3n").innerHTML = risk === "heat" ? (r.heat_index_c.lower_90 != null ? `${r.heat_index_c.lower_90.toFixed(1)} to ${r.heat_index_c.upper_90.toFixed(1)}<small> °C</small>` : `<small>This simple guess gives no range</small>`) : `<small>${r.decision_threshold != null ? `Alert raised above ${(100 * r.decision_threshold).toFixed(1)}% chance` : "Probability only, no alert level"}</small>`; await wait(700);
    on(4);
    if (risk === "heat") { $("#f4n").innerHTML = `<span id="o1" data-v="0">0</span><small> °C heat index</small><br><small>Reality: ${r.heat_index_c.observed_at_target != null ? r.heat_index_c.observed_at_target.toFixed(1) + " °C" : "not recorded"} · forecast band: ${esc(p.risk_category)}</small>`; countUp($("#o1"), r.heat_index_c.predicted, 800, 1); }
    else { $("#f4n").innerHTML = `<span id="o1" data-v="0">0</span><small>% chance of heavy rain</small><br><small>${r.flagged ? "Alert raised" : "No alert"} · reality: ${r.observed_24h_rain_mm != null ? r.observed_24h_rain_mm.toFixed(1) + " mm in 24 h" : "not recorded"}</small>`; countUp($("#o1"), 100 * r.probability_heavy_rain, 800, 1); }
    $("#sm_note").textContent = `Asked ${p.issue_time.slice(0, 16).replace("T", " ")}, answer for ${p.target_time.slice(0, 16).replace("T", " ")}.`;
    renderCase(results, risk, sm.model); btn.disabled = false;
  };

  /* headline result over the whole test period; the full scoreboard lives on the "All models" page */
  async function drawScore() {
    const el = $("#skscore"); el.innerHTML = skel(); let ml;
    try { sk.ml ??= await api("/ml/summary"); ml = sk.ml; } catch (e) { el.innerHTML = err(e); return; }
    const risk = sm.risk, rows = ml.results.filter((r) => (risk === "heat" ? r.risk.startsWith("A") && r.target === "heat_index_c" : r.risk.startsWith("B")) && r.horizon_h === sm.h);
    const get = (m) => rows.find((r) => r.model === sumName(m, risk)), h = get("hgb"), b = get("persistence");
    if (!h || !b) { el.innerHTML = `<p class="sub">No evaluation stored for this horizon.</p>`; return; }
    $("#skscoresub").textContent = `${h.n ? fmtn(h.n) : "All"} forecasts, ten cities, ${sm.h} h ahead`;
    const link = `<p class="sub" style="margin-top:12px">Every model, every look-ahead and the full metric tables are on the <a href="#/models" style="color:#ffb74d;font-weight:600">All models page</a>.</p>`;
    if (risk === "heat") {
      const mh = h.metrics.mae, mb = b.metrics.mae, pct = (100 * (mb - mh)) / mb, cov = h.metrics.interval_coverage_90;
      el.innerHTML = `<div class="kpis">${kt(mh.toFixed(2) + "<small> °C</small>", "Decision-tree model: average miss", "#ffb74d")}${kt(mb.toFixed(2) + "<small> °C</small>", "“No change” guess: average miss")}${kt((pct >= 0 ? "" : "−") + Math.abs(pct).toFixed(0) + "%", pct >= 0 ? "Smaller error than “no change”" : "Larger error than “no change”", pct >= 0 ? "#5eead4" : "#ff8a8a")}${kt((100 * cov).toFixed(0) + "%", "Real value inside the 90% range (aim: 90%)")}</div>${link}`;
    } else {
      const x = (m, k) => get(m).metrics[k];
      el.innerHTML = `<div class="kpis">${kt((100 * x("hgb", "prevalence")).toFixed(1) + "%", "Cases with heavy rain")}${kt((100 * x("hgb", "precision")).toFixed(0) + "%", "Alerts that were real", "#4dd0e1")}${kt((100 * x("hgb", "recall")).toFixed(0) + "%", "Heavy-rain cases caught", "#ffb74d")}${kt(x("hgb", "roc_auc").toFixed(2), "Ranking score (0.5 = coin flip)")}</div><p class="sub">A watch signal, not a guarantee.</p>${link}`;
    }
  }

  /* predicted-vs-actual proof (heat model, one test year) */
  async function drawProof() {
    const el = $("#skproof"); if (!el) return;
    if (sm.risk !== "heat") { el.innerHTML = ""; return; }
    const sel = cities.find((c) => c.location_id === S.city), yr = sm2.year; let rows;
    try { rows = (await api("/ml/predictions?" + qs({ location_id: S.city, model: "hgb", target: "heat_index_c", horizon_h: sm.h, year: yr }))).filter((r) => r.actual != null); } catch (e) { el.innerHTML = err(e); return; }
    if (!rows.length) { el.innerHTML = `<p class="sub">No stored test predictions for ${sel.city} in ${yr}.</p>`; return; }
    const ae = rows.map((r) => Math.abs(r.predicted - r.actual)), mae = ae.reduce((a, b) => a + b, 0) / ae.length, inside = rows.filter((r) => r.actual >= r.pred_lower && r.actual <= r.pred_upper).length / rows.length, worst = rows[ae.indexOf(Math.max(...ae))];
    el.innerHTML = `<h3 class="mhead">Proof: ${sel.city} ${yr}, predicted vs real<small>${sm.h} h ahead, one noon prediction per day</small></h3>
      <div class="toolbar" style="margin-top:0">${seg("sm_year", "Test year", [[2019, "2019"], [2020, "2020"], [2021, "2021"], [2022, "2022"]], yr)}</div>
      <div class="kpis">${kt(mae.toFixed(1) + "<small> °C</small>", "Average miss", "#ffb74d")}${kt((100 * inside).toFixed(0) + "%", "Days inside 90% range", "#5eead4")}${kt(Math.max(...ae).toFixed(1) + "<small> °C</small>", "Biggest miss · " + new Date(worst.target_time).toLocaleDateString("en-IN", { day: "numeric", month: "short" }))}</div>
      <div class="proofgrid"><div id="skp1" style="height:300px"></div><div id="skp2" style="height:300px"></div></div>`;
    bindSeg("sm_year", (v) => { sm2.year = +v; drawProof(); });
    const t = rows.map((r) => r.target_time);
    plot("#skp1", [{ x: t, y: rows.map((r) => r.pred_lower), type: "scatter", mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false }, { x: t, y: rows.map((r) => r.pred_upper), type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(255,183,77,.2)", name: "90% range", hoverinfo: "skip" },
      { x: t, y: rows.map((r) => r.predicted), type: "scatter", mode: "lines", line: { color: "#ffb74d", width: 2 }, name: "Model predicted", hovertemplate: "%{x|%d %b}: %{y:.1f} °C<extra>Model</extra>" }, { x: t, y: rows.map((r) => r.actual), type: "scatter", mode: "lines", line: { color: "#5eead4", width: 1.6 }, name: "What happened", hovertemplate: "%{x|%d %b}: %{y:.1f} °C<extra>Real</extra>" }],
      DK({ margin: { l: 50, r: 10, t: 10, b: 60 }, legend: { orientation: "h", y: -0.2, font: { color: "#dbe7f5" } }, yaxis: { title: { text: "Heat index °C", font: { size: 12 } } } }), `${sel.city} ${yr}: predicted versus observed heat index, mean miss ${mae.toFixed(1)} °C.`, { animate: false });
    const lo = Math.min(...rows.map((r) => r.actual), ...rows.map((r) => r.predicted)), hi = Math.max(...rows.map((r) => r.actual), ...rows.map((r) => r.predicted));
    plot("#skp2", [{ x: [lo, hi], y: [lo, hi], type: "scatter", mode: "lines", line: { color: "rgba(255,255,255,.5)", dash: "dot" }, name: "Perfect", hoverinfo: "skip" }, { x: rows.map((r) => r.actual), y: rows.map((r) => r.predicted), type: "scatter", mode: "markers", marker: { size: 6, color: ae, colorscale: [[0, "#5eead4"], [0.35, "#ffb74d"], [1, "#ff6b6b"]], cmin: 0, cmax: 5, opacity: 0.85, colorbar: { title: { text: "Miss °C", font: { size: 11 } }, thickness: 8, len: 0.7, tickfont: { color: "#a9bdd4" } } }, name: "Days", hovertemplate: "Real %{x:.1f}<br>Model %{y:.1f}<extra></extra>" }],
      DK({ margin: { l: 50, r: 10, t: 10, b: 60 }, showlegend: false, xaxis: { title: { text: "What happened °C", font: { size: 12 } } }, yaxis: { title: { text: "Predicted °C", font: { size: 12 } } } }), `Scatter of predicted against observed heat index for ${yr}: points cluster along the diagonal.`, { animate: false });
  }

  loadCity(); drawMap(); syncDate(); drawScore(); drawProof();
}
function skel() { return `<div class="skeleton" role="status" aria-label="Loading"></div>`; }

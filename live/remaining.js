// The races still out: the frozen forecast's own simulations reweighted by every call and count so far.
//
//   node live/remaining.js        data/cache/live_obs.json (midterms/live_obs.py) -> web/data/live.json, web/data/live_history.json
//
// The simulations are the forecast's: live/engine.js and data/final/whatif_2026.json are the published what-if engine and its
// data from the forecast's frozen commit, with the same seed, so the margins below are the forecast's own draws. They share
// national, state, urban and demographic-group shocks, so a call or a count in one race moves every race tied to it.
//   observations  [{race: "H:IN-3" | "S:KY" | "G:FL", call: "D" | "R", d, r, pct, skew, skew_sd}]: call = the side that won
//                 ("D" the Republican's opponent); d, r = each side's share of the ballots counted; pct = share counted
//   count error   final margin = counted margin - skew + Student-t (4 df) error, sd 2 + 28 (1 - f)^1.5 at share counted f,
//                 plus skew_sd in quadrature: wide early, tight once most of the vote is in, heavy tails so one odd count
//                 cannot take over
//   calls         a called race counts for its winner in every seat total; in the weights it enters softly, as
//                 log Phi(+-margin / CALL_SD), so a simulation where the winner lost by more than a few points is
//                 down-weighted rather than dropped (dropping left too few simulations once a dozen upsets were called)
//   tempering     with hundreds of observations the weights collapse onto a few simulations, so their joint log-likelihood
//                 is scaled by the largest a <= 1 that keeps an effective sample of ESS_TARGET
//   race odds     an uncalled race with a count reads its own count directly: prior = its margin under the weights without
//                 its own count (leave-one-out, normal approximation), likelihood = the count at 60 % of the count error
//                 plus skew_sd; normal posterior
// The calls and counts are Wikipedia's (midterms/results_wiki.py) unless the results file holds official returns.
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const WhatIf = require("./engine.js");

const ROOT = path.join(__dirname, "..");
const D = JSON.parse(fs.readFileSync(path.join(ROOT, "data/final/whatif_2026.json"), "utf8"));
const OBS = JSON.parse(fs.readFileSync(path.join(ROOT, "data/cache/live_obs.json"), "utf8"));
const OUT = path.join(ROOT, "web/data/live.json");
const HIST = path.join(ROOT, "web/data/live_history.json");
const N = 20000;
const CALL_SD = 3;
const ESS_TARGET = 2000;
const E = new WhatIf.Engine(D, N);

function Phi(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
const logPhi = (x) => (x > -5 ? Math.log(Math.max(Phi(x), 1e-300)) : -0.5 * x * x - Math.log(-x) - 0.9189385);
const logT4 = (z) => -2.5 * Math.log(1 + z * z / 4); // Student-t, 4 df, up to a constant
const countSd = (f) => 2 + 28 * Math.pow(Math.max(0, 1 - f), 1.5);

function simulate() {
  // Engine.run's arithmetic at the published settings, keeping every simulation's margins
  const H = D.house, sh = D.shared, gs = sh.group_sd, hs = H.sd, mv = E.mv;
  const nH = E.nH, nSG = E.nS + E.nG, nSt = H.states.length, races = D.senate.races.concat(D.governor.races);
  const hOwn = E.hPart.map((p) => p.own), sOwn = E.sPart.concat(E.gPart).map((p) => p.own);
  const split = (s, m) => { const mm = Math.min(m, 0.95 * s); return { rest: Math.sqrt(s * s - mm * mm), m: mm }; };
  const sRest = races.map((x) => (mv ? split(x.ssh, mv.m).rest : x.ssh) * x.sl);
  const sMove = races.map((x, j) => (mv ? split(x.ssh, mv.m).m : 0) * E.kS[j] * x.sl);
  const hsp = mv ? split(hs.nat, mv.m) : { rest: hs.nat, m: 0 };
  const HM = new Float32Array(N * nH), SM = new Float32Array(N * nSG), NAT = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const natRest = E.hNat[i] * hsp.rest, natMove = E.hMv[i] * hsp.m, urb = E.hUrb[i] * hs.urban, g = E.hG;
    NAT[i] = natRest + natMove;
    for (let j = 0; j < nH; j++) {
      const s = H.seats[j];
      HM[i * nH + j] = s.fix ? (s.fix === "D" ? 50 : -50)
        : s.mu + natRest + natMove * E.kH[j] + E.hSt[i * nSt + s.st] * hs.state + urb * s.u
          + g[0][i] * hs.hisp * s.h + g[1][i] * hs.cuban * s.c + g[2][i] * hs.asian * s.a + g[3][i] * hs.wnc * s.w
          + g[4][i] * (hs.htx || 0) * (s.htx || 0) + g[5][i] * (hs.hfl || 0) * (s.hfl || 0)
          + g[6][i] * (hs.hwest || 0) * (s.hwe || 0) + E.hRes[i * nH + j] * hOwn[j];
    }
    const sg = E.sG, wz = E.sW[i] * sh.wnc_sd;
    for (let j = 0; j < nSG; j++) {
      const x = races[j];
      SM[i * nSG + j] = x.mu + E.sNat[i] * sRest[j] + E.hMv[i] * sMove[j] + sg[0][i] * gs.hisp * x.h
        + sg[1][i] * gs.cuban * x.c + sg[2][i] * gs.asian * x.a + wz * x.z + E.sRes[i * nSG + j] * sOwn[j];
    }
  }
  return { HM, SM, NAT, races };
}

const H = D.house, nH = E.nH, nS = E.nS, nSG = E.nS + E.nG;
const { HM, SM, NAT, races } = simulate();
const idx = {};
H.seats.forEach((s, j) => (idx["H:" + s.seat] = ["H", j]));
races.forEach((x, j) => (idx[(j < nS ? "S:" : "G:") + x.st] = ["S", j]));
const margin = (i, [t, j]) => (t === "H" ? HM[i * nH + j] : SM[i * nSG + j]);

function weightedQuantiles(vals, w, sw, ps) {
  const o = Array.from(vals.keys()).filter((i) => w[i] > 0).sort((x, y) => vals[x] - vals[y]);
  const out = [];
  let c = 0, k = 0;
  for (const i of o) {
    c += w[i] / sw;
    while (k < ps.length && c >= ps[k]) { out.push(vals[i]); k++; }
  }
  while (out.length < ps.length) out.push(vals[o[o.length - 1]]);
  return out;
}

function run(obs) {
  const lw = new Float64Array(N), forced = new Map(), own = [];
  for (const o of obs) {
    const k = idx[o.race];
    if (!k) { console.error("unknown race " + o.race); continue; }
    if (o.call) {
      const s = o.call === "D" ? 1 : -1;
      forced.set(k.join(":"), s);
      for (let i = 0; i < N; i++) lw[i] += logPhi(s * margin(i, k) / CALL_SD);
    }
    if (!(o.pct > 0)) continue;
    const f = Math.min(1, o.pct / 100), m = o.d - o.r - (o.skew || 0), sd = Math.hypot(countSd(f), o.skew_sd || 0);
    const ow = new Float32Array(N);
    for (let i = 0; i < N; i++) { ow[i] = logT4((margin(i, k) - m) / sd); lw[i] += ow[i]; }
    if (!o.call) own.push({ k, m, f, ssd: o.skew_sd || 0, ow });
  }
  let mx = -Infinity;
  for (const v of lw) if (v > mx) mx = v;
  const w = new Float64Array(N);
  let sw = 0, alpha = 1;
  const fill = (a) => {
    let s2 = 0; sw = 0;
    for (let i = 0; i < N; i++) { w[i] = Math.exp(a * (lw[i] - mx)); sw += w[i]; s2 += w[i] * w[i]; }
    return sw * sw / s2;
  };
  let ess = fill(1);
  if (ess < ESS_TARGET) {
    let lo = 0.001, hi = 1;
    for (let it = 0; it < 40; it++) { const a = (lo + hi) / 2; if (fill(a) < ESS_TARGET) hi = a; else lo = a; }
    alpha = lo;
    ess = fill(alpha);
  }
  const fH = new Int8Array(nH), fS = new Int8Array(nSG); // 1 = called for the Republican's opponent, -1 = for the Republican
  for (const [key, s] of forced) { const [t, j] = key.split(":"); (t === "H" ? fH : fS)[+j] = s; }
  const won = (f, v) => (f ? f > 0 : v > 0);
  const hSeats = new Float64Array(N), sSeats = new Float64Array(N), gSeats = new Float64Array(N);
  const hWin = new Float64Array(nH), sWin = new Float64Array(nSG);
  let hMaj = 0, sCtl = 0, gMaj = 0;
  for (let i = 0; i < N; i++) {
    const wi = w[i] / sw;
    let hd = 0, sd = D.senate.holdD, si = 0, gd = D.governor.holdD;
    for (let j = 0; j < nH; j++) if (won(fH[j], HM[i * nH + j])) { hWin[j] += wi; if (!H.seats[j].ind) hd++; }
    for (let j = 0; j < nS; j++) if (won(fS[j], SM[i * nSG + j])) { sWin[j] += wi; races[j].ind ? si++ : sd++; }
    for (let j = nS; j < nSG; j++) if (won(fS[j], SM[i * nSG + j])) { sWin[j] += wi; gd++; }
    hSeats[i] = hd; sSeats[i] = sd; gSeats[i] = gd;
    hMaj += wi * (hd >= H.need); sCtl += wi * (sd > 100 - sd - si); gMaj += wi * (gd >= D.governor.need);
  }
  const local = new Map();
  for (const c of own) {
    let z = 0, a = 0, b = 0;
    for (let i = 0; i < N; i++) {
      const wi = w[i] * Math.exp(-alpha * c.ow[i]), v = margin(i, c.k);
      z += wi; a += wi * v; b += wi * v * v;
    }
    if (!(z > 0)) continue;
    const m0 = a / z, s0 = Math.sqrt(Math.max(b / z - m0 * m0, 1)), so = Math.hypot(0.6 * countSd(c.f), c.ssd);
    const prec = 1 / (s0 * s0) + 1 / (so * so), pm = (m0 / (s0 * s0) + c.m / (so * so)) / prec, ps = Math.sqrt(1 / prec);
    local.set(c.k.join(":"), { p: Phi(pm / ps), med: pm, lo: pm - 1.2816 * ps, hi: pm + 1.2816 * ps });
  }
  const chamber = (seats, p) => {
    const [p10, p50, p90] = weightedQuantiles(seats, w, sw, [0.1, 0.5, 0.9]);
    let mean = 0;
    const hist = {};
    for (let i = 0; i < N; i++) { const wi = w[i] / sw; mean += wi * seats[i]; hist[seats[i]] = (hist[seats[i]] || 0) + wi; }
    for (const s in hist) hist[s] = +hist[s].toFixed(5);
    return { p_control: +p.toFixed(4), mean: +mean.toFixed(1), p10, p50, p90, hist };
  };
  const rows = [], byRace = new Map(obs.map((o) => [o.race, o]));
  for (const [key, k] of Object.entries(idx)) {
    const [t, j] = k, f = (t === "H" ? fH : fS)[j];
    if (f || (t === "H" && H.seats[j].fix)) continue; // called, or never contested
    const vals = Float64Array.from({ length: N }, (_, i) => margin(i, k));
    const [lo, med, hi] = weightedQuantiles(vals, w, sw, [0.1, 0.5, 0.9]);
    const r = local.get(k.join(":")) || { p: (t === "H" ? hWin : sWin)[j], med, lo, hi };
    const o = byRace.get(key), count = o && o.pct > 0 ? { margin: +(o.d - o.r).toFixed(2), pct: o.pct } : null;
    rows.push({ race: key, p: +r.p.toFixed(4), med: +r.med.toFixed(1), lo: +r.lo.toFixed(1), hi: +r.hi.toFixed(1), count });
  }
  const [n10, n90] = weightedQuantiles(NAT, w, sw, [0.1, 0.9]);
  let nat = 0;
  for (let i = 0; i < N; i++) nat += (w[i] / sw) * NAT[i];
  return {
    ess: Math.round(ess), alpha: +alpha.toFixed(4),
    national: { mean: +(D.E0 + nat).toFixed(2), p10: +(D.E0 + n10).toFixed(2), p90: +(D.E0 + n90).toFixed(2) },
    house: chamber(hSeats, hMaj), senate: chamber(sSeats, sCtl), governor: chamber(gSeats, gMaj), races: rows,
  };
}

const sig = crypto.createHash("sha256").update(JSON.stringify(OBS)).digest("hex").slice(0, 16);
let old = null;
try { old = JSON.parse(fs.readFileSync(OUT, "utf8")); } catch (e) { /* first run */ }
if (old && old.obs_hash === sig) {
  console.log(`${path.relative(ROOT, OUT)}: unchanged (same ${OBS.length} observations)`);
  process.exit(0);
}
const now = run(OBS);
const at = new Date().toISOString().slice(0, 16) + "Z";
const calls = OBS.filter((o) => o.call).length, counts = OBS.filter((o) => o.pct > 0).length;
fs.writeFileSync(OUT, JSON.stringify({ generated: at, obs_hash: sig, n_sims: N, calls, counts, ...now }));
let hist = [];
try { hist = JSON.parse(fs.readFileSync(HIST, "utf8")); } catch (e) { /* first run */ }
const row = { at, calls, counts, ess: now.ess, national: now.national.mean };
for (const c of ["house", "senate", "governor"]) {
  const x = now[c];
  Object.assign(row, { [c + "_p"]: x.p_control, [c + "_p10"]: x.p10, [c + "_p50"]: x.p50, [c + "_p90"]: x.p90 });
}
hist.push(row);
fs.writeFileSync(HIST, JSON.stringify(hist));
const pc = (x) => (100 * x).toFixed(1) + "%";
console.log(`${OBS.length} observations (${calls} calls, ${counts} counts): effective sample ${now.ess}, tempering ${now.alpha}`);
console.log(`national ${now.national.mean} (80% ${now.national.p10} to ${now.national.p90})`);
for (const c of ["house", "senate", "governor"]) {
  const x = now[c];
  console.log(`${c.padEnd(8)} control ${pc(x.p_control)}  D seats ${x.p50} (80% ${x.p10}-${x.p90})`);
}
console.log(`wrote ${path.relative(ROOT, OUT)} (${now.races.length} races out) and ${hist.length} history rows`);

(function () {
  const $ = (id) => document.getElementById(id);
  const state = {
    demo: false,
    dataPath: "",
    dataKey: "",
    token: "",
    chartRange: "1D",
    account: null,
    home: null,
    mint: null,
    enc: false,
  };

  function readHash() {
    const h = (location.hash || "").replace(/^#/, "");
    const out = {};
    h.split("&").forEach((part) => {
      const i = part.indexOf("=");
      if (i > 0) out[part.slice(0, i)] = decodeURIComponent(part.slice(i + 1));
    });
    // #t= APP_TOKEN (API / future); #d= AES data key (Plan B / Pages) — never put either in the URL bar long-term
    if (out.t) { try { localStorage.setItem("APP_TOKEN", out.t); } catch (e) {} }
    if (out.d) {
      try {
        localStorage.setItem("DATA_KEY", out.d);
        // legacy: older links used #d= as DATA_PATH; keep both until migrated
        localStorage.setItem("DATA_PATH", out.d);
      } catch (e) {}
    }
    if (out.t || out.d) history.replaceState(null, "", location.pathname + location.search);
    try { state.token = localStorage.getItem("APP_TOKEN") || ""; } catch (e) {}
    try { state.dataKey = localStorage.getItem("DATA_KEY") || ""; } catch (e) {}
    try { state.dataPath = localStorage.getItem("DATA_PATH") || ""; } catch (e) {}
    const q = new URLSearchParams(location.search);
    state.demo = q.get("demo") === "1";
  }

  // Keep the public FOMO referral on destination links. Leave a URL unchanged when r=savipww is already present.
  function fomoUrl(url) {
    if (!url || url === "#") return url || "#";
    let parsed;
    try { parsed = new URL(url); } catch (e) { return url; }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return url;
    if (parsed.searchParams.get("r") === "savipww") return url;
    parsed.searchParams.set("r", "savipww");
    return parsed.toString();
  }

  function dataBase() {
    if (state.demo) { state.enc = false; return "/demo-data"; }
    // GitHub Pages / encrypted static: <siteRoot>/data/*.json envelopes
    if (state.dataKey) {
      state.enc = true;
      let root = location.pathname;
      if (root.endsWith(".html")) root = root.slice(0, root.lastIndexOf("/") + 1);
      if (!root.endsWith("/")) root += "/";
      return root + "data";
    }
    // Legacy local plan B plaintext under /d/{DATA_PATH}/
    if (state.dataPath) { state.enc = false; return "/d/" + encodeURIComponent(state.dataPath); }
    return null;
  }

  function b64urlToBytes(s) {
    const pad = "=".repeat((4 - (s.length % 4)) % 4);
    const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function deriveKeyBytes(fragment) {
    // Prefer raw 32-byte base64url key; else SHA-256(legacy DATA_PATH string)
    try {
      const raw = b64urlToBytes(fragment);
      if (raw.length === 32) return raw;
    } catch (e) {}
    const enc = new TextEncoder();
    const dig = await crypto.subtle.digest("SHA-256", enc.encode(fragment));
    return new Uint8Array(dig);
  }

  async function decryptEnvelope(envelope, keyBytes) {
    if (!envelope || envelope.alg !== "A256GCM" || envelope.v !== 1) {
      throw new Error("not ciphertext");
    }
    const iv = b64urlToBytes(envelope.iv);
    const ct = b64urlToBytes(envelope.ct);
    const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["decrypt"]);
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
    return JSON.parse(new TextDecoder().decode(pt));
  }

  async function getJSON(path) {
    const base = dataBase();
    if (!base) throw new Error("missing data key — open private link (#t=…&d=…)");
    const url = base.replace(/\/$/, "") + "/" + path;
    const res = await fetch(url, { cache: "no-store" });
    if (res.status === 404) throw new Error("not found");
    if (!res.ok) throw new Error("load " + res.status);
    const body = await res.json();
    if (body && body.alg === "A256GCM" && body.ct) {
      if (!state.dataKey) throw new Error("encrypted data — open private link");
      const keyBytes = await deriveKeyBytes(state.dataKey);
      return decryptEnvelope(body, keyBytes);
    }
    return body;
  }

  /* Hand-drawn wobble line chart — skip null/zero, pad scale, map marks to time */
  function drawLineChart(canvas, points, marks, opts) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 390;
    const h = canvas.clientHeight || 180;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#16161d";
    roundRect(ctx, 0, 0, w, h, 12); ctx.fill();
    const clean = (points || []).filter((p) => {
      const v = Number(p && p.v);
      return Number.isFinite(v) && v !== 0;
    });
    if (clean.length < 2) {
      ctx.fillStyle = "#8e8e93"; ctx.font = "13px -apple-system,sans-serif";
      ctx.fillText("No points yet", 16, h / 2); return;
    }
    const vals = clean.map((p) => +p.v);
    let min = Math.min(...vals), max = Math.max(...vals);
    const span = max - min || Math.abs(max) * 0.02 || 1;
    const padY = span * 0.12;
    min -= padY; max += padY;
    const pad = 16;
    const xAt = (i) => pad + (i / (clean.length - 1)) * (w - pad * 2);
    const yAt = (v) => pad + (1 - (v - min) / (max - min)) * (h - pad * 2);
    ctx.strokeStyle = "#2c2c33"; ctx.lineWidth = 1;
    for (let g = 0; g < 4; g++) {
      const y = pad + (g / 3) * (h - pad * 2);
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
    }
    ctx.strokeStyle = (opts && opts.color) || "#64d2ff";
    ctx.lineWidth = 2.2; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    clean.forEach((p, i) => {
      const x = xAt(i) + Math.sin(i * 1.7) * 0.6;
      const y = yAt(+p.v) + Math.cos(i * 1.3) * 0.6;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    // soft fill under line (separate path so stroke isn't polluted)
    ctx.beginPath();
    clean.forEach((p, i) => {
      const x = xAt(i);
      const y = yAt(+p.v);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.lineTo(xAt(clean.length - 1), h - pad);
    ctx.lineTo(xAt(0), h - pad);
    ctx.closePath();
    ctx.fillStyle = "rgba(100,210,255,0.08)";
    ctx.fill();
    // marks on nearest point by time; stagger when several share a slot
    const slotCounts = {};
    (marks || []).forEach((m) => {
      if (!m || !m.t) return;
      let best = 0, bestD = Infinity;
      const mt = Date.parse(m.t);
      clean.forEach((p, i) => {
        const d = Math.abs(Date.parse(p.t) - mt);
        if (d < bestD) { bestD = d; best = i; }
      });
      const n = slotCounts[best] || 0;
      slotCounts[best] = n + 1;
      const x = xAt(best) + (n - 1) * 7;
      const y = yAt(+clean[best].v) - 10 - n * 2;
      ctx.fillStyle = m.kind === "bought" ? "#30d158"
        : (m.kind === "rejected" || m.kind === "stopped") ? "#ff453a" : "#0a84ff";
      ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fill();
    });
    if (opts && opts.stopLine != null && Number.isFinite(+opts.stopLine)) {
      const y = yAt(+opts.stopLine);
      ctx.strokeStyle = "#ff9f0a"; ctx.setLineDash([6, 5]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#ff9f0a"; ctx.font = "11px -apple-system,sans-serif";
      ctx.fillText("Risk trailing stop", pad + 4, y - 4);
    }
  }

  /* Real OHLC candles + volume strip + stop/trail lines */
  function drawCandles(canvas, candles, lines) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 390;
    const h = canvas.clientHeight || 220;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#16161d"; roundRect(ctx, 0, 0, w, h, 12); ctx.fill();
    if (!candles || !candles.length) {
      ctx.fillStyle = "#8e8e93"; ctx.font = "13px -apple-system,sans-serif";
      ctx.fillText("No candles", 16, h / 2); return;
    }
    const stopPrice = lines && lines.stop != null ? +lines.stop : null;
    const trailPrice = lines && lines.trail != null ? +lines.trail : null;
    const pad = 12;
    const volH = Math.max(28, Math.floor(h * 0.18));
    const gap = 6;
    const chartTop = pad;
    const chartH = h - pad - volH - gap - 4;
    const volTop = chartTop + chartH + gap;

    let min = Infinity, max = -Infinity;
    candles.forEach((c) => {
      const lo = Number(c.l != null ? c.l : c.c);
      const hi = Number(c.h != null ? c.h : c.c);
      const o = Number(c.o != null ? c.o : c.c);
      const cl = Number(c.c);
      [lo, hi, o, cl].forEach((v) => {
        if (Number.isFinite(v) && v > 0) { if (v < min) min = v; if (v > max) max = v; }
      });
    });
    if (Number.isFinite(stopPrice) && stopPrice > 0) { min = Math.min(min, stopPrice); max = Math.max(max, stopPrice); }
    if (Number.isFinite(trailPrice) && trailPrice > 0) { min = Math.min(min, trailPrice); max = Math.max(max, trailPrice); }
    if (!Number.isFinite(min) || !Number.isFinite(max)) { min = 0; max = 1; }
    const span = max - min || Math.abs(max) * 0.02 || 1e-9;
    min -= span * 0.08; max += span * 0.08;

    const yAt = (v) => chartTop + (1 - (v - min) / (max - min)) * chartH;
    const slot = (w - pad * 2) / candles.length;
    const bw = Math.max(2, slot * 0.62);
    const maxVol = Math.max(1, ...candles.map((c) => +c.volume_m5 || 0));

    // price grid
    ctx.strokeStyle = "#2c2c33"; ctx.lineWidth = 1;
    for (let g = 0; g < 4; g++) {
      const y = chartTop + (g / 3) * chartH;
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
    }

    candles.forEach((c, i) => {
      const o = Number(c.o != null ? c.o : c.c);
      const hi = Number(c.h != null ? c.h : Math.max(o, +c.c));
      const lo = Number(c.l != null ? c.l : Math.min(o, +c.c));
      const cl = Number(c.c);
      const up = cl >= o;
      const color = up ? "#30d158" : "#ff453a";
      const cx = pad + i * slot + slot / 2;
      // wick
      ctx.strokeStyle = color; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(cx, yAt(hi)); ctx.lineTo(cx, yAt(lo)); ctx.stroke();
      // body
      const y1 = yAt(Math.max(o, cl));
      const y2 = yAt(Math.min(o, cl));
      const bodyH = Math.max(1.5, y2 - y1);
      ctx.fillStyle = color;
      ctx.fillRect(cx - bw / 2, y1, bw, bodyH);
      // volume
      const vol = +c.volume_m5 || 0;
      const vh = (vol / maxVol) * (volH - 2);
      ctx.globalAlpha = 0.55;
      ctx.fillStyle = up ? "#30d158" : "#ff453a";
      ctx.fillRect(cx - bw / 2, volTop + (volH - vh), bw, Math.max(1, vh));
      ctx.globalAlpha = 1;
    });

    // volume baseline
    ctx.strokeStyle = "#2c2c33"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad, volTop); ctx.lineTo(w - pad, volTop); ctx.stroke();

    function dashLine(price, color, label) {
      if (!Number.isFinite(price) || price <= 0) return;
      const y = yAt(price);
      if (y < chartTop - 2 || y > chartTop + chartH + 2) return;
      ctx.strokeStyle = color; ctx.setLineDash([6, 5]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.font = "11px -apple-system,sans-serif";
      ctx.fillText(label, pad + 4, Math.max(chartTop + 11, y - 4));
    }
    dashLine(stopPrice, "#ff9f0a", "Stop");
    dashLine(trailPrice, "#64d2ff", "Trail");
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function money(n, opts) {
    n = Number(n);
    if (!Number.isFinite(n)) return "—";
    const neg = n < 0;
    const a = Math.abs(n);
    let core;
    if (opts && opts.compact) {
      if (a >= 1e9) core = (a / 1e9).toFixed(a >= 1e10 ? 1 : 2).replace(/\.0$/, "") + "B";
      else if (a >= 1e6) core = (a / 1e6).toFixed(a >= 1e7 ? 1 : 2).replace(/\.0$/, "") + "M";
      else if (a >= 10000) core = (a / 1e3).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "") + "K";
      else core = a.toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 0 : 2 });
    } else if (a >= 1000) {
      core = a.toLocaleString("en-US", { maximumFractionDigits: 0 });
    } else if (a >= 1) {
      core = a.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } else {
      core = a.toPrecision(4).replace(/0+$/, "").replace(/\.$/, "");
    }
    return (neg ? "-$" : "$") + core;
  }

  function showTab(name) {
    ["home", "agents", "account"].forEach((t) => {
      $("screen-" + t).classList.toggle("active", t === name);
    });
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    closePush();
  }

  function closePush() {
    $("screen-auditor").style.display = "none";
    $("screen-coin").style.display = "none";
  }

  async function loadHome() {
    try {
      const [st, chart, act] = await Promise.all([
        getJSON("state.json"),
        getJSON("chart_" + state.chartRange + ".json").catch(() => getJSON("chart.json")),
        getJSON("activity.json"),
      ]);
      state.home = st;
      $("home-balance").textContent = money(st.balance);
      const tod = Number(st.today_pct || 0);
      $("home-today").textContent = "today " + (tod >= 0 ? "+" : "") + tod.toFixed(1) + "%";
      $("home-today").className = tod >= 0 ? "up" : "down";
      $("home-mult").textContent = "×" + Number(st.multiplier || 1).toFixed(2);
      $("mode-pill").textContent = st.mode || "PAPER";
      $("mode-pill").className = "pill " + ((st.mode || "PAPER") === "LIVE" ? "live" : "paper");
      $("home-agents").innerHTML = (st.agents || []).map((a) =>
        `<div class="agent"><div class="name">${a.name}</div><div class="label">${a.label || a.status}</div></div>`
      ).join("");
      $("home-activity").innerHTML = (act.rows || []).slice(0, 12).map((r) =>
        `<div class="row" data-mint="${r.mint || ""}"><div class="dot ${r.kind}"></div>
         <div><div class="title">${esc(r.title || r.kind)}</div><div class="sub">${esc(r.subtitle || "")}</div></div>
         <div class="val">${esc(r.value || "")}</div></div>`
      ).join("") || `<div class="empty">No activity yet</div>`;
      drawLineChart($("home-chart"), chart.points || [], chart.marks || []);
      $("home-activity").onclick = (e) => {
        const row = e.target.closest(".row");
        if (!row || !row.dataset.mint) return;
        openCoin(row.dataset.mint);
      };
    } catch (e) {
      $("home-activity").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  }

  async function loadAgents() {
    const st = state.home || await getJSON("state.json");
    const cards = [];
    for (const a of st.agents || []) {
      let decisions = { decisions: [] };
      try { decisions = await getJSON("agents_" + a.name + "_decisions.json"); } catch (e) {}
      const last = (decisions.decisions || [])[0];
      cards.push(`<div class="card agent-card" data-agent="${a.name}">
        <div class="name" style="font-size:18px;text-transform:capitalize">${a.name}</div>
        <div class="label">${esc(a.label || a.status)}</div>
        <div class="note">${last ? esc(last.title || last.kind) + " · " + esc(last.subtitle || "") : "No decisions yet"}</div>
        ${a.name === "auditor" ? `<button class="bigbtn" style="margin-top:10px;padding:12px;font-size:15px" data-open-auditor>Open Auditor</button>` : ""}
      </div>`);
    }
    $("agents-list").innerHTML = cards.join("") || `<div class="empty">No agents</div>`;
    $("agents-list").onclick = (e) => {
      if (e.target.matches("[data-open-auditor]")) {
        const mint = state.mint || (window.__DEMO_MINT || "");
        if (mint) openAuditor(mint);
      }
    };
  }

  async function loadAccount() {
    try {
      const acct = await getJSON("account.json");
      state.account = acct;
      $("stack-field").value = acct.stack_usd || 200;
      document.querySelectorAll("#mode-toggle button").forEach((b) => {
        b.classList.toggle("on", b.dataset.mode === (acct.mode || "PAPER"));
      });
      $("profile-link").href = fomoUrl(acct.profile_url || "#");
      $("tg-status").textContent = (acct.telegram && acct.telegram.connected)
        ? "Connected · " + (acct.telegram.push || "telegram")
        : "Not connected";
      const grouped = acct.settings_grouped || {};
      let html = "";
      Object.keys(grouped).forEach((group) => {
        const entries = Object.entries(grouped[group] || {});
        if (!entries.length) return;
        html += `<div class="note" style="margin-top:12px;text-transform:uppercase">${esc(group)}</div>`;
        entries.forEach(([k, v]) => {
          const num = !isNaN(parseFloat(v)) && isFinite(v);
          html += `<div class="setting"><label><span>${esc(k)}</span><span>${esc(v)}</span></label>`;
          if (num) html += `<input type="range" min="0" max="${Math.max(100, parseFloat(v) * 2)}" step="any" value="${esc(v)}" disabled />`;
          html += `</div>`;
        });
      });
      $("settings-list").innerHTML = html || `<div class="empty">No settings</div>`;
      $("invite-btn").onclick = async () => {
        const url = fomoUrl(acct.signup_url);
        if (navigator.share) {
          try { await navigator.share({ title: "FOMO", url }); return; } catch (e) {}
        }
        try { await navigator.clipboard.writeText(url); $("invite-btn").querySelector(".muted").textContent = "Link copied"; }
        catch (e) { alert(url); }
      };
    } catch (e) {
      $("settings-list").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  }

  async function openAuditor(mint) {
    state.mint = mint;
    $("screen-auditor").style.display = "block";
    $("auditor-title").textContent = "Auditor";
    $("auditor-status").textContent = "taking apart…";
    $("auditor-layers").innerHTML = "";
    try {
      const audit = await getJSON("audit_" + mint + ".json");
      const layers = audit.layers || [];
      const allPass = layers.length >= 8 && layers.every((l) => l.pass);
      $("auditor-shield").textContent = (audit.layersChecked || layers.length) + "/8";
      $("auditor-status").textContent = allPass
        ? ("Cleared · 8/8 parts — Sent to Executor")
        : ("Rejected · " + ((layers.find((l) => l.pass === false) || {}).value || "failed"));
      $("auditor-fomo").href = (await safeCoinUrl(mint));
      // animate layers
      for (let i = 0; i < layers.length; i++) {
        await sleep(120);
        const l = layers[i];
        const el = document.createElement("div");
        el.className = "layer beam " + (l.pass ? "pass" : "fail");
        el.innerHTML = `<div class="n">Layer ${l.n}</div><div class="nm">${esc(l.name)}</div><div class="val">${esc(l.value || "")} ${l.pass ? "✓" : "✕"}</div>`;
        $("auditor-layers").appendChild(el);
      }
    } catch (e) {
      $("auditor-status").innerHTML = `<span class="error">${esc(e.message)}</span>`;
    }
  }

  async function openCoin(mint) {
    state.mint = mint;
    $("screen-coin").style.display = "block";
    try {
      const [coin, audit] = await Promise.all([
        getJSON("coin_" + mint + ".json"),
        getJSON("audit_" + mint + ".json").catch(() => ({ layers: [] })),
      ]);
      const c = coin.coin || {};
      $("coin-name").textContent = c.symbol || c.name || mint.slice(0, 6);
      $("coin-meta").textContent = `${c.name || "Token"} · Solana · ${ageLabel(c.migrated_at)}`;
      const checked = (audit.layers || []).length;
      $("coin-shield").textContent = (checked || 0) + "/8";
      $("coin-price").textContent = coin.price != null ? Number(coin.price).toPrecision(4) : "—";
      const pos = coin.position;
      if (pos && pos.entry_price && coin.price) {
        const pct = (coin.price / pos.entry_price - 1) * 100;
        $("coin-pnl").innerHTML = `<span class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "▲" : "▼"} ${pct.toFixed(1)}% since entry</span>`;
      } else $("coin-pnl").textContent = "No open position";
      const st = coin.stats || {};
      $("coin-stats").innerHTML = [
        ["Market cap", st.market_cap != null ? money(st.market_cap, { compact: true }) : "n/a"],
        ["Liquidity", st.liquidity_usd != null ? money(st.liquidity_usd, { compact: true }) : "n/a"],
        ["24h volume", st.volume_h24 != null ? money(st.volume_h24, { compact: true }) : "n/a"],
        ["Holders", st.holders != null ? Number(st.holders).toLocaleString("en-US") : "n/a"],
      ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
      const stop = (coin.exit_lines && coin.exit_lines.stop_price != null)
        ? +coin.exit_lines.stop_price
        : (pos && pos.stop_price != null ? +pos.stop_price : null);
      const trailPrice = (coin.exit_lines && coin.exit_lines.trail_price != null)
        ? +coin.exit_lines.trail_price : null;
      drawCandles($("coin-chart"), coin.candles || [], { stop, trail: trailPrice });
      if (pos) {
        const lines = coin.exit_lines || {};
        $("coin-position").innerHTML = `<h2>Your position · ${money(pos.size_usd)} in · Executor</h2>
          <div class="note">Stop ${Number(pos.stop_price).toPrecision(4)} · trail ${lines.trail_pct || 12}% · timeout ${lines.timeout_h || 3}h</div>`;
        $("coin-fomo").textContent = "Sell on FOMO";
        $("coin-fomo").classList.add("sell");
      } else {
        $("coin-position").innerHTML = `<h2>No position</h2><div class="note">Paper desk sizes via Executor.</div>`;
        $("coin-fomo").textContent = "Buy on FOMO";
        $("coin-fomo").classList.remove("sell");
      }
      $("coin-fomo").href = fomoUrl(coin.coin_url || "#");
    } catch (e) {
      $("coin-meta").innerHTML = `<span class="error">${esc(e.message)}</span>`;
    }
  }

  async function safeCoinUrl(mint) {
    try { const c = await getJSON("coin_" + mint + ".json"); return fomoUrl(c.coin_url || "#"); }
    catch (e) { return "#"; }
  }

  function ageLabel(iso) {
    if (!iso) return "—";
    const ms = Date.now() - Date.parse(iso);
    const h = Math.max(0, Math.round(ms / 3600000));
    return h + "h old";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
  }
  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async function boot() {
    readHash();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("./sw.js", { scope: "./" }).catch(() => {});
    if (state.demo) {
      try {
        const mint = (await fetch("/demo-data/demo_mint.txt").then((r) => r.text())).trim();
        window.__DEMO_MINT = mint;
        state.mint = mint;
      } catch (e) {}
    }
    document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
      showTab(b.dataset.tab);
      if (b.dataset.tab === "home") loadHome();
      if (b.dataset.tab === "agents") loadAgents();
      if (b.dataset.tab === "account") loadAccount();
    });
    $("chart-ranges").onclick = (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      state.chartRange = btn.dataset.range;
      [...$("chart-ranges").children].forEach((b) => b.classList.toggle("on", b === btn));
      loadHome();
    };
    $("auditor-back").onclick = closePush;
    $("coin-back").onclick = closePush;
    if (!state.demo && !state.dataKey && !state.dataPath) {
      $("home-activity").innerHTML = `<div class="error">Open the private link from Telegram / Grok (URL ends with #t=…&d=…).</div>`;
      return;
    }
    await loadHome();
  }

  boot();
  // expose for screenshots
  window.__desk = { openAuditor, openCoin, showTab, loadHome, loadAgents, loadAccount, fomoUrl, state };
})();

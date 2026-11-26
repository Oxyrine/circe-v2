/* demo/assistant.js: analyst context panel, deterministic re-ranking client, grounded chat.
 *
 * The server decides (POST /api/prioritise) and explains (POST /api/assistant).
 * Everything server-sourced is written with textContent, never innerHTML.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "circe_analyst_context";
  var SECTORS = ["distribution", "manufacturing", "trading", "services"];
  var CRORE = 1e7;
  var DEFAULTS = { risk_threshold: 0.70, exposure_limit_inr: null, sectors: [], daily_capacity: 10 };
  var BUCKET_LABEL = {
    escalate: "ESCALATE", review_today: "REVIEW TODAY", backlog: "BACKLOG", other_desk: "OTHER DESK"
  };
  var SUGGESTIONS = [
    "Why is this flagged?",
    "Which signals abstained?",
    "What does the closure mean?",
    "What should I review first?"
  ];

  var ctx = load();
  var ranking = null;   // { ring_id: {rank, bucket, reasons} } or null when the server is unreachable
  var timer = null;
  var statusEl = null;

  function load() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (raw && typeof raw === "object") return Object.assign({}, DEFAULTS, raw);
    } catch (e) {}
    return Object.assign({}, DEFAULTS);
  }
  function save() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(ctx)); } catch (e) {} }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // --- screen-reader announcements -------------------------------------------
  function liveRegion() {
    var r = document.getElementById("circe-live");
    if (!r) {
      r = el("div", "sr-only");
      r.id = "circe-live";
      r.setAttribute("role", "status");
      r.setAttribute("aria-live", "polite");
      document.body.appendChild(r);
    }
    return r;
  }
  function announce(msg) {
    var r = liveRegion();
    r.textContent = "";
    setTimeout(function () { r.textContent = msg; }, 50); // re-set so repeats are announced
  }

  // --- server calls ------------------------------------------------------------
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; });
    });
  }

  function refresh() {
    return post("/api/prioritise", { context: ctx }).then(function (res) {
      if (!res.ok) throw new Error(res.data && res.data.error || "ranking failed");
      var map = {};
      res.data.ranked.forEach(function (x) { map[x.ring_id] = x; });
      ranking = map;
      window.circeRender && window.circeRender();
      var d = res.data, esc = 0, today = 0;
      d.ranked.forEach(function (x) { if (x.bucket === "escalate") esc++; else if (x.bucket === "review_today") today++; });
      var msg = d.flagged_count + " rings flagged at your threshold: " + esc + " to escalate, " + today + " to review today.";
      setStatus(msg, false);
      announce("Queue updated. " + msg);
    }).catch(function () {
      ranking = null;
      window.circeRender && window.circeRender();
      setStatus("Context ranking needs the server; showing the default exposure order.", true);
    });
  }

  function scheduleRefresh() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }

  function setStatus(msg, warn) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = "ctx-status" + (warn ? " warn" : "");
  }

  // --- context panel -------------------------------------------------------------
  function field(labelText, input, id, hint) {
    var wrap = el("div", "ctx-field");
    var lab = el("label", "ctx-label", labelText);
    lab.setAttribute("for", id);
    wrap.appendChild(lab);
    wrap.appendChild(input);
    if (hint) {
      var h = el("span", "ctx-hint", hint);
      h.id = id + "-hint";
      input.setAttribute("aria-describedby", h.id);
      wrap.appendChild(h);
    }
    return wrap;
  }

  function buildPanel() {
    var sec = el("section", "ctx-panel");
    sec.setAttribute("aria-labelledby", "ctx-title");
    var head = el("div", "ctx-head");
    var title = el("h2", "ctx-title", "My review context");
    title.id = "ctx-title";
    head.appendChild(title);
    head.appendChild(el("p", "ctx-sub", "Tell the assistant how you work. The queue re-ranks by your rules and every placement shows its reason."));
    sec.appendChild(head);

    var grid = el("div", "ctx-grid");

    var thr = el("input", "ctx-range");
    thr.type = "range"; thr.id = "ctx-threshold"; thr.min = "0.5"; thr.max = "0.9"; thr.step = "0.01";
    thr.value = String(ctx.risk_threshold);
    var thrOut = el("output", "ctx-out mono", Number(ctx.risk_threshold).toFixed(2));
    thrOut.setAttribute("for", "ctx-threshold");
    thr.addEventListener("input", function () {
      ctx.risk_threshold = parseFloat(thr.value);
      thrOut.textContent = ctx.risk_threshold.toFixed(2);
      save(); scheduleRefresh();
    });
    var thrWrap = field("Risk threshold", thr, "ctx-threshold", "Rings scoring at or above this are flagged for you.");
    thrWrap.insertBefore(thrOut, thrWrap.querySelector(".ctx-hint"));
    grid.appendChild(thrWrap);

    var lim = el("input", "control-input ctx-num");
    lim.type = "number"; lim.id = "ctx-limit"; lim.min = "0"; lim.step = "0.5"; lim.placeholder = "No limit";
    lim.value = ctx.exposure_limit_inr ? String(ctx.exposure_limit_inr / CRORE) : "";
    lim.addEventListener("change", function () {
      var v = parseFloat(lim.value);
      ctx.exposure_limit_inr = (isFinite(v) && v > 0) ? v * CRORE : null;
      save(); scheduleRefresh();
    });
    grid.appendChild(field("Escalate at exposure of (₹ crore)", lim, "ctx-limit", "Flagged rings at or above this go to the top as ESCALATE."));

    var cap = el("input", "control-input ctx-num");
    cap.type = "number"; cap.id = "ctx-capacity"; cap.min = "1"; cap.max = "50"; cap.step = "1";
    cap.value = String(ctx.daily_capacity);
    cap.addEventListener("change", function () {
      var v = parseInt(cap.value, 10);
      ctx.daily_capacity = isFinite(v) ? Math.min(50, Math.max(1, v)) : DEFAULTS.daily_capacity;
      cap.value = String(ctx.daily_capacity);
      save(); scheduleRefresh();
    });
    grid.appendChild(field("Rings I can review today", cap, "ctx-capacity", "The rest go to BACKLOG."));

    var fs = el("fieldset", "ctx-fieldset");
    fs.appendChild(el("legend", "ctx-label", "Sectors I cover"));
    SECTORS.forEach(function (s) {
      var id = "ctx-sector-" + s;
      var lab = el("label", "ctx-check");
      var cb = el("input"); cb.type = "checkbox"; cb.id = id; cb.value = s;
      cb.checked = ctx.sectors.indexOf(s) !== -1;
      cb.addEventListener("change", function () {
        ctx.sectors = SECTORS.filter(function (x) { return document.getElementById("ctx-sector-" + x).checked; });
        save(); scheduleRefresh();
      });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" " + s.charAt(0).toUpperCase() + s.slice(1)));
      fs.appendChild(lab);
    });
    fs.appendChild(el("span", "ctx-hint", "None ticked means all sectors."));
    grid.appendChild(fs);
    sec.appendChild(grid);

    var foot = el("div", "ctx-foot");
    statusEl = el("p", "ctx-status", "");
    statusEl.setAttribute("role", "status");
    foot.appendChild(statusEl);
    var reset = el("button", "btn", "RESET CONTEXT");
    reset.type = "button";
    reset.addEventListener("click", function () {
      ctx = Object.assign({}, DEFAULTS);
      save();
      var old = document.querySelector(".ctx-panel");
      if (old) old.parentNode.replaceChild(buildPanel(), old);
      refresh();
    });
    foot.appendChild(reset);
    sec.appendChild(foot);
    return sec;
  }

  // --- per-card decoration: queue placement + chat ------------------------------------
  function decorateCard(body, ring) {
    var placed = ranking && ranking[ring.ring_id];
    if (placed) {
      var box = el("div", "ctx-placement");
      var badge = el("span", "ctx-badge " + placed.bucket, BUCKET_LABEL[placed.bucket] || placed.bucket);
      box.appendChild(badge);
      var ul = el("ul", "ctx-reasons");
      placed.reasons.forEach(function (r) { ul.appendChild(el("li", null, r)); });
      box.appendChild(ul);
      body.insertBefore(box, body.firstChild);
    }
    body.appendChild(buildChat(ring));
  }

  function buildChat(ring) {
    var id = "chat-" + ring.ring_id;
    var sec = el("section", "chat");
    sec.setAttribute("aria-labelledby", id + "-title");
    var title = el("h3", "chat-title", "Ask about this ring");
    title.id = id + "-title";
    sec.appendChild(title);
    sec.appendChild(el("p", "chat-note", "Answers come only from this ring's scored evidence. The assistant explains; it never changes a score or a decision."));

    var chips = el("div", "chat-chips");
    sec.appendChild(chips);

    var log = el("div", "chat-log");
    log.id = id + "-log";
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-label", "Conversation about ring " + ring.ring_id);
    sec.appendChild(log);

    var form = el("form", "chat-form");
    var input = el("input", "control-input chat-input");
    input.type = "text"; input.id = id + "-q"; input.maxLength = 500;
    input.setAttribute("autocomplete", "off");
    var lab = el("label", "sr-only", "Your question about ring " + ring.ring_id);
    lab.setAttribute("for", input.id);
    var send = el("button", "btn", "ASK");
    send.type = "submit";
    form.appendChild(lab); form.appendChild(input); form.appendChild(send);
    sec.appendChild(form);

    var asked = [];
    function add(cls, who, text) {
      var m = el("div", "chat-msg " + cls);
      m.appendChild(el("span", "chat-who", who));
      var t = el("span", "chat-text", text);
      m.appendChild(t);
      log.appendChild(m);
      log.scrollTop = log.scrollHeight;
      return m;
    }

    function ask(q) {
      q = (q || "").trim();
      if (!q) return;
      add("user", "You", q);
      input.value = "";
      send.disabled = true;
      var pending = add("bot", "Assistant", "Thinking…");
      post("/api/assistant", { ring_id: ring.ring_id, question: q, context: ctx, history: asked.slice(-3) })
        .then(function (res) {
          var t = pending.querySelector(".chat-text");
          if (res.ok) {
            asked.push(q);
            t.textContent = res.data.answer;
            pending.appendChild(el("span", "chat-src", res.data.source === "llm"
              ? "AI answer, grounded in this ring's evidence"
              : "Deterministic answer from this ring's evidence"));
          } else if (res.status === 400 && res.data.error === "question_rejected") {
            pending.classList.add("warn");
            t.textContent = res.data.reason === "too_long"
              ? "That question is too long. Keep it under 500 characters."
              : "I can't act on that. Ask about this ring's evidence, signals or your queue.";
          } else if (res.status === 404) {
            t.textContent = "This ring isn't in the server's scored set (it may come from a live rescore), so I can't answer about it.";
          } else if (res.status === 429) {
            t.textContent = "Too many questions in a short time. Try again in a minute.";
          } else {
            t.textContent = "Something went wrong. Please try again.";
          }
        })
        .catch(function () {
          pending.querySelector(".chat-text").textContent = "The assistant needs the server running. Start it with python server.py.";
        })
        .then(function () { send.disabled = false; input.focus(); });
    }

    SUGGESTIONS.forEach(function (s) {
      var b = el("button", "chat-chip", s);
      b.type = "button";
      b.addEventListener("click", function () { ask(s); });
      chips.appendChild(b);
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); ask(input.value); });
    return sec;
  }

  // --- boot ------------------------------------------------------------------------------
  window.CirceAssist = {
    threshold: function () { return ranking ? ctx.risk_threshold : null; },
    ranking: function () { return ranking; },
    decorateCard: decorateCard,
    announce: announce,
    refresh: refresh
  };

  function boot() {
    var q = document.getElementById("view-queue");
    if (!q) return;
    q.insertBefore(buildPanel(), q.firstChild);
    liveRegion();
    refresh();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

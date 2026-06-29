// history_chart.js — "stock-price" view of decision quality over time.
// Self-contained vanilla-JS SVG chart (no external libraries, works offline).
//
// Mounts into #dq-chart. Controls:
//   • range buttons  1D / 1W / 1M / All  (anchored to the most recent decision)
//   • metric select  Overall (per-decision) + each of the six dimensions (per-session)
//   • refresh button  re-fetches /api/history
//
// "Overall" plots one point per graded decision (quality = 1 − EV-loss). Picking a
// dimension plots that dimension's score once per session (those scores are
// session-level aggregates, not per-decision).

(function () {
  "use strict";

  var mount = document.getElementById("dq-chart");
  if (!mount) return;

  var rangeEl = document.getElementById("dq-range");
  var metricEl = document.getElementById("dq-metric");
  var refreshEl = document.getElementById("dq-refresh");

  var STATE = { data: null, range: "1m", metric: "overall" };
  var SVGNS = "http://www.w3.org/2000/svg";

  var RANGE_MS = {
    "1d": 24 * 3600e3,
    "1w": 7 * 24 * 3600e3,
    "1m": 30 * 24 * 3600e3,
    "all": Infinity,
  };

  function parseT(s) { var d = new Date(s); return isNaN(d) ? null : d.getTime(); }

  function fmtDate(ms) {
    var d = new Date(ms);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function fmtDateTime(ms) {
    var d = new Date(ms);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  // Build the {x,y,meta} series for the current metric, before range filtering.
  function fullSeries() {
    var d = STATE.data;
    if (!d) return [];
    if (STATE.metric === "overall") {
      return (d.decisions || [])
        .filter(function (p) { return p.quality != null && parseT(p.t) != null; })
        .map(function (p) {
          return { x: parseT(p.t), y: p.quality,
                   label: "Decision · " + (p.domain || "") +
                          " · session " + p.session_id };
        });
    }
    var dim = STATE.metric;
    return (d.sessions || [])
      .filter(function (s) { return parseT(s.t) != null && s.scores && s.scores[dim] != null; })
      .map(function (s) {
        return { x: parseT(s.t), y: s.scores[dim],
                 label: s.session_id + " · " + (s.domain || "") +
                        " · " + (s.n_decisions || 0) + " decisions" };
      });
  }

  function rangedSeries() {
    var all = fullSeries();
    if (!all.length) return all;
    var win = RANGE_MS[STATE.range];
    if (win === Infinity) return all;
    var anchor = all[all.length - 1].x;       // most recent point
    var cutoff = anchor - win;
    return all.filter(function (p) { return p.x >= cutoff; });
  }

  function el(tag, attrs, text) {
    var n = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }

  function metricLabel() {
    if (STATE.metric === "overall") return "Overall quality";
    var labels = (STATE.data && STATE.data.labels) || {};
    return labels[STATE.metric] || STATE.metric;
  }

  function render() {
    mount.innerHTML = "";
    var series = rangedSeries();

    var W = Math.max(mount.clientWidth || 640, 320);
    var H = 280;
    var pad = { l: 34, r: 14, t: 16, b: 28 };
    var iw = W - pad.l - pad.r;
    var ih = H - pad.t - pad.b;

    var svg = el("svg", {
      viewBox: "0 0 " + W + " " + H, width: "100%", height: H,
      class: "dq-svg", preserveAspectRatio: "none",
    });

    // Gradient under the line.
    var defs = el("defs");
    var grad = el("linearGradient", { id: "dqfill", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": "var(--accent)", "stop-opacity": ".28" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": "var(--accent)", "stop-opacity": "0" }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    // Y gridlines + labels (0..100).
    [0, 25, 50, 75, 100].forEach(function (v) {
      var y = pad.t + ih * (1 - v / 100);
      svg.appendChild(el("line", {
        x1: pad.l, y1: y, x2: pad.l + iw, y2: y,
        stroke: "var(--line)", "stroke-width": v === 0 ? 1.4 : 1,
        "stroke-dasharray": v === 0 ? "0" : "3 4",
      }));
      svg.appendChild(el("text", {
        x: pad.l - 6, y: y + 4, "text-anchor": "end",
        class: "dq-axis",
      }, v));
    });

    if (!series.length) {
      svg.appendChild(el("text", {
        x: W / 2, y: H / 2, "text-anchor": "middle", class: "dq-empty",
      }, "No data in this window."));
      mount.appendChild(svg);
      renderMeta(series, null);
      return;
    }

    // X scale.
    var xs = series.map(function (p) { return p.x; });
    var xmin = Math.min.apply(null, xs);
    var xmax = Math.max.apply(null, xs);
    var span = xmax - xmin || 1;
    function px(x) { return pad.l + iw * (x - xmin) / span; }
    function py(y) { return pad.t + ih * (1 - Math.max(0, Math.min(100, y)) / 100); }

    // X axis labels (start / mid / end).
    [xmin, xmin + span / 2, xmax].forEach(function (x, i) {
      svg.appendChild(el("text", {
        x: i === 0 ? pad.l : (i === 2 ? pad.l + iw : pad.l + iw / 2),
        y: H - 8,
        "text-anchor": i === 0 ? "start" : (i === 2 ? "end" : "middle"),
        class: "dq-axis",
      }, fmtDate(x)));
    });

    // Build path + area.
    var line = "", area = "";
    series.forEach(function (p, i) {
      var X = px(p.x), Y = py(p.y);
      line += (i === 0 ? "M" : "L") + X.toFixed(1) + " " + Y.toFixed(1) + " ";
    });
    area = line + "L" + px(xmax).toFixed(1) + " " + (pad.t + ih) + " " +
           "L" + px(xmin).toFixed(1) + " " + (pad.t + ih) + " Z";

    svg.appendChild(el("path", { d: area, fill: "url(#dqfill)", stroke: "none" }));
    svg.appendChild(el("path", {
      d: line, fill: "none", stroke: "var(--accent)", "stroke-width": 2.2,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }));

    // Points (last one emphasised, like a live ticker).
    var tip = document.createElement("div");
    tip.className = "dq-tip"; tip.style.display = "none";
    series.forEach(function (p, i) {
      var last = i === series.length - 1;
      var dot = el("circle", {
        cx: px(p.x), cy: py(p.y), r: last ? 4.5 : 3,
        fill: last ? "var(--accent)" : "var(--panel)",
        stroke: "var(--accent)", "stroke-width": 1.6, class: "dq-dot",
      });
      dot.addEventListener("mouseenter", function () {
        tip.style.display = "block";
        tip.innerHTML = "<strong>" + p.y + "</strong> / 100<br>" +
          "<span class='dq-tip-sub'>" + fmtDateTime(p.x) + "</span><br>" +
          "<span class='dq-tip-sub'>" + p.label + "</span>";
        var r = mount.getBoundingClientRect();
        var cx = px(p.x), cy = py(p.y);
        tip.style.left = Math.min(Math.max(cx, 60), W - 60) + "px";
        tip.style.top = (cy - 10) + "px";
      });
      dot.addEventListener("mouseleave", function () { tip.style.display = "none"; });
      svg.appendChild(dot);
    });

    mount.appendChild(svg);
    mount.appendChild(tip);
    renderMeta(series, { first: series[0], last: series[series.length - 1] });
  }

  function renderMeta(series, ends) {
    var bar = document.createElement("div");
    bar.className = "dq-meta";
    if (!series.length) {
      bar.innerHTML = "<span class='muted'>" + metricLabel() +
        " — try a wider range or play some hands.</span>";
      mount.appendChild(bar);
      return;
    }
    var ys = series.map(function (p) { return p.y; });
    var avg = ys.reduce(function (a, b) { return a + b; }, 0) / ys.length;
    var latest = ends.last.y;
    var first = ends.first.y;
    var delta = latest - first;
    var arrow = delta > 0.05 ? "▲" : (delta < -0.05 ? "▼" : "—");
    var cls = delta > 0.05 ? "up" : (delta < -0.05 ? "down" : "flat");
    bar.innerHTML =
      "<span class='dq-meta-item'><span class='dq-k'>" + metricLabel() + "</span></span>" +
      "<span class='dq-meta-item'><span class='dq-k'>Latest</span> <strong>" + latest + "</strong></span>" +
      "<span class='dq-meta-item'><span class='dq-k'>Avg</span> <strong>" + avg.toFixed(1) + "</strong></span>" +
      "<span class='dq-meta-item dq-delta " + cls + "'>" + arrow + " " +
        (delta >= 0 ? "+" : "") + delta.toFixed(1) + " over range</span>" +
      "<span class='dq-meta-item muted'>" + series.length + " point" + (series.length === 1 ? "" : "s") + "</span>";
    mount.appendChild(bar);
  }

  function buildMetricOptions() {
    if (!metricEl) return;
    metricEl.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = "overall"; opt.textContent = "Overall quality (per decision)";
    metricEl.appendChild(opt);
    var dims = (STATE.data && STATE.data.dimensions) || [];
    var labels = (STATE.data && STATE.data.labels) || {};
    dims.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d; o.textContent = (labels[d] || d) + " (per session)";
      metricEl.appendChild(o);
    });
    metricEl.value = STATE.metric;
  }

  function fetchAndRender() {
    mount.innerHTML = "<div class='dq-loading muted'>Loading…</div>";
    fetch("/api/history")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        STATE.data = d;
        buildMetricOptions();
        render();
      })
      .catch(function () {
        mount.innerHTML = "<div class='dq-loading muted'>Couldn't load history.</div>";
      });
  }

  // Wire controls.
  if (rangeEl) {
    rangeEl.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-range]");
      if (!b) return;
      STATE.range = b.getAttribute("data-range");
      rangeEl.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      render();
    });
  }
  if (metricEl) {
    metricEl.addEventListener("change", function () { STATE.metric = metricEl.value; render(); });
  }
  if (refreshEl) {
    refreshEl.addEventListener("click", function () {
      refreshEl.classList.add("spin");
      fetchAndRender();
      setTimeout(function () { refreshEl.classList.remove("spin"); }, 600);
    });
  }
  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt); rt = setTimeout(function () { if (STATE.data) render(); }, 150);
  });

  fetchAndRender();
})();

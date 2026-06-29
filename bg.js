// bg.js — light, interactive "markets" backdrop for Edge Over Variance.
//
// A soft 3D perspective grid receding to a horizon, drifting candlesticks, and a
// glowing price line — tuned for a LIGHT blue theme. Interactive: the vanishing
// point gently parallaxes toward the cursor. Performance-first: capped DPR,
// ~30fps, cached gradients, no per-frame blur, and it PAUSES whenever the tab is
// hidden or the window loses focus. Honors prefers-reduced-motion (static frame).

(function () {
  const canvas = document.getElementById("bg");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const BLUE = "43,134,216", SKY = "90,169,238", GREEN = "31,158,107", RED = "214,73,60";

  let W, H, horizon, candles = [], price = [];
  let vignetteGrad = null, horizonGrad = null;
  const DPR = 1, FRAME_MS = 1000 / 30;

  // Cursor parallax (eased).
  let mx = 0.5, my = 0.5, tx = 0.5, ty = 0.5;

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * DPR; canvas.height = H * DPR;
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    horizon = H * 0.42;

    horizonGrad = ctx.createLinearGradient(0, horizon - 60, 0, horizon + 60);
    horizonGrad.addColorStop(0, "rgba(0,0,0,0)");
    horizonGrad.addColorStop(0.5, `rgba(${BLUE},0.06)`);
    horizonGrad.addColorStop(1, "rgba(0,0,0,0)");

    vignetteGrad = ctx.createRadialGradient(W / 2, H * 0.5, Math.min(W, H) * 0.25,
                                            W / 2, H * 0.5, Math.max(W, H) * 0.8);
    vignetteGrad.addColorStop(0, "rgba(0,0,0,0)");
    vignetteGrad.addColorStop(1, "rgba(208,224,243,0.5)");

    buildCandles(); buildPrice();
    if (reduced) draw(0);
  }

  function buildCandles() {
    candles = [];
    const n = Math.max(8, Math.floor(W / 100));
    let p = horizon * 0.55;
    for (let i = 0; i < n; i++) {
      const up = Math.random() < 0.5;
      const body = 8 + Math.random() * 30;
      p += (Math.random() - 0.5) * 24;
      p = Math.max(40, Math.min(horizon * 0.9, p));
      candles.push({ up, mid: p, body, wick: body * (0.5 + Math.random()) });
    }
  }
  function buildPrice() {
    price = [];
    let y = horizon * 0.7;
    for (let i = 0; i <= 48; i++) {
      y += (Math.random() - 0.5) * 15;
      y = Math.max(36, Math.min(horizon * 0.95, y));
      price.push(y);
    }
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);

    // Ease the cursor parallax.
    mx += (tx - mx) * 0.06; my += (ty - my) * 0.06;
    const vp = W / 2 + (mx - 0.5) * W * 0.10;        // vanishing point follows cursor
    const hz = horizon + (my - 0.5) * 26;

    // Perspective grid.
    const scroll = (t * 0.018) % 1;
    ctx.lineWidth = 1;
    for (let i = 0; i < 18; i++) {
      const f = (i + scroll) / 18;
      const y = hz + Math.pow(f, 2.1) * (H - hz);
      ctx.strokeStyle = `rgba(${BLUE},${(0.11 * (1 - f) + 0.02).toFixed(3)})`;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    for (let i = -12; i <= 12; i++) {
      const xx = vp + (i / 12) * W * 0.62;
      ctx.strokeStyle = `rgba(${SKY},0.05)`;
      ctx.beginPath(); ctx.moveTo(vp, hz); ctx.lineTo(xx, H); ctx.stroke();
    }
    ctx.fillStyle = horizonGrad;
    ctx.fillRect(0, hz - 60, W, 120);

    // Candlesticks.
    const n = candles.length, step = W / n, drift = (t * 8) % step;
    for (let i = 0; i < n; i++) {
      const c = candles[i], xx = i * step - drift + step / 2, col = c.up ? GREEN : RED;
      ctx.strokeStyle = `rgba(${col},0.26)`;
      ctx.fillStyle = `rgba(${col},0.15)`;
      ctx.beginPath(); ctx.moveTo(xx, c.mid - c.wick); ctx.lineTo(xx, c.mid + c.wick); ctx.stroke();
      const w = Math.min(14, step * 0.42);
      ctx.fillRect(xx - w / 2, c.mid - c.body / 2, w, c.body);
      ctx.strokeRect(xx - w / 2, c.mid - c.body / 2, w, c.body);
    }

    // Price line — two cheap solid strokes for a soft glow.
    const pts = price.length - 1, pstep = W / pts, shift = (t * 12) % pstep;
    const path = () => {
      ctx.beginPath();
      for (let i = 0; i <= pts; i++) {
        const xx = i * pstep - shift, y = price[i] + Math.sin(t * 0.6 + i * 0.35) * 3;
        i ? ctx.lineTo(xx, y) : ctx.moveTo(xx, y);
      }
    };
    ctx.strokeStyle = `rgba(${BLUE},0.10)`; ctx.lineWidth = 5; path(); ctx.stroke();
    ctx.strokeStyle = `rgba(${BLUE},0.42)`; ctx.lineWidth = 1.6; path(); ctx.stroke();

    ctx.fillStyle = vignetteGrad;
    ctx.fillRect(0, 0, W, H);
  }

  const reduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let running = false, rafId = null, last = 0;

  function loop(ms) {
    if (!running) return;
    rafId = requestAnimationFrame(loop);
    if (ms - last < FRAME_MS) return;
    last = ms;
    draw(ms / 1000);
  }
  function start() { if (running || reduced) return; running = true; last = 0; rafId = requestAnimationFrame(loop); }
  function stop() { running = false; if (rafId) { cancelAnimationFrame(rafId); rafId = null; } }

  window.addEventListener("resize", resize);
  window.addEventListener("mousemove", (e) => { tx = e.clientX / window.innerWidth; ty = e.clientY / window.innerHeight; });
  document.addEventListener("visibilitychange", () => document.hidden ? stop() : start());
  window.addEventListener("blur", stop);
  window.addEventListener("focus", start);

  resize();
  if (reduced) draw(0); else start();
})();

// desk_hand.js — "At the table" view for the Markets desk.
//
// The left half shows the news brief exactly as the main desk does. The right
// half dramatises the SAME decision as a live, playable poker hand: hero cards
// are dealt, the board comes street by street, an equity meter moves, and each
// beat is tied back to the news event. At the key street the trading options
// become the action buttons — graded by the same /api/desk endpoint. After the
// call, the hand runs out and shows what the choice meant.
//
// Variant is Texas Hold'em for every story: a community card arriving on the
// turn is the cleanest mirror for "the news just changed the board".
//
// Depends on cards.js (makeCard) and the global answerDesk() in desk.html.

(function () {
  "use strict";

  // ---- parse the day's hands injected by the template ---------------------
  var HANDS = [];
  try {
    HANDS = JSON.parse(document.getElementById("desk-data").textContent || "[]");
  } catch (e) { HANDS = []; }
  var BY_ID = {};
  HANDS.forEach(function (h) { BY_ID[h.hand_id] = h; });

  // ---- per-category poker scenes -----------------------------------------
  // equity = scripted hero equity at that street (illustrative, 0-100).
  var SCENES = {
    macro: {
      hero: ["As", "Ks"], villain: "The Fed",
      intro: "You hold A♠K♠ — long duration, positioned for the rate cuts you've been " +
             "drawing to. A premium hand with a clear plan.",
      steps: [
        { name: "Preflop", deal: [], equity: 55,
          tie: "You open strong: long-duration bonds into an expected easing cycle." },
        { name: "Flop", deal: ["Qs", "7s", "2d"], equity: 62,
          tie: "Two spades — your draw to the cuts is live and you're ahead of the story." },
        { name: "Turn", deal: ["9h"], equity: 28, scare: true,
          tie: "The Fed turns hawkish — Chair Warsh strips the easing bias. The cut you were " +
               "drawing to is now a DEAD card. Your edge just left the board." },
        { name: "River", deal: ["4d"], equity: 14,
          tie: "Brick. The draw never gets there." },
      ],
      decisionIndex: 2,
      good: "You folded the dead draw and protected your stack — the disciplined exit when the " +
            "catalyst is gone.",
      bad: "You paid off The Fed, chasing a draw the new information had already killed.",
    },
    tech: {
      hero: ["Qd", "Qs"], villain: "Broadcom",
      intro: "You hold Q♦Q♠ — a basket of quality chip names. A genuinely strong, well-built hand.",
      steps: [
        { name: "Preflop", deal: [], equity: 80,
          tie: "Premium holding: the sector's demand (Nvidia, Micron) is booming behind you." },
        { name: "Flop", deal: ["Qc", "8d", "3s"], equity: 92,
          tie: "You flop top set. Your hand's real strength is intact — the fundamentals haven't changed." },
        { name: "Turn", deal: ["Kh"], equity: 86, scare: true,
          tie: "Broadcom shoves after a soft guide — one loud, scary bet drags the whole table red. " +
               "But that overcard doesn't actually beat your set." },
        { name: "River", deal: ["2c"], equity: 90,
          tie: "Brick. Your set holds — the scare was just one player's story." },
      ],
      decisionIndex: 2,
      good: "You held the core and let a strong hand play — outcome-independence in action.",
      bad: "You folded top set to a single scary bet — capitulating on a hand that was still ahead.",
    },
    energy: {
      hero: ["Ac", "5c"], villain: "The Tape",
      intro: "You're long oil on the war premium — a speculative A♣5♣ that's been winning because " +
             "the whole table is scared.",
      steps: [
        { name: "Preflop", deal: [], equity: 52,
          tie: "Thin speculative long — it works while fear keeps the risk premium bid." },
        { name: "Flop", deal: ["Kd", "9c", "4c"], equity: 60,
          tie: "A flush draw plus all that fear folding equity to you. The premium is doing the work." },
        { name: "Turn", deal: ["8s"], equity: 30, scare: true,
          tie: "A ceasefire deal is announced. The fear leaves the table — the scare card that fuelled " +
               "your edge is pulled from the deck. Your long has nothing behind it now." },
        { name: "River", deal: ["2h"], equity: 16,
          tie: "Brick. The premium is gone for good." },
      ],
      decisionIndex: 2,
      good: "You banked the move and stood down — the reason you held the trade was gone, so the trade " +
            "went with it.",
      bad: "You held a position whose entire rationale had just been removed — hoping, not trading an edge.",
    },
    _default: {
      hero: ["Ad", "Kd"], villain: "The Market",
      intro: "You hold A♦K♦ — a real but thin edge. The whole game is sizing it correctly.",
      steps: [
        { name: "Preflop", deal: [], equity: 50,
          tie: "A genuine edge, but a slim one — respect it with your size." },
        { name: "Flop", deal: ["Jd", "7c", "2s"], equity: 49,
          tie: "The board develops; your edge is real but uncertain." },
        { name: "Turn", deal: ["9h"], equity: 47, scare: true,
          tie: "Decision time — how do you act, and how big?" },
        { name: "River", deal: ["3c"], equity: 45,
          tie: "The hand runs out." },
      ],
      decisionIndex: 2,
      good: "You sized to the edge and stayed disciplined — the right process regardless of the card.",
      bad: "Your line let the tape, not the edge, decide the size.",
    },
  };

  function sceneFor(hand) {
    var t = SCENES[hand.category] || SCENES._default;
    return JSON.parse(JSON.stringify(t)); // fresh copy
  }

  // ---- DOM helpers --------------------------------------------------------
  var $ = function (id) { return document.getElementById(id); };
  function tile(cls, html) {
    var d = document.createElement("div");
    d.className = cls; if (html != null) d.innerHTML = html; return d;
  }

  var cur = null; // { hand, scene, step, answered, fb }

  // ---- left brief (mirrors the main desk, fully interactive) --------------
  function renderBrief(hand) {
    var host = $("split-brief");
    host.innerHTML = "";
    var sec = document.createElement("section");
    sec.className = "news-hand";
    sec.setAttribute("data-hand", hand.hand_id);
    var opts = (hand.options || []).map(function (o) {
      return '<button class="news-opt" data-key="' + o.key + '" ' +
             'onclick="answerDesk(\'' + hand.hand_id + "', '" + o.key + '\', this)">' +
             esc(o.label) + "</button>";
    }).join("");
    sec.innerHTML =
      '<div class="news-head"><span class="tag ' + hand.category + '">' + hand.category + "</span>" +
        "<h2>" + esc(hand.headline) + "</h2></div>" +
      '<p class="news-summary">' + esc(hand.summary) +
        ' <a class="news-src" href="' + hand.source_url + '" target="_blank" rel="noopener">' +
        esc(hand.source) + " ↗</a></p>" +
      '<div class="spot"><strong>The analogy:</strong> ' + esc(hand.analogy) + "</div>" +
      '<p class="news-prompt"><strong>Your move:</strong> ' + esc(hand.prompt) + "</p>" +
      '<div class="news-options" data-hand="' + hand.hand_id + '">' + opts + "</div>" +
      '<div class="news-feedback"></div>';
    host.appendChild(sec);
  }

  // ---- right hand (playable) ---------------------------------------------
  function renderHand(hand) {
    cur = { hand: hand, scene: sceneFor(hand), step: -1, answered: false, fb: null };
    var host = $("split-hand");
    host.innerHTML =
      '<div class="dh-table">' +
        '<div class="dh-villain"><span class="seat-label">' + esc(cur.scene.villain) + "</span>" +
          '<div class="cards-row" id="dh-villain-cards"></div></div>' +
        '<div class="dh-boardwrap"><div class="seat-label">Board</div>' +
          '<div class="cards-row" id="dh-board"></div></div>' +
        '<div class="dh-equity"><div class="dh-eq-top"><span>Your edge (equity)</span>' +
          '<strong id="dh-eq-val">—</strong></div>' +
          '<div class="dh-eq-bar"><span id="dh-eq-fill" style="width:0%"></span></div></div>' +
        '<div class="dh-hero"><span class="seat-label">Your hand</span>' +
          '<div class="cards-row" id="dh-hero-cards"></div></div>' +
        '<div class="dh-tie" id="dh-tie">' + esc(cur.scene.intro) + "</div>" +
        '<div class="dh-controls" id="dh-controls"></div>' +
      "</div>" +
      '<div class="dh-verdict" id="dh-verdict"></div>';
    // hero face-down until dealt
    var hh = $("dh-hero-cards");
    cur.scene.hero.forEach(function () { hh.appendChild(makeCard("", false)); });
    var vc = $("dh-villain-cards");
    vc.appendChild(makeCard("", false)); vc.appendChild(makeCard("", false));
    controlsDeal();
  }

  function controlsDeal() {
    var c = $("dh-controls"); c.innerHTML = "";
    var b = document.createElement("button");
    b.className = "primary"; b.textContent = "Deal the hand";
    b.onclick = startHand; c.appendChild(b);
  }

  function startHand() {
    // flip hero up
    var hh = $("dh-hero-cards"); hh.innerHTML = "";
    cur.scene.hero.forEach(function (cd) {
      var el = makeCard(cd, true); el.classList.add("dh-in"); hh.appendChild(el);
    });
    cur.step = 0;
    applyStep(cur.scene.steps[0]);
    nextControls();
  }

  function applyStep(st) {
    if (st.deal && st.deal.length) {
      var bd = $("dh-board");
      st.deal.forEach(function (cd, i) {
        setTimeout(function () {
          var el = makeCard(cd, true);
          el.classList.add("dh-in");
          if (cur.scene.steps[cur.step] && cur.scene.steps[cur.step].scare) el.classList.add("dh-scare");
          bd.appendChild(el);
        }, i * 140);
      });
    }
    setEquity(st.equity);
    var tie = $("dh-tie");
    tie.className = "dh-tie" + (st.scare ? " scare" : "");
    tie.innerHTML = "<span class='dh-street'>" + st.name + "</span>" + esc(st.tie);
  }

  function setEquity(v) {
    var fill = $("dh-eq-fill"), val = $("dh-eq-val");
    if (!fill) return;
    fill.style.width = Math.max(0, Math.min(100, v)) + "%";
    fill.className = v >= 60 ? "good" : (v < 35 ? "bad" : "mid");
    val.textContent = v + "%";
  }

  function nextControls() {
    var c = $("dh-controls"); c.innerHTML = "";
    var scene = cur.scene;
    var nextIdx = cur.step + 1;
    // If the NEXT street is the decision street, deal it then ask for the call.
    if (cur.step === scene.decisionIndex) { showDecision(); return; }
    if (nextIdx >= scene.steps.length) { return; }
    var b = document.createElement("button");
    b.className = "primary";
    b.textContent = "Deal the " + scene.steps[nextIdx].name.toLowerCase();
    b.onclick = function () {
      cur.step = nextIdx;
      applyStep(scene.steps[cur.step]);
      nextControls();
    };
    c.appendChild(b);
  }

  function showDecision() {
    var c = $("dh-controls"); c.innerHTML = "";
    var lbl = document.createElement("div");
    lbl.className = "dh-decision-lbl";
    lbl.textContent = "Your move at the table — and how big?";
    c.appendChild(lbl);
    var wrap = document.createElement("div");
    wrap.className = "news-options dh-actions";
    wrap.setAttribute("data-hand", cur.hand.hand_id);
    (cur.hand.options || []).forEach(function (o) {
      var b = document.createElement("button");
      b.className = "news-opt"; b.setAttribute("data-key", o.key);
      b.textContent = o.label;
      b.onclick = function () { answerDesk(cur.hand.hand_id, o.key, b); };
      wrap.appendChild(b);
    });
    c.appendChild(wrap);
  }

  // called by answerDesk() after grading (from either pane)
  function onAnswered(handId, fb) {
    if (!cur || cur.hand.hand_id !== handId || cur.answered) return;
    cur.answered = true; cur.fb = fb;
    // lock every option button for this hand (both panes)
    document.querySelectorAll('[data-hand="' + handId + '"] button').forEach(function (b) {
      b.disabled = true;
    });
    // make sure the board is fully out (covers answering from the left early)
    var scene = cur.scene;
    var c = $("dh-controls"); c.innerHTML = "";
    var verdict = $("dh-verdict");
    var good = fb.correct;
    verdict.innerHTML =
      '<div class="dh-call ' + (fb.quality >= 70 ? "ok" : "no") + '">' +
        "You chose: <strong>" + esc(fb.chosen_label) + "</strong> · quality <strong>" +
        fb.quality + "</strong>/100" +
        (fb.correct ? "" : "<br>Best line: <strong>" + esc(fb.best_label) + "</strong>") +
      "</div>";
    // run out any remaining streets, then show the outcome
    var i = cur.step;
    function advance() {
      i += 1;
      if (i < scene.steps.length) {
        cur.step = i;
        applyStep(scene.steps[i]);
        setTimeout(advance, 900);
      } else {
        showOutcome(good);
      }
    }
    if (cur.step < scene.steps.length - 1) setTimeout(advance, 700);
    else showOutcome(good);
  }

  function showOutcome(good) {
    var verdict = $("dh-verdict");
    var scene = cur.scene;
    var box = document.createElement("div");
    box.className = "dh-outcome " + (good ? "win" : "lose");
    box.innerHTML =
      "<div class='dh-out-head'>" + (good ? "✓ Disciplined line" : "✗ Costly line") + "</div>" +
      "<p>" + esc(good ? scene.good : scene.bad) + "</p>" +
      "<p class='muted'>Full breakdown and key terms are in the brief on the left.</p>";
    verdict.appendChild(box);
    var again = document.createElement("button");
    again.className = "ghost"; again.textContent = "↻ Replay this hand";
    again.onclick = function () { select(cur.hand.hand_id); };
    verdict.appendChild(again);
    verdict.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---- selection / wiring -------------------------------------------------
  function select(handId) {
    var hand = BY_ID[handId];
    if (!hand) return;
    document.querySelectorAll("#hand-picker .hp").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-hand") === handId);
    });
    renderBrief(hand);
    renderHand(hand);
  }

  function init() {
    var picker = $("hand-picker");
    if (picker && !picker._wired) {
      picker._wired = true;
      picker.addEventListener("click", function (e) {
        var b = e.target.closest(".hp"); if (!b) return;
        select(b.getAttribute("data-hand"));
      });
    }
    if (HANDS.length) select(HANDS[0].hand_id);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.DeskTable = { init: init, select: select, onAnswered: onAnswered };
})();

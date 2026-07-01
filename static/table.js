// table.js — shared 3D poker-table renderer for all three play modes.
// One renderer draws Hold'em, Stud heads-up and Stud table so they stay in sync.
// Sleek-dark style: navy felt on a slight perspective tilt, seats around an
// ellipse, hero at the bottom with their position, dealer button, pot + board in
// the centre, and the action buttons below.

(function () {
  const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };

  // Opponent seat slots (left%, top%) by opponent count (1..7); hero is fixed at
  // the bottom centre. Tuned to sit around the felt's upper arc and sides.
  const SLOTS = {
    1: [[50, 15]],
    2: [[20, 26], [80, 26]],
    3: [[15, 38], [50, 13], [85, 38]],
    4: [[13, 52], [30, 18], [70, 18], [87, 52]],
    5: [[12, 56], [24, 22], [50, 13], [76, 22], [88, 56]],
    6: [[11, 58], [19, 28], [40, 14], [60, 14], [81, 28], [89, 58]],
    7: [[11, 60], [16, 32], [33, 17], [50, 13], [67, 17], [84, 32], [89, 60]],
  };

  function el(tag, cls) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function card(txt, opts) {
    opts = opts || {};
    const d = document.createElement("div");
    const cls = ["tcard"];
    if (opts.lg) cls.push("lg");
    if (opts.mini) cls.push("mini");
    if (!opts.up || !txt) {
      cls.push("back");
      d.className = cls.join(" ");
      return d;
    }
    let rank = txt.slice(0, -1);
    if (rank === "T") rank = "10";
    const suit = txt.slice(-1);
    if (suit === "h" || suit === "d") cls.push("red");
    d.className = cls.join(" ");
    d.innerHTML = `<span class="r">${rank}</span><span class="p">${SUIT[suit] || "?"}</span>`;
    return d;
  }

  function potEl(pot) {
    const p = el("div", "pot");
    p.innerHTML =
      '<span class="chip"></span><span class="chip cyan"></span><span class="chip"></span>';
    const s = el("span");
    s.textContent = "Pot " + pot;
    p.appendChild(s);
    return p;
  }

  function boardEl(board) {
    const b = el("div", "board");
    if (!board || !board.length) {
      const ph = el("span", "board-ph");
      ph.textContent = "pre-flop";
      b.appendChild(ph);
    } else {
      board.forEach((c) => b.appendChild(card(c, { up: true })));
    }
    return b;
  }

  function posTag(label) {
    if (!label) return null;
    const t = el(
      "span",
      "tag" + (label === "SB" ? " sb" : label === "BB" ? " bb" : "")
    );
    t.textContent = label;
    return t;
  }

  function seatEl(o, slot, game) {
    const s = el("div", "seat" + (o.folded ? " folded" : ""));
    s.style.left = slot[0] + "%";
    s.style.top = slot[1] + "%";

    const cw = el("div", "cw");
    let any = false;
    if (game === "stud" && o.up) {
      o.up.forEach((c) => {
        cw.appendChild(card(c, { mini: true, up: true }));
        any = true;
      });
    }
    for (let i = 0; i < (o.hidden || 0); i++) {
      cw.appendChild(card("", { mini: true, up: false }));
      any = true;
    }
    if (!any) cw.appendChild(card("", { mini: true, up: false }));
    s.appendChild(cw);

    const who = el("div", "who");
    who.appendChild(document.createTextNode(o.name + (o.folded ? " · folded" : "")));
    const tag = posTag(o.position);
    if (tag) who.appendChild(tag);
    s.appendChild(who);

    if (o.stack != null) {
      const st = el("div", "stack");
      st.textContent = o.stack + " bb";
      s.appendChild(st);
    }
    return s;
  }

  function heroSeatEl(state, game) {
    const live = state.awaiting && !state.finished;
    const s = el("div", "seat hero" + (live ? " toact" : ""));
    s.style.left = "50%";
    s.style.top = "89%";

    const cw = el("div", "cw");
    const heroCards = state.hero || [];
    const big = heroCards.length <= 2;          // lg for Hold'em (2), normal for stud
    heroCards.forEach((hc) => cw.appendChild(card(hc.card, { lg: big, up: true })));
    s.appendChild(cw);

    const who = el("div", "who");
    who.appendChild(document.createTextNode("You"));
    const tag = posTag(game === "holdem" ? state.hero_position : null);
    if (tag) who.appendChild(tag);
    s.appendChild(who);

    const st = el("div", "stack");
    st.textContent =
      (state.hero_stack != null ? state.hero_stack + " bb" : "") +
      (live ? " · to act" : "");
    s.appendChild(st);
    return s;
  }

  function dealerEl(state, slots) {
    let slot = null;
    if (state.hero_is_button) slot = [50, 89];
    else {
      const idx = (state.opponents || []).findIndex((o) => o.is_button);
      if (idx >= 0 && slots[idx]) slot = slots[idx];
    }
    if (!slot) return null;
    const d = el("div", "dealer");
    d.textContent = "D";
    d.style.left = slot[0] + (50 - slot[0]) * 0.22 + "%";
    d.style.top = slot[1] + (50 - slot[1]) * 0.22 + "%";
    return d;
  }

  function actionsEl(state, onAction) {
    const wrap = el("div", "pk-actions-wrap");
    if (state.finished || !state.awaiting) return wrap;

    const act = el("div", "pk-actions");
    (state.awaiting.options || []).forEach((o) => {
      const b = document.createElement("button");
      b.textContent = o.label;
      b.className =
        o.key === "fold"
          ? "btn-fold"
          : o.key === "check" || o.key === "call"
          ? "btn-call"
          : "btn-raise";
      b.onclick = () => onAction(o.key);
      act.appendChild(b);
    });
    wrap.appendChild(act);

    const bits = [];
    if (state.awaiting.hero_stack != null) bits.push("Stack " + state.awaiting.hero_stack);
    if (state.awaiting.to_call > 0) bits.push("To call " + state.awaiting.to_call);
    if (bits.length) {
      const info = el("div", "pk-tocall");
      info.textContent = bits.join("  ·  ");
      wrap.appendChild(info);
    }
    return wrap;
  }

  // Public: render the whole table into opts.container.
  function render(state, opts) {
    opts = opts || {};
    const game = opts.game || "holdem";
    const c = opts.container;
    c.innerHTML = "";

    const top = el("div", "pk-top");
    const title = el("span", "pk-title");
    title.textContent = opts.title || "";
    top.appendChild(title);
    const meta = el("span", "pk-pos");
    if (game === "holdem" && state.hero_position && !state.finished) {
      meta.appendChild(document.createTextNode("your position "));
      const b = el("span", "badge-pos");
      b.textContent = state.hero_position;
      meta.appendChild(b);
    } else {
      meta.textContent = state.finished
        ? "hand complete"
        : state.num_opponents +
          (state.num_opponents === 1 ? " opponent · " : " opponents · ") +
          state.street_name +
          (game === "stud" ? " street" : "");
    }
    top.appendChild(meta);
    c.appendChild(top);

    const scene = el("div", "pk-scene");
    scene.appendChild(el("div", "pk-table"));

    const center = el("div", "pk-center");
    center.appendChild(potEl(state.pot));
    if (game === "holdem") center.appendChild(boardEl(state.board));
    scene.appendChild(center);

    const opps = state.opponents || [];
    const slots = SLOTS[Math.min(Math.max(opps.length, 1), 7)] || SLOTS[1];
    opps.forEach((o, i) => {
      if (slots[i]) scene.appendChild(seatEl(o, slots[i], game));
    });
    scene.appendChild(heroSeatEl(state, game));

    if (game === "holdem") {
      const d = dealerEl(state, slots);
      if (d) scene.appendChild(d);
    }
    c.appendChild(scene);
    c.appendChild(actionsEl(state, opts.onAction || function () {}));
  }

  window.PokerTable = { render: render };
})();

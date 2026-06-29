// cards.js — shared playing-card rendering for the play pages.
// Renders an elegant-but-simple card face: corner index (rank + suit) top-left
// and bottom-right (mirrored), a large centre pip, red/black by suit. Down
// cards show a patterned back.

const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };  // ♠ ♥ ♦ ♣

function _rankLabel(r) {
  return r === "T" ? "10" : r;
}

// txt like "As", "Td", "9c"; up=false renders a face-down back.
function makeCard(txt, up, opts = {}) {
  const d = document.createElement("div");
  const cls = ["pcard"];
  if (opts.mini) cls.push("mini");
  if (!up) {
    cls.push("back");
    d.className = cls.join(" ");
    return d;
  }
  const rank = _rankLabel(txt.slice(0, -1));
  const suit = txt.slice(-1);
  if (suit === "h" || suit === "d") cls.push("red");
  d.className = cls.join(" ");
  const sym = SUIT[suit] || "?";
  d.innerHTML =
    `<span class="corner tl">${rank}<br>${sym}</span>` +
    `<span class="pip">${sym}</span>` +
    `<span class="corner br">${rank}<br>${sym}</span>`;
  return d;
}

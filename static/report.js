// report.js — shared end-of-hand report rendering for all three play modes.
// Renders: result, per-street grade table, the educational markets translation
// (analogy + question + a "Teach me" block + a clickable "Key terms" tab), and
// the answer box. Kept in one place so the three play pages stay in sync.

function _esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function _termsTab(terms){
  if(!terms || !terms.length) return '';
  let items = terms.map(t =>
    `<div class="term"><span class="term-name">${_esc(t.term)}</span>`
    + `<span class="term-def">${_esc(t.definition)}</span></div>`).join('');
  return `<details class="glossary"><summary>📖 Key terms (${terms.length})</summary>`
       + `<div class="term-list">${items}</div></details>`;
}

function reportHtml(rep, res){
  const t = rep.translation || {};
  let html = `<h2>Hand report</h2>`;

  // Result line (board shown for Hold'em; opponents' shown hands otherwise).
  const sd = (res.showdown||[]).filter(s => s.name!=='You')
              .map(s => `${_esc(s.name)}: ${_esc((s.cards||[]).join(' '))}`).join(' · ');
  const board = (res.board && res.board.length) ? ` Board: ${_esc(res.board.join(' '))}.` : '';
  html += `<p class="result-line ${res.winner_is_hero?'won':'lost'}">`
        + `Result: <strong>${_esc(res.winner||'—')}</strong> by ${_esc(res.by||'—')} `
        + `(pot ${_esc(res.pot)}).${board}${sd?` ${sd}.`:''}</p>`;

  html += `<p class="lede">${_esc(rep.overview.headline)} `
        + `Average decision quality: <strong>${rep.overview.average_adherence}</strong>/100.</p>`;

  // Per-street grades.
  html += `<table class="grid"><thead><tr><th>Street</th><th>You did</th>`
        + `<th class="num">Equity</th><th>Best</th><th class="num">Quality</th><th>Why</th></tr></thead><tbody>`;
  (rep.streets||[]).forEach(s => {
    const cls = s.adherence>=70?'good':(s.adherence<40?'bad':'');
    html += `<tr><td>${_esc(s.street_name)}</td><td>${_esc(s.action)}</td>`
          + `<td class="num">${Math.round(s.equity*100)}%</td><td>${_esc(s.best_action)}</td>`
          + `<td class="num ${cls}">${s.adherence}</td><td class="note">${_esc(s.why)}</td></tr>`;
  });
  html += `</tbody></table>`;

  // The math behind each decision.
  const mathRows = (rep.streets || []).filter(s => s.math);
  if (mathRows.length) {
    let items = mathRows.map(s =>
      `<div class="math-row"><span class="math-street">${_esc(s.street_name)} · `
      + `you ${_esc(s.action)}</span><span class="math-text">${_esc(s.math)}</span></div>`).join('');
    html += `<details class="glossary" open><summary>📐 The math behind each decision</summary>`
          + `<div class="math-list">${items}</div></details>`;
  }

  // Markets translation — the teaching moment.
  html += `<div class="explain">`
        + `<div class="explain-head"><span class="badge">Markets lesson</span>`
        + `<h3>From the felt to the screen — ${_esc(t.lesson||'')}</h3></div>`
        + `<p>${_esc(t.analogy||'')}</p>`;
  if(t.teaching){
    html += `<div class="teach-box"><div class="teach-label">How this works in trading</div>`
          + `<p>${_esc(t.teaching)}</p></div>`;
  }
  html += _termsTab(t.terms);
  html += `<div class="spot"><strong>Your move:</strong> ${_esc(t.question||'')}</div>`
        + `<textarea id="answer" rows="4" placeholder="What would you do, and why? Reason from edge, expected value, and risk."></textarea>`
        + `<button onclick="submitAnswer()">Submit answer</button>`
        + `<div id="answer-feedback"></div></div>`;

  html += `<p style="margin-top:18px"><button class="primary" onclick="newHand()">Deal again →</button></p>`;
  return html;
}

async function showReport(HAND){
  const r = await fetch(`/api/hand/${HAND.hand_id}/report`);
  if(!r.ok) return;
  const rep = await r.json();
  window._HAND = HAND;
  const el = document.getElementById('report');
  el.innerHTML = reportHtml(rep, HAND.result || {});
  el.style.display = 'block';
}

async function submitAnswer(){
  const HAND = window._HAND;
  const answer = document.getElementById('answer').value;
  const r = await fetch(`/api/hand/${HAND.hand_id}/answer`,
    {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({answer})});
  const fb = await r.json();
  document.getElementById('answer-feedback').innerHTML =
      `<div class="verdict ${fb.score>=70?'correct':'incorrect'}">`
    + `<p>Score: <strong>${fb.score}</strong>/100. ${_esc(fb.feedback||'')}</p>`
    + `<p><strong>Model answer:</strong> ${_esc(fb.model_answer||'')}</p></div>`;
}

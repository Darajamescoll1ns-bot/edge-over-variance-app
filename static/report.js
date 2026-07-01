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

function _pct(x){ return Math.round((x||0)*100) + '%'; }
function _ev(x){ return (x>=0?'+':'') + (Math.round(x*10)/10); }
function _mchip(label, val, tone){
  return `<span class="m-chip ${tone||''}"><span class="m-k">${label}</span><b>${val}</b></span>`;
}

// One tidy card per decision: the key numbers as chips, a plain-English
// takeaway, and (on the spotlight decision) the implied-odds result. The full
// arithmetic stays available behind a "full arithmetic" toggle.
function _decisionCard(s){
  const q = s.adherence>=70 ? 'good' : (s.adherence<40 ? 'bad' : 'mid');
  const chips = [];
  if (s.conditioned){
    chips.push(_mchip('vs random', _pct(s.equity_vs_random)));
    const tone = s.equity_effective < s.equity_vs_random-0.005 ? 'down'
               : (s.equity_effective > s.equity_vs_random+0.005 ? 'up' : '');
    chips.push(_mchip('when called', _pct(s.equity_effective), tone));
  } else {
    chips.push(_mchip('equity', _pct(s.equity)));
  }
  if (s.to_call>0) chips.push(_mchip('break-even', _pct(s.to_call/(s.pot+s.to_call))));
  if (s.fold_equity>0) chips.push(_mchip('fold equity', _pct(s.fold_equity)));
  const taken = s.option_evs ? s.option_evs[s.action] : null;
  if (taken!=null) chips.push(_mchip('your EV', _ev(taken), taken>=0?'up':'down'));
  if (s.best_action && s.best_action!==s.action && s.option_evs && s.option_evs[s.best_action]!=null){
    chips.push(_mchip('best · '+_esc(s.best_action), _ev(s.option_evs[s.best_action])));
  }

  let implied = '';
  if (s.implied){
    const d = Math.round(s.implied.implied_delta*10)/10;
    implied = `<div class="m-implied">⟳ <b>Implied odds</b> (simulated to showdown): true EV ≈ `
      + `<b>${_ev(s.implied.ev_call)}</b> — `
      + (d>=0 ? `${_ev(d)} extra won on later streets when you improve.`
              : `${d} — reverse implied odds: you pay off better hands.`)
      + `</div>`;
  }

  return `<div class="m-card ${q}">`
    + `<div class="m-head"><span class="m-street">${_esc(s.street_name)} · you ${_esc(s.action)}</span>`
    + `<span class="m-q ${q}">${s.adherence}/100</span></div>`
    + `<div class="m-chips">${chips.join('')}</div>`
    + `<div class="m-why">${_esc(s.why||'')}</div>`
    + implied
    + (s.math ? `<details class="m-detail"><summary>full arithmetic</summary><p>${_esc(s.math)}</p></details>` : '')
    + `</div>`;
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

  // The math behind each decision — a clean, structured breakdown.
  const mathRows = (rep.streets || []);
  if (mathRows.length) {
    html += `<details class="glossary" open><summary>📐 The math behind each decision</summary>`
          + `<div class="m-list">${mathRows.map(_decisionCard).join('')}</div></details>`;
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

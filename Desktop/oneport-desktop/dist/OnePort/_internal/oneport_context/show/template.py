# SPDX-License-Identifier: Apache-2.0
# Copyright 2024 OnePort Contributors
"""The self-contained walkthrough page. Scenes/modules injected as JSON."""
from __future__ import annotations

import json


def render_html(payload: dict) -> str:
    return _HTML.replace("__PAYLOAD__", json.dumps(payload)).replace("__REPO__", payload.get("repo", "repo"))


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__REPO__ — oneport-context walkthrough</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');
  *,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
  body{background:#080808;color:#e0e0e0;font-family:'DM Sans',sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}
  header{height:54px;border-bottom:1px solid rgba(255,255,255,.07);display:flex;align-items:center;gap:14px;padding:0 20px;flex-shrink:0}
  .logo{font-weight:600;font-size:14px}
  .tag{font-size:12px;color:rgba(255,255,255,.35)}
  .brain{margin-left:auto;font-family:'DM Mono',monospace;font-size:11px;color:rgba(62,207,142,.8);border:1px solid rgba(62,207,142,.25);border-radius:6px;padding:3px 9px}
  main{flex:1;display:flex;min-height:0}
  .stage{flex:1;position:relative;overflow:hidden}
  svg{width:100%;height:100%;display:block}
  .box{cursor:pointer;transition:opacity .3s}
  .box rect{fill:#0f0f0f;stroke:rgba(255,255,255,.12);stroke-width:1;rx:10;transition:stroke .3s,fill .3s}
  .box.dim{opacity:.25}
  .box.hot rect{stroke:rgba(62,207,142,.9);fill:rgba(62,207,142,.08)}
  .box .mname{fill:#e6e6e6;font-family:'DM Mono',monospace;font-size:12px;font-weight:500}
  .box .mmeta{fill:rgba(255,255,255,.3);font-family:'DM Mono',monospace;font-size:10px}
  .edge{stroke:rgba(255,255,255,.14);stroke-width:1.2;fill:none}
  .edge.hot{stroke:rgba(62,207,142,.55);stroke-width:1.6}
  /* side panel */
  .panel{width:340px;border-left:1px solid rgba(255,255,255,.07);padding:18px;overflow-y:auto;flex-shrink:0;background:#0a0a0a}
  .panel.hidden{display:none}
  .panel h3{font-family:'DM Mono',monospace;font-size:13px;color:#e0e0e0;margin-bottom:6px}
  .panel .psum{font-size:12.5px;color:rgba(255,255,255,.45);line-height:1.6;margin-bottom:14px;font-weight:300}
  .file{border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:9px 11px;margin-bottom:8px}
  .file .fp{font-family:'DM Mono',monospace;font-size:11.5px;color:rgba(62,207,142,.85)}
  .file .fs{font-size:11.5px;color:rgba(255,255,255,.4);line-height:1.5;margin-top:3px}
  .file .sym{font-family:'DM Mono',monospace;font-size:10px;color:rgba(255,255,255,.28);margin-top:4px}
  /* narration */
  .narr{border-top:1px solid rgba(255,255,255,.07);padding:16px 20px;flex-shrink:0;background:rgba(8,8,8,.98)}
  .narr-title{font-size:13px;color:rgba(62,207,142,.9);font-family:'DM Mono',monospace;margin-bottom:6px}
  .narr-text{font-size:14px;color:rgba(255,255,255,.75);line-height:1.6;font-weight:300;min-height:44px}
  .controls{display:flex;align-items:center;gap:10px;margin-top:12px}
  .btn{background:#111;border:1px solid rgba(255,255,255,.1);color:rgba(255,255,255,.7);border-radius:8px;padding:7px 12px;font-family:'DM Mono',monospace;font-size:12px;cursor:pointer;transition:all .15s}
  .btn:hover{background:#1a1a1a;border-color:rgba(255,255,255,.18)}
  .btn.play{background:rgba(62,207,142,.12);border-color:rgba(62,207,142,.3);color:rgba(62,207,142,.95)}
  .dots{display:flex;gap:6px;margin-left:auto}
  .dot{width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,.15);cursor:pointer;transition:background .2s}
  .dot.on{background:rgba(62,207,142,.9)}
  .hint{font-size:11px;color:rgba(255,255,255,.25);margin-left:12px;font-family:'DM Mono',monospace}
</style>
</head>
<body>
  <header>
    <span class="logo">Oneport</span>
    <span class="tag">context walkthrough · __REPO__</span>
    <span class="brain" id="brain"></span>
  </header>
  <main>
    <div class="stage"><svg id="diagram" preserveAspectRatio="xMidYMid meet"></svg></div>
    <div class="panel hidden" id="panel"></div>
  </main>
  <div class="narr">
    <div class="narr-title" id="ntitle"></div>
    <div class="narr-text" id="ntext"></div>
    <div class="controls">
      <button class="btn" id="prev">⏮ prev</button>
      <button class="btn play" id="play">▶ play</button>
      <button class="btn" id="next">next ⏭</button>
      <span class="hint" id="scenehint"></span>
      <span class="dots" id="dots"></span>
    </div>
  </div>
<script>
const DATA = __PAYLOAD__;
const NS = "http://www.w3.org/2000/svg";
const BW = 190, BH = 62, GX = 60, GY = 60, MX = 40, MY = 40;
let scene = 0, playing = false;

document.getElementById('brain').textContent = 'brain: ' + (DATA.brain || 'heuristic');

// ---- layout modules on a grid ----
const mods = DATA.modules;
const cols = Math.max(1, Math.min(4, Math.ceil(Math.sqrt(mods.length))));
const pos = {};
mods.forEach((m, i) => {
  const c = i % cols, r = Math.floor(i / cols);
  pos[m.name] = { x: MX + c*(BW+GX), y: MY + r*(BH+GY) };
});
const rows = Math.ceil(mods.length / cols);
const W = MX*2 + cols*BW + (cols-1)*GX;
const H = MY*2 + rows*BH + (rows-1)*GY;
const svg = document.getElementById('diagram');
svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

// arrow marker
svg.innerHTML = `<defs><marker id="arw" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
  <path d="M0,0 L8,4 L0,8 Z" fill="rgba(255,255,255,.25)"/></marker></defs>`;

// edges
mods.forEach(m => (m.depends_on||[]).forEach(dep => {
  if (!pos[dep]) return;
  const a = pos[m.name], b = pos[dep];
  const line = document.createElementNS(NS, 'line');
  line.setAttribute('x1', a.x+BW/2); line.setAttribute('y1', a.y+BH/2);
  line.setAttribute('x2', b.x+BW/2); line.setAttribute('y2', b.y+BH/2);
  line.setAttribute('class', 'edge'); line.setAttribute('marker-end', 'url(#arw)');
  line.dataset.from = m.name; line.dataset.to = dep;
  svg.appendChild(line);
}));

// boxes
mods.forEach(m => {
  const p = pos[m.name];
  const g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'box'); g.setAttribute('transform', `translate(${p.x},${p.y})`);
  g.dataset.name = m.name;
  const rect = document.createElementNS(NS, 'rect');
  rect.setAttribute('width', BW); rect.setAttribute('height', BH); rect.setAttribute('rx', 10);
  const t1 = document.createElementNS(NS, 'text');
  t1.setAttribute('class','mname'); t1.setAttribute('x', 14); t1.setAttribute('y', 26);
  t1.textContent = m.name.length > 24 ? m.name.slice(0,23)+'…' : m.name;
  const t2 = document.createElementNS(NS, 'text');
  t2.setAttribute('class','mmeta'); t2.setAttribute('x', 14); t2.setAttribute('y', 44);
  t2.textContent = m.files.length + ' files · ' + m.loc + ' loc';
  g.append(rect, t1, t2);
  g.addEventListener('click', () => openPanel(m.name));
  svg.appendChild(g);
});

function boxes(){ return [...svg.querySelectorAll('.box')]; }

function highlight(name){
  boxes().forEach(b => {
    b.classList.toggle('hot', name && b.dataset.name === name);
    b.classList.toggle('dim', name && b.dataset.name !== name);
  });
  svg.querySelectorAll('.edge').forEach(e =>
    e.classList.toggle('hot', name && (e.dataset.from === name || e.dataset.to === name)));
}

function openPanel(name){
  const m = mods.find(x => x.name === name);
  if(!m) return;
  const p = document.getElementById('panel');
  p.classList.remove('hidden');
  p.innerHTML = `<h3>${m.name}</h3><div class="psum">${esc(m.summary)}</div>` +
    m.files.map(f => `<div class="file"><div class="fp">${esc(f.path)}</div>` +
      (f.summary?`<div class="fs">${esc(f.summary)}</div>`:'') +
      (f.symbols&&f.symbols.length?`<div class="sym">${esc(f.symbols.join(', '))}</div>`:'') +
      `</div>`).join('');
}
function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

// ---- scenes ----
const scenes = DATA.scenes;
const dotsEl = document.getElementById('dots');
scenes.forEach((_, i) => {
  const d = document.createElement('span'); d.className = 'dot';
  d.onclick = () => { stop(); go(i); }; dotsEl.appendChild(d);
});

function renderScene(i){
  const s = scenes[i];
  document.getElementById('ntitle').textContent = s.title;
  document.getElementById('ntext').textContent = s.narration;
  document.getElementById('scenehint').textContent = `${i+1}/${scenes.length}`;
  [...dotsEl.children].forEach((d,k)=>d.classList.toggle('on',k===i));
  highlight(s.highlight);
  if (s.highlight) openPanel(s.highlight); else document.getElementById('panel').classList.add('hidden');
}

function speak(text){
  if (!('speechSynthesis' in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02; u.pitch = 1;
  u.onend = () => { if (playing && scene < scenes.length-1) go(scene+1); else if (playing) stop(); };
  speechSynthesis.speak(u);
}

function go(i){ scene = Math.max(0, Math.min(scenes.length-1, i)); renderScene(scene); if (playing) speak(scenes[scene].narration); }
function play(){ playing = true; document.getElementById('play').textContent = '⏸ pause'; speak(scenes[scene].narration); }
function stop(){ playing = false; document.getElementById('play').textContent = '▶ play'; if ('speechSynthesis' in window) speechSynthesis.cancel(); }

document.getElementById('play').onclick = () => playing ? stop() : play();
document.getElementById('next').onclick = () => { stop(); go(scene+1); };
document.getElementById('prev').onclick = () => { stop(); go(scene-1); };
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight'){ stop(); go(scene+1); }
  if (e.key === 'ArrowLeft'){ stop(); go(scene-1); }
  if (e.key === ' '){ e.preventDefault(); playing?stop():play(); }
});

renderScene(0);
</script>
</body>
</html>
"""

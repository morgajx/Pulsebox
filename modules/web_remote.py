"""
PulseBox Module: Web Remote
============================
Runs a lightweight HTTP server alongside the Tkinter UI, exposing a
REST-like JSON API and a single-page web remote that works on any
phone, tablet, or browser on the same network.

No extra dependencies — uses Python's built-in http.server and
base64 for cover art.  The full single-page UI is inlined below.

Default port : 8080  (configurable in Settings → Web Remote)
Access        : http://<pi-ip>:8080  from any device on your network

Find the Pi's IP: hostname -I   (shown in the module's UI panel)

API endpoints
─────────────
GET  /                    → web remote UI (HTML)
GET  /api/status          → player state, current track, volume, queue length
GET  /api/now             → current track detail + art (base64 JPEG)
GET  /api/queue           → full pending queue
GET  /api/library         → all albums (artist, name, track count, key)
GET  /api/art/<album_key> → album art as JPEG image
POST /api/play            → play / resume
POST /api/pause           → pause
POST /api/stop            → stop
POST /api/next            → next track
POST /api/prev            → previous track
POST /api/volume          → body: {"volume": 0-100}
POST /api/seek            → body: {"position": seconds}
POST /api/queue/add       → body: {"track_id": "...", "album_key": "..."}
POST /api/queue/clear     → clear pending queue
"""

import base64
import io
import json
import socket
import threading
import tkinter as tk
from http.server import BaseHTTPRequestHandler, HTTPServer
from tkinter import ttk
from urllib.parse import parse_qs, urlparse

from core.config import JukeboxModule, APP_VERSION
from ui.theme import (ACCENT, BG_CARD, BG_DARK, BG_PANEL, BORDER,
                      DANGER, FONT_BODY, FONT_SMALL, FONT_SUBHEAD,
                      FONT_TINY, PAD, SUCCESS, TEXT_DIM,
                      TEXT_PRIMARY, TEXT_SECONDARY, WARNING)

DEFAULT_PORT = 8080


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_local_ip() -> str:
    """Best-effort local IP (not loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# HTML — the entire single-page remote UI, inlined as a string
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>PulseBox</title>
<style>
:root{
  --bg:#0a0a0f;--panel:#12121c;--card:#1a1a2e;--card2:#232342;
  --accent:#e8b004;--neon:#4fc3f7;--danger:#ff4444;--success:#00cc88;
  --pink:#ff2d78;--text:#ffffff;--dim:#888;--border:#2a2a40;
  --radius:12px;
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;overflow:hidden}
#nav{display:flex;background:#000;border-bottom:1px solid var(--border);height:52px;flex-shrink:0}
.nav-btn{flex:1;border:none;background:#4fc3f7;color:#0a0a0f;font-size:12px;font-weight:600;cursor:pointer;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  border-right:1px solid #000;transition:opacity .15s}
.nav-btn:last-child{border-right:none}
.nav-btn.active{background:var(--accent)}
.nav-btn .ico{font-size:18px;line-height:1}
#app{display:flex;flex-direction:column;height:100vh}
#pages{flex:1;overflow:hidden;position:relative}
.page{position:absolute;inset:0;display:none;flex-direction:column;overflow:hidden}
.page.active{display:flex}
.scroll-area{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.scroll-area::-webkit-scrollbar{width:3px}
.scroll-area::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
/* now playing */
#now-wrap{padding:16px;background:var(--panel);border-bottom:1px solid var(--border);flex-shrink:0}
#now-art{width:min(240px,60vw);height:min(240px,60vw);margin:0 auto 12px;
  background:var(--card);border-radius:14px;overflow:hidden;
  display:flex;align-items:center;justify-content:center;font-size:56px;
  border:1px solid var(--border)}
#now-art img{width:100%;height:100%;object-fit:cover}
#now-info{text-align:center;margin-bottom:10px}
#now-title{font-size:17px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#now-artist{font-size:13px;color:var(--accent);margin-top:2px}
#now-album{font-size:11px;color:var(--dim);margin-top:1px}
#seek-wrap{padding:0 4px}
#seek{width:100%;height:4px;-webkit-appearance:none;appearance:none;background:var(--border);border-radius:2px;outline:none;cursor:pointer}
#seek::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--accent)}
#seek-times{display:flex;justify-content:space-between;font-size:10px;color:var(--dim);margin-top:2px}
#transport{display:flex;align-items:center;justify-content:center;gap:10px;padding:10px 0}
.tp{background:var(--card);border:1px solid var(--border);color:var(--text);
  width:50px;height:50px;border-radius:50%;font-size:18px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:background .1s}
.tp:active{background:var(--card2)}
#tp-play{background:var(--accent);color:#000;width:62px;height:62px;font-size:24px;border-color:var(--accent)}
#tp-stop{color:var(--danger);border-color:var(--danger)}
#vol-wrap{display:flex;align-items:center;gap:8px;padding:0 4px}
#vol{flex:1;height:4px;-webkit-appearance:none;appearance:none;background:var(--border);border-radius:2px;outline:none;cursor:pointer}
#vol::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--neon)}
#vol-val{font-size:11px;color:var(--dim);width:34px;text-align:right}
/* search */
.srch{padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--border);flex-shrink:0}
.srch input{width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);
  padding:7px 12px;border-radius:20px;font-size:14px;outline:none}
.srch input:focus{border-color:var(--accent)}
/* tree */
.tree-hdr{display:flex;align-items:center;gap:10px;padding:10px 14px;
  background:var(--panel);border-bottom:1px solid var(--border);cursor:pointer;user-select:none}
.tree-hdr:active{background:var(--card)}
.t-art{width:46px;height:46px;border-radius:8px;background:var(--card);flex-shrink:0;
  overflow:hidden;display:flex;align-items:center;justify-content:center;font-size:20px}
.t-art img{width:100%;height:100%;object-fit:cover}
.t-meta{flex:1;overflow:hidden}
.t-name{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t-sub{font-size:11px;color:var(--dim);margin-top:1px}
.t-chev{font-size:18px;color:var(--dim);transition:transform .2s}
.tree-hdr.open .t-chev{transform:rotate(90deg)}
.tree-body{display:none;background:var(--bg)}
.tree-body.open{display:block}
.alb-actions{display:flex;gap:8px;padding:8px 14px;border-bottom:1px solid var(--border)}
.alb-actions button{flex:1;padding:7px;border:none;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer}
.btn-play-all{background:var(--accent);color:#000}
.btn-queue-all{background:var(--card);color:var(--accent);border:1px solid var(--accent)!important}
/* track */
.tr{display:flex;align-items:center;gap:8px;padding:9px 14px 9px 24px;
  border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s}
.tr:active{background:var(--card)}
.tr.playing{border-left:3px solid var(--accent);padding-left:21px}
.tr-no{font-size:11px;color:var(--dim);width:18px;text-align:right;flex-shrink:0}
.tr-info{flex:1;overflow:hidden}
.tr-title{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tr-dur{font-size:11px;color:var(--dim);flex-shrink:0}
.tr-add{background:var(--card);border:1px solid var(--border);color:var(--accent);
  padding:3px 9px;border-radius:14px;font-size:11px;cursor:pointer}
/* radio */
.r-row{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s}
.r-row:active{background:var(--card2)}
.r-row.playing{border-left:3px solid var(--pink);padding-left:11px}
.r-ico{width:42px;height:42px;border-radius:8px;background:var(--card);
  display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.r-info{flex:1;overflow:hidden}
.r-name{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r-genre{font-size:11px;color:var(--dim);margin-top:1px}
.r-play{background:var(--pink);border:none;color:#fff;padding:5px 12px;border-radius:14px;font-size:12px;cursor:pointer}
/* video */
.v-row{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border-bottom:1px solid var(--border);cursor:pointer}
.v-thumb{width:76px;height:48px;border-radius:7px;background:var(--card);
  display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0;overflow:hidden}
.v-thumb img{width:100%;height:100%;object-fit:cover}
.v-info{flex:1;overflow:hidden}
.v-title{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-meta{font-size:11px;color:var(--dim);margin-top:2px}
.v-play{background:var(--neon);border:none;color:#000;padding:5px 12px;border-radius:14px;font-size:12px;font-weight:600;cursor:pointer}
/* page header */
.pg-hdr{padding:10px 14px;background:var(--panel);border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.pg-hdr h2{font-size:15px;font-weight:700;color:var(--accent)}
.pg-hdr .ct{font-size:11px;color:var(--dim)}
/* power */
.pw-row{display:flex;gap:12px;padding:16px}
.pw-btn{flex:1;padding:14px;border:none;border-radius:var(--radius);font-size:14px;font-weight:700;cursor:pointer}
.pw-btn:active{opacity:.7}
#btn-reboot{background:var(--card);color:var(--accent);border:1px solid var(--accent)}
#btn-shutdown{background:var(--danger);color:#fff;border:none}
.empty,.loading{padding:32px 16px;text-align:center;color:var(--dim);font-size:14px}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);
  background:var(--accent);color:#000;padding:7px 18px;border-radius:18px;
  font-size:13px;font-weight:600;opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}
#toast.show{opacity:1}
</style>
</head>
<body>
<div id="app">
<div id="nav">
  <button class="nav-btn active" onclick="showPage('music',this)">
    <span class="ico">🎵</span><span>Music</span>
  </button>
  <button class="nav-btn" onclick="showPage('radio',this)">
    <span class="ico">📻</span><span>Radio</span>
  </button>
  <button class="nav-btn" onclick="showPage('video',this)">
    <span class="ico">🎬</span><span>Video</span>
  </button>
  <button class="nav-btn" onclick="showPage('power',this)">
    <span class="ico">⏻</span><span>Power</span>
  </button>
</div>
<div id="pages">

  <!-- MUSIC -->
  <div id="page-music" class="page active" style="overflow-y:auto;-webkit-overflow-scrolling:touch">
    <div id="now-wrap">
      <div id="now-art">♪</div>
      <div id="now-info">
        <div id="now-title">Nothing playing</div>
        <div id="now-artist"></div>
        <div id="now-album"></div>
      </div>
      <div id="seek-wrap">
        <input type="range" id="seek" min="0" max="100" value="0" oninput="onSeek(this)">
        <div id="seek-times"><span id="pos-lbl">0:00</span><span id="dur-lbl">0:00</span></div>
      </div>
      <div id="transport">
        <button class="tp" onclick="cmd('prev')">⏮</button>
        <button class="tp" id="tp-stop" onclick="cmd('stop')">⏹</button>
        <button class="tp" id="tp-play" onclick="togglePlay()">▶</button>
        <button class="tp" onclick="cmd('next')">⏭</button>
      </div>
      <div id="vol-wrap">
        <span style="font-size:16px;color:var(--dim)">🔈</span>
        <input type="range" id="vol" min="0" max="100" value="80" oninput="onVol(this)">
        <span id="vol-val">80%</span>
      </div>
    </div>
    <div class="srch">
      <input type="search" id="music-search" placeholder="Search albums or artists…" oninput="filterAlbums(this.value)">
    </div>
    <div id="album-tree"></div>
  </div>

  <!-- RADIO -->
  <div id="page-radio" class="page" style="overflow-y:auto;-webkit-overflow-scrolling:touch">
    <div class="pg-hdr"><h2>📻 Internet Radio</h2><span class="ct" id="radio-ct"></span></div>
    <div class="srch">
      <input type="search" id="radio-search" placeholder="Search stations…" oninput="filterRadio(this.value)">
    </div>
    <div id="radio-list" style="overflow-y:auto;flex:1;-webkit-overflow-scrolling:touch">
      <div class="loading">Loading stations…</div>
    </div>
  </div>

  <!-- VIDEO -->
  <div id="page-video" class="page" style="overflow-y:auto;-webkit-overflow-scrolling:touch">
    <div class="pg-hdr"><h2>🎬 Video Library</h2><span class="ct" id="video-ct"></span></div>
    <div id="video-list" style="overflow-y:auto;flex:1;-webkit-overflow-scrolling:touch">
      <div class="loading">Loading…</div>
    </div>
  </div>

  <!-- POWER -->
  <div id="page-power" class="page">
    <div class="pg-hdr"><h2>⏻ System</h2></div>
    <div class="pw-row">
      <button class="pw-btn" id="btn-reboot" onclick="sysAction('reboot')">↺ Reboot</button>
      <button class="pw-btn" id="btn-shutdown" onclick="sysAction('shutdown')">⏻ Shutdown</button>
    </div>
    <p style="padding:0 16px;font-size:12px;color:var(--dim)">
      These actions will immediately reboot or shut down the Pi.
    </p>
  </div>

</div><!-- #pages -->
</div><!-- #app -->
<div id="toast"></div>
<script>
let _albums=[],_radio=[],_videos=[];
let _playing=false,_seekDrag=false,_volDrag=false;

// ── Nav ────────────────────────────────────────────────────────────────────
function showPage(id,btn){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  btn.classList.add('active');
  if(id==='music'&&!_albums.length)loadAlbums();
  if(id==='radio'&&!_radio.length)loadRadio();
  if(id==='video'&&!_videos.length)loadVideo();
}

// ── HTTP ───────────────────────────────────────────────────────────────────
async function get(u){try{return await(await fetch(u)).json()}catch{return {}}}
async function post(u,b={}){
  try{return await(await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json()}
  catch{return{}}
}
async function cmd(a,b={}){return post('/api/'+a,b)}

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2000)}

// ── Utils ──────────────────────────────────────────────────────────────────
function fmt(s){s=Math.floor(s||0);return`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function eq(s){return String(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'")}

// ── Poll ───────────────────────────────────────────────────────────────────
async function poll(){
  const d=await get('/api/status');
  if(!d.state)return;
  _playing=d.state==='playing';
  document.getElementById('tp-play').textContent=_playing?'⏸':'▶';
  document.getElementById('now-title').textContent=d.title||'Nothing playing';
  document.getElementById('now-artist').textContent=d.artist||'';
  document.getElementById('now-album').textContent=d.album||'';
  if(!_seekDrag){
    const pct=d.duration>0?(d.position/d.duration)*100:0;
    document.getElementById('seek').value=pct;
    document.getElementById('pos-lbl').textContent=fmt(d.position);
    document.getElementById('dur-lbl').textContent=fmt(d.duration);
  }
  if(!_volDrag){
    document.getElementById('vol').value=d.volume;
    document.getElementById('vol-val').textContent=d.volume+'%';
  }
  const el=document.getElementById('now-art');
  if(d.art_key){
    const url='/api/art/'+encodeURIComponent(d.art_key);
    if(el.dataset.key!==d.art_key){
      el.dataset.key=d.art_key;
      el.innerHTML=`<img src="${url}" onerror="this.parentNode.textContent='♪'">`;
    }
  } else if(el.dataset.key){el.dataset.key='';el.textContent='♪'}
}
setInterval(poll,2000);poll();

// ── Transport ──────────────────────────────────────────────────────────────
async function togglePlay(){await cmd(_playing?'pause':'play');setTimeout(poll,300)}
function onSeek(el){
  _seekDrag=true;
  el.addEventListener('pointerup',async()=>{
    const d=await get('/api/status');
    await post('/api/seek',{position:(el.value/100)*(d.duration||0)});
    _seekDrag=false;setTimeout(poll,300);
  },{once:true});
}
function onVol(el){
  _volDrag=true;
  document.getElementById('vol-val').textContent=el.value+'%';
  el.addEventListener('pointerup',async()=>{
    await post('/api/volume',{volume:parseInt(el.value)});_volDrag=false;
  },{once:true});
}

// ── Music tree ─────────────────────────────────────────────────────────────
async function loadAlbums(){
  document.getElementById('album-tree').innerHTML='<div class="loading">Loading library…</div>';
  const d=await get('/api/library');
  _albums=d.albums||[];
  renderTree(_albums);
}
function renderTree(albums){
  const el=document.getElementById('album-tree');
  if(!albums.length){el.innerHTML='<div class="empty">No albums found</div>';return}
  el.innerHTML=albums.map((a,i)=>`
    <div id="alb-${i}" style="border-bottom:1px solid var(--border)">
      <div class="tree-hdr" onclick="toggleAlb(${i},'${eq(a.key)}')">
        <div class="t-art" id="tart-${i}">💿</div>
        <div class="t-meta">
          <div class="t-name">${esc(a.name)}</div>
          <div class="t-sub">${esc(a.artist)} · ${a.track_count} tracks</div>
        </div>
        <span class="t-chev">›</span>
      </div>
      <div class="tree-body" id="tbody-${i}"></div>
    </div>`).join('');
}
async function toggleAlb(i,key){
  const hdr=document.querySelector(`#alb-${i} .tree-hdr`);
  const body=document.getElementById('tbody-'+i);
  const wasOpen=hdr.classList.contains('open');
  document.querySelectorAll('.tree-hdr.open').forEach(h=>{
    h.classList.remove('open');
    h.nextElementSibling.classList.remove('open');
  });
  if(!wasOpen){
    hdr.classList.add('open');body.classList.add('open');
    if(!body.dataset.loaded){
      body.innerHTML='<div class="loading">Loading tracks…</div>';
      const d=await get('/api/album/'+encodeURIComponent(key));
      const alb=d.album||{};body.dataset.loaded='1';
      const tracks=alb.tracks||[];
      body.innerHTML=`
        <div class="alb-actions">
          <button class="alb-actions button btn-play-all" onclick="playAll('${eq(key)}')">▶ Play All</button>
          <button class="alb-actions button btn-queue-all" onclick="queueAll('${eq(key)}')">+ Queue All</button>
        </div>
        ${tracks.map((t,ti)=>`
          <div class="tr" onclick="playTr('${eq(t.id)}','${eq(key)}')">
            <span class="tr-no">${t.track_no||ti+1}</span>
            <div class="tr-info"><div class="tr-title">${esc(t.title)}</div></div>
            <span class="tr-dur">${fmt(t.duration||0)}</span>
            <button class="tr-add" onclick="event.stopPropagation();queueTr('${eq(t.id)}','${eq(key)}')">+</button>
          </div>`).join('')}`;
      // Load art into thumbnail — only when album is first opened
      const thumb=document.getElementById('tart-'+i);
      if(thumb){const img=new Image();img.src='/api/art/'+encodeURIComponent(key);
        img.onload=()=>{thumb.innerHTML='';thumb.appendChild(img)}}
    }
  }
}
function filterAlbums(q){
  q=q.toLowerCase();
  renderTree(q?_albums.filter(a=>a.name.toLowerCase().includes(q)||a.artist.toLowerCase().includes(q)):_albums);
}
async function playTr(id,key){
  await post('/api/queue/clear');await post('/api/queue/add',{track_id:id,album_key:key});
  await cmd('play');toast('Playing track');setTimeout(poll,500);
}
async function playAll(key){
  const d=await get('/api/album/'+encodeURIComponent(key));
  const tracks=(d.album||{}).tracks||[];
  await post('/api/queue/clear');
  for(const t of tracks)await post('/api/queue/add',{track_id:t.id,album_key:key});
  await cmd('play');toast('Playing album');setTimeout(poll,500);
}
async function queueAll(key){
  const d=await get('/api/album/'+encodeURIComponent(key));
  const tracks=(d.album||{}).tracks||[];
  for(const t of tracks)await post('/api/queue/add',{track_id:t.id,album_key:key});
  toast(tracks.length+' tracks queued');
}
async function queueTr(id,key){
  await post('/api/queue/add',{track_id:id,album_key:key});toast('Track queued');
}

// ── Radio ──────────────────────────────────────────────────────────────────
async function loadRadio(){
  document.getElementById('radio-list').innerHTML='<div class="loading">Loading…</div>';
  const d=await get('/api/radio');_radio=d.stations||[];
  document.getElementById('radio-ct').textContent=_radio.length+' stations';
  renderRadio(_radio);
}
function renderRadio(st){
  const el=document.getElementById('radio-list');
  if(!st.length){el.innerHTML='<div class="empty">No stations found</div>';return}
  el.innerHTML=st.map(s=>`
    <div class="r-row" onclick="playRadio('${eq(s.url)}','${eq(s.name)}')">
      <div class="r-ico">📻</div>
      <div class="r-info">
        <div class="r-name">${esc(s.name)}</div>
        <div class="r-genre">${esc(s.genre||'')}</div>
      </div>
      <button class="r-play" onclick="event.stopPropagation();playRadio('${eq(s.url)}','${eq(s.name)}')">▶</button>
    </div>`).join('');
}
function filterRadio(q){
  q=q.toLowerCase();
  renderRadio(q?_radio.filter(s=>s.name.toLowerCase().includes(q)||(s.genre||'').toLowerCase().includes(q)):_radio);
}
async function playRadio(url,name){
  await post('/api/radio/play',{url,name});toast('Playing '+name);setTimeout(poll,500);
}

// ── Video ──────────────────────────────────────────────────────────────────
async function loadVideo(){
  document.getElementById('video-list').innerHTML='<div class="loading">Loading…</div>';
  const d=await get('/api/videos');_videos=d.videos||[];
  document.getElementById('video-ct').textContent=_videos.length+' videos';
  renderVideo(_videos);
}
function renderVideo(vids){
  const el=document.getElementById('video-list');
  if(!vids.length){el.innerHTML='<div class="empty">No videos found</div>';return}
  el.innerHTML=vids.map(v=>`
    <div class="v-row" onclick="playVideo('${eq(v.path)}','${eq(v.title)}')">
      <div class="v-thumb">${v.has_poster
        ?`<img src="/api/video-art/${encodeURIComponent(v.path)}" onerror="this.parentNode.textContent='🎬'">`
        :'🎬'}</div>
      <div class="v-info">
        <div class="v-title">${esc(v.title)}</div>
        <div class="v-meta">${v.size_mb?v.size_mb+' MB':''}</div>
      </div>
      <button class="v-play" onclick="event.stopPropagation();playVideo('${eq(v.path)}','${eq(v.title)}')">▶</button>
    </div>`).join('');
}
async function playVideo(path,title){
  await post('/api/video/play',{path});toast('Playing '+title);
}

// ── Power ──────────────────────────────────────────────────────────────────
async function sysAction(a){
  if(!confirm(a==='shutdown'?'Shut down PulseBox now?':'Reboot PulseBox now?'))return;
  await post('/api/system/'+a);toast(a==='shutdown'?'Shutting down…':'Rebooting…');
}
</script>
</body>
</html>"""



# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """Handles all incoming HTTP requests for the web remote."""

    module: "WebRemoteModule" = None   # set by server factory

    # Silence access log — too noisy in PulseBox console
    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        pass

    # ── Routing ──────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send(200, "text/html; charset=utf-8", _HTML.encode())
        elif path == "/api/status":
            self._json(self._status())
        elif path == "/api/now":
            self._json(self._now())
        elif path == "/api/queue":
            self._json(self._queue())
        elif path == "/api/library":
            self._json(self._library())
        elif path.startswith("/api/art/"):
            album_key = path[len("/api/art/"):]
            self._serve_art(album_key)
        elif path.startswith("/api/album/"):
            album_key = path[len("/api/album/"):]
            self._json(self._album_detail(album_key))
        elif path == "/api/radio":
            self._json(self._radio_stations())
        elif path == "/api/videos":
            self._json(self._videos())
        elif path.startswith("/api/video-art/"):
            video_path = path[len("/api/video-art/"):]
            self._serve_video_art(video_path)
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")
        body   = self._read_body()

        player   = self.module.player
        playlist = self.module.app.playlist

        if path == "/api/play":
            player.play()
            self._json({"ok": True})
        elif path == "/api/pause":
            player.pause()
            self._json({"ok": True})
        elif path == "/api/stop":
            player.stop()
            # Navigate the app UI to the playlist page on the Tkinter thread
            app = self.module.app
            if hasattr(app, "root") and hasattr(app, "show_page"):
                app.root.after(0, lambda: app.show_page("playlist"))
            self._json({"ok": True})
        elif path == "/api/next":
            player.next_track()
            self._json({"ok": True})
        elif path == "/api/prev":
            player.previous_track()
            self._json({"ok": True})
        elif path == "/api/volume":
            vol = int(body.get("volume", player.get_volume()))
            player.set_volume(vol)
            self._json({"ok": True, "volume": vol})
        elif path == "/api/seek":
            pos = float(body.get("position", 0))
            player.seek(pos)
            self._json({"ok": True})
        elif path == "/api/queue/add":
            track_id  = body.get("track_id", "")
            album_key = body.get("album_key", "")
            result    = self._add_to_queue(track_id, album_key)
            self._json(result)
        elif path == "/api/queue/clear":
            playlist.clear_selections()
            self._json({"ok": True})
        elif path == "/api/radio/play":
            url  = body.get("url", "")
            name = body.get("name", "Radio")
            if url:
                player.play_url(url, title=name)
            self._json({"ok": bool(url)})
        elif path == "/api/video/play":
            video_path = body.get("path", "")
            app = self.module.app
            if video_path and hasattr(app, "root"):
                app.root.after(0, lambda p=video_path: self._play_video_on_ui(p))
            self._json({"ok": bool(video_path)})
        elif path == "/api/system/reboot":
            import subprocess, threading
            threading.Thread(
                target=lambda: subprocess.run(["sudo", "reboot"], timeout=10),
                daemon=True).start()
            self._json({"ok": True})
        elif path == "/api/system/shutdown":
            import subprocess, threading
            threading.Thread(
                target=lambda: subprocess.run(
                    ["sudo", "shutdown", "-h", "now"], timeout=10),
                daemon=True).start()
            self._json({"ok": True})
        else:
            self._send(404, "text/plain", b"Not found")

    # ── API builders ─────────────────────────────────────────────────

    def _status(self) -> dict:
        player = self.module.player
        track  = player.get_current_track() or {}
        state  = player.get_state()
        pos    = player.get_position()
        dur    = player.get_duration()

        # Build an art_key the browser can use to fetch the art.
        # Use artist::album format — _get_art_blob_by_key uses this to
        # look up art by artist+album match across all albums.
        art_key = ""
        if track:
            artist = track.get("artist", "")
            album  = track.get("album", "")
            if artist or album:
                art_key = f"{artist}::{album}"

        return {
            "state":    state,
            "title":    track.get("title", ""),
            "artist":   track.get("artist", ""),
            "album":    track.get("album", ""),
            "art_key":  art_key,
            "position": round(pos, 1),
            "duration": round(dur, 1),
            "volume":   player.get_volume(),
            "queue_len": self.module.app.playlist.selection_count,
        }

    def _now(self) -> dict:
        d = self._status()
        # Embed art as base64 for the now-playing detail view
        player = self.module.player
        track  = player.get_current_track() or {}
        if track:
            blob = self._get_art_blob_for_track(track)
            if blob:
                d["art_b64"] = base64.b64encode(blob).decode()
        return d

    def _queue(self) -> dict:
        sels = self.module.app.playlist.pending_selections
        return {
            "queue": [
                {
                    "title":  s.track.get("title", ""),
                    "artist": s.track.get("artist", ""),
                    "album":  s.track.get("album", ""),
                }
                for s in sels
            ]
        }

    def _library(self) -> dict:
        library = self.module.library
        albums  = library.get_all_albums()
        out = []
        for alb in albums:
            tracks = alb.get("tracks", [])
            out.append({
                "key":         alb.get("key", ""),
                "name":        alb.get("name", ""),
                "artist":      alb.get("artist", ""),
                "track_count": len(tracks),
                "tracks": [
                    {
                        "id":        t.get("id", ""),
                        "title":     t.get("title", ""),
                        "artist":    t.get("artist", ""),
                        "album":     t.get("album", ""),
                        "track_num": self._parse_tracknum(t.get("tracknumber", "0")),
                        "duration":  t.get("duration", 0),
                    }
                    for t in sorted(
                        tracks,
                        key=lambda t: self._parse_tracknum(t.get("tracknumber", "0")))
                ],
            })
        return {"albums": out, "total": len(out)}

    def _album_detail(self, album_key: str) -> dict:
        """Return full album detail including all tracks."""
        from urllib.parse import unquote
        album_key = unquote(album_key)
        lib = self.module.app.library
        albums = lib.get_all_albums()
        for alb in albums:
            if alb.get("key") == album_key:
                tracks = []
                for t in alb.get("tracks", []):
                    # Track number is stored as "tracknumber" string e.g. "3/12"
                    raw_no = t.get("tracknumber", "0") or "0"
                    try:
                        track_no = int(str(raw_no).split("/")[0])
                    except (ValueError, TypeError):
                        track_no = 0
                    tracks.append({
                        "id":       t.get("id", ""),
                        "title":    t.get("title", ""),
                        "track_no": track_no,
                        "duration": round(float(t.get("duration", 0) or 0), 1),
                    })
                tracks.sort(key=lambda x: x.get("track_no") or 999)
                return {"album": {
                    "name":    alb.get("name", ""),
                    "artist":  alb.get("artist", ""),
                    "key":     album_key,
                    "tracks":  tracks,
                }}
        return {"album": {}}

    def _radio_stations(self) -> dict:
        """Return radio stations from the radio page's fallback list."""
        try:
            from ui.radio_page import FALLBACK_STREAMS
            stations = []
            for name, (url, genre) in FALLBACK_STREAMS.items():
                stations.append({"name": name, "url": url, "genre": genre})
            return {"stations": stations}
        except Exception as e:
            print(f"[WebRemote] Radio stations error: {e}")
            return {"stations": []}

    def _videos(self) -> dict:
        """Return the video library."""
        try:
            from ui.video_page import scan_video_library
            app = self.module.app
            video_dir = app.config.get("video_dir",
                        __import__("os").path.expanduser("~/Videos"))
            movies = scan_video_library(video_dir)
            result = []
            for m in movies:
                import os
                size_mb = 0
                try:
                    size_mb = round(os.path.getsize(m.get("path","")) / (1024*1024), 1)
                except Exception:
                    pass
                result.append({
                    "title":      m.get("title", ""),
                    "path":       m.get("path", ""),
                    "has_poster": bool(m.get("poster")),
                    "size_mb":    size_mb,
                })
            return {"videos": result}
        except Exception as e:
            print(f"[WebRemote] Videos error: {e}")
            return {"videos": []}

    def _serve_video_art(self, video_path: str):
        """Serve a video's poster.jpg as an image."""
        import os
        folder = os.path.dirname(video_path)
        for fname in ("poster.jpg", "poster.png", "folder.jpg"):
            p = os.path.join(folder, fname)
            if os.path.isfile(p):
                try:
                    with open(p, "rb") as f:
                        data = f.read()
                    mt = "image/jpeg" if p.endswith(".jpg") else "image/png"
                    self._send(200, mt, data)
                    return
                except Exception:
                    pass
        self._send(404, "text/plain", b"No poster")

    def _play_video_on_ui(self, path: str):
        """Play a video file via the VideoPage on the Tkinter thread."""
        try:
            app = self.module.app
            if hasattr(app, "show_page"):
                app.show_page("video")
            if hasattr(app, "_video_page"):
                app._video_page.play_file(path)
        except Exception as e:
            print(f"[WebRemote] Video play error: {e}")

    def _serve_art(self, album_key: str):
        from urllib.parse import unquote
        album_key = unquote(album_key)
        """Serve album art as a JPEG image, or a 1x1 transparent PNG if none."""
        blob = self._get_art_blob_by_key(album_key)
        if blob:
            self._send(200, "image/jpeg", blob)
        else:
            # 1×1 transparent PNG — avoids broken-image icons in browser
            _empty = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00'
                      b'\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS'
                      b'\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00'
                      b'\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND'
                      b'\xaeB`\x82')
            self._send(200, "image/png", _empty)

    def _add_to_queue(self, track_id: str, album_key: str) -> dict:
        library  = self.module.library
        playlist = self.module.app.playlist

        # Look up the track from the library
        track = None
        if track_id:
            track = library.get_track(track_id)

        if not track and album_key:
            # Fallback: search by album_key
            for alb in library.get_all_albums():
                if alb.get("key") == album_key:
                    for t in alb.get("tracks", []):
                        if t.get("id") == track_id:
                            track = t
                            break
                    break

        if not track:
            return {"ok": False, "message": "Track not found"}

        ok, msg = playlist.add_selection(track)
        return {"ok": ok, "message": msg}

    # ── Art helpers ───────────────────────────────────────────────────

    def _get_art_blob_by_key(self, album_key: str) -> bytes | None:
        """
        Resolve album art blob from the library.
        album_key may be:
          - Full key: "artist::name::dir" (exact match)
          - Short key: "artist::album" (match by artist+name prefix)
        """
        library = self.module.library
        # Try exact key match first
        for alb in library.get_all_albums():
            if alb.get("key") == album_key:
                blob = alb.get("art")
                if isinstance(blob, (bytes, bytearray)):
                    return blob

        # Try artist::album prefix match (format used by web status endpoint)
        parts = album_key.split("::")
        if len(parts) >= 2:
            artist_q = parts[0].lower()
            album_q  = parts[1].lower()
            for alb in library.get_all_albums():
                if (alb.get("artist","").lower() == artist_q and
                        alb.get("name","").lower() == album_q):
                    blob = alb.get("art")
                    if isinstance(blob, (bytes, bytearray)):
                        return blob

        # Last resort: use library's get_art_blob (keyed by album_dir internally)
        if len(parts) >= 2:
            blob = library.get_art_blob(parts[0], parts[1])
            if blob:
                return blob

        return None

    def _get_art_blob_for_track(self, track: dict) -> bytes | None:
        library = self.module.library
        artist  = track.get("artist", "")
        album   = track.get("album", "")
        # Try the library's art cache (keyed by album_dir in library.py)
        blob = library.get_art_blob(artist, album)
        if blob:
            return blob
        return None

    # ── Low-level HTTP helpers ────────────────────────────────────────

    def _json(self, data: dict):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(200, "application/json", payload)

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Allow access from any origin (useful if testing from desktop)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            try:
                return json.loads(self.rfile.read(length))
            except Exception:
                pass
        return {}

    # ── Track number parser ───────────────────────────────────────────

    @staticmethod
    def _parse_tracknum(tn) -> int:
        try:
            return int(str(tn).split("/")[0])
        except (ValueError, TypeError):
            return 0


# ---------------------------------------------------------------------------
# Module class
# ---------------------------------------------------------------------------

class WebRemoteModule(JukeboxModule):

    MODULE_ID          = "web_remote"
    MODULE_NAME        = "Web Remote"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("HTTP server exposing a phone-friendly web remote. "
                          "Browse albums, control playback, and manage the queue "
                          "from any device on your network.")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._server:  HTTPServer | None = None
        self._thread:  threading.Thread | None = None
        self._port:    int  = DEFAULT_PORT
        self._running: bool = False

        # Tkinter StringVar refs (created in get_ui_panel)
        self._status_var:  tk.StringVar | None = None
        self._url_var:     tk.StringVar | None = None
        self._port_var:    tk.StringVar | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        self._port = int(self.config.get("web_remote_port", DEFAULT_PORT))
        self._start_server()

    def on_unload(self):
        self._stop_server()
        super().on_unload()

    # ------------------------------------------------------------------
    # Server management
    # ------------------------------------------------------------------

    def _start_server(self):
        if self._running:
            return

        # Build a handler class with a back-reference to this module
        module_ref = self

        class BoundHandler(_Handler):
            module = module_ref

        try:
            self._server  = HTTPServer(("", self._port), BoundHandler)
            self._running = True
            self._thread  = threading.Thread(
                target=self._server.serve_forever,
                daemon=True, name="PulseBox-WebRemote")
            self._thread.start()
            ip  = _get_local_ip()
            url = f"http://{ip}:{self._port}"
            print(f"[WebRemote] Listening on {url}")
            self._update_ui(f"● Running on {url}", url)
        except OSError as e:
            msg = f"Port {self._port} unavailable: {e}"
            print(f"[WebRemote] {msg}")
            self._update_ui(f"✗ {msg}", "")

    def _stop_server(self):
        if self._server:
            self._server.shutdown()
            self._server = None
        self._running = False
        self._update_ui("● Stopped", "")

    def _restart_server(self):
        self._stop_server()
        if self._port_var:
            try:
                self._port = int(self._port_var.get())
                self.config.set("web_remote_port", self._port)
            except ValueError:
                pass
        self._start_server()

    def _update_ui(self, status: str, url: str):
        if self._status_var:
            self._status_var.set(status)
        if self._url_var:
            self._url_var.set(url)

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        self._status_var = tk.StringVar(value="● Starting…")
        self._url_var    = tk.StringVar(value="")
        self._port_var   = tk.StringVar(value=str(self._port))

        frame = tk.Frame(parent, bg=BG_CARD, padx=PAD, pady=PAD)

        # Title row
        hdr = tk.Frame(frame, bg=BG_CARD)
        hdr.pack(fill="x", pady=(0, 12))
        tk.Label(hdr, text="🌐  Web Remote",
                 font=FONT_SUBHEAD, fg=ACCENT, bg=BG_CARD).pack(side="left")

        # Status
        tk.Label(frame, text="Status", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_CARD).pack(anchor="w")
        tk.Label(frame, textvariable=self._status_var,
                 font=FONT_BODY, fg=SUCCESS, bg=BG_CARD,
                 wraplength=400, justify="left").pack(anchor="w", pady=(0, 12))

        # URL (selectable)
        tk.Label(frame, text="URL", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_CARD).pack(anchor="w")
        url_entry = tk.Entry(frame, textvariable=self._url_var,
                             font=FONT_BODY, fg=ACCENT, bg=BG_DARK,
                             relief="flat", bd=4,
                             readonlybackground=BG_DARK, state="readonly",
                             width=36)
        url_entry.pack(anchor="w", pady=(0, 16))

        # Port setting
        port_row = tk.Frame(frame, bg=BG_CARD)
        port_row.pack(fill="x", pady=(0, 12))

        tk.Label(port_row, text="Port:", font=FONT_BODY,
                 fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        tk.Entry(port_row, textvariable=self._port_var,
                 font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK,
                 relief="flat", bd=4, width=7).pack(side="left", padx=8)
        tk.Button(port_row, text="Apply & Restart",
                  font=FONT_SMALL, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2",
                  command=self._restart_server).pack(side="left")

        # Network info
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=12)
        ip = _get_local_ip()
        tk.Label(frame,
                 text=f"This Pi's IP address: {ip}\n"
                      f"Open the URL above on any phone or computer\n"
                      f"connected to the same WiFi network.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                 justify="left").pack(anchor="w")

        # Stop / Start buttons
        btn_row = tk.Frame(frame, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(16, 0))

        tk.Button(btn_row, text="Stop",
                  font=FONT_SMALL, fg=TEXT_PRIMARY, bg=DANGER,
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                  command=self._stop_server).pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="Start",
                  font=FONT_SMALL, fg=BG_DARK, bg=SUCCESS,
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                  command=self._start_server).pack(side="left")

        # If server already running, push correct status into the new vars
        if self._running:
            url = f"http://{ip}:{self._port}"
            self._update_ui(f"● Running on {url}", url)
        else:
            self._update_ui("● Stopped", "")

        return frame

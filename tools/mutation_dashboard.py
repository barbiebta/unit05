"""Lightweight live review and prompt-steering page for the mutation cascade."""

import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

UNIT05_SOURCE = Path(os.environ.get("UNIT05_SOURCE", "/workspace/unit05"))
if not UNIT05_SOURCE.exists():
    UNIT05_SOURCE = Path(__file__).resolve().parent / "unit05"
sys.path.insert(0, str(UNIT05_SOURCE))

from unit05.living_prompt import (
    add_anchor_atoms,
    compiled_text,
    load_state as load_living_state,
    remove_atom,
    save_state as save_living_state,
    state_lock as living_state_lock,
    update_atom,
)

ROOT = Path("/workspace/ComfyUI/output/lumina_random_mutation_cascade")
STATE = ROOT / "dashboard_state.json"
LIVING_PROMPT = ROOT / "living_prompt.json"
CURRENT = ROOT / "CURRENT_MUTATION_CASCADE.mp4"
HOST = "127.0.0.1"
PORT = 8791


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instrumentality mutation cascade</title>
<style>
:root{color-scheme:light;--paper:#f3f0e9;--panel:#fff;--ink:#191817;--muted:#6c675f;--line:#bdb6ab;--fill:#ddd2bf;--hot:#cb473a}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--paper);color:var(--ink);font:14px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}
body{padding:18px}.shell{width:min(1500px,100%);margin:auto;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(310px,.7fr);gap:16px}
.panel{background:var(--panel);border:1px solid var(--line)}header{grid-column:1/-1;padding:14px 16px;display:flex;justify-content:space-between;align-items:baseline;gap:20px}
h1{font:750 22px/1 system-ui,sans-serif;letter-spacing:-.04em;margin:0}.muted{color:var(--muted)}.video-wrap{background:#111;aspect-ratio:7/4;display:grid;place-items:center}
.prompt-banner{grid-column:1/-1;padding:13px 16px}.prompt-label{color:var(--muted);font:700 11px/1 system-ui,sans-serif;letter-spacing:.09em;text-transform:uppercase;margin-bottom:7px}.prompt-text{font-size:15px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:8.5em;overflow:auto}.atom-head{display:flex;justify-content:space-between;align-items:baseline;margin-top:12px;border-top:1px solid var(--line);padding-top:10px}.atoms{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}.atom{display:flex;align-items:center;gap:5px;border:1px solid var(--line);background:#faf8f3;padding:4px 5px 4px 7px;max-width:100%}.atom.fermented{background:#eee7f1}.atom-text{overflow-wrap:anywhere}.atom-source{color:var(--muted);font-size:10px}.atom-remove{border:0;background:transparent;color:var(--muted);padding:1px 3px;font:700 14px/1 system-ui,sans-serif}.atom-remove:hover{color:var(--hot)}.atom-add{display:flex;gap:6px;margin-top:8px}.atom-add input{flex:1;min-width:0;border:1px solid var(--line);padding:7px 8px;font:inherit}.history{margin-top:9px}.history summary{cursor:pointer;color:var(--muted)}.history-list{display:grid;gap:6px;margin-top:7px}.history-item{border-top:1px solid var(--line);padding-top:6px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:5.8em;overflow:auto}.history-step{color:var(--muted);font-size:11px}
video{width:100%;height:100%;object-fit:contain;display:block}.status{padding:13px 15px;border-top:1px solid var(--line)}
.latest{border-top:1px solid var(--line);padding:12px 15px;display:grid;grid-template-columns:minmax(220px,420px) 1fr;gap:13px;align-items:start}.latest-video{aspect-ratio:7/4;background:#111}.latest-copy{color:var(--muted)}
.bar{height:12px;border:1px solid var(--line);background:#eee9df;margin:8px 0}.bar>div{height:100%;background:var(--fill);width:0;transition:width .25s}
.grid{display:grid;grid-template-columns:auto 1fr;gap:5px 12px}.grid dt{color:var(--muted)}.grid dd{margin:0;overflow-wrap:anywhere}
.steer{padding:15px}.steer h2{font:700 17px/1 system-ui,sans-serif;margin:0 0 6px}
button{border:1px solid #403c37;background:#282522;color:#fff;padding:9px 13px;font:700 13px/1 system-ui,sans-serif;cursor:pointer}button:disabled{opacity:.45}
.flash{color:var(--hot)}@media(max-width:850px){body{padding:0}.shell{grid-template-columns:1fr;gap:0}.panel,header{border-left:0;border-right:0}.video-wrap{aspect-ratio:7/4}.latest{grid-template-columns:1fr}.latest-video{max-width:420px}.steer{border-top:0}header{position:sticky;top:0;z-index:2}}
</style>
</head>
<body><div class="shell">
<header class="panel"><h1>Instrumentality · mutation cascade</h1><div id="clock" class="muted">connecting…</div></header>
<section class="panel prompt-banner">
  <div id="promptLabel" class="prompt-label">Current prompt</div><div id="promptText" class="prompt-text">loading…</div>
  <div class="atom-head"><strong>living fragments</strong><span id="atomCount" class="muted">0</span></div>
  <div id="atoms" class="atoms"></div>
  <div class="atom-add"><input id="newAtom" placeholder="add one or more fragments; separate with semicolons"><button id="addAtom" type="button">add</button></div>
  <details class="history" open><summary>recent generation prompts</summary><div id="promptHistory" class="history-list"></div></details>
</section>
<section class="panel">
  <div class="video-wrap"><video id="video" controls playsinline loop preload="metadata"></video></div>
  <div class="status">
    <div><strong id="headline">Waiting for first mutation</strong></div>
    <div class="bar"><div id="bar"></div></div>
    <dl class="grid">
      <dt>progress</dt><dd id="progress">0 / 40</dd>
      <dt>active hole</dt><dd id="block">—</dd>
      <dt>character references</dt><dd id="rabbit">—</dd>
      <dt>render status</dt><dd id="render">—</dd>
      <dt>temporal mode</dt><dd id="temporal">—</dd>
      <dt>prompt energy</dt><dd id="energy">—</dd>
      <dt>current version</dt><dd id="version">—</dd>
    </dl>
  </div>
  <div class="latest">
    <div class="latest-video"><video id="latestChunk" controls playsinline loop preload="metadata"></video></div>
    <div class="latest-copy"><strong>most recent finished chunk</strong><br>Raw 39-frame H3 output before it is inserted into the evolving full video.</div>
  </div>
</section>
<aside class="panel steer">
  <h2>Automatic temporal steering</h2>
  <p>The living prompt now chooses pacing deterministically for every hole.</p>
  <dl class="grid">
    <dt>−12 to −5</dt><dd>double slow</dd>
    <dt>−4 to −2</dt><dd>slow</dd>
    <dt>−1 to 2</dt><dd>no compression</dd>
    <dt>3 to 7</dt><dd>compressed motion</dd>
    <dt>8 to 12</dt><dd>compressed + inverted bookends</dd>
    <dt>matched cues</dt><dd id="energyCues">—</dd>
  </dl>
  <p class="muted">Change the fragments above to change both content and pacing. The player reloads after every completed hole and loops by default.</p>
</aside>
</div>
<script>
const $=id=>document.getElementById(id), video=$('video'), latestChunk=$('latestChunk'), newAtom=$('newAtom'), addAtom=$('addAtom');
let videoVersion=-1;
function renderLiving(state, status){
  const anchor=(state&&state.anchor_atoms)||[], fermented=(state&&state.fermented_atoms)||[], all=[...anchor,...fermented], box=$('atoms');
  box.replaceChildren();$('atomCount').textContent=`${all.length} fragments`;
  for(const atom of all){
    const row=document.createElement('span');row.className='atom'+(atom.source==='joycaption'?' fermented':'');
    const copy=document.createElement('span');copy.className='atom-text';copy.textContent=atom.text||'';copy.title='click to edit';
    copy.addEventListener('click',async()=>{const value=prompt('edit fragment',atom.text||'');if(value===null||!value.trim())return;await mutateAtom('api/atoms/update',{id:atom.id,text:value})});
    const source=document.createElement('span');source.className='atom-source';source.textContent=atom.source==='joycaption'?'joy':'';
    const remove=document.createElement('button');remove.className='atom-remove';remove.type='button';remove.textContent='×';remove.title='remove fragment';remove.addEventListener('click',()=>mutateAtom('api/atoms/remove',{id:atom.id}));
    row.append(copy,source,remove);box.append(row);
  }
  const history=[...((state&&state.history)||[])].reverse().slice(0,6), list=$('promptHistory');list.replaceChildren();
  for(const item of history){const row=document.createElement('div');row.className='history-item';const label=document.createElement('div');label.className='history-step';label.textContent=`generation ${item.step}`;const copy=document.createElement('div');copy.textContent=item.compiled_prompt||'';row.append(label,copy);list.append(row)}
  if(!history.length){const empty=document.createElement('div');empty.className='muted';empty.textContent='no recorded prompts yet';list.append(empty)}
  const active=status&&status.active_full_prompt;
  $('promptLabel').textContent=active?'Currently rendering prompt':'Current sticky prompt';
  $('promptText').textContent=active||(state&&state.compiled_text)||'(base prompt only)';
}
async function mutateAtom(path,payload){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),s=await r.json();
  if(!r.ok){alert(s.error||'fragment change failed');return}
  renderLiving(s,null);
}
async function refresh(){
  try{
    const r=await fetch('api/status',{cache:'no-store'}), s=await r.json();
    const done=s.completed_steps||0,total=s.total_steps;
    $('clock').textContent=new Date((s.updated_unix||Date.now()/1000)*1000).toLocaleTimeString();
    if(s.continuous||total==null){$('progress').textContent=`${done} complete · continuous`;$('bar').style.width=s.status==='rendering'?'100%':'0'}else{$('progress').textContent=`${done} / ${total} · ${(100*done/total).toFixed(1)}%`;$('bar').style.width=`${100*done/total}%`}
    $('block').textContent=s.active_hole_start==null?'—':`frame ${s.active_hole_start} (mutation ${s.active_step})`;
    $('rabbit').textContent=s.active_rabbit_reference||s.last_rabbit_reference||'—';
    $('render').textContent=s.status||'waiting';
    $('temporal').textContent=(s.active_temporal_mode||'normal').replaceAll('_',' ');
    $('energy').textContent=s.active_frantic_score==null?'—':`${s.active_frantic_score} · automatic`;
    $('energyCues').textContent=(s.active_frantic_cues||[]).join(' · ')||'none';
    renderLiving(s.living_prompt||{},s);
    $('version').textContent=s.video_version?`after ${s.video_version} mutation${s.video_version===1?'':'s'}`:'waiting';
    $('headline').textContent=s.status==='rendering'?`Rendering mutation ${s.active_step}`:(done?`Current evolving render · ${done} mutations`:'Waiting for first mutation');
    if(s.current_video&&s.video_version!==videoVersion){
      const t=video.currentTime||0,wasPlaying=!video.paused; videoVersion=s.video_version;
      video.src=`current.mp4?v=${videoVersion}`; video.loop=true;
      video.addEventListener('loadedmetadata',()=>{video.currentTime=Math.min(t,Math.max(0,video.duration-.05));if(wasPlaying)video.play().catch(()=>{})},{once:true});
      if(s.last_chunk_path){latestChunk.src=`last.mp4?v=${videoVersion}`;latestChunk.loop=true}
    }
  }catch(e){$('clock').textContent=`offline · ${e.message}`;$('clock').className='flash'}
}
addAtom.addEventListener('click',async()=>{const value=newAtom.value.trim();if(!value)return;addAtom.disabled=true;try{await mutateAtom('api/atoms/add',{text:value});newAtom.value=''}finally{addAtom.disabled=false}});
newAtom.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();addAtom.click()}});
refresh();setInterval(refresh,4000);
</script></body></html>"""


def read_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


class Handler(BaseHTTPRequestHandler):
    server_version = "InstrumentalityDashboard/1"

    def log_message(self, fmt, *args):
        print("dashboard:", fmt % args, flush=True)

    def send_bytes(self, body, content_type, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.send_bytes(
            json.dumps(value).encode("utf-8"), "application/json; charset=utf-8", status
        )

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            self.send_bytes(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            state = read_json(STATE, {"status": "waiting", "completed_steps": 0, "total_steps": None, "continuous": True})
            with living_state_lock(LIVING_PROMPT):
                living = load_living_state(LIVING_PROMPT)
            living["compiled_text"] = compiled_text(living)
            state["living_prompt"] = living
            self.send_json(state)
            return
        if path == "/current.mp4":
            self.send_file_range(CURRENT)
            return
        if path == "/last.mp4":
            state = read_json(STATE, {})
            candidate = Path(str(state.get("last_chunk_path") or ""))
            try:
                candidate.resolve().relative_to(ROOT.resolve())
            except (ValueError, OSError):
                self.send_error(404, "No completed chunk yet")
                return
            self.send_file_range(candidate)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path not in {"/api/atoms/add", "/api/atoms/update", "/api/atoms/remove"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                raise ValueError("request is too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            with living_state_lock(LIVING_PROMPT):
                living = load_living_state(LIVING_PROMPT)
                if path == "/api/atoms/add":
                    value = str(payload.get("text") or "").strip()
                    if not value or len(value) > 12000:
                        raise ValueError("fragment text is empty or too long")
                    add_anchor_atoms(living, value)
                elif path == "/api/atoms/update":
                    if not update_atom(living, payload.get("id"), payload.get("text", "")):
                        raise ValueError("fragment was not found or text is empty")
                elif not remove_atom(living, payload.get("id")):
                    raise ValueError("fragment was not found")
                save_living_state(LIVING_PROMPT, living)
            living["compiled_text"] = compiled_text(living)
            self.send_json(living)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def send_file_range(self, path):
        if not path.is_file():
            self.send_error(404, "No completed mutation yet")
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        status = 200
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
            if not match:
                self.send_error(416)
                return
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
            if start > end or start >= size:
                self.send_error(416)
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Instrumentality dashboard listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()



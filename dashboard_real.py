import base64
import hmac
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.environ.get("SPOTOPS_HOME", "/home/ubuntu")
CKPT = HOME + "/checkpoint_real.json"
MODEL = HOME + "/model_real.pkl"
RUNS = HOME + "/runs_real"
EVF = HOME + "/events_real.json"
KEY_PATH = HOME + "/spotops-aws-key-v2.pem"
DATA = HOME + "/dataset.csv"
DATA_NAME = HOME + "/dataset.name"
NOTICE = HOME + "/spot_notice.json"
PASSFILE = HOME + "/dash_pass.txt"
USER = "admin"
MAX_UPLOAD = 20 * 1024 * 1024
TOTAL = 20
PORT = 8001
NODES = {"spot": "16.176.137.75", "fallback": "13.210.203.78", "storage": "3.27.11.65"}
ST = {"last": None, "prev": "idle", "vm": None, "events": []}


def trigger_interrupt():
    key = ["-i", KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    try:
        subprocess.run(["ssh"] + key + ["ubuntu@%s" % NODES["spot"],
                        "pkill -f '[t]rain_real.py'"],
                        capture_output=True, text=True, timeout=10)
        subprocess.run(["ssh"] + key + ["ubuntu@%s" % NODES["fallback"],
                        "nohup python3 -u train_real.py fallback > train_real.log 2>&1 &"],
                        capture_output=True, text=True, timeout=10)
    except Exception as e:
        return False, "Could not reach a VM over SSH: %s" % e
    add_event("alert", "Manual interruption triggered on the spot VM; fallback VM started")
    return True, "Spot VM interrupted. The fallback VM is now resuming the real model from the last saved checkpoint."


def trigger_notice():
    key = ["-i", KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    try:
        subprocess.run(["ssh"] + key + ["ubuntu@%s" % NODES["spot"], "touch ~/fake_notice"],
                        capture_output=True, text=True, timeout=10)
    except Exception as e:
        return False, "Could not reach the spot VM over SSH: %s" % e
    return True, ("Warning delivered. The spot VM will finish its current epoch, save to the storage VM and S3, "
                  "then start the fallback VM (takes a few seconds).")


def dataset_info():
    try:
        with open(DATA_NAME) as f:
            name = f.read().strip()
    except OSError:
        name = ""
    return {"selected": name if name and os.path.exists(DATA) else None}


def save_dataset(name, data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "That file is not a UTF-8 text CSV."
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 20 or "," not in lines[0]:
        return False, "CSV needs at least 20 rows and comma-separated columns (last column = label)."
    with open(DATA, "wb") as f:
        f.write(data)
    with open(DATA_NAME, "w") as f:
        f.write(name)
    add_event("info", "Dataset uploaded from the dashboard: %s (%d rows)" % (name, len(lines)))
    return True, "Uploaded %s (%d rows). Click Reset & Start New Run to train on it." % (name, len(lines))


def clear_dataset():
    for f in (DATA, DATA_NAME):
        try:
            os.remove(f)
        except OSError:
            pass
    return True, "Switched back to the built-in Fashion-MNIST dataset. Click Reset & Start New Run to use it."


def trigger_reset():
    key = ["-i", KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    try:
        for ip in (NODES["spot"], NODES["fallback"]):
            subprocess.run(["ssh"] + key + ["ubuntu@%s" % ip,
                            "pkill -f '[t]rain_real.py'; "
                            "rm -f ~/checkpoint_real.json ~/model_real.pkl ~/train_real.log ~/fake_notice ~/spot_notice.json"],
                            capture_output=True, text=True, timeout=10)
        for f in (CKPT, MODEL, NOTICE, NOTICE + '.seen'):
            try:
                os.remove(f)
            except OSError:
                pass
        subprocess.run(["ssh"] + key + ["ubuntu@%s" % NODES["spot"],
                        "nohup python3 -u train_real.py spot --fresh > train_real.log 2>&1 &"],
                        capture_output=True, text=True, timeout=10)
    except Exception as e:
        return False, "Reset failed: could not reach one of the VMs (%s)." % e
    ST["prev"] = "idle"
    ST["vm"] = None
    add_event("info", "Real-dataset demo reset: checkpoints cleared, new run started on the spot VM")
    return True, "Reset complete. A fresh real training run has started on the spot VM."


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SpotOps - Real Example</title>
<style>
:root{--bg:#f3f5f9;--panel:#fff;--line:#e3e8ef;--tx:#111827;--mu:#6b7280;--nav:#0f1b2d;--acc:#2563eb;--ok:#16a34a;--bad:#dc2626;--fb:#ea580c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 "Segoe UI",system-ui,sans-serif;display:grid;grid-template-columns:220px 1fr;min-height:100vh}
nav{background:var(--nav);padding:18px 10px;position:sticky;top:0;height:100vh}
nav h1{font-size:20px;margin:0 8px 2px;color:#fff}
nav .tag{font-size:11px;color:#9fb0c8;font-weight:400;margin:0 8px 18px}
nav button{display:block;width:100%;text-align:left;background:none;border:0;border-left:3px solid transparent;color:#9fb0c8;font:inherit;padding:10px 12px;border-radius:0 8px 8px 0;cursor:pointer}
nav button.on{background:#1a2b47;color:#fff;border-left-color:var(--acc)}
nav button:focus-visible,input:focus-visible{outline:2px solid var(--acc)}
main{padding:24px;max-width:1040px}
h2{margin:0 0 16px;font-size:22px}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:14px}
.c{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;margin-bottom:14px}
.g .c{margin:0;border-left:5px solid var(--line)}
.c-blue{background:#eff6ff;border-color:#bfdbfe;border-left-color:#2563eb}.c-blue .v{color:#1d4ed8}
.c-orange{background:#fff7ed;border-color:#fed7aa;border-left-color:#ea580c}.c-orange .v{color:#c2410c}
.c-green{background:#f0fdf4;border-color:#bbf7d0;border-left-color:#16a34a}.c-green .v{color:#15803d}
.c-purple{background:#f5f3ff;border-color:#ddd6fe;border-left-color:#7c3aed}.c-purple .v{color:#6d28d9}
.c-teal{background:#f0fdfa;border-color:#99f6e4;border-left-color:#0d9488}.c-teal .v{color:#0f766e}
.c-red{background:#fef2f2;border-color:#fecaca;border-left-color:#dc2626}.c-red .v{color:#b91c1c}
.c-gray{background:#f8fafc;border-color:#e2e8f0;border-left-color:#64748b}.c-gray .v{color:#334155}
.l{color:#374151;font-size:13px;font-weight:600}
.v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
.b{display:inline-block;padding:2px 12px;border-radius:99px;font-weight:600;font-size:13px;background:#e5e7eb;color:#374151}
.running,.ok{background:#dcfce7;color:#166534}.complete{background:#e0e7ff;color:#3730a3}.stalled,.bad{background:#fee2e2;color:#991b1b}.spot{background:#dbeafe;color:#1e40af}.fallback{background:#ffedd5;color:#9a3412}.warn{background:#fef9c3;color:#854d0e}
.bar{height:10px;background:#e5e7eb;border-radius:5px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--ok);transition:width .5s}
.ban{display:none;padding:12px 16px;border-radius:8px;margin-bottom:14px;font-weight:600;border:1px solid #fdba74;background:#fff7ed;color:#9a3412}
.ban.bad{border-color:#fca5a5;background:#fef2f2;color:#991b1b}
canvas{width:100%;height:auto;display:block}
table{width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden}
td,th{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{color:var(--mu);font-weight:600;background:#eef2f7}
table tr:nth-child(even){background:#f8fafc}
table tr:hover{background:#eef4ff}
.ev{padding:8px 0;border-bottom:1px solid var(--line)}
.m{color:var(--mu)}
.sub{color:var(--mu);margin:-10px 0 16px;max-width:680px}
.demo{border-left-color:#dc2626!important}
.demo p{margin:4px 0 12px;color:#374151;max-width:640px}
button.a{background:var(--acc);border:0;color:#fff;padding:8px 14px;border-radius:6px;cursor:pointer;font:inherit}
button.danger{background:#dc2626}
button.a:disabled{opacity:.55;cursor:not-allowed}
#intmsg{margin-top:10px;font-weight:600}
.badge-real{display:inline-block;background:#166534;color:#fff;font-size:11px;font-weight:700;padding:2px 10px;border-radius:99px;margin-left:8px;vertical-align:middle}
@media(max-width:760px){body{grid-template-columns:1fr}nav{position:static;height:auto;display:flex;flex-wrap:wrap}nav h1{width:100%}nav button{width:auto}}
</style></head><body>
<nav><h1>SpotOps</h1><div class="tag">Real-dataset training example</div>
<button data-t="overview">Overview</button><button data-t="failover">Failover</button><button data-t="infra">Infrastructure</button>
<button data-t="runs">Run history</button><button data-t="events">Event log</button></nav>
<main>
<section id="t-overview"><h2>Overview<span class="badge-real">REAL DATA</span></h2>
<p class="sub">The real-model companion to the main SpotOps demo, on the same Spot / Fallback / Storage infrastructure. It trains an actual classifier on the Fashion-MNIST dataset (clothing images released by Zalando, the European e-commerce fashion company) or on any CSV you upload from your computer. Checkpoints are replicated to the storage VM and Amazon S3, metrics go to CloudWatch, and the job reacts to the 2-minute Spot interruption warning that AWS sends.</p>
<div class="c demo"><div class="l">Live demo control</div>
<p>1) "Reset &amp; Start New Run" trains a fresh model from scratch on the Spot VM. 2) "Simulate 2-minute Spot warning" feeds the Spot VM the same signal AWS sends before reclaiming it: it finishes the epoch, saves to the storage VM and S3, and hands over to the Fallback VM with nothing lost. 3) "Simulate Spot Interruption" is the hard-kill version: the process dies without warning and the Fallback VM resumes from the last checkpoint.</p>
<button class="a" id="reset" style="background:#0d9488;margin-right:10px">Reset &amp; Start New Run</button>
<button class="a" id="notice" style="background:#b45309;margin-right:10px">Simulate 2-minute Spot warning</button>
<button class="a danger" id="interrupt">Simulate Spot Interruption</button>
<div id="intmsg"></div></div>
<div id="ban" class="ban"></div>
<div class="g"><div class="c c-gray"><div class="l">Status</div><span id="st" class="b">idle</span></div>
<div class="c c-gray"><div class="l">Active VM</div><span id="vm" class="b">-</span></div>
<div class="c c-teal"><div class="l">Last checkpoint</div><div class="v" id="age">-</div></div>
<div class="c c-purple"><div class="l">Real test accuracy</div><div class="v" id="acc">-</div></div>
<div class="c c-blue"><div class="l">Time remaining</div><div class="v" id="eta">-</div></div>
<div class="c c-green"><div class="l">S3 backup</div><div class="v" id="s3">-</div></div></div>
<div class="c"><div class="l">Dataset</div>
<p class="m" style="margin:4px 0 10px">Next run trains on: <b id="dssel">-</b> &nbsp;|&nbsp; current run is using: <b id="dsrun">-</b></p>
<input type="file" id="dsfile" accept=".csv" style="width:auto;max-width:340px">
<button class="a" id="dsup">Upload &amp; use</button> <button class="a" id="dsclr" style="background:#64748b">Use built-in dataset</button>
<div class="m" style="margin-top:6px">CSV format: numeric feature columns, last column = label (2 to 50 classes, up to 20 MB). Then click "Reset &amp; Start New Run".</div>
<div id="dsmsg" style="margin-top:6px;font-weight:600"></div></div>
<div class="c"><div class="l">Training progress</div><div class="v" id="ep">0 / 20</div><div class="bar"><i id="fill" style="width:0"></i></div></div>
<div class="c"><div class="l">Real test-set accuracy per epoch, measured on held-out test data. Blue is the spot VM, orange is the fallback VM.</div><canvas id="ch" width="940" height="280"></canvas></div></section>

<section id="t-failover" hidden><h2>Failover</h2>
<div class="g"><div class="c c-orange"><div class="l">Failovers</div><div class="v" id="fn">0</div></div>
<div class="c c-red"><div class="l">Recovery time (RTO)</div><div class="v" id="rto">-</div></div>
<div class="c c-red"><div class="l">Epochs lost</div><div class="v" id="lost">-</div></div></div>
<div class="c"><div class="l">What "recovered" means here</div>The fallback VM doesn't retrain a new model -- it loads the exact saved model weights from the interrupted run and continues training that same model, so its accuracy curve picks up right where the spot VM left off.</div>
<div class="c"><div class="l">Handovers</div><table id="swt"></table></div></section>

<section id="t-infra" hidden><h2>Infrastructure</h2>
<div class="g" id="nodes"></div>
<div class="c"><div class="l">Storage VM health</div><div id="sys"></div></div></section>

<section id="t-runs" hidden><h2>Run history</h2><div class="c"><table id="rt"></table></div></section>

<section id="t-events" hidden><h2>Event log</h2><div class="c" id="evl"></div></section>
</main>
<script>
const $=i=>document.getElementById(i),COL={spot:'#2563eb',fallback:'#ea580c'};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const fmt=s=>s<60?Math.round(s)+' s':Math.floor(s/60)+' min '+Math.round(s%60)+' s';
const bc=v=>v==='spot'||v==='fallback'?v:'';
let S=null,tab='overview';
const get=async u=>(await fetch(u,{cache:'no-store'})).json();
const sw=h=>h.slice(1).map((b,i)=>[h[i],b]).filter(p=>p[0].vm!==p[1].vm).map(p=>({from:p[0].vm,to:p[1].vm,epoch:p[1].epoch,gap:p[1].time-p[0].time,lost:p[1].epoch-p[0].epoch-1}));
function draw(h){const c=$('ch'),g=c.getContext('2d'),W=c.width,H=c.height,L=44,R=16,T=16,B=30;g.clearRect(0,0,W,H);
const x=e=>L+(W-L-R)*(e-1)/19,y=a=>T+(H-T-B)*(1-a);g.font='12px sans-serif';g.textAlign='right';
for(let i=0;i<=4;i++){const yy=T+(H-T-B)*i/4;g.strokeStyle='#e5e7eb';g.beginPath();g.moveTo(L,yy);g.lineTo(W-R,yy);g.stroke();g.fillStyle='#6b7280';g.fillText((1-i/4).toFixed(2),L-6,yy+4)}
g.textAlign='center';[1,5,10,15,20].forEach(e=>g.fillText('epoch '+e,x(e),H-10));
if(!h.length){g.fillText('Waiting for the first checkpoint',W/2,H/2);return}
for(let i=1;i<h.length;i++){if(h[i].vm!==h[i-1].vm){const xv=(x(h[i].epoch)+x(h[i-1].epoch))/2;g.save();g.setLineDash([6,5]);g.strokeStyle='#ea580c';g.beginPath();g.moveTo(xv,T);g.lineTo(xv,H-B);g.stroke();g.restore();g.fillStyle='#ea580c';g.fillText('failover',xv,T+12)}
g.lineWidth=3;g.strokeStyle=COL[h[i].vm]||'#64748b';g.beginPath();g.moveTo(x(h[i-1].epoch),y(h[i-1].accuracy));g.lineTo(x(h[i].epoch),y(h[i].accuracy));g.stroke()}
h.forEach(p=>{g.fillStyle=COL[p.vm]||'#64748b';g.beginPath();g.arc(x(p.epoch),y(p.accuracy),4.5,0,7);g.fill()})}
function over(s){const h=s.history;$('st').textContent=s.state;$('st').className='b '+s.state;$('vm').textContent=s.vm;$('vm').className='b '+bc(s.vm);
$('age').textContent=s.age===null?'-':s.state==='complete'?'finished':fmt(s.age)+' ago';$('acc').textContent=s.epoch?(s.accuracy*100).toFixed(1)+'%':'-';
let e='-';if(s.state==='complete')e='done';else if(s.state==='running'&&h.length>2){const w=h.slice(-5);e='~'+fmt((w[w.length-1].time-w[0].time)/(w.length-1)*(s.total-s.epoch))}$('eta').textContent=e;
$('ep').textContent=s.epoch+' / '+s.total;$('fill').style.width=100*s.epoch/s.total+'%';
const f=sw(h),b=$('ban');if(s.state==='stalled'){b.className='ban bad';b.style.display='block';b.textContent='No checkpoint for '+fmt(s.age)+'. The training node may be down. Waiting for failover.'}
else if(f.length){const k=f[f.length-1];b.className='ban';b.style.display='block';b.textContent='Failover: '+k.from+' stopped, '+k.to+' resumed at epoch '+k.epoch+' after '+fmt(k.gap)+'. '+(k.lost===0?'No epochs were lost.':k.lost+' epoch(s) lost.')}else b.style.display='none';draw(h)}
function fail(s){const f=sw(s.history);$('fn').textContent=f.length;$('rto').textContent=f.length?fmt(Math.max(...f.map(k=>k.gap))):'-';$('lost').textContent=f.length?f.reduce((a,k)=>a+k.lost,0):'-';
$('swt').innerHTML='<tr><th>From</th><th>To</th><th>Resumed at</th><th>Gap</th><th>Epochs lost</th></tr>'+(f.map(k=>'<tr><td>'+esc(k.from)+'</td><td>'+esc(k.to)+'</td><td>epoch '+k.epoch+'</td><td>'+fmt(k.gap)+'</td><td>'+k.lost+'</td></tr>').join('')||'<tr><td colspan=5 class=m>No failover in this run yet.</td></tr>')}
function infra(d){$('nodes').innerHTML=Object.keys(d.nodes).map(k=>{const ms=d.nodes[k];const cc=k==='spot'?'c-blue':k==='fallback'?'c-orange':'c-green';return '<div class="c '+cc+'"><div class=l>'+k+' VM</div><span class="b '+(ms===null?'bad':'ok')+'">'+(ms===null?'unreachable':'reachable')+'</span><div class=m>'+d.ips[k]+(ms===null?'':' · '+ms+' ms')+'</div></div>'}).join('');
const row=(n,p,t)=>'<div style="margin:8px 0">'+n+': '+t+'<div class=bar><i style="width:'+p+'%"></i></div></div>',gb=x=>(x/1e9).toFixed(1)+' GB';
$('sys').innerHTML=row('Disk',100*d.disk[0]/d.disk[1],gb(d.disk[0])+' of '+gb(d.disk[1]))+row('Memory',100*d.mem[0]/d.mem[1],(d.mem[0]/1e6).toFixed(2)+' of '+(d.mem[1]/1e6).toFixed(2)+' MB')+'<div class=m>Load '+d.load.toFixed(2)+', up '+fmt(d.uptime)+'</div>'}
function runs(r){$('rt').innerHTML='<tr><th>Finished</th><th>Epochs</th><th>Accuracy</th><th>Failovers</th><th>Slowest recovery</th></tr>'+(r.map(x=>'<tr><td>'+new Date(x.end*1000).toLocaleString()+'</td><td>'+Object.entries(x.vms).map(e=>esc(e[0])+' '+e[1]).join(', ')+'</td><td>'+(x.accuracy*100).toFixed(1)+'%</td><td>'+x.failovers+'</td><td>'+(x.failovers?fmt(x.rto):'-')+'</td></tr>').join('')||'<tr><td colspan=5 class=m>Finished runs appear here and survive a reset.</td></tr>')}
function events(e){$('evl').innerHTML=e.map(x=>'<div class=ev><span class=m>'+new Date(x.t*1000).toLocaleTimeString()+'</span> <span class="b '+(x.kind==='alert'?'bad':x.kind==='failover'?'fallback':x.kind==='done'?'complete':'')+'">'+esc(x.kind)+'</span> '+esc(x.msg)+'</div>').join('')||'<span class=m>No events yet.</span>'}
async function load(){try{S=await get('/api/status');over(S);fail(S);extra(S);
if(tab==='infra')infra(await get('/api/infra'));if(tab==='runs')runs(await get('/api/runs'));if(tab==='events')events(await get('/api/events'))}catch(e){$('st').textContent='offline'}}
function show(){document.querySelectorAll('section').forEach(s=>s.hidden=s.id!=='t-'+tab);document.querySelectorAll('nav button').forEach(b=>b.className=b.dataset.t===tab?'on':'');load()}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{tab=b.dataset.t;show()});
$('interrupt').onclick=async()=>{
  if(!confirm('This stops the live training process on the Spot VM to demonstrate automatic failover of the real model. Continue?'))return;
  const btn=$('interrupt'),msg=$('intmsg');btn.disabled=true;btn.textContent='Sending interruption…';msg.textContent='';
  try{const r=await(await fetch('/api/interrupt',{method:'POST'})).json();msg.textContent=r.msg;msg.style.color=r.ok?'#15803d':'#b91c1c'}
  catch(e){msg.textContent='Request failed: '+e;msg.style.color='#b91c1c'}
  btn.disabled=false;btn.textContent='Simulate Spot Interruption';
};
$('reset').onclick=async()=>{
  if(!confirm('This clears old checkpoints on all three VMs and starts a brand new real training run. Continue?'))return;
  const btn=$('reset'),msg=$('intmsg');btn.disabled=true;btn.textContent='Resetting…';msg.textContent='';
  try{const r=await(await fetch('/api/reset',{method:'POST'})).json();msg.textContent=r.msg;msg.style.color=r.ok?'#15803d':'#b91c1c'}
  catch(e){msg.textContent='Request failed: '+e;msg.style.color='#b91c1c'}
  btn.disabled=false;btn.textContent='Reset & Start New Run';
};
function extra(s){const e=$('s3');e.textContent=s.epoch?s.s3:'-';e.style.color=s.s3==='ok'?'#15803d':s.s3==='error'?'#b91c1c':'#6b7280';$('dsrun').textContent=s.epoch&&s.dataset&&s.dataset!=='-'?s.dataset:'-'}
async function dsLoad(){try{const d=await get('/api/dataset');$('dssel').textContent=d.selected?('uploaded file "'+d.selected+'"'):'built-in Fashion-MNIST (Zalando)'}catch(e){}}
function say(m,ok){const x=$('dsmsg');x.textContent=m;x.style.color=ok?'#15803d':'#b91c1c'}
$('dsup').onclick=async()=>{const f=$('dsfile').files[0];if(!f){say('Choose a CSV file first.',false);return}
  $('dsmsg').textContent='Uploading…';
  try{const r=await(await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f})).json();say(r.msg,r.ok);dsLoad()}catch(e){say('Upload failed: '+e,false)}};
$('dsclr').onclick=async()=>{try{const r=await(await fetch('/api/dataset/clear',{method:'POST'})).json();say(r.msg,r.ok);dsLoad()}catch(e){say('Request failed: '+e,false)}};
$('notice').onclick=async()=>{
  if(!confirm('This delivers a simulated 2-minute Spot warning to the Spot VM so it can hand over gracefully. Continue?'))return;
  const btn=$('notice'),msg=$('intmsg');btn.disabled=true;msg.textContent='';
  try{const r=await(await fetch('/api/notice',{method:'POST'})).json();msg.textContent=r.msg;msg.style.color=r.ok?'#15803d':'#b91c1c'}
  catch(e){msg.textContent='Request failed: '+e;msg.style.color='#b91c1c'}
  btn.disabled=false;
};
dsLoad();show();setInterval(load,3000);
</script></body></html>"""


def idle():
    return {"state": "idle", "epoch": 0, "total": TOTAL, "accuracy": 0, "vm": "-",
            "age": None, "history": [], "dataset": "-", "s3": "off"}


def read_status():
    try:
        with open(CKPT) as f:
            d = json.load(f)
    except FileNotFoundError:
        ST["last"] = None
        return idle()
    except ValueError:
        return ST["last"] or idle()
    h = d.get("history", [])
    age = time.time() - d.get("time", time.time())
    state = "complete" if d["epoch"] >= TOTAL else ("running" if age < 15 else "stalled")
    ST["last"] = {"state": state, "epoch": d["epoch"], "total": TOTAL, "accuracy": d["accuracy"],
                  "vm": d.get("vm", "-"), "age": age, "history": h,
                  "dataset": d.get("dataset", "-"), "s3": d.get("s3", "off")}
    return ST["last"]


def add_event(kind, msg):
    ST["events"] = (ST["events"] + [{"t": time.time(), "kind": kind, "msg": msg}])[-200:]
    try:
        with open(EVF, "w") as f:
            json.dump(ST["events"], f)
    except OSError:
        pass


def archive(s):
    h = s["history"]
    if not h:
        return
    os.makedirs(RUNS, exist_ok=True)
    path = "%s/run-%d.json" % (RUNS, h[-1]["time"])
    if os.path.exists(path):
        return
    gaps = [b["time"] - a["time"] for a, b in zip(h, h[1:]) if a["vm"] != b["vm"]]
    vms = {}
    for p in h:
        vms[p["vm"]] = vms.get(p["vm"], 0) + 1
    with open(path, "w") as f:
        json.dump({"end": h[-1]["time"], "accuracy": s["accuracy"], "vms": vms,
                   "failovers": len(gaps), "rto": max(gaps) if gaps else 0}, f)


def watcher():
    while True:
        try:
            if os.path.exists(NOTICE):
                add_event("alert", "2-minute Spot warning received on the spot VM. Final checkpoint saved, fallback VM started.")
                os.replace(NOTICE, NOTICE + ".seen")
            s = read_status()
            st, p = s["state"], ST["prev"]
            if st != p:
                if st == "running" and p == "idle":
                    add_event("start", "Real training started on the %s VM" % s["vm"])
                elif st == "running":
                    k = "failover" if s["vm"] != ST["vm"] else "info"
                    add_event(k, "%s VM resumed at epoch %d" % (s["vm"], s["epoch"]))
                elif st == "stalled":
                    add_event("alert", "No checkpoint for 15 s at epoch %d" % s["epoch"])
                elif st == "complete":
                    add_event("done", "Real training complete, test accuracy %.1f%%" % (s["accuracy"] * 100))
                    archive(s)
                elif st == "idle":
                    add_event("info", "Run reset")
                ST["prev"] = st
            if st == "running":
                ST["vm"] = s["vm"]
        except Exception:
            pass
        time.sleep(2)


def probe(ip):
    t = time.time()
    try:
        socket.create_connection((ip, 22), 1.5).close()
        return int((time.time() - t) * 1000)
    except OSError:
        return None


def infra():
    du = shutil.disk_usage("/")
    mem, up = {}, 0
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            mem[k] = int(v.split()[0])
        up = float(open("/proc/uptime").read().split()[0])
    except (OSError, ValueError):
        pass
    tot = mem.get("MemTotal", 1)
    return {"nodes": {k: probe("127.0.0.1" if k == "storage" else v) for k, v in NODES.items()},
            "ips": NODES, "disk": [du.used, du.total], "load": os.getloadavg()[0], "uptime": up,
            "mem": [(tot - mem.get("MemAvailable", tot)) * 1000, tot * 1000]}


def runs():
    out = []
    try:
        for n in sorted(os.listdir(RUNS), reverse=True)[:20]:
            with open(RUNS + "/" + n) as f:
                out.append(json.load(f))
    except (OSError, ValueError):
        pass
    return out


ROUTES = {"/api/status": read_status, "/api/infra": infra, "/api/runs": runs,
          "/api/events": lambda: ST["events"][::-1], "/api/dataset": dataset_info}


def password():
    try:
        with open(PASSFILE) as f:
            p = f.read().strip()
            if p:
                return p
    except OSError:
        pass
    return "spotops@2026"


class Handler(BaseHTTPRequestHandler):
    def authed(self):
        try:
            kind, cred = self.headers.get("Authorization", "").split(" ", 1)
            user, pw = base64.b64decode(cred).decode().split(":", 1)
            return (kind == "Basic"
                    and hmac.compare_digest(user.encode(), USER.encode())
                    & hmac.compare_digest(pw.encode(), password().encode()))
        except Exception:
            return False

    def send(self, body, ctype="application/json", code=200):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SpotOps"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.authed():
            return self.deny()
        fn = ROUTES.get(urlparse(self.path).path)
        if fn:
            self.send(fn())
        else:
            self.send(PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self.authed():
            return self.deny()
        u = urlparse(self.path)
        actions = {"/api/interrupt": trigger_interrupt, "/api/reset": trigger_reset,
                   "/api/notice": trigger_notice, "/api/dataset/clear": clear_dataset}
        if u.path in actions:
            ok, msg = actions[u.path]()
        elif u.path == "/api/upload":
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > MAX_UPLOAD:
                ok, msg = False, "File must be between 1 byte and 20 MB."
                self.rfile.read(min(n, MAX_UPLOAD))
            else:
                name = os.path.basename(parse_qs(u.query).get("name", ["uploaded.csv"])[0])[:80] or "uploaded.csv"
                ok, msg = save_dataset(name, self.rfile.read(n))
        else:
            return self.send({"ok": False, "msg": "not found"}, code=404)
        self.send({"ok": ok, "msg": msg})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    try:
        with open(EVF) as f:
            ST["events"] = json.load(f)
    except (OSError, ValueError):
        pass
    threading.Thread(target=watcher, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

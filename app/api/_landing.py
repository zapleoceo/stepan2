"""Public marketing landing for Stepan — the AI sales agent product. Served at "/".

Self-contained HTML (own <!doctype> + inline CSS). One optional external asset: the
Space Grotesk display font (graceful fallback to a system grotesk if it fails to load).
No mention of any specific client."""
# ruff: noqa: E501 — inline CSS/HTML string; long lines are inherent, not code smell
from __future__ import annotations

from app.api._landing_analytics import analytics_section

# Secondary contact links in the footer (the main demo is the in-page chat widget).
_DEMO_IG = "https://ig.me/m/zapleo_ceo"
_DEMO_WA = "https://wa.me/380994811889"
_DEMO_TG = "https://t.me/zapleosoft"
_DEMO_FB = "https://www.facebook.com/zapleo.ceo"

_WIDGET_JS = r"""
var STP={msgs:[],busy:false};
var STP_GREET="Hey — I'm Stepan. Quick one: what do you sell, and where do most of your leads come from (Instagram, WhatsApp, ads)?";
function stpAdd(role,text){
  STP.msgs.push({role:role,content:text});
  var b=document.getElementById('stp-body');
  var d=document.createElement('div');
  d.className='stp-msg '+(role==='user'?'u':'a');
  d.textContent=text; b.appendChild(d); b.scrollTop=b.scrollHeight;
}
function stpTyping(on){
  var t=document.getElementById('stp-typing');
  if(on&&!t){var b=document.getElementById('stp-body');t=document.createElement('div');t.id='stp-typing';t.className='stp-msg a stp-typ';t.innerHTML='<span></span><span></span><span></span>';b.appendChild(t);b.scrollTop=b.scrollHeight;}
  if(!on&&t)t.remove();
}
function openStepan(){
  document.getElementById('stp-w').classList.add('on');
  document.getElementById('stp-fab').style.display='none';
  if(!STP.msgs.length)stpAdd('assistant',STP_GREET);
  document.getElementById('stp-in').focus();
}
function closeStepan(){
  document.getElementById('stp-w').classList.remove('on');
  document.getElementById('stp-fab').style.display='';
}
async function sendStepan(){
  var inp=document.getElementById('stp-in');var text=(inp.value||'').trim();
  if(!text||STP.busy)return;
  inp.value='';inp.style.height='auto';stpAdd('user',text);STP.busy=true;stpTyping(true);
  try{
    var r=await fetch('/demo/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages:STP.msgs})});
    var j=await r.json();stpTyping(false);stpAdd('assistant',(j&&j.reply)||'…');
  }catch(e){stpTyping(false);stpAdd('assistant','Connection hiccup — try again?');}
  STP.busy=false;inp.focus();
}
function stpKey(e){
  var ta=e.target;ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,110)+'px';
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendStepan();}
}
"""

# Hero WebGL2 fragment shader (flowing curl-ish FBM in the brand accent, reacts to the
# cursor) + IntersectionObserver scroll-reveals. Both degrade cleanly: no WebGL → the CSS
# grid hero shows; reduced-motion → one static shader frame and reveals appear instantly;
# no JS at all → all content is visible (the reveal hide is scoped to html.has-reveal).
_FX_JS = r"""
(function(){
  var html=document.documentElement; html.classList.add('has-reveal');
  var mq=window.matchMedia('(prefers-reduced-motion: reduce)');
  function initReveal(){
    var els=[].slice.call(document.querySelectorAll('.reveal'));
    if(mq.matches||!('IntersectionObserver' in window)){els.forEach(function(e){e.classList.add('in');});return;}
    var io=new IntersectionObserver(function(ents){ents.forEach(function(en){if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);}});},{threshold:0.15});
    els.forEach(function(e){io.observe(e);});
  }
  function initShader(){
    var cv=document.getElementById('herofx'); if(!cv) return;
    var gl; try{gl=cv.getContext('webgl2',{antialias:false,alpha:false});}catch(e){}
    if(!gl) return;
    var vs='#version 300 es\nprecision highp float;\nvoid main(){vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2));gl_Position=vec4(p*2.0-1.0,0.0,1.0);}';
    var fs='#version 300 es\nprecision highp float;\nuniform vec2 u_res;uniform float u_time;uniform vec2 u_mouse;out vec4 o;'
      +'float hash(vec2 p){p=fract(p*vec2(123.34,456.21));p+=dot(p,p+45.32);return fract(p.x*p.y);}'
      +'float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);float a=hash(i),b=hash(i+vec2(1,0)),c=hash(i+vec2(0,1)),d=hash(i+vec2(1,1));return mix(mix(a,b,f.x),mix(c,d,f.x),f.y);}'
      +'float fbm(vec2 p){float v=0.0,a=0.5;for(int i=0;i<5;i++){v+=a*noise(p);p*=2.03;a*=0.5;}return v;}'
      +'void main(){vec2 uv=gl_FragCoord.xy/u_res.xy;vec2 p=uv;p.x*=u_res.x/u_res.y;vec2 m=(u_mouse-0.5)*0.35;float t=u_time*0.045;'
      +'vec2 q=vec2(fbm(p*1.6+t+m),fbm(p*1.6-t+vec2(5.2)));float f=fbm(p*1.9+q*1.8+t*1.4);'
      +'vec3 bg=vec3(0.031,0.035,0.047);vec3 acc=vec3(1.0,0.36,0.208);'
      +'vec3 col=mix(bg,acc,smoothstep(0.35,0.95,f)*0.55);col=mix(col,vec3(0.05,0.08,0.14),smoothstep(0.2,0.0,f)*0.4);'
      +'float vig=smoothstep(1.15,0.25,length(uv-0.5));col*=mix(0.6,1.0,vig);col+=(hash(gl_FragCoord.xy+u_time)-0.5)*0.02;o=vec4(col,1.0);}';
    function sh(ty,src){var s=gl.createShader(ty);gl.shaderSource(s,src);gl.compileShader(s);return gl.getShaderParameter(s,gl.COMPILE_STATUS)?s:null;}
    var v=sh(gl.VERTEX_SHADER,vs),fr=sh(gl.FRAGMENT_SHADER,fs); if(!v||!fr) return;
    var pr=gl.createProgram();gl.attachShader(pr,v);gl.attachShader(pr,fr);gl.linkProgram(pr);
    if(!gl.getProgramParameter(pr,gl.LINK_STATUS)) return;
    gl.useProgram(pr);
    var uRes=gl.getUniformLocation(pr,'u_res'),uTime=gl.getUniformLocation(pr,'u_time'),uMouse=gl.getUniformLocation(pr,'u_mouse');
    var mouse=[0.5,0.5],mt=[0.5,0.5],raf=null,start=null,visible=true;
    function resize(){var dpr=Math.min(window.devicePixelRatio||1,2);cv.width=Math.max(1,(cv.clientWidth*dpr)|0);cv.height=Math.max(1,(cv.clientHeight*dpr)|0);gl.viewport(0,0,cv.width,cv.height);}
    function draw(tsec){gl.uniform2f(uRes,cv.width,cv.height);gl.uniform1f(uTime,tsec);gl.uniform2f(uMouse,mouse[0],mouse[1]);gl.drawArrays(gl.TRIANGLES,0,3);}
    function frame(ts){raf=null;if(start===null)start=ts;mouse[0]+=(mt[0]-mouse[0])*0.05;mouse[1]+=(mt[1]-mouse[1])*0.05;draw((ts-start)/1000);if(visible&&!document.hidden)req();}
    function req(){if(raf===null&&!mq.matches)raf=requestAnimationFrame(frame);}
    resize();window.addEventListener('resize',resize);
    cv.parentNode.addEventListener('pointermove',function(e){var r=cv.getBoundingClientRect();mt[0]=(e.clientX-r.left)/r.width;mt[1]=1.0-(e.clientY-r.top)/r.height;});
    if('IntersectionObserver' in window){var io2=new IntersectionObserver(function(en){visible=en[0].isIntersecting;if(visible)req();},{threshold:0});io2.observe(cv);}
    document.addEventListener('visibilitychange',function(){if(!document.hidden)req();});
    if(mq.matches){draw(12.0);}else{req();}
  }
  function boot(){initReveal();initShader();}
  if(document.readyState!=='loading')boot();else document.addEventListener('DOMContentLoaded',boot);
})();
"""

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#08090c;--panel:#0e1014;--panel2:#15171d;
  --line:#20232b;--line2:#2b2f38;
  --ink:#f2f4f7;--mut:#9aa3b2;--faint:#666e7d;
  --acc:#ff5c35;--acc-soft:rgba(255,92,53,.12);
  --ok:#4cc38a;--sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --disp:'Space Grotesk',var(--sans);--mono:ui-monospace,'SF Mono','JetBrains Mono',monospace;
}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.6;-webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","cv04";overflow-x:hidden}
a{color:inherit;text-decoration:none}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
.num{font-variant-numeric:tabular-nums}
/* buttons */
.btn{display:inline-flex;align-items:center;gap:.5rem;border-radius:11px;padding:.78rem 1.35rem;font-weight:600;font-size:.94rem;transition:transform .12s ease,background .18s,border-color .18s,color .18s;cursor:pointer;border:1px solid transparent;font-family:var(--sans)}
.btn-p{background:var(--ink);color:#000}
.btn-p:hover{background:#fff;transform:translateY(-1px)}
.btn-g{background:transparent;color:var(--ink);border-color:var(--line2)}
.btn-g:hover{border-color:var(--faint)}
/* nav */
nav{position:sticky;top:0;z-index:20;background:rgba(8,9,12,.72);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.nav{display:flex;align-items:center;justify-content:space-between;height:64px}
.brand{display:flex;align-items:center;gap:.6rem;font-family:var(--disp);font-weight:700;font-size:1.16rem;letter-spacing:-.01em}
.logo{width:29px;height:29px;border-radius:8px;background:var(--ink);color:#000;display:flex;align-items:center;justify-content:center;font-family:var(--disp);font-weight:700;font-size:.98rem}
.brand small{font-family:var(--sans);font-weight:500;font-size:.62rem;color:var(--faint);letter-spacing:.13em;text-transform:uppercase}
.login{font-size:.9rem;color:var(--mut);border:1px solid var(--line2);padding:.44rem .95rem;border-radius:9px;transition:.16s}
.login:hover{color:var(--ink);border-color:var(--faint)}
/* hero */
.hero{position:relative;padding:6.5rem 0 4rem;text-align:center;overflow:hidden}
.hero::before{content:"";position:absolute;inset:0;background-image:radial-gradient(circle at 1px 1px,rgba(255,255,255,.045) 1px,transparent 0);background-size:26px 26px;-webkit-mask-image:radial-gradient(70% 60% at 50% 30%,#000,transparent 75%);mask-image:radial-gradient(70% 60% at 50% 30%,#000,transparent 75%);pointer-events:none}
.eyebrow{display:inline-flex;align-items:center;gap:.5rem;font-size:.72rem;color:var(--mut);border:1px solid var(--line2);background:var(--panel);padding:.34rem .8rem;border-radius:999px;margin-bottom:1.7rem;position:relative;letter-spacing:.02em}
.eyebrow .d{width:6px;height:6px;border-radius:50%;background:var(--acc)}
h1{font-family:var(--disp);font-size:clamp(2.3rem,6vw,4rem);line-height:1.04;font-weight:700;letter-spacing:-.03em;position:relative}
h1 em{font-style:normal;color:var(--acc)}
.sub{max-width:620px;margin:1.5rem auto 0;color:var(--mut);font-size:1.12rem;line-height:1.6;position:relative}
.cta{margin-top:2.3rem;display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;position:relative}
.note{margin-top:1.1rem;font-size:.82rem;color:var(--faint);position:relative}
/* sections */
section{padding:5rem 0;position:relative}
.shead{max-width:620px;margin-bottom:1rem}
.kick{color:var(--acc);font-size:.74rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;margin-bottom:.85rem}
h2{font-family:var(--disp);font-size:clamp(1.7rem,3.8vw,2.5rem);font-weight:700;letter-spacing:-.025em;line-height:1.12}
.lead{color:var(--mut);max-width:600px;margin-top:.9rem;font-size:1.02rem}
.divide{border-top:1px solid var(--line)}
/* steps */
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin-top:2.8rem;background:var(--line);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.step{background:var(--panel);padding:1.6rem 1.4rem}
.step .n{font-family:var(--mono);font-size:.74rem;color:var(--faint);margin-bottom:1.4rem;letter-spacing:.1em}
.step .ic{color:var(--ink);margin-bottom:.9rem;display:block}
.step h3{font-family:var(--disp);font-size:1.02rem;font-weight:600;margin-bottom:.4rem;letter-spacing:-.01em}
.step p{font-size:.87rem;color:var(--mut);line-height:1.55}
/* feature grid */
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2.8rem}
.feat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.5rem 1.3rem;transition:border-color .18s,transform .12s}
.feat:hover{border-color:var(--line2);transform:translateY(-2px)}
.feat .ic{color:var(--acc);margin-bottom:1rem;display:block}
.feat h3{font-family:var(--disp);font-size:.96rem;font-weight:600;margin-bottom:.4rem;letter-spacing:-.01em}
.feat p{font-size:.83rem;color:var(--mut);line-height:1.55}
/* pricing */
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:2.6rem}
.pcard{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1.8rem}
.pcard.hi{border-color:var(--acc);background:linear-gradient(180deg,var(--acc-soft),var(--panel) 60%)}
.ptag{color:var(--acc);font-size:.72rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;margin-bottom:.9rem}
.pnum{font-family:var(--disp);font-size:2.4rem;font-weight:600;letter-spacing:-.02em}
.pnum small{font-size:1rem;color:var(--mut);font-weight:500}
.pwhat{font-size:.9rem;color:var(--ink);margin-top:.3rem;font-weight:600}
.pnote{font-size:.83rem;color:var(--mut);line-height:1.55;margin-top:.7rem}
.pinc{margin-top:1.8rem;display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;list-style:none}
.pinc li{display:flex;align-items:flex-start;gap:.55rem;font-size:.85rem;color:var(--mut);line-height:1.5}
.pinc li .ic{color:var(--ok);flex-shrink:0;margin-top:.15rem}
@media (max-width:760px){.pgrid,.pinc{grid-template-columns:1fr}}
/* compare */
.cmp{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-top:2.8rem}
.col{border-radius:16px;padding:1.7rem;border:1px solid var(--line);background:var(--panel)}
.col.good{border-color:var(--line2);background:var(--panel2)}
.col h3{font-family:var(--disp);font-size:1.05rem;font-weight:600;margin-bottom:1.1rem;display:flex;align-items:center;gap:.55rem;letter-spacing:-.01em}
.col ul{list-style:none}
.col li{font-size:.9rem;color:var(--mut);padding:.55rem 0 .55rem 1.7rem;position:relative;border-top:1px solid var(--line)}
.col li:first-child{border-top:none}
.col.bad li::before{content:"";position:absolute;left:0;top:.85rem;width:11px;height:1.5px;background:var(--faint)}
.col.good li::before{content:"";position:absolute;left:1px;top:.68rem;width:6px;height:10px;border:solid var(--ok);border-width:0 1.5px 1.5px 0;transform:rotate(45deg)}
.col.good li{color:var(--ink)}
/* meta comparison table */
.mtwrap{margin-top:2.8rem;border:1px solid var(--line);border-radius:16px;overflow:hidden;overflow-x:auto}
.mtable{width:100%;border-collapse:collapse;min-width:640px}
.mtable th,.mtable td{padding:.9rem 1.1rem;text-align:left;font-size:.88rem;border-top:1px solid var(--line)}
.mtable thead th{border-top:none;background:var(--panel2);font-family:var(--disp);font-weight:600;font-size:.86rem;letter-spacing:-.01em;color:var(--ink)}
.mtable thead th:first-child{color:var(--faint);font-family:var(--sans);font-weight:500;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}
.mtable tbody th{font-weight:500;color:var(--mut);white-space:nowrap}
.mtable td{color:var(--mut)}
.mtable td.win{color:var(--ink)}
.mtable td .yes{color:var(--ok)}
.mtable td .no{color:var(--faint)}
.mtable tbody tr:hover td,.mtable tbody tr:hover th{background:rgba(255,255,255,.02)}
.mtcap{font-size:.72rem;color:var(--faint);margin-top:.9rem}
/* enterprise trust strip */
.trust{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-top:2.8rem}
.tcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.4rem 1.2rem}
.tcard .ic{color:var(--acc);margin-bottom:.9rem;display:block}
.tcard h3{font-family:var(--disp);font-size:.92rem;font-weight:600;margin-bottom:.35rem;letter-spacing:-.01em}
.tcard p{font-size:.8rem;color:var(--mut);line-height:1.5}
@media (max-width:860px){.trust{grid-template-columns:1fr 1fr}}
@media (max-width:480px){.trust{grid-template-columns:1fr}}
/* mcp / crm */
.mcp{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:0;margin-top:2.8rem;border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:1.6rem}
.mnode{padding:1.3rem;text-align:center}
.mnode .ic{color:var(--ink);display:inline-flex;margin-bottom:.7rem}
.mnode b{font-family:var(--disp);font-size:1.05rem;font-weight:600;display:block;letter-spacing:-.01em}
.mnode small{color:var(--mut);font-size:.8rem}
.mwire{display:flex;flex-direction:column;align-items:center;gap:.5rem;min-width:130px;padding:0 .4rem}
.mwire .ln{width:100%;height:1px;background:linear-gradient(90deg,transparent,var(--line2),transparent);position:relative}
.mcpbadge{font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;color:var(--acc);border:1px solid var(--acc-soft);background:var(--acc-soft);padding:.28rem .7rem;border-radius:7px}
.mcpsub{font-size:.66rem;color:var(--faint)}
.mcrm .stacks{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:center;margin-top:.6rem}
.mcrm .stacks span{font-size:.7rem;color:var(--mut);border:1px solid var(--line2);border-radius:7px;padding:.18rem .5rem}
.syncs{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1.3rem}
.sync{display:inline-flex;align-items:center;gap:.45rem;font-size:.82rem;color:var(--mut);background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:.42rem .75rem}
.sync .dt{width:6px;height:6px;border-radius:2px;background:var(--acc)}
/* channels */
.chan{display:flex;gap:.7rem;flex-wrap:wrap;margin-top:2.4rem}
.pill{display:inline-flex;align-items:center;gap:.55rem;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.7rem 1.15rem;font-size:.92rem;font-weight:500}
.pill .ic{color:var(--mut)}
.pill.soon{opacity:.62}
.pill .tag{font-family:var(--mono);font-size:.6rem;letter-spacing:.1em;color:var(--acc);border:1px solid var(--acc-soft);background:var(--acc-soft);padding:.1rem .4rem;border-radius:5px;text-transform:uppercase}
.pill .tag.custom{color:var(--mut);border-color:var(--line2);background:transparent}
/* final */
.final{border:1px solid var(--line2);border-radius:22px;padding:3.6rem 1.5rem;text-align:center;background:var(--panel2);position:relative;overflow:hidden}
.final::before{content:"";position:absolute;inset:0;background-image:radial-gradient(circle at 1px 1px,rgba(255,255,255,.05) 1px,transparent 0);background-size:24px 24px;-webkit-mask-image:radial-gradient(60% 80% at 50% 0%,#000,transparent);mask-image:radial-gradient(60% 80% at 50% 0%,#000,transparent);pointer-events:none}
.final h2{font-size:clamp(1.8rem,4.2vw,2.7rem)}
.final .lead{margin-left:auto;margin-right:auto}
/* footer */
footer{border-top:1px solid var(--line);padding:2.4rem 0;color:var(--mut);font-size:.85rem}
.foot{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem}
.foot a{color:var(--mut);transition:.15s}.foot a:hover{color:var(--ink)}
@media (max-width:860px){.steps,.grid{grid-template-columns:1fr 1fr}}
@media (max-width:760px){.cmp{grid-template-columns:1fr}.mcp{grid-template-columns:1fr}.mwire{flex-direction:row;min-width:0;padding:.8rem 0}.mwire .ln{width:1px;height:34px;background:linear-gradient(180deg,transparent,var(--line2),transparent)}.hero{padding:4.5rem 0 3rem}}
@media (max-width:480px){.steps,.grid{grid-template-columns:1fr}}
/* chat widget */
.stp-fab{position:fixed;right:22px;bottom:22px;z-index:60;background:var(--ink);color:#000;border:none;border-radius:12px;padding:.85rem 1.25rem;font-weight:600;font-size:.9rem;box-shadow:0 10px 30px rgba(0,0,0,.5);cursor:pointer;transition:transform .12s;display:inline-flex;align-items:center;gap:.5rem}
.stp-fab:hover{transform:translateY(-2px)}
.stp-w{position:fixed;right:22px;bottom:22px;z-index:61;width:374px;max-width:calc(100vw - 32px);height:566px;max-height:calc(100vh - 40px);background:var(--panel);border:1px solid var(--line2);border-radius:18px;box-shadow:0 30px 70px rgba(0,0,0,.6);display:none;flex-direction:column;overflow:hidden}
.stp-w.on{display:flex}
.stp-hd{display:flex;align-items:center;gap:.6rem;padding:.9rem 1rem;border-bottom:1px solid var(--line);background:var(--panel2)}
.stp-hd b{font-family:var(--disp);font-size:.95rem}.stp-hd small{display:block;font-size:.66rem;color:var(--faint)}
.stp-x{margin-left:auto;background:none;border:none;color:var(--mut);font-size:1.4rem;cursor:pointer;line-height:1}
.stp-body{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.5rem}
.stp-msg{max-width:82%;padding:.55rem .8rem;border-radius:14px;font-size:.9rem;line-height:1.45;white-space:pre-wrap;word-wrap:break-word}
.stp-msg.a{align-self:flex-start;background:var(--panel2);border:1px solid var(--line);border-bottom-left-radius:5px}
.stp-msg.u{align-self:flex-end;background:var(--ink);color:#000;border-bottom-right-radius:5px}
.stp-typ{display:flex;gap:4px;align-items:center}
.stp-typ span{width:6px;height:6px;border-radius:50%;background:var(--mut);animation:stpb 1s infinite}
.stp-typ span:nth-child(2){animation-delay:.15s}.stp-typ span:nth-child(3){animation-delay:.3s}
@keyframes stpb{0%,60%,100%{opacity:.3}30%{opacity:1}}
.stp-foot{display:flex;gap:.5rem;padding:.7rem;border-top:1px solid var(--line);align-items:flex-end}
.stp-foot textarea{flex:1;background:var(--bg);border:1px solid var(--line2);border-radius:10px;color:var(--ink);padding:.55rem .7rem;font-size:.9rem;resize:none;font-family:inherit;max-height:110px}
.stp-foot textarea:focus{outline:none;border-color:var(--faint)}
.stp-send{background:var(--ink);color:#000;border:none;border-radius:10px;width:40px;height:38px;font-size:1rem;cursor:pointer;display:flex;align-items:center;justify-content:center}
@media (max-width:460px){.stp-w{right:8px;bottom:8px;width:calc(100vw - 16px);height:calc(100vh - 16px)}.stp-fab{right:12px;bottom:12px}}
/* product mockups (illustrative — not real data) */
.shots{display:grid;grid-template-columns:1fr 1fr;gap:1.4rem;margin-top:2.8rem;align-items:start}
.frame{background:var(--bg);border:1px solid var(--line2);border-radius:16px;overflow:hidden}
.fbar{display:flex;align-items:center;gap:.4rem;padding:.55rem .8rem;border-bottom:1px solid var(--line);background:var(--panel2)}
.fbar i{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--line2)}
.furl{margin-left:.5rem;font-family:var(--mono);font-size:.66rem;color:var(--faint);flex:1;text-align:center}
.ph-top{display:flex;align-items:center;gap:.55rem;padding:.7rem .9rem;border-bottom:1px solid var(--line);background:var(--panel2)}
.ph-ava{width:32px;height:32px;border-radius:50%;background:var(--panel);border:1px solid var(--line2);display:flex;align-items:center;justify-content:center;font-weight:600;color:var(--ink);font-size:.8rem}
.ph-top b{font-size:.9rem}.ph-top small{display:block;font-size:.64rem;color:var(--ok)}
.ph-body{padding:.9rem;display:flex;flex-direction:column;gap:.45rem;background:var(--bg)}
.mb{max-width:82%;padding:.5rem .75rem;border-radius:14px;font-size:.83rem;line-height:1.45}
.mb.in{align-self:flex-start;background:var(--panel2);border:1px solid var(--line);border-bottom-left-radius:5px}
.mb.out{align-self:flex-end;background:var(--ink);color:#000;border-bottom-right-radius:5px}
.mb .who{display:block;font-size:.56rem;opacity:.6;margin-bottom:.15rem;font-weight:600}
.dash{padding:1rem;display:flex;flex-direction:column;gap:.9rem;background:var(--bg)}
.mcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.9rem}
.mlbl{font-family:var(--mono);font-size:.6rem;color:var(--faint);text-transform:uppercase;letter-spacing:.1em;margin-bottom:.6rem}
.leadrow{display:flex;align-items:center;gap:.5rem;margin-bottom:.7rem}
.av{width:28px;height:28px;border-radius:50%;background:var(--panel2);border:1px solid var(--line2);display:flex;align-items:center;justify-content:center;font-size:.72rem;font-weight:600;color:var(--ink)}
.leadrow b{font-size:.85rem}
.spill{margin-left:auto;font-size:.62rem;background:var(--panel2);border:1px solid var(--line2);color:var(--mut);border-radius:20px;padding:.14rem .6rem}
.chips{display:flex;flex-wrap:wrap;gap:.35rem}
.ch2{font-size:.68rem;border-radius:8px;padding:.22rem .55rem;border:1px solid var(--line2);color:var(--mut);display:inline-flex;align-items:center;gap:.35rem}
.ch2 .dt{width:6px;height:6px;border-radius:2px}
.d-goal{background:#4d8dff}.d-pain{background:#ff5c5c}.d-gain{background:#4cc38a}
.fn{display:flex;flex-direction:column;gap:.5rem}
.fnrow{display:flex;align-items:center;gap:.6rem}
.fnrow .nm{width:74px;color:var(--mut);font-size:.72rem}
.fnbar{height:13px;border-radius:4px;background:var(--ink);opacity:.85}
.fnrow .v{font-family:var(--mono);font-weight:600;font-size:.72rem;margin-left:auto}
.alert{display:flex;align-items:center;gap:.5rem;font-size:.78rem;color:var(--ok);background:rgba(76,195,138,.08);border:1px solid rgba(76,195,138,.24);border-radius:10px;padding:.6rem .75rem}
.mnote{font-size:.72rem;color:var(--faint);margin-top:1.1rem}
/* ad accounts / attribution mockup */
.meta{display:grid;grid-template-columns:1.15fr .85fr;gap:1.4rem;margin-top:2.8rem;align-items:stretch}
.mpanel{background:var(--bg);border:1px solid var(--line2);border-radius:16px;overflow:hidden;display:flex;flex-direction:column}
.mhd{display:flex;align-items:center;gap:.5rem;padding:.75rem .95rem;border-bottom:1px solid var(--line);background:var(--panel2);font-size:.8rem;font-weight:600}
.mhd .dot{width:7px;height:7px;border-radius:50%;background:var(--ok)}
.mhd .live{margin-left:auto;font-family:var(--mono);font-size:.58rem;letter-spacing:.1em;color:var(--ok);border:1px solid rgba(76,195,138,.28);border-radius:20px;padding:.1rem .55rem;text-transform:uppercase}
.mbody{padding:1rem;display:flex;flex-direction:column;gap:.7rem}
.adrow{display:flex;align-items:center;gap:.7rem;padding:.65rem .75rem;background:var(--panel);border:1px solid var(--line);border-radius:11px}
.adth{width:34px;height:34px;border-radius:9px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:var(--ink);background:var(--panel2);border:1px solid var(--line2)}
.adrow .an{font-size:.8rem;font-weight:600}.adrow .as{font-family:var(--mono);font-size:.64rem;color:var(--mut)}
.adkpi{margin-left:auto;text-align:right}.adkpi b{font-size:.9rem;display:block;font-variant-numeric:tabular-nums}.adkpi span{font-family:var(--mono);font-size:.6rem;color:var(--faint)}
.push{display:flex;align-items:center;gap:.5rem;font-size:.72rem;color:var(--mut);margin-top:.4rem}
.flow{display:flex;flex-direction:column;gap:.55rem;padding:.2rem 0}
.fsrc{display:flex;align-items:center;gap:.55rem;font-size:.76rem;background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:.45rem .6rem}
.fsrc .ic{color:var(--mut)}
.fsrc .ph{margin-left:auto;font-family:var(--mono);font-size:.62rem;color:var(--faint)}
.merge{text-align:center;font-family:var(--mono);font-size:.66rem;letter-spacing:.08em;color:var(--faint);text-transform:uppercase}
.uni{background:var(--panel);border:1px solid var(--line2);border-radius:12px;padding:.85rem}
.uni .ur{display:flex;align-items:center;gap:.5rem}
.uni b{font-size:.85rem}.uni .ph2{margin-left:auto;font-family:var(--mono);font-size:.62rem;color:var(--faint)}
.uni .ut{margin-top:.5rem;font-size:.68rem;color:var(--mut)}
.uni .tags{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.5rem}
.uni .tg{font-size:.64rem;color:var(--mut);border:1px solid var(--line2);border-radius:6px;padding:.14rem .45rem}
/* re-qualification strip */
.requal{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-top:1.4rem;padding:1.1rem 1.3rem;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.requal .rq-t{font-size:.86rem;color:var(--mut);flex:1;min-width:200px}
.requal .rq-t b{color:var(--ink);font-weight:600}
.rqtag{display:inline-flex;align-items:center;gap:.4rem;font-size:.76rem;border:1px solid var(--line2);border-radius:8px;padding:.3rem .6rem;color:var(--mut)}
.rqtag .dt{width:7px;height:7px;border-radius:50%}
.rq-old .dt{background:var(--faint)}.rq-new{color:var(--ink);border-color:rgba(255,92,53,.4)}.rq-new .dt{background:var(--acc)}
.rq-arrow{color:var(--faint)}
/* analytics dashboard */
.anl{display:flex;flex-direction:column;gap:1.2rem;margin-top:2.8rem}
.apanel{background:var(--bg);border:1px solid var(--line2);border-radius:16px;padding:1.1rem 1.2rem}
.atitle{font-family:var(--disp);font-size:.85rem;font-weight:600;margin-bottom:.8rem;color:var(--ink)}
.asub{font-family:var(--mono);font-weight:400;color:var(--faint);font-size:.7rem}
@media (max-width:760px){.shots{grid-template-columns:1fr}.meta{grid-template-columns:1fr}}
/* hero WebGL shader + contrast scrim */
.hero-fx{position:absolute;inset:0;z-index:0;width:100%;height:100%;display:block}
.hero-scrim{position:absolute;inset:0;z-index:1;pointer-events:none;background:radial-gradient(62% 58% at 50% 40%,rgba(8,9,12,.28),rgba(8,9,12,.86) 80%)}
.hero .wrap{position:relative;z-index:2}
/* scroll-reveal (progressive: content stays visible without JS or under reduced-motion) */
@media (prefers-reduced-motion:no-preference){
  html.has-reveal .reveal{opacity:0;transform:translateY(22px);transition:opacity .7s cubic-bezier(.16,1,.3,1),transform .7s cubic-bezier(.16,1,.3,1)}
  html.has-reveal .reveal.in{opacity:1;transform:none}
}
/* mono eyebrow (tech-editorial) */
.kick,.eyebrow{font-family:var(--mono)}
/* insights cloud */
.cloud{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-top:2.6rem}
.cloud-col{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:1.3rem 1.15rem}
.cloud-h{font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);display:flex;align-items:center;gap:.5rem;margin-bottom:1rem}
.cloud-h .dt{width:8px;height:8px;border-radius:50%}
.g-goal{background:#4d8dff}.g-pain{background:#ff5c5c}.g-fear{background:#4cc38a}
.chip{display:block;font-size:.9rem;color:var(--ink);background:var(--panel2);border:1px solid var(--line2);border-radius:10px;padding:.5rem .7rem;margin-bottom:.55rem;will-change:transform}
.cloud-col.c-goal{background:linear-gradient(180deg,rgba(77,141,255,.07),var(--panel) 60%)}
.cloud-col.c-pain{background:linear-gradient(180deg,rgba(255,92,92,.07),var(--panel) 60%)}
.cloud-col.c-fear{background:linear-gradient(180deg,rgba(76,195,138,.07),var(--panel) 60%)}
@media (prefers-reduced-motion:no-preference){.chip{animation:floaty var(--d,7s) ease-in-out infinite}}
@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}
@media (max-width:760px){.cloud{grid-template-columns:1fr}}
/* persona library — split: copy left, versioned persona rows right */
.plib{display:grid;grid-template-columns:1fr 1fr;gap:2.2rem;align-items:center;margin-top:2.6rem}
.plib-list{display:flex;flex-direction:column;gap:.8rem}
.prow{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.05rem 1.15rem;transition:border-color .18s,transform .12s}
.prow:hover{border-color:var(--line2);transform:translateY(-2px)}
.prow.hi{border-color:var(--acc);background:linear-gradient(180deg,var(--acc-soft),var(--panel) 65%)}
.prow-top{display:flex;align-items:center;gap:.6rem;margin-bottom:.3rem}
.prow-nm{font-family:var(--disp);font-weight:600;font-size:1rem;letter-spacing:-.01em}
.prow-ver{font-family:var(--mono);font-size:.64rem;color:var(--faint);border:1px solid var(--line2);border-radius:6px;padding:.1rem .4rem}
.prow-metric{margin-left:auto;text-align:right}
.prow-metric b{font-family:var(--disp);font-size:1.15rem;color:var(--ok)}
.prow-metric span{display:block;font-size:.6rem;color:var(--faint);text-transform:uppercase;letter-spacing:.08em}
.prow-desc{font-size:.85rem;color:var(--mut)}
.plib .plq li{display:flex;gap:.55rem;align-items:flex-start;font-size:.9rem;color:var(--mut);margin-top:.7rem;list-style:none}
@media (max-width:760px){.plib{grid-template-columns:1fr}}
"""


def _svg(inner: str, size: int = 22) -> str:
    return (f'<svg class="ic" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{inner}</svg>')


_IC = {
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M17 7h4v4"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/>',
    "bulb": '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12c.5.5 1 1.6 1 2h6c0-.4.5-1.5 1-2a7 7 0 0 0-4-12Z"/>',
    "msgs": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18Z"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>',
    "arrow": '<circle cx="12" cy="12" r="9"/><path d="m10 8 4 4-4 4"/>',
    "chart": '<path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="7" rx="1"/><rect x="13" y="6" width="3" height="11" rx="1"/>',
    "grad": '<path d="M12 3 2 8l10 5 10-5-10-5Z"/><path d="M6 10v5c0 1 2.7 3 6 3s6-2 6-3v-5"/>',
    "bot": '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/>',
    "crm": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "ig": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none"/>',
    "wa": '<path d="M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 21l2.2-5.3A8.5 8.5 0 1 1 21 11.5Z"/><path d="M8.5 9c0 4 2.5 6.5 6.5 6.5"/>',
    "msgr": '<path d="M12 3C6.5 3 2 7.1 2 12c0 2.7 1.3 5.1 3.4 6.7V22l3.1-1.7c1.1.3 2.3.5 3.5.5 5.5 0 10-4.1 10-9s-4.5-9-10-9Z"/><path d="m7 13 3-3 2.5 2L16 11l-3 3-2.5-2Z"/>',
    "tiktok": '<path d="M16 3c.3 2 1.7 3.6 4 4v3c-1.6 0-3-.4-4-1v6.5A5.5 5.5 0 1 1 10.5 10v3a2.5 2.5 0 1 0 2.5 2.5V3z"/>',
    "telegram": '<path d="M22 3 2 11l7 2.5M22 3l-4 18-8-6.5M22 3 9.5 13.5l-1 6"/>',
    "plug": '<path d="M9 2v4M15 2v4M7 10h10v3a5 5 0 0 1-10 0z"/><path d="M12 17v5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "layers": '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    "building": '<rect x="4" y="2" width="16" height="20" rx="1"/><path d="M9 22v-4h6v4"/>'
                '<path d="M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01'
                'M8 14h.01M12 14h.01M16 14h.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
}


def _step(n: str, ic: str, title: str, body: str) -> str:
    return (f'<div class="step"><div class="n">{n}</div>{_svg(_IC[ic])}'
            f'<h3>{title}</h3><p>{body}</p></div>')


def _feat(ic: str, title: str, body: str) -> str:
    return (f'<div class="feat">{_svg(_IC[ic])}'
            f'<h3>{title}</h3><p>{body}</p></div>')


def _adrow(mark: str, name: str, stats: str, booked: str, roas: str) -> str:
    return (f'<div class="adrow"><span class="adth">{mark}</span>'
            f'<div><div class="an">{name}</div><div class="as">{stats}</div></div>'
            f'<div class="adkpi"><b class="num">{booked}</b><span>{roas}</span></div></div>')


def _pricing_section() -> str:
    """Simple usage-based pricing: up to 10 leads/day free, then a flat $1/lead — charged
    once per lead regardless of outcome or how long the conversation runs. No per-message
    or per-token metering (unlike Meta's own token-billed agent)."""
    included = [
        "Unlimited messages per lead — a 40-turn qualification costs the same $1 as one reply",
        "Instagram + WhatsApp + Messenger, every language",
        "CRM sync via MCP, ad attribution, analytics dashboard",
    ]
    inc_items = "".join(f'<li>{_svg(_IC["check"], 16)}{i}</li>' for i in included)
    return (
        "<section class=\"divide\" id=\"pricing\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Pricing</div>"
        "<h2>Pay for leads, not for chatting</h2>"
        "<p class=\"lead\">No seats, no message caps, no token metering — a flat fee per "
        "lead, charged once, no matter the outcome or how long Stepan talks to them.</p></div>"
        "<div class=\"pgrid\">"
        "<div class=\"pcard\">"
        "<div class=\"ptag\">Get started</div>"
        "<div class=\"pnum\">Free</div>"
        "<div class=\"pwhat\">Up to 10 leads / day</div>"
        "<p class=\"pnote\">Run Stepan on real traffic before you pay anything.</p>"
        "</div>"
        "<div class=\"pcard hi\">"
        "<div class=\"ptag\">Pay as you grow</div>"
        "<div class=\"pnum\">$1<small>/lead</small></div>"
        "<div class=\"pwhat\">Past 10 leads / day</div>"
        "<p class=\"pnote\">Charged once per lead — regardless of result or how long the "
        "conversation runs. Never per message, never per token.</p>"
        "</div></div>"
        f"<ul class=\"pinc\">{inc_items}</ul>"
        "<p class=\"mnote\" style=\"margin-top:1.4rem\">Running hundreds of leads a day across "
        "multiple brands or locations? <a href=\"javascript:void(0)\" style=\"color:var(--acc);"
        "text-decoration:underline\" onclick=\"openStepan()\">talk to us</a> about volume "
        "pricing and a dedicated rollout.</p>"
        "<div class=\"cta\" style=\"margin-top:1.6rem\">"
        "<button class=\"btn btn-p\" onclick=\"openStepan()\">Talk to Stepan</button></div>"
        "</div></section>"
    )


def _meta_compare_section() -> str:
    """Head-to-head vs Meta's own Business Agent (launched June 2026) — grounded in Meta's
    public announcement + independent reviews, not a strawman. Framed for a buyer who will
    fact-check both, so claims about Meta stay to what's publicly documented."""
    rows = [
        ("Pricing model", "Per-token — cost scales with every reply sent",
         "Flat $1 per lead, charged once, any length"),
        ("Sales approach", "Q&amp;A + catalog recommendations + booking",
         "Consultative: discovery &rarr; needs &rarr; objection handling &rarr; timed offer"),
        ("Grounding / anti-hallucination", "Not publicly documented",
         "Built-in fact-checking guard — regenerates or hands off rather than invent"),
        ("Lead scoring", "Basic qualifying questions",
         "Two-axis intent + audience scoring, re-qualifies mid-conversation"),
        ("CRM integration", "No native CRM — custom API work required",
         "Open MCP connector — plugs into your CRM's own fields"),
        ("Ad performance &amp; attribution", "Not a stated feature",
         "Pulls ad spend/CPL, maps every lead to its exact ad, merges identity across ads"),
        ("Multi-brand / multi-location", "Not documented for franchise-style management",
         "Native multi-branch with role-based access per team"),
        ("Channels", "WhatsApp, Instagram, Messenger",
         "Instagram, WhatsApp, Messenger, TikTok soon — plus Telegram or any messenger with "
         "an API, built to order"),
    ]
    body = "".join(
        f'<tr><th scope="row">{k}</th><td>{m}</td><td class="win">{s}</td></tr>'
        for k, m, s in rows
    )
    return (
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Stepan vs. Meta's own AI</div>"
        "<h2>Meta just shipped an AI agent too. Here's the real difference.</h2>"
        "<p class=\"lead\">Meta Business Agent launched June 2026 and answers messages "
        "across WhatsApp, Instagram and Messenger for free, at first. It's a solid Q&amp;A "
        "bot. It isn't a closer.</p></div>"
        "<div class=\"mtwrap\"><table class=\"mtable\">"
        "<thead><tr><th scope=\"col\"></th><th scope=\"col\">Meta Business Agent</th>"
        "<th scope=\"col\">Stepan</th></tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
        "<p class=\"mtcap\">Meta Business Agent details from Meta's June 2026 announcement "
        "and independent reviews at time of writing — features change; verify current specs "
        "with Meta.</p>"
        "</div></section>"
    )


def _trust_section() -> str:
    """Enterprise-facing trust strip: what a buyer running multiple brands/locations
    actually asks about before signing — access control, auditability, integration depth,
    rollout support. All facts, no illustrative mockup data."""
    cards = [
        ("building", "Built for multiple brands", "Run every location or brand as its own "
         "branch: separate knowledge base, numbers and reporting, one account."),
        ("users", "Role-based access", "Admin, branch admin or view-only. Control exactly "
         "who can see or touch which brand's leads."),
        ("shield", "Grounded &amp; auditable", "Every claim traces back to your own facts. "
         "Full transcript on every lead, synced to your CRM."),
        ("layers", "Deep integrations", "MCP connector, ad-account attribution, CRM sync, "
         "built to sit inside a real stack, not replace it."),
    ]
    items = "".join(
        f'<div class="tcard">{_svg(_IC[ic], 22)}<h3>{t}</h3><p>{b}</p></div>'
        for ic, t, b in cards
    )
    return f'<div class="trust">{items}</div>'


def _ad_accounts_section() -> str:
    rows = "".join([
        _adrow("HF", "Home Fitness — Reels", "142 leads · CPL $3.10", "28 booked", "ROAS 4.6×"),
        _adrow("MP", "Meal Plan — Stories", "96 leads · CPL $4.80", "11 booked", "ROAS 2.1×"),
        _adrow("1:1", "1-on-1 Coaching — Feed", "54 leads · CPL $6.20", "19 booked", "ROAS 5.9×"),
    ])
    return (
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Connected to your ad accounts</div>"
        "<h2>Every ad measured. Every lead unified.</h2>"
        "<p class=\"lead\">Stepan pulls performance straight from your marketing cabinets, knows "
        "which product each ad promotes, and merges the same person across products into one "
        "profile by phone number. Conversions flow back so the algorithm learns who actually buys."
        "</p></div>"
        "<div class=\"meta\">"
        "<div class=\"mpanel\">"
        "<div class=\"mhd\"><span class=\"dot\"></span>Ad performance · by product"
        "<span class=\"live\">live</span></div>"
        f"<div class=\"mbody\">{rows}"
        "<div class=\"push\">Conversions pushed back to Meta — the algorithm learns who buys.</div>"
        "</div></div>"
        "<div class=\"mpanel\">"
        "<div class=\"mhd\"><span class=\"dot\"></span>One lead, every touchpoint</div>"
        "<div class=\"mbody\"><div class=\"flow\">"
        f"<div class=\"fsrc\">{_svg(_IC['trend'], 16)}Home Fitness ad"
        "<span class=\"ph\">+1 ••• 4471</span></div>"
        f"<div class=\"fsrc\">{_svg(_IC['users'], 16)}Coaching ad"
        "<span class=\"ph\">+1 ••• 4471</span></div>"
        "<div class=\"merge\">merged by phone</div>"
        "<div class=\"uni\"><div class=\"ur\"><span class=\"av\">M</span><b>Maya R.</b>"
        "<span class=\"ph2\">+1 ••• 4471</span></div>"
        "<div class=\"ut\">2 ad sources · first seen 6 days ago · now Qualifying</div>"
        "<div class=\"tags\"><span class=\"tg\">Home Fitness</span>"
        "<span class=\"tg\">1-on-1 Coaching</span></div></div>"
        "</div></div></div>"
        # re-qualification strip
        "<div class=\"requal\">"
        "<div class=\"rq-t\"><b>Re-qualifies mid-conversation.</b> When a lead reveals a deeper "
        "pain or real urgency, Stepan updates the segment and score on the fly — no rigid tag "
        "set at first contact.</div>"
        "<span class=\"rqtag rq-old\"><span class=\"dt\"></span>Cold · low intent</span>"
        "<span class=\"rq-arrow\">→</span>"
        "<span class=\"rqtag rq-new\"><span class=\"dt\"></span>Hot · ready to buy</span>"
        "</div>"
        "<p class=\"mnote\">Illustrative — sample data, not a real account.</p>"
        "</div></section>"
    )


def _insights_cloud_section() -> str:
    """What Stepan captures from every conversation — goals / pains / fears — as a living,
    gently-floating cloud of chips (a real conceptual viz, not a fake screenshot)."""
    def chip(text: str, d: str) -> str:
        return f'<span class="chip" style="--d:{d}s">{text}</span>'
    goals = "".join(chip(x, d) for x, d in (
        ("Land my first data job", "6.5"), ("Grow my brand's reach", "8"),
        ("Switch careers into tech", "7.2"), ("Earn on the side freelancing", "9")))
    pains = "".join(chip(x, d) for x, d in (
        ("No time for a full course", "7.8"), ("Tried tutorials, got stuck", "6.2"),
        ("Budget is tight right now", "8.6"), ("Burned before by empty promises", "7")))
    fears = "".join(chip(x, d) for x, d in (
        ("What if I'm too old to start?", "6.8"), ("Scared it's too technical", "8.2"),
        ("Worried I won't finish it", "7.5"), ("Afraid it won't get me hired", "9.1")))
    return (
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead reveal\">"
        "<div class=\"kick\">Lead intelligence</div>"
        "<h2>Every lead's goals, pains and fears, captured while they talk</h2>"
        "<p class=\"lead\">Stepan listens for what each lead really wants, what is blocking "
        "them, and what they are quietly afraid of, then turns it into a living profile you "
        "can segment, score and act on.</p></div>"
        "<div class=\"cloud reveal\">"
        "<div class=\"cloud-col c-goal\"><div class=\"cloud-h\">"
        f"<span class=\"dt g-goal\"></span>Goals</div>{goals}</div>"
        "<div class=\"cloud-col c-pain\"><div class=\"cloud-h\">"
        f"<span class=\"dt g-pain\"></span>Pains</div>{pains}</div>"
        "<div class=\"cloud-col c-fear\"><div class=\"cloud-h\">"
        f"<span class=\"dt g-fear\"></span>Fears</div>{fears}</div>"
        "</div>"
        "<p class=\"mnote\" style=\"margin-top:1rem\">Illustrative examples of what Stepan "
        "captures per lead.</p>"
        "</div></section>"
    )


def _persona_row(name: str, ver: str, desc: str, metric: str, hi: bool = False) -> str:
    cls = "prow hi" if hi else "prow"
    return (
        f'<div class="{cls}"><div class="prow-top">'
        f'<span class="prow-nm">{name}</span>'
        f'<span class="prow-ver">{ver}</span>'
        f'<div class="prow-metric"><b>{metric}</b><span>ready leads</span></div></div>'
        f'<div class="prow-desc">{desc}</div></div>'
    )


def _persona_library_section() -> str:
    """The Seller Persona Library — soft selling craft shared + versioned, catalog stays
    per-branch. Split layout: pitch left, versioned persona rows (with a close-rate metric,
    a number not a progress-track bar) right."""
    rows = (
        _persona_row("The Consultative Closer", "v2.1",
                     "Warm, asks sharp questions, times the offer to the buying signal.",
                     "41%", hi=True)
        + _persona_row("The Warm Advisor", "v1.4",
                       "Patient and reassuring, great with nervous first-time buyers.", "34%")
        + _persona_row("The Fast Mover", "v1.2",
                       "Concise and momentum-driven, built for high-volume inbound.", "29%")
    )
    bullets = "".join(
        f'<li>{_svg(_IC["check"], 16)}{b}</li>' for b in (
            "Load a proven persona per brand or location instead of writing one from scratch.",
            "Every persona is versioned, so you can roll back or improve the core over time.",
            "Stepan tracks which persona sells best and helps you roll out the winner.",
        ))
    return (
        "<section class=\"divide\"><div class=\"wrap\"><div class=\"plib\">"
        "<div class=\"reveal\">"
        "<div class=\"kick\">Seller persona library</div>"
        "<h2>A library of proven sales personas</h2>"
        "<p class=\"lead\">The selling craft is shared, your catalog stays yours. Pick a "
        "battle-tested persona for each brand, keep your own products and facts, and let the "
        "best-performing personas spread across your locations.</p>"
        f"<ul class=\"plq\">{bullets}</ul></div>"
        f"<div class=\"plib-list reveal\">{rows}</div>"
        "</div>"
        "<p class=\"mnote\" style=\"margin-top:1.4rem\">Illustrative personas and sample close "
        "rates.</p>"
        "</div></section>"
    )


def landing_html() -> str:
    steps = "".join([
        _step("01", "chat", "Greets every lead", "The moment someone DMs — from an ad, a comment, a "
              "story reply — Stepan answers in seconds, day or night."),
        _step("02", "search", "Qualifies like a pro", "It asks the right questions, uncovers the real "
              "goal and the pain behind it — not a rigid form, a real conversation."),
        _step("03", "trend", "Sells, not just chats", "Value before price, honest objection handling, "
              "the right offer at the right moment. It moves the deal forward."),
        _step("04", "users", "Follows up &amp; hands off", "Revives silent leads with fresh angles, and "
              "passes a hot, qualified lead to your team exactly when it's ready to buy."),
    ])
    feats = "".join([
        _feat("bulb", "Consultative selling", "Reaches the emotional layer and handles "
              "objections — a trusted advisor, not a FAQ bot."),
        _feat("msgs", "Instagram &amp; WhatsApp", "Meets buyers where they already are: IG, "
              "WhatsApp and Messenger DMs, one brain across all."),
        _feat("globe", "Speaks their language", "Replies in each lead's own language, "
              "automatically — no separate setup per market."),
        _feat("shield", "Never makes things up", "Every claim is grounded in your facts — no "
              "fake promises, no invented prices. It survives a screenshot."),
        _feat("refresh", "Smart follow-ups", "Brings back leads who went quiet — varied, natural, "
              "never spammy, and safe for your account."),
        _feat("arrow", "Human handoff", "Alerts your team and passes the lead the instant it's "
              "hot — never a dead-end bot."),
        _feat("chart", "Live funnel &amp; analytics", "See every stage, peak activity hours and "
              "which ad drives which sale — operator-grade, not vanity metrics."),
        _feat("grad", "You coach it in plain words", "Teach it a new fact or a better pitch in "
              "one sentence — it updates its own playbook, with your approval."),
    ])
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Stepan — the AI sales agent that closes in your DMs</title>"
        "<meta name=\"description\" content=\"Stepan is an AI sales agent that qualifies and "
        "sells to your leads in Instagram &amp; WhatsApp DMs — like your best rep, 24/7.\">"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>"
        "<link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&"
        "family=JetBrains+Mono:wght@400;500&"
        "family=Space+Grotesk:wght@500;600;700&display=swap\" rel=\"stylesheet\">"
        "<link rel=\"icon\" href=\"data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='8' fill='%23f2f4f7'/>"
        "<text x='16' y='23' font-size='20' font-weight='700' fill='black' "
        "text-anchor='middle' font-family='Arial'>S</text></svg>\">"
        f"<style>{_CSS}</style></head><body>"
        # nav — login top-right
        "<nav><div class=\"wrap nav\">"
        "<div class=\"brand\"><span class=\"logo\">S</span>Stepan"
        "<small>AI Sales Agent</small></div>"
        "<div style=\"display:flex;align-items:center;gap:1.1rem\">"
        "<a href=\"/whats-new\" style=\"font-size:.9rem;color:var(--mut)\">What's new</a>"
        "<a class=\"login\" href=\"/login\">Log in</a></div>"
        "</div></nav>"
        # hero — a live WebGL shader breathes behind the copy (falls back to the CSS grid)
        "<header class=\"hero\">"
        "<canvas id=\"herofx\" class=\"hero-fx\" aria-hidden=\"true\"></canvas>"
        "<div class=\"hero-scrim\"></div>"
        "<div class=\"wrap\">"
        "<span class=\"eyebrow\"><span class=\"d\"></span>Built for teams running serious lead "
        "volume</span>"
        "<h1>Your best salesperson,<br><em>scaled across every brand you run.</em></h1>"
        "<p class=\"sub\">Stepan greets, qualifies and actually <b>sells</b> to every lead in "
        "your Instagram and WhatsApp DMs, in any language.</p>"
        "<div class=\"cta\">"
        "<button class=\"btn btn-p\" onclick=\"openStepan()\">Talk to Stepan</button>"
        "<a class=\"btn btn-g\" href=\"#how\">See how it works</a>"
        "</div>"
        "<p class=\"note\">The best demo is Stepan itself. Message it and watch it qualify you."
        "</p>"
        f"{_trust_section()}"
        "</div></header>"
        # how it works
        "<section id=\"how\" class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">How it works</div>"
        "<h2>From \"hi\" to a hot lead — on its own</h2>"
        "<p class=\"lead\">Stepan runs the whole first conversation the way your best closer "
        "would, then hands you the ready-to-buy leads.</p></div>"
        f"<div class=\"steps\">{steps}</div>"
        "</div></section>"
        # ── a peek inside (illustrative mockups) ──
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">A peek inside</div>"
        "<h2>See what it actually does</h2>"
        "<p class=\"lead\">A real conversation on one side, your live dashboard on the other — "
        "Stepan works the lead end to end and hands you the ready-to-buy ones.</p></div>"
        "<div class=\"shots\">"
        # chat mockup
        "<div class=\"frame\">"
        "<div class=\"ph-top\"><span class=\"ph-ava\">M</span>"
        "<div><b>Maya</b><small>Instagram · online</small></div></div>"
        "<div class=\"ph-body\">"
        "<div class=\"mb in\">hi! saw your ad — is this ok if I've literally never trained "
        "before?</div>"
        "<div class=\"mb out\"><span class=\"who\">Stepan</span>Totally — that's exactly where "
        "most people start, Maya. Quick one: what would you most love to change first — "
        "energy, strength, or how you feel in your clothes?</div>"
        "<div class=\"mb in\">honestly how I feel in my clothes… but I have zero time for a "
        "gym</div>"
        "<div class=\"mb out\"><span class=\"who\">Stepan</span>Hear you — \"no time\" is the "
        "#1 reason people stall. It's built around 20-min sessions you can do at home, "
        "shaped to your week. Want me to show how your first two weeks would look?</div>"
        "<div class=\"mb in\">yes please</div>"
        "</div></div>"
        # dashboard mockup
        "<div class=\"frame\">"
        "<div class=\"fbar\"><i></i><i></i><i></i>"
        "<span class=\"furl\">stepan · dashboard</span></div>"
        "<div class=\"dash\">"
        "<div class=\"mcard\"><div class=\"mlbl\">Lead — captured automatically</div>"
        "<div class=\"leadrow\"><span class=\"av\">M</span><b>Maya R.</b>"
        "<span class=\"spill\">Qualifying</span></div>"
        "<div class=\"chips\">"
        "<span class=\"ch2\"><span class=\"dt d-goal\"></span>Goal · feel great in her clothes</span>"
        "<span class=\"ch2\"><span class=\"dt d-pain\"></span>Pain · no time for the gym</span>"
        "<span class=\"ch2\"><span class=\"dt d-gain\"></span>Gain · 20-min home workouts</span>"
        "</div></div>"
        "<div class=\"mcard\"><div class=\"mlbl\">Live funnel · this week</div>"
        "<div class=\"fn\">"
        "<div class=\"fnrow\"><span class=\"nm\">New</span>"
        "<span class=\"fnbar\" style=\"width:100%\"></span><span class=\"v num\">128</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Nurturing</span>"
        "<span class=\"fnbar\" style=\"width:58%\"></span><span class=\"v num\">74</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Qualifying</span>"
        "<span class=\"fnbar\" style=\"width:32%\"></span><span class=\"v num\">41</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Presenting</span>"
        "<span class=\"fnbar\" style=\"width:18%\"></span><span class=\"v num\">22</span></div>"
        "<div class=\"fnrow\"><span class=\"nm\">Ready</span>"
        "<span class=\"fnbar\" style=\"width:8%\"></span><span class=\"v num\">9</span></div>"
        "</div></div>"
        "<div class=\"alert\">Maya's ready to book — handed to your team just now.</div>"
        "</div></div>"
        "</div>"
        "<p class=\"mnote\">Illustrative — sample data, not a real customer.</p>"
        "</div></section>"
        # what Stepan captures per lead (goals/pains/fears) + the seller persona library
        + _insights_cloud_section()
        + _persona_library_section()
        # ad accounts: direct pull + product↔ad mapping + identity + re-qualification
        + _ad_accounts_section() +
        # analytics dashboard
        analytics_section() +
        # mcp / crm
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Works with your stack</div>"
        "<h2>Syncs to your CRM — through an open MCP connector</h2>"
        "<p class=\"lead\">Stepan ships a Model-Context-Protocol connector, so every qualified "
        "lead — with its captured needs, stage and source ad — flows straight into the CRM you "
        "already run. No brittle exports, no copy-paste.</p></div>"
        "<div class=\"mcp\">"
        f"<div class=\"mnode\">{_svg(_IC['bot'], 30)}<b>Stepan</b>"
        "<small>captures &amp; qualifies</small></div>"
        "<div class=\"mwire\"><div class=\"ln\"></div>"
        "<span class=\"mcpbadge\">MCP</span>"
        "<span class=\"mcpsub\">open connector</span><div class=\"ln\"></div></div>"
        f"<div class=\"mnode mcrm\">{_svg(_IC['crm'], 30)}<b>Your CRM</b>"
        "<div class=\"stacks\"><span>HubSpot</span><span>Salesforce</span>"
        "<span>Pipedrive</span><span>Custom</span></div></div>"
        "</div>"
        "<div class=\"syncs\">"
        "<span class=\"sync\"><span class=\"dt\"></span>Contact &amp; phone</span>"
        "<span class=\"sync\"><span class=\"dt\"></span>Goal · pain · gain</span>"
        "<span class=\"sync\"><span class=\"dt\"></span>Stage &amp; intent score</span>"
        "<span class=\"sync\"><span class=\"dt\"></span>Source ad &amp; campaign</span>"
        "<span class=\"sync\"><span class=\"dt\"></span>Full transcript</span>"
        "</div>"
        "<p class=\"mnote\">Illustrative — connector maps to your CRM's own fields.</p>"
        "</div></section>"
        # capabilities
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">What it does</div>"
        "<h2>Everything a great rep does — at scale</h2></div>"
        f"<div class=\"grid\">{feats}</div>"
        "</div></section>"
        # comparison
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Why Stepan</div>"
        "<h2>Not another flow bot</h2>"
        "<p class=\"lead\">Rule-based DM bots capture leads. Stepan closes them.</p></div>"
        "<div class=\"cmp\">"
        f"<div class=\"col bad\"><h3>{_svg(_IC['bot'], 20)}Typical DM bot</h3><ul>"
        "<li>Canned button flows — breaks off-script</li>"
        "<li>Can't handle a real objection</li>"
        "<li>Just collects a contact, then stalls</li>"
        "<li>Makes things up when it doesn't know</li>"
        "<li>One channel, one language</li></ul></div>"
        f"<div class=\"col good\"><h3>{_svg(_IC['trend'], 20)}Stepan</h3><ul>"
        "<li>Real conversation — adapts to every lead</li>"
        "<li>Diagnoses the pain, reframes objections</li>"
        "<li>Sells value, times the offer, drives the deal</li>"
        "<li>Grounded in your facts — never invents</li>"
        "<li>IG + WhatsApp + Messenger, any language</li></ul></div>"
        "</div></div></section>"
        # meta business agent comparison
        + _meta_compare_section() +
        # channels — with TikTok coming soon
        "<section class=\"divide\"><div class=\"wrap\">"
        "<div class=\"shead\">"
        "<div class=\"kick\">Where it works</div>"
        "<h2>Right inside the chats your buyers already use</h2></div>"
        "<div class=\"chan\">"
        f"<span class=\"pill\">{_svg(_IC['ig'], 18)}Instagram</span>"
        f"<span class=\"pill\">{_svg(_IC['wa'], 18)}WhatsApp</span>"
        f"<span class=\"pill\">{_svg(_IC['msgr'], 18)}Messenger</span>"
        f"<span class=\"pill soon\">{_svg(_IC['tiktok'], 18)}TikTok"
        "<span class=\"tag\">soon</span></span>"
        f"<span class=\"pill\">{_svg(_IC['telegram'], 18)}Telegram"
        "<span class=\"tag custom\">on request</span></span>"
        "</div>"
        "<p class=\"mnote\" style=\"margin-top:1rem\">"
        f"{_svg(_IC['plug'], 14)} Any messenger with an API can be wired in — Telegram is "
        "the same connector pattern as the channels above.</p>"
        "</div></section>"
        # pricing
        + _pricing_section() +
        # final CTA
        "<section class=\"divide\"><div class=\"wrap\"><div class=\"final\">"
        "<div class=\"kick\" style=\"position:relative\">See it for yourself</div>"
        "<h2>Let Stepan sell <em>you</em>.</h2>"
        "<p class=\"lead\">Message it like one of your leads and watch it qualify and pitch — "
        "in real time.</p>"
        "<div class=\"cta\">"
        "<button class=\"btn btn-p\" onclick=\"openStepan()\">Talk to Stepan</button></div>"
        "</div></div></section>"
        # footer
        "<footer><div class=\"wrap foot\">"
        "<div class=\"brand\" style=\"font-size:1rem\"><span class=\"logo\" "
        "style=\"width:24px;height:24px;font-size:.8rem\">S</span>Stepan</div>"
        "<div>AI sales agent for Instagram &amp; WhatsApp · "
        f"<a href=\"{_DEMO_IG}\" target=\"_blank\" rel=\"noopener\">Instagram</a> · "
        f"<a href=\"{_DEMO_WA}\" target=\"_blank\" rel=\"noopener\">WhatsApp</a> · "
        f"<a href=\"{_DEMO_TG}\" target=\"_blank\" rel=\"noopener\">Telegram</a> · "
        f"<a href=\"{_DEMO_FB}\" target=\"_blank\" rel=\"noopener\">Facebook</a> · "
        "<a href=\"/whats-new\">What's new</a> · "
        "<a href=\"/login\">Log in</a></div>"
        "</div></footer>"
        # live demo chat widget — Stepan sells itself (POST /demo/chat)
        f"<button class=\"stp-fab\" id=\"stp-fab\" onclick=\"openStepan()\">{_svg(_IC['chat'], 18)}Chat with Stepan</button>"
        "<div id=\"stp-w\" class=\"stp-w\">"
        "<div class=\"stp-hd\"><span class=\"logo\">S</span>"
        "<div><b>Stepan</b><small>Live demo · a real conversation</small></div>"
        "<button class=\"stp-x\" onclick=\"closeStepan()\" aria-label=\"Close\">×</button></div>"
        "<div id=\"stp-body\" class=\"stp-body\"></div>"
        "<div class=\"stp-foot\">"
        "<textarea id=\"stp-in\" rows=\"1\" placeholder=\"Message like one of your leads…\""
        " onkeydown=\"stpKey(event)\"></textarea>"
        "<button class=\"stp-send\" onclick=\"sendStepan()\" aria-label=\"Send\">➤</button></div>"
        "</div>"
        f"<script>{_WIDGET_JS}</script>"
        f"<script>{_FX_JS}</script>"
        "</body></html>"
    )

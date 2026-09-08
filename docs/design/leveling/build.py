#!/usr/bin/env python3
"""Build the .dc.html artboards for the Patchbay loudness-leveling design canvas.

Values are lifted from the shipped Patchbay stylesheet
(app/out/renderer/assets/index-*.css) so the mockups match the real app.
Four exceptions: --sz-glass-line, --sz-glass-rim, --sz-control-line and
--sz-control-fill are referenced by .mod/.step/.device there but defined nowhere
in the shipped bundle, so those values are the intended reading, not lifted ones.
Run:  python3 build.py
"""
import math, os, random

HERE = os.path.dirname(os.path.abspath(__file__))

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Bricolage+Grotesque:opsz,wght@12..96,400..800&'
         'family=Martian+Mono:wdth,wght@75..112.5,300..700&display=swap">')

CSS = """
:root{
  --sz-bg:#12100d; --sz-panel:#1b1814; --sz-panel-deep:#0f0d0a; --sz-surface-raised:#1e1a15;
  --sz-line:rgba(255,240,214,.08); --sz-line-strong:rgba(255,240,214,.2);
  --sz-glass-line:rgba(255,240,214,.08); --sz-glass-rim:rgba(255,240,214,.16);
  --sz-control-line:rgba(255,240,214,.08); --sz-control-fill:rgba(255,240,214,.03);
  --sz-text:#f4efe6; --sz-dim:#9b917e; --sz-faint:#6b6355;
  --sz-accent:#ffa028; --sz-accent-deep:#ff8a1f; --sz-accent-soft:rgba(255,160,40,.13);
  --sz-accent-ink:#241705; --sz-success:#58d68a; --sz-danger:#ff7a5c;
  --sz-danger-strong:#ff8a7a; --sz-danger-strong-line:rgba(255,138,122,.4);
  --sz-danger-strong-wash:rgba(255,138,122,.12);
  --sz-font-display:'Bricolage Grotesque',system-ui,sans-serif;
  --sz-font-mono:'Martian Mono',ui-monospace,'SF Mono',monospace;
}
*{box-sizing:border-box;}
body{margin:0;background:var(--sz-bg);color:var(--sz-text);
  font-family:var(--sz-font-display);font-size:14px;-webkit-font-smoothing:antialiased;}
a{color:var(--sz-accent);} a:hover{color:var(--sz-accent-deep);}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;}
h1,h2,h3,p,dl,dd,dt,ul,li{margin:0;padding:0;}
li{list-style:none;}
.grow{flex:1 1 auto;}
.mono{font-family:var(--sz-font-mono);font-variant-numeric:tabular-nums;
  font-variation-settings:'wdth' 87.5;}

/* ---- the app ground: two radial washes + a fine noise veil ---- */
.ground{position:relative;overflow:hidden;background:var(--sz-bg);}
.ground::before{content:'';position:absolute;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(900px 620px at 16% -12%,rgba(255,160,40,.09),transparent 60%),
             radial-gradient(760px 540px at 96% 112%,rgba(69,214,181,.05),transparent 60%);}
.ground::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:9;opacity:.05;
  mix-blend-mode:overlay;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='220' height='220' filter='url(%23n)'/%3E%3C/svg%3E");}
.app{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;}

/* ---- titlebar ---- */
.titlebar{height:52px;flex:none;display:flex;align-items:center;gap:14px;
  padding:0 16px 0 92px;border-bottom:1px solid var(--sz-line);}
.wm{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:700;letter-spacing:.01em;}
.wm svg{display:block;color:var(--sz-accent);}
.tb-sess{display:flex;align-items:center;gap:7px;color:var(--sz-dim);font-size:11.5px;}

/* ---- layout ---- */
.view{flex:1 1 auto;overflow:hidden;padding:20px 22px 24px;}
.rack{display:grid;gap:12px;}

/* ---- module card (glass; mac) ---- */
.mod{border-radius:20px;border:1px solid var(--sz-glass-line);border-top-color:var(--sz-glass-rim);
  background:linear-gradient(180deg,rgba(255,240,220,.042),rgba(255,240,220,0) 40%),
             color-mix(in srgb,var(--sz-panel) 70%,transparent);
  backdrop-filter:blur(20px) saturate(1.12);box-shadow:0 12px 30px rgba(0,0,0,.36);}
.mod.live{border-top-color:rgba(255,160,40,.3);
  box-shadow:0 0 0 1px rgba(255,160,40,.1),0 12px 34px rgba(0,0,0,.4);}
.mod-head{display:flex;align-items:center;gap:10px;padding:13px 16px;flex-wrap:wrap;}
.mod-head h2{font-size:13.5px;font-weight:700;white-space:nowrap;}
.mod-head .acts{display:flex;align-items:center;gap:8px;}
.mod-body{padding:0 16px 15px;display:grid;gap:13px;}

/* ---- micro type ---- */
.eyebrow{font-size:9.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:var(--sz-faint);}
.hint{font-size:11px;line-height:1.55;color:var(--sz-faint);text-wrap:pretty;}

/* ---- facts ---- */
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(146px,1fr));gap:12px 18px;
  padding-top:12px;border-top:1px solid var(--sz-line);}
.fact dt{font-size:9px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  color:var(--sz-faint);margin-bottom:4px;}
.fact dd{font-family:var(--sz-font-mono);font-size:10.5px;line-height:1.5;color:var(--sz-text);
  font-variation-settings:'wdth' 87.5;font-variant-numeric:tabular-nums;}
.fact dd.muted{color:var(--sz-dim);}

/* ---- controls ---- */
.pill{display:inline-flex;align-items:center;gap:7px;padding:9px 16px;border-radius:999px;
  font-size:13px;font-weight:600;letter-spacing:.01em;border:1px solid transparent;}
.pill.small{padding:6px 12px;font-size:12px;}
.pill.big{padding:13px 28px;font-size:15px;}
.pill.ghost{border-color:var(--sz-line);color:var(--sz-text);}
.pill.primary{background:linear-gradient(180deg,#ffbe58,var(--sz-accent-deep));
  color:var(--sz-accent-ink);
  box-shadow:0 1px 0 rgba(255,255,255,.25) inset,0 6px 22px rgba(255,150,40,.28);}
.pill.danger{color:var(--sz-danger-strong);border-color:var(--sz-danger-strong-line);}
.pill.dim{color:var(--sz-dim);border-color:var(--sz-line);}
.pill:disabled{opacity:.45;cursor:default;box-shadow:none;}
.pill svg{display:block;}
.chip{width:26px;height:24px;border-radius:7px;border:1px solid var(--sz-line);font-size:11px;
  font-weight:800;color:var(--sz-dim);display:grid;place-items:center;}
.chip.active{background:var(--sz-accent-soft);
  border-color:color-mix(in srgb,var(--sz-accent) 55%,transparent);color:var(--sz-accent);}
.badge{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--sz-dim);border:1px solid var(--sz-line-strong);border-radius:99px;padding:2px 7px;
  display:inline-flex;align-items:center;gap:5px;}
.badge.live{color:var(--sz-success);border-color:rgba(88,214,138,.42);}
.badge.attn{color:var(--sz-accent);border-color:rgba(255,160,40,.45);}
.badge.off{color:var(--sz-faint);border-color:var(--sz-line);}
.badge.bad{color:var(--sz-danger-strong);border-color:var(--sz-danger-strong-line);}
.dot{width:7px;height:7px;border-radius:50%;flex:none;}
.dot.ok{background:var(--sz-success);box-shadow:0 0 8px rgba(88,214,138,.7);}
.dot.idle{background:var(--sz-faint);}
.dot.warn{background:var(--sz-accent);box-shadow:0 0 8px rgba(255,160,40,.7);
  animation:pulse 1.6s ease-in-out infinite;}
.dot.bad{background:var(--sz-danger);box-shadow:0 0 8px rgba(255,122,92,.6);}
@keyframes pulse{50%{opacity:.45;}}
.mode-seg{display:inline-flex;border:1px solid var(--sz-line);border-radius:999px;overflow:hidden;}
.mode-seg button{padding:5px 14px;font-size:12px;font-weight:600;color:var(--sz-dim);}
.mode-seg button.on{background:var(--sz-accent-soft);color:var(--sz-accent);}
.strip{display:flex;align-items:center;gap:11px;padding:10px 13px;border-radius:13px;
  border:1px solid rgba(255,160,40,.28);background:var(--sz-accent-soft);font-size:12px;
  line-height:1.5;}
.strip svg{flex:none;color:var(--sz-accent);}
.strip.bad{border-color:var(--sz-danger-strong-line);background:var(--sz-danger-strong-wash);}
.strip.bad svg{color:var(--sz-danger-strong);}
.strip.quiet{border-color:var(--sz-line);background:rgba(255,240,214,.03);color:var(--sz-dim);}
.strip.quiet svg{color:var(--sz-faint);}

/* ---- level meter ---- */
.meter{display:flex;align-items:center;gap:12px;}
.meter .box{flex:1 1 auto;min-width:0;position:relative;}
.lvl{display:block;position:relative;height:10px;border-radius:3px;background:var(--sz-panel-deep);
  border:1px solid var(--sz-line);overflow:hidden;}
.lvl .fill{position:absolute;left:0;top:0;bottom:0;border-radius:2px;
  background:linear-gradient(90deg,rgba(255,160,40,.45),var(--sz-accent));}
.lvl .fill.hot{background:linear-gradient(90deg,rgba(255,160,40,.45),var(--sz-accent) 72%,
  var(--sz-danger) 100%);}
.lvl .fill.cool{background:linear-gradient(90deg,rgba(255,160,40,.28),rgba(255,160,40,.62));}
.lvl .peak{position:absolute;top:0;bottom:0;width:2px;background:var(--sz-text);opacity:.6;}
.thr{position:absolute;top:-3px;height:16px;width:0;border-left:1px dashed var(--sz-accent);
  opacity:.9;}
.scale{display:flex;justify-content:space-between;margin-top:5px;font-family:var(--sz-font-mono);
  font-size:8.5px;color:var(--sz-faint);font-variation-settings:'wdth' 87.5;}
.meter .read{flex:none;text-align:right;min-width:64px;}
.meter .read .n{font-family:var(--sz-font-mono);font-size:13px;color:var(--sz-text);
  font-variant-numeric:tabular-nums;font-variation-settings:'wdth' 87.5;}
.meter .read .u{font-size:9px;font-weight:700;letter-spacing:.12em;color:var(--sz-faint);
  text-transform:uppercase;}

/* ---- looper pedal control ---- */
.pedal{display:grid;justify-items:center;gap:10px;}
.pedalbtn{position:relative;width:132px;height:132px;border-radius:50%;display:grid;
  place-items:center;border:1px solid var(--sz-line-strong);
  background:radial-gradient(120% 120% at 50% 0%,rgba(255,240,220,.07),rgba(255,240,220,0) 58%),
             var(--sz-surface-raised);
  box-shadow:0 10px 26px rgba(0,0,0,.45),0 1px 0 rgba(255,255,255,.05) inset;}
.pedalbtn .cap{display:grid;justify-items:center;gap:6px;}
.pedalbtn .lab{font-size:15px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;}
.pedalbtn .sub{font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--sz-faint);}
.pedalbtn.idle .lab{color:var(--sz-accent);}
.pedalbtn.armed{border-color:rgba(255,160,40,.6);
  box-shadow:0 0 0 6px rgba(255,160,40,.07),0 0 34px rgba(255,160,40,.22),
             0 10px 26px rgba(0,0,0,.45);animation:breathe 1.9s ease-in-out infinite;}
.pedalbtn.armed .lab{color:var(--sz-accent);}
@keyframes breathe{50%{box-shadow:0 0 0 12px rgba(255,160,40,.045),
  0 0 44px rgba(255,160,40,.3),0 10px 26px rgba(0,0,0,.45);}}
.pedalbtn.rec{border-color:rgba(255,160,40,.5);}
.pedalbtn.rec .lab{color:var(--sz-text);}
.pedalbtn .ring{position:absolute;inset:-9px;}
.pedalbtn .ring circle{fill:none;}
.pedal-cap{font-size:10.5px;color:var(--sz-faint);text-align:center;line-height:1.5;}
.kbd{display:inline-grid;place-items:center;min-width:26px;height:22px;padding:0 9px;
  border-radius:7px;border:1px solid var(--sz-line-strong);background:rgba(255,240,214,.04);
  font-family:var(--sz-font-mono);font-size:9.5px;font-weight:700;color:var(--sz-dim);
  vertical-align:middle;}
.pedalbtn.armed ~ .pedal-cap .kbd,.pedalbtn.rec ~ .pedal-cap .kbd{color:var(--sz-text);
  border-color:rgba(255,160,40,.4);}

/* ---- waveform lane ---- */
.lane{position:relative;border-radius:13px;background:var(--sz-panel-deep);
  border:1px solid var(--sz-line);overflow:hidden;}
.lane svg{display:block;}
.lane .head{position:absolute;top:0;bottom:0;width:1px;background:var(--sz-accent);
  box-shadow:0 0 12px 2px rgba(255,160,40,.55);}
.lane .trimzone{position:absolute;top:0;bottom:0;background:rgba(8,6,3,.62);}
.lane .handle{position:absolute;top:0;bottom:0;width:1px;background:rgba(255,240,214,.45);}
.lane .handle::after{content:'';position:absolute;top:50%;left:-3px;width:7px;height:22px;
  margin-top:-11px;border-radius:3px;background:var(--sz-surface-raised);
  border:1px solid var(--sz-line-strong);}
.lane .tag{position:absolute;top:7px;font-family:var(--sz-font-mono);font-size:8.5px;
  color:var(--sz-faint);font-variation-settings:'wdth' 87.5;}

/* ---- table ---- */
.thead,.trow{display:grid;align-items:center;
  grid-template-columns:22px 30px minmax(0,1fr) 74px 68px 82px 214px;gap:10px;}
.selbox{position:relative;width:17px;height:17px;border-radius:5px;
  border:1px solid var(--sz-line-strong);display:grid;place-items:center;background:none;}
.selbox svg{opacity:0;color:var(--sz-accent-ink);}
.selbox.on{background:var(--sz-accent);border-color:var(--sz-accent);}
.selbox.on svg{opacity:1;}
.selbox.some{border-color:var(--sz-accent);}
.selbox.some::after{content:'';position:absolute;left:50%;top:50%;
  transform:translate(-50%,-50%);width:8px;height:2px;border-radius:1px;
  background:var(--sz-accent);}
.trow.skip .nm,.trow.skip .num{color:var(--sz-faint);}
.trow.skip .idx{opacity:.6;}
.thead{padding:0 12px 8px;border-bottom:1px solid var(--sz-line);}
.thead span{font-size:9px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  color:var(--sz-faint);}
.trow{padding:9px 12px;border-radius:12px;border:1px solid transparent;}
.trow.on{background:rgba(255,240,214,.035);border-color:var(--sz-line);}
.trow .nm{font-size:12.5px;font-weight:600;display:flex;align-items:center;gap:8px;min-width:0;}
.num{font-family:var(--sz-font-mono);font-size:11px;font-variant-numeric:tabular-nums;
  font-variation-settings:'wdth' 87.5;text-align:right;color:var(--sz-text);}
.num.dim{color:var(--sz-dim);}
.idx{font-family:var(--sz-font-mono);font-size:10px;color:var(--sz-faint);
  font-variation-settings:'wdth' 87.5;}
.right{text-align:right;}

.fine{font-size:11.5px;color:var(--sz-dim);line-height:1.55;text-wrap:pretty;}
.modal-scrim{position:absolute;inset:0;z-index:70;background:rgba(8,6,3,.62);
  backdrop-filter:blur(8px);display:grid;place-items:center;}
.modal-card{width:min(620px,calc(100% - 60px));background:var(--sz-surface-raised);
  border:1px solid var(--sz-line-strong);border-radius:18px;padding:28px;
  box-shadow:0 30px 80px rgba(0,0,0,.6);}
.modal-card h2{font-size:16px;font-weight:700;margin-bottom:6px;
  font-variation-settings:'wdth' 92;}
.modal-card>p.fine{margin-bottom:16px;max-width:52ch;}
.modal-actions{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap;align-items:center;}
.tog{display:flex;align-items:center;gap:12px;width:100%;text-align:left;padding:10px 11px;
  border-radius:12px;border:1px solid transparent;background:rgba(255,240,214,.035);}
.tog .meta{flex:1 1 auto;min-width:0;display:grid;gap:3px;}
.tog .meta .n{font-size:12.5px;font-weight:600;}
.tog .meta .d{font-size:10.5px;color:var(--sz-faint);line-height:1.55;}
.tog .badge{flex:none;white-space:nowrap;}
.tog .selbox{flex:none;}

/* ---- signed correction bar ---- */
.corr{display:flex;align-items:center;gap:10px;}
.corr-track{position:relative;flex:1 1 auto;height:16px;}
.corr-track::before{content:'';position:absolute;left:0;right:0;top:7px;height:2px;
  background:rgba(255,240,214,.05);border-radius:2px;}
.corr-zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--sz-line-strong);}
.corr-bar{position:absolute;top:5px;height:6px;border-radius:2px;background:var(--sz-accent);
  opacity:.9;}
.corr-bar.big{box-shadow:0 0 10px rgba(255,160,40,.45);}
.corr-bar.muted{background:var(--sz-faint);}
.corr.muted .corr-num{color:var(--sz-faint);}
.corr-num{flex:none;width:48px;text-align:right;font-family:var(--sz-font-mono);font-size:11px;
  font-variant-numeric:tabular-nums;font-variation-settings:'wdth' 87.5;}

/* ---- iteration steps ---- */
.steps{display:grid;gap:8px;}
.step{display:grid;grid-template-columns:26px 1fr auto;align-items:start;gap:13px;
  padding:12px 15px;backdrop-filter:blur(16px);border-radius:15px;border:1px solid var(--sz-glass-line);
  border-top-color:var(--sz-glass-rim);
  background:color-mix(in srgb,var(--sz-panel) 62%,transparent);}
.step.ok{opacity:.72;}
.step.missing{opacity:.72;}
.step.busy{border-color:rgba(255,160,40,.34);
  background:color-mix(in srgb,var(--sz-panel) 78%,transparent);}
.step .num{width:26px;height:26px;border-radius:9px;display:grid;place-items:center;
  font-family:var(--sz-font-mono);font-size:10px;font-weight:700;
  border:1px solid var(--sz-control-line);background:var(--sz-control-fill);
  color:var(--sz-faint);}
.step.ok .num{border-color:rgba(88,214,138,.4);color:var(--sz-success);
  background:rgba(88,214,138,.1);}
.step .txt{min-width:0;}
.step .txt h3{font-size:12.5px;font-weight:650;margin-bottom:3px;}
.step .txt p{font-size:11px;color:var(--sz-dim);line-height:1.5;text-wrap:pretty;}
.step .txt p code{font-family:var(--sz-font-mono);font-size:10px;color:var(--sz-faint);
  font-variation-settings:'wdth' 87.5;}
.step .right{display:flex;align-items:center;gap:8px;padding-top:2px;}
.step.busy .num{border-color:rgba(255,160,40,.5);color:var(--sz-accent);
  background:var(--sz-accent-soft);}
.step .t{font-size:12px;}
.step .t em{font-style:normal;color:var(--sz-dim);}

/* ---- thin progress ---- */
.prog{height:3px;border-radius:99px;background:rgba(255,240,214,.09);overflow:hidden;}
.prog span{display:block;height:100%;border-radius:99px;
  background:linear-gradient(90deg,var(--sz-accent-deep),#ffd489);}

.dock{flex:none;height:54px;display:flex;align-items:center;gap:18px;padding:0 22px;
  border-top:1px solid var(--sz-line);
  background:color-mix(in srgb,var(--sz-panel-deep) 62%,transparent);}
.dports{display:flex;align-items:center;gap:17px;}
.dp{display:flex;align-items:center;gap:8px;}
.dp i{font-style:normal;font-size:9px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--sz-faint);width:32px;}
.dp .lvl{width:62px;height:7px;}
.dp b{font-family:var(--sz-font-mono);font-size:9.5px;font-weight:400;color:var(--sz-dim);
  width:32px;text-align:right;font-variation-settings:'wdth' 87.5;}

/* ---- port meter rows (IOMeter strip) ---- */
.ports{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 22px;}
.port{display:grid;grid-template-columns:44px minmax(0,1fr) 54px 30px;align-items:center;gap:9px;}
.port .pn{font-size:9.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--sz-dim);}
.port .pv{font-family:var(--sz-font-mono);font-size:10px;text-align:right;color:var(--sz-text);
  font-variant-numeric:tabular-nums;font-variation-settings:'wdth' 87.5;}
.port .pv.off{color:var(--sz-faint);}
.lim{justify-self:end;font-size:8px;font-weight:800;letter-spacing:.08em;padding:2px 5px;
  border-radius:5px;border:1px solid var(--sz-line);color:var(--sz-faint);}
.lim.on{color:var(--sz-danger-strong);border-color:var(--sz-danger-strong-line);
  background:var(--sz-danger-strong-wash);}
.scenes{display:flex;gap:7px;flex-wrap:wrap;}
.scene{display:grid;justify-items:center;gap:5px;min-width:52px;}
.scene .lb{font-family:var(--sz-font-mono);font-size:9px;color:var(--sz-faint);
  font-variation-settings:'wdth' 87.5;}
"""

ICON = {
    "info": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.6v.6"/></svg>',
    "alert": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4.5 21 19H3z"/><path d="M12 10v4"/><path d="M12 16.6v.5"/></svg>',
    "play": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M8 5.5 18.5 12 8 18.5z"/></svg>',
    "stop": '<svg width="26" height="26" viewBox="0 0 24 24" fill="currentColor"><rect x="7.5" y="7.5" width="9" height="9" rx="1.6"/></svg>',
    "trash": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4.5 6.5h15"/><path d="M9.5 6.5V4.8h5v1.7"/><path d="M6.6 6.5 7.5 19h9l.9-12.5"/></svg>',
    "pause": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9.5 6v12M14.5 6v12"/></svg>',
    "book": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10a3 3 0 0 1 2 5.2V19a3 3 0 0 0-2-.8H5.5A1.5 1.5 0 0 1 4 16.7z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H14a3 3 0 0 0-2 5.2V19a3 3 0 0 1 2-.8h4.5a1.5 1.5 0 0 0 1.5-1.5z"/></svg>',
    "check": '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 10 17.5 19 7"/></svg>',
    "chev": '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5 12 15.5 18 9.5"/></svg>',
    "wave": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M3 12h2"/><path d="M8 7.5v9"/><path d="M12 4.5v15"/><path d="M16 8.5v7"/><path d="M20 11h1"/></svg>',
    "logo": '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="6" cy="6.5" r="2.6"/><circle cx="18" cy="17.5" r="2.6"/><path d="M6 9.1v4.4a4.4 4.4 0 0 0 4.4 4.4h4.9"/></svg>',
}


# ---------------------------------------------------------------- waveform ---
def bars(n, w, h, seed, notes, gate=None):
    """Bar heights for a plucked-guitar envelope, deterministic per seed."""
    random.seed(seed)
    pitch = w / n
    bw = max(1.5, pitch * 0.56)
    out = []
    for i in range(n):
        t = (i + 0.5) / n
        amp = 0.0
        for t0, a, k in notes:
            if t >= t0:
                amp = max(amp, a * math.exp(-(t - t0) * k))
        amp *= 0.5 + 0.5 * random.random()
        if gate is not None:
            lo, hi, floor = gate
            if t < lo or t > hi:
                amp *= floor
        amp = min(1.0, amp)
        hh = max(1.4, amp * (h / 2 - 3))
        out.append((i * pitch + (pitch - bw) / 2, bw, hh))
    return out


RIFF = [(0.015, 1.0, 7.5), (0.13, 0.86, 8.5), (0.245, 0.98, 6.5), (0.375, 0.72, 9.0),
        (0.47, 0.93, 7.0), (0.60, 1.0, 6.0), (0.725, 0.8, 8.0), (0.845, 0.9, 5.5)]


def kcap(tail):
    """The pedal is a footswitch; on a desk the foot is the space bar."""
    return '<div class="pedal-cap"><span class="kbd">space</span> %s</div>' % tail


def lane_svg(w, h, seed=7, notes=RIFF, n=None, lit_to=1.0, dim_color="#6b6355",
             gate=None, show_dim=True):
    n = n or int(w / 3.6)
    rs = bars(n, w, h, seed, notes, gate)
    cy = h / 2
    lit, dim = [], []
    for x, bw, hh in rs:
        r = ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f"/>'
             % (x, cy - hh, bw, hh * 2, min(bw / 2, 1.2)))
        if (x + bw / 2) / w <= lit_to:
            lit.append(r)
        elif show_dim:
            dim.append(r)
    return (
        '<svg width="100%%" height="%d" viewBox="0 0 %d %d" preserveAspectRatio="none"'
        ' xmlns="http://www.w3.org/2000/svg">'
        '<g fill="#ffa028" opacity=".5" style="filter:blur(5px)">%s</g>'
        '<g fill="#ffa028" opacity=".95">%s</g>'
        '<g fill="%s" opacity=".35">%s</g>'
        '</svg>' % (h, w, h, "".join(lit), "".join(lit), dim_color, "".join(dim))
    )


def pc(db, floor=-60.0, ceil=0.0):
    """One mapping for every bar in the design: linear in dB over the meter's
    span. The scale labels are evenly spaced, so they only tell the truth if the
    bars are linear in dB too."""
    if db is None:
        return 0.0
    return max(0.0, min(100.0, (db - floor) / (ceil - floor) * 100.0))


def dbs(db):
    return "&minus;%.1f" % abs(db) if db < 0 else "%.1f" % db


def meter(pct, label, unit="dBFS", thr=None, peak=None, cls="", scale=True):
    t = ('<span class="thr" style="left:%.1f%%"></span>' % thr) if thr is not None else ""
    p = ('<span class="peak" style="left:%.1f%%"></span>' % peak) if peak is not None else ""
    sc = ('<div class="scale"><span>&minus;60</span><span>&minus;40</span>'
          '<span>&minus;20</span><span>0</span></div>') if scale else ""
    return ('<div class="meter"><div class="box"><div class="lvl">'
            '<span class="fill %s" style="width:%.1f%%"></span>%s</div>%s%s</div>'
            '<div class="read"><div class="n">%s</div><div class="u">%s</div></div></div>'
            % (cls, pct, p, t, sc, label, unit))


# ------------------------------------------------------------------ shell ----
def doc(body, w, h, extra=""):
    return ('<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
            '<script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n'
            '<helmet>\n%s\n<style>%s%s</style>\n</helmet>\n'
            '<div class="ground" style="width:%dpx;height:%dpx">%s</div>\n'
            '</x-dc>\n</body>\n</html>\n' % (FONTS, CSS, extra, w, h, body))


def shell(inner):
    return '<div class="app">%s</div>' % inner


def titlebar(active="Leveling"):
    segs = "".join('<button type="button"%s>%s</button>'
                   % (' class="on"' if s == active else "", s)
                   for s in ("Device", "Leveling", "Logs"))
    return ('<div class="titlebar">'
            '<div class="wm">%s<span>Patchbay</span></div>'
            '<div class="mode-seg">%s</div>'
            '<div class="grow"></div>'
            '<span class="badge live"><i class="dot ok"></i>Bridge</span>'
            '<span class="tb-sess">Quad Cortex &middot; 48 kHz</span>'
            '</div>' % (ICON["logo"], segs))


def dock(limiting=False):
    rows = [("XLR 1", -22.8), ("XLR 2", -24.0), ("Out 3", -35.4), ("Out 4", -35.4),
            ("HP", -29.4)]
    dps = "".join('<span class="dp"><i>%s</i><span class="lvl">'
                  '<span class="fill" style="width:%.1f%%"></span></span>'
                  '<b>%s</b></span>' % (n, pc(d), dbs(d)) for n, d in rows)
    tail = ('<span class="badge bad"><i class="dot bad"></i>XLR limiting</span>' if limiting
            else '<span class="badge off">limiters clear</span>')
    return ('<div class="dock"><span class="eyebrow">Outputs</span>'
            '<div class="dports">%s</div><div class="grow"></div>%s</div>' % (dps, tail))


def mod(title, badge, acts, body, cls=""):
    return ('<section class="mod %s"><header class="mod-head"><h2>%s</h2>%s'
            '<div class="grow"></div><div class="acts">%s</div></header>'
            '<div class="mod-body">%s</div></section>' % (cls, title, badge, acts, body))


# ------------------------------------------------- artboard 1: Main (idle) ---
def sampler_settings():
    rows = [("Start threshold", "&minus;40 dBFS", "recording begins on the first note above this"),
            ("Stop after silence", "1.5 s", "or press stop yourself"),
            ("Maximum length", "30 s", "a riff, not a performance")]
    cells = "".join(
        '<div><div class="eyebrow">%s</div>'
        '<div class="mono" style="font-size:13px;margin:5px 0 4px">%s</div>'
        '<div class="hint">%s</div></div>' % r for r in rows)
    return ('<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;'
            'padding-top:12px;border-top:1px solid var(--sz-line)">%s</div>' % cells)


def sampler_idle_body():
    return (
        '<div class="strip">%s<span>Play the riff you want every preset leveled against. '
        'Recording starts on your first note and stops when you stop playing.</span></div>'
        '<div style="display:grid;grid-template-columns:172px minmax(0,1fr);gap:22px;'
        'align-items:center">'
        '<div class="pedal"><button type="button" class="pedalbtn idle">'
        '<span class="cap"><span class="lab">Arm</span>'
        '<span class="sub">then play</span></span></button>%s</div>'
        '<div style="display:grid;gap:14px">'
        '<div><div class="eyebrow" style="margin-bottom:8px">Input &middot; USB 1/2 dry DI</div>%s</div>'
        '<div class="strip quiet">%s<span>No reference sample yet. The one you record is kept '
        'until you replace it, and every preset is measured with it.</span></div>'
        '</div></div>%s' % (ICON["info"], kcap("to arm"),
                            meter(pc(-58.1), dbs(-58.1), thr=pc(-40), cls="cool"),
                            ICON["wave"], sampler_settings()))


def build_main():
    acts = ('<button type="button" class="pill small ghost">USB 1/2 &middot; dry DI %s</button>'
            % ICON["chev"])
    m = mod("Reference sample", '<span class="badge off">Not recorded</span>', acts,
            sampler_idle_body())
    presets = mod(
        "Level presets", '<span class="badge off">Needs a reference sample</span>',
        '<button type="button" class="pill small ghost" disabled="disabled">Measure setlist</button>',
        '<p class="hint" style="font-size:12px">Record a reference sample first. Patchbay then '
        'plays it into each preset through the USB reamp input, measures what comes back, and '
        'reports the correction in dB &mdash; or writes it for you.</p>')
    return doc(shell(titlebar() + '<div class="view"><div class="rack">%s%s</div></div>'
                     % (m, presets) + dock()), 1120, 660)


# ------------------------------------------------------ artboard 2: armed ---
def build_armed():
    body = (
        '<div class="strip">%s<span>Listening. Recording starts on the first note above '
        '&minus;40 dBFS.</span><div class="grow"></div>'
        '<button type="button" class="pill small ghost">Cancel</button></div>'
        '<div style="display:grid;grid-template-columns:172px minmax(0,1fr);gap:22px;'
        'align-items:center">'
        '<div class="pedal"><button type="button" class="pedalbtn armed">'
        '<span class="cap"><span class="lab">Listening</span>'
        '<span class="sub">play to start</span></span></button>%s</div>'
        '<div style="display:grid;gap:14px">'
        '<div><div class="eyebrow" style="margin-bottom:8px">Input &middot; USB 1/2 dry DI</div>%s'
        '<div class="hint" style="margin-top:7px">The dashed line is the start threshold.</div>'
        '</div></div></div>'
        % (ICON["info"], kcap("to cancel"),
           meter(pc(-46.3), dbs(-46.3), thr=pc(-40), peak=pc(-44.0), cls="cool")))
    m = mod("Reference sample", '<span class="badge attn"><i class="dot warn"></i>Armed</span>',
            '<button type="button" class="pill small ghost">USB 1/2 &middot; dry DI</button>',
            body, cls="live")
    return doc('<div style="padding:20px">%s</div>' % m, 680, 360)


# -------------------------------------------------- artboard 3: recording ---
def build_recording():
    ring = ('<svg class="ring" viewBox="0 0 150 150">'
            '<circle cx="75" cy="75" r="72" stroke="rgba(255,240,214,.07)" stroke-width="3"/>'
            '<circle cx="75" cy="75" r="72" stroke="#ffa028" stroke-width="3"'
            ' stroke-linecap="round" stroke-dasharray="452" stroke-dashoffset="211"'
            ' transform="rotate(-90 75 75)" opacity=".9"/></svg>')
    lane = ('<div class="lane" style="height:96px">%s'
            '<div class="head" style="right:0"></div>'
            '<div class="tag" style="left:10px">0:00</div></div>'
            % lane_svg(414, 96, seed=11, show_dim=False))
    body = (
        '<div style="display:grid;grid-template-columns:172px minmax(0,1fr);gap:22px;'
        'align-items:center">'
        '<div class="pedal"><button type="button" class="pedalbtn rec">%s'
        '<span class="cap"><span class="lab">%s</span>'
        '<span class="sub">stop</span></span></button>%s'
        '<div class="pedal-cap">Stops in 0.8 s<br>unless you play</div></div>'
        '<div style="display:grid;gap:12px">'
        '<div style="display:flex;align-items:baseline;gap:10px">'
        '<span class="mono" style="font-size:26px;letter-spacing:-.01em">0:07.4</span>'
        '<span class="eyebrow">elapsed of 0:30 max</span><div class="grow"></div>'
        '<span class="mono" style="font-size:11px;color:var(--sz-dim)">peak &minus;3.4 dBFS</span>'
        '</div>%s</div></div>' % (ring, ICON["stop"], kcap("to stop"), lane))
    m = mod("Reference sample", '<span class="badge attn"><i class="dot warn"></i>Recording</span>',
            '<button type="button" class="pill small ghost">USB 1/2 &middot; dry DI</button>',
            body, cls="live")
    return doc('<div style="padding:20px">%s</div>' % m, 680, 335)


# ------------------------------------------------------- artboard 4: done ---
def build_done():
    lane = (
        '<div class="lane" style="height:104px">%s'
        '<div class="trimzone" style="left:0;width:7%%"></div>'
        '<div class="trimzone" style="right:0;width:9%%"></div>'
        '<div class="handle" style="left:7%%"></div>'
        '<div class="handle" style="right:9%%"></div>'
        '<div class="tag" style="left:calc(7%% + 9px)">trimmed &middot; 20 ms fades</div></div>'
        % lane_svg(608, 104, seed=11, gate=(0.075, 0.905, 0.05)))
    facts = "".join('<div class="fact"><dt>%s</dt><dd%s>%s</dd></div>' % f for f in [
        ("Duration", "", "6.82 s"), ("True peak", "", "&minus;3.1 dBTP"),
        ("Loudness", "", "&minus;16.4 LUFS"), ("Format", ' class="muted"', "48 kHz &middot; 24-bit")])
    src = (
        '<div style="display:grid;gap:8px;padding-top:12px;border-top:1px solid var(--sz-line)">'
        '<div style="display:flex;align-items:center;gap:12px">'
        '<span class="eyebrow">Sample source</span>'
        '<div class="mode-seg"><button type="button" class="on">Mac sampler</button>'
        '<button type="button">QC Looper X</button></div></div>'
        '<p class="hint">Looper X records on the Quad Cortex and plays back through the whole '
        'preset, so nothing is routed through USB. It needs the block on the grid, and a loop '
        'that survives a preset recall.</p></div>')
    body = ('%s<dl class="facts">%s</dl>'
            '<div style="display:flex;gap:9px;align-items:center;padding-top:2px">'
            '<button type="button" class="pill primary">Use as reference</button>'
            '<button type="button" class="pill ghost">%s Play through preset</button>'
            '<div class="grow"></div>'
            '<button type="button" class="pill danger">%s Discard</button></div>%s'
            % (lane, facts, ICON["play"], ICON["trash"], src))
    m = mod("Reference sample", '<span class="badge live"><i class="dot ok"></i>Ready</span>',
            '<button type="button" class="pill small ghost">Re-record'
            '<span class="kbd">space</span></button>', body)
    return doc('<div style="padding:20px">%s</div>' % m, 680, 480)


# ----------------------------------------------- artboard 5: level report ---
# (index, name, LUFS, N5 relative, true peak dBTP, flag, in the run)
PRESETS = [
    ("01", "SRV Multiamp", -21.4, 18.2, -3.8, "", True),
    ("02", "Fender Scenes", -24.8, 14.1, -6.2, "", True),
    ("03", "Dumble Lead", -19.9, 21.0, -2.1, "", True),
    ("04", "5150 Rhythm", -18.2, 29.6, -1.4, "dense", True),
    ("05", "Ambient Cleans", -27.6, 11.3, -8.0, "", True),
    ("06", "Transparent Blend", -20.3, 19.4, -2.9, "", False),
]
CHECK = ('<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
         ' stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">'
         '<path d="M20 6 9 17l-5-5"/></svg>')


def selbox(on, cls=""):
    return '<span class="selbox%s%s">%s</span>' % (" on" if on else "",
                                                   (" " + cls) if cls else "", CHECK)
TARGET = -18.0


def corr_cell(d, scale=10.0, muted=False):
    w = min(abs(d) / scale, 1.0) * 50.0
    left = 50.0 if d >= 0 else 50.0 - w
    big = " muted" if muted else (" big" if abs(d) >= 6 else "")
    return ('<div class="corr%s"><div class="corr-track"><span class="corr-zero"></span>'
            '<span class="corr-bar%s" style="left:%.2f%%;width:%.2f%%"></span></div>'
            '<span class="corr-num">%+.1f</span></div>'
            % (" muted" if muted else "", big, left, w, d))


def build_report():
    head = ('<div class="thead">%s<span></span><span>Preset</span><span class="right">LUFS</span>'
            '<span class="right">N5 rel</span><span class="right">True pk</span>'
            '<span class="right">Correction &nbsp;dB</span></div>' % selbox(False, "some"))
    rows = []
    for idx, name, lufs, n5, tp, flag, sel in PRESETS:
        d = TARGET - lufs
        badge = ('<span class="badge attn" style="font-size:8.5px">dense</span>'
                 if flag == "dense" else "")
        if not sel:
            badge = '<span class="badge off" style="font-size:8.5px">excluded</span>'
        rows.append('<div class="trow%s">%s<span class="idx">%s</span>'
                    '<span class="nm">%s%s</span>'
                    '<span class="num">%.1f</span><span class="num dim">%.1f</span>'
                    '<span class="num dim">%.1f</span>%s</div>'
                    % ("" if sel else " skip", selbox(sel), idx, name, badge,
                       lufs, n5, tp, corr_cell(d, muted=not sel)))
    controls = (
        '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">'
        '<div style="display:flex;align-items:center;gap:8px"><span class="eyebrow">Match to</span>'
        '<div class="mode-seg"><button type="button" class="on">Target</button>'
        '<button type="button">A preset</button></div>'
        '<button type="button" class="pill small ghost">&minus;18.0 LUFS %s</button></div>'
        '<div style="display:flex;align-items:center;gap:8px"><span class="eyebrow">Metric</span>'
        '<div class="mode-seg"><button type="button" class="on">LUFS</button>'
        '<button type="button">Perceived N5</button></div></div>'
        '<div style="display:flex;align-items:center;gap:8px"><span class="eyebrow">Scenes</span>'
        '<div class="mode-seg"><button type="button" class="on">Whole preset</button>'
        '<button type="button">Every scene</button></div></div>'
        '<div style="display:flex;align-items:center;gap:10px;margin-left:auto">'
        '<span class="mono" style="font-size:10.5px;color:var(--sz-dim)">5 of 6 selected</span>'
        '<div class="mode-seg"><button type="button" class="on">Report only</button>'
        '<button type="button">Write to device</button></div></div></div>' % ICON["chev"])
    dense = (
        '<div class="strip">%s<span><strong>5150 Rhythm</strong> is the one preset already at '
        'the target in LUFS, yet it reads highest of the six on perceived loudness. Level by '
        'LUFS and it still comes out in front. Switch the metric to close that gap instead.'
        '</span></div>' % ICON["info"])
    summary = (
        '<div style="display:flex;align-items:center;gap:14px;padding-top:12px;'
        'border-top:1px solid var(--sz-line)">'
        '<div><div class="eyebrow">Spread</div>'
        '<div class="mono" style="font-size:13px;margin-top:4px">9.4 LU across 5 presets</div></div>'
        '<div style="margin-left:8px"><div class="eyebrow">After leveling</div>'
        '<div class="mono" style="font-size:13px;margin-top:4px">within 0.5 LU of &minus;18.0</div>'
        '</div><div class="grow"></div>'
        '<div style="display:flex;align-items:center;gap:8px"><span class="eyebrow">Run</span>'
        '<div class="mode-seg"><button type="button" class="on">Auto</button>'
        '<button type="button">Step</button></div></div>'
        '<button type="button" class="pill ghost">Export report</button>'
        '<button type="button" class="pill primary">Write to device</button></div>')
    m = mod("Level report", '<span class="badge live"><i class="dot ok"></i>Measured</span>',
            '<button type="button" class="pill small ghost">%s How this works</button>'
            '<button type="button" class="pill small ghost">My Presets %s</button>'
            % (ICON["book"], ICON["chev"]),
            controls + head + '<div style="display:grid;gap:2px">%s</div>' % "".join(rows)
            + dense + summary)
    ref = mod("Reference sample", '<span class="badge live"><i class="dot ok"></i>Ready</span>',
              '<button type="button" class="pill small ghost">%s Play</button>' % ICON["play"],
              '<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;'
              'align-items:center">'
              '<div class="lane" style="height:44px">%s</div>'
              '<div class="mono" style="font-size:10.5px;color:var(--sz-dim);text-align:right">'
              '6.82 s &middot; &minus;16.4 LUFS<br>&minus;3.1 dBTP</div></div>'
              % lane_svg(876, 44, seed=11, gate=(0.075, 0.905, 0.05)))
    return doc(shell(titlebar() + '<div class="view"><div class="rack">%s%s</div></div>'
                     % (m, ref) + dock()), 1120, 840)


# ---------------------------------------------- artboard 6: leveling run ----
def build_running():
    iters = [
        ("1", "Measured <em>&minus;24.8 LUFS</em> &middot; &Delta; +6.8 LU", "Gain +6.8 dB", "ok"),
        ("2", "Measured <em>&minus;18.6 LUFS</em> &middot; &Delta; +0.6 LU", "Gain +7.4 dB", "ok"),
        ("3", "Verifying <em>&minus;18.1 LUFS</em> &middot; &Delta; +0.1 LU", "within 0.5 LU", "busy"),
    ]
    steps = "".join('<div class="step %s"><span class="num">%s</span>'
                    '<span class="t">%s</span>'
                    '<span class="mono" style="font-size:10.5px;color:var(--sz-dim)">%s</span>'
                    '</div>' % (c, n, t, r) for n, t, r, c in iters)
    guards = (
        '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 22px;'
        'padding-top:12px;border-top:1px solid var(--sz-line)">'
        '<div><div class="eyebrow" style="margin-bottom:7px">True-peak guard</div>%s</div>'
        '<div><div class="eyebrow" style="margin-bottom:7px">Output limiters</div>'
        '<div style="display:flex;gap:7px;flex-wrap:wrap">'
        '<span class="lim">XLR 1</span><span class="lim">XLR 2</span>'
        '<span class="lim">Out 3</span><span class="lim">Out 4</span>'
        '<span class="lim">HP</span>'
        '<span class="mono" style="font-size:10px;color:var(--sz-dim);align-self:center">'
        'all clear</span></div></div></div>'
        % meter(pc(-1.9, floor=-12.0), dbs(-1.9), unit="dBTP",
                thr=pc(-1.0, floor=-12.0), peak=pc(-1.4, floor=-12.0), scale=False))
    scenes = []
    for lb, nm, val, st in [("A", "Clean", "+6.9", ""), ("B", "Crunch", "+5.2", ""),
                            ("C", "Lead", "+3.1", "lim"), ("D", "Ambient", "+7.4", ""),
                            ("E", "&mdash;", "", "off"), ("F", "&mdash;", "", "off"),
                            ("G", "&mdash;", "", "off"), ("H", "&mdash;", "", "off")]:
        cls = "chip" if st == "off" else "chip active"
        val_html = ('<span class="lb" style="color:var(--sz-danger-strong)">%s</span>' % val
                    if st == "lim" else
                    '<span class="lb">%s</span>' % (val or "&mdash;"))
        scenes.append('<div class="scene"><span class="%s">%s</span>%s'
                      '<span class="lb" style="font-family:var(--sz-font-display);'
                      'font-size:9.5px">%s</span></div>' % (cls, lb, val_html, nm))
    scene_block = (
        '<div style="display:grid;gap:9px;padding-top:12px;border-top:1px solid var(--sz-line)">'
        '<div style="display:flex;align-items:center;gap:10px">'
        '<span class="eyebrow">Per-scene trim</span>'
        '<span class="badge off">4 of 8 defined</span></div>'
        '<div class="scenes">%s</div>'
        '<p class="hint">Scene C hit the output limiter at +3.5 dB and was held at +3.1 dB. '
        'Scenes with no data are left alone.</p></div>' % "".join(scenes))
    current = mod(
        "Fender Scenes", '<span class="badge attn"><i class="dot warn"></i>Leveling</span>',
        '<span class="mono" style="font-size:10.5px;color:var(--sz-dim)">preset 2 of 5</span>',
        '<div class="prog"><span style="width:36%%"></span></div>'
        '<div style="display:grid;gap:7px">%s</div>%s%s' % (steps, guards, scene_block),
        cls="live")
    queue_rows = []
    for idx, name, lufs, n5, tp, flag, sel in PRESETS:
        d = TARGET - lufs
        if not sel:
            state = ('<span class="badge off">excluded</span>',
                     '<span class="mono" style="font-size:10.5px;color:var(--sz-faint)">'
                     'left alone</span>')
        elif idx == "01":
            state = ('<span class="badge live">%s done</span>' % ICON["check"],
                     '<span class="mono" style="font-size:10.5px">&minus;18.0 LUFS &middot; '
                     'Gain +3.4 dB</span>')
        elif idx == "02":
            state = ('<span class="badge attn"><i class="dot warn"></i>now</span>',
                     '<span class="mono" style="font-size:10.5px;color:var(--sz-dim)">'
                     'iteration 3</span>')
        else:
            state = ('<span class="badge off">queued</span>',
                     '<span class="mono" style="font-size:10.5px;color:var(--sz-faint)">'
                     '%+.1f dB expected</span>' % d)
        queue_rows.append(
            '<div class="trow%s%s" style="grid-template-columns:30px minmax(0,1fr) 96px 210px">'
            '<span class="idx">%s</span><span class="nm">%s</span>%s'
            '<span class="right">%s</span></div>'
            % (" on" if idx == "02" else "", "" if sel else " skip",
               idx, name, state[0], state[1]))
    queue = mod("Setlist &middot; My Presets",
                '<span class="badge attn"><i class="dot warn"></i>Auto</span>',
                '<button type="button" class="pill small ghost">%s Pause</button>'
                '<button type="button" class="pill small danger">Abort</button>'
                % ICON["pause"],
                '<div style="display:grid;gap:2px">%s</div>'
                '<p class="hint">Each preset is recalled, measured with the reference sample, '
                'trimmed and verified, then it moves to the next one on its own. Presets left '
                'out of the selection are not touched. '
                'Nothing is saved until the whole run passes.</p>'
                % "".join(queue_rows))
    return doc(shell(titlebar() + '<div class="view"><div class="rack">%s%s</div></div>'
                     % (current, queue) + dock(limiting=True)), 1120, 960)


# ------------------------------------------------ artboard 8: help window ---
HELP_STEPS = [
    ("Record a reference riff", "Arm the sampler and play &mdash; recording starts on your "
     "first note and stops when you stop. Space works the pedal. The riff is taken from "
     "<code>USB 1/2</code>, the dry DI, so it is your playing with no preset on it."),
    ("Choose what to level", "Tick the presets in the report. Match them to a fixed target "
     "or to a preset that already sits where you want it. Level whole presets, or each "
     "scene separately."),
    ("Measure", "Each preset is recalled, the riff is played into it through "
     "<code>USB in 5/6</code>, and what comes back is measured. <em>Report only</em> changes "
     "nothing on the device &mdash; it just tells you the correction in dB."),
    ("Write the trim", "The correction goes to a Gain block at the end of the chain, or to "
     "that block&rsquo;s per-scene values. Amp master, cab and drive are never touched, so "
     "the tone stays where you put it."),
    ("Verify, then save", "Every preset is measured again after its trim and has to land "
     "within 0.5 LU. If the output limiter engages, the trim is backed off instead. Nothing "
     "is saved until the whole run passes."),
]


def build_help():
    steps = "".join(
        '<div class="step"><span class="num">%d</span>'
        '<div class="txt"><h3>%s</h3><p>%s</p></div></div>' % (i, h, b)
        for i, (h, b) in enumerate(HELP_STEPS, 1))
    need = "".join(
        '<div class="fact"><dt>%s</dt><dd%s>%s</dd></div>' % f for f in [
            ("Connection", "", "Bridge &middot; Cortex Control running"),
            ("Audio device", "", "Quad Cortex &middot; 48 kHz fixed"),
            ("USB dry/wet", ' class="muted"', "1/2 must carry the dry DI"),
        ])
    card = (
        '<div class="modal-card">'
        '<h2>Leveling, start to finish</h2>'
        '<p class="fine">Patchbay plays one riff of yours into every preset and trims each '
        'one until they all sit at the same loudness. Five steps, and only the fourth writes '
        'anything to the device.</p>'
        '<div class="steps">%s</div>'
        '<dl class="facts" style="margin-top:16px">%s</dl>'
        '<p class="fine" style="margin-top:12px">The output meter&rsquo;s dB readings come '
        'straight from the device and are still provisional &mdash; use them for spotting '
        'signal and limiting, not for absolute levels.</p>'
        '<div class="modal-actions">'
        '<button type="button" class="pill primary">Record a riff</button>'
        '<button type="button" class="pill ghost">%s Open the guide</button>'
        '<div class="grow"></div>'
        '<button type="button" class="pill dim">Close</button></div>'
        '</div>' % (steps, need, ICON["book"]))
    under = ('<div style="filter:saturate(.6)">%s</div>'
             % (titlebar() + '<div class="view"><div class="rack">%s</div></div>'
                % mod("Level report", '<span class="badge live"><i class="dot ok"></i>'
                      'Measured</span>', "",
                      '<p class="hint" style="font-size:12px">&nbsp;</p>')))
    return doc(shell(under) + '<div class="modal-scrim">%s</div>' % card, 1120, 880)


# -------------------------------------------- artboard 9: scene leveling ----
SCENES = [
    ("A", "Clean", -24.9, -6.8, 6.9, ""),
    ("B", "Crunch", -23.2, -5.4, 5.2, ""),
    ("C", "Lead", -21.5, -1.2, 3.1, "held"),
    ("D", "Ambient", -25.4, -7.1, 7.4, ""),
    ("E", None, None, None, None, ""),
    ("F", None, None, None, None, ""),
    ("G", None, None, None, None, ""),
    ("H", None, None, None, None, ""),
]


def build_scenes():
    head = ('<div class="thead" style="grid-template-columns:22px 34px minmax(0,1fr) '
            '74px 76px 214px"><span></span><span></span><span>Scene</span>'
            '<span class="right">LUFS</span><span class="right">True pk</span>'
            '<span class="right">Trim &nbsp;dB</span></div>')
    rows = []
    for lb, nm, lufs, tp, trim, flag in SCENES:
        cols = ('style="grid-template-columns:22px 34px minmax(0,1fr) 74px 76px 214px"')
        if nm is None:
            rows.append('<div class="trow skip" %s>%s'
                        '<span class="chip">%s</span>'
                        '<span class="nm">Undefined<span class="badge off" '
                        'style="font-size:8.5px">no data</span></span>'
                        '<span class="num">&mdash;</span><span class="num">&mdash;</span>'
                        '<span class="right num">&mdash;</span></div>'
                        % (cols, selbox(False), lb))
            continue
        badge = ('<span class="badge bad" style="font-size:8.5px">held</span>'
                 if flag == "held" else "")
        rows.append('<div class="trow" %s>%s<span class="chip active">%s</span>'
                    '<span class="nm">%s%s</span>'
                    '<span class="num">%.1f</span><span class="num dim">%.1f</span>%s</div>'
                    % (cols, selbox(True), lb, nm, badge, lufs, tp,
                       corr_cell(trim, scale=8.0)))
    mode = (
        '<div class="tog" style="margin-top:12px">%s'
        '<div class="meta"><div class="n">Per-scene trim is on</div>'
        '<div class="d">The Gain block&rsquo;s LEVEL is assigned to scenes, so each scene '
        'stores its own value. Assigning is a one-time step on the device; after it, '
        'Patchbay confirms every scene switch before writing.</div></div>'
        '<span class="badge live"><i class="dot ok"></i>8 scenes</span></div>'
        % selbox(True))
    where = (
        '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding-top:12px;'
        'border-top:1px solid var(--sz-line)">'
        '<div><div class="eyebrow">Trim block</div>'
        '<div class="mono" style="font-size:11px;margin-top:4px">Gain &middot; row 1, col 7'
        '</div></div>'
        '<div><div class="eyebrow">Scenes selected</div>'
        '<div class="mono" style="font-size:11px;margin-top:4px">4 of 4 defined</div></div>'
        '<div class="grow"></div>'
        '<button type="button" class="pill ghost">%s Audition scene</button>'
        '<button type="button" class="pill primary">Level 4 scenes</button></div>'
        % ICON["play"])
    note = ('<p class="hint">Scene C wanted +3.5 dB but its true peak left only +3.1 before '
            'the output limiter, so it was held there &mdash; 0.4 LU under the others. '
            'Undefined scenes are left untouched.</p>')
    m = mod("Fender Scenes &middot; scenes",
            '<span class="badge live"><i class="dot ok"></i>Measured</span>',
            '<div class="mode-seg"><button type="button">Whole preset</button>'
            '<button type="button" class="on">By scene</button></div>',
            head + '<div style="display:grid;gap:2px">%s</div>%s%s%s'
            % ("".join(rows), mode, where, note))
    return doc('<div style="padding:20px">%s</div>' % m, 1120, 720)


# ------------------------------------------- artboard 7: output meter strip --
def port(name, db, lim=False, peak_db=None):
    """lim=None marks a port the device reports no limiter flag for (headphones
    carry a single shared hp_limiter_active, not one per channel)."""
    off = db is None
    pct = pc(db)
    p = ('<span class="peak" style="left:%.1f%%"></span>' % pc(peak_db)) if peak_db else ""
    fill = "cool" if off else ("hot" if pct > 92 else "")
    chip = ('<span></span>' if lim is None else
            '<span class="lim%s">lim</span>' % (" on" if lim else ""))
    return ('<div class="port"><span class="pn">%s</span>'
            '<span class="lvl"><span class="fill %s" style="width:%.1f%%"></span>%s</span>'
            '<span class="pv%s">%s</span>%s</div>'
            % (name, fill, pct, p, " off" if off else "",
               "&minus;&#8734;" if off else dbs(db), chip))


def build_meterstrip():
    def rows(x1, x2, o3, o4, hl, hr, xlim=False):
        # 3-column grid, so this order stacks each L/R pair vertically
        return "".join([port("XLR 1", x1, lim=xlim, peak_db=None if x1 is None else x1 + 1.6),
                        port("Out 3", o3, peak_db=None if o3 is None else o3 + 1.4),
                        port("HP L", hl, lim=None),
                        port("XLR 2", x2, lim=xlim, peak_db=None if x2 is None else x2 + 1.6),
                        port("Out 4", o4, peak_db=None if o4 is None else o4 + 1.4),
                        port("HP R", hr, lim=None)])

    def hp(state):
        return ('<div style="display:flex;align-items:center;gap:9px;padding-top:11px;'
                'margin-top:2px;border-top:1px solid var(--sz-line)">'
                '<span class="eyebrow">Headphone limiter</span>%s'
                '<span class="hint">one flag for both channels</span></div>' % state)

    def block(badge, ports, tail=""):
        return mod("Outputs", badge,
                   '<span class="mono" style="font-size:10px;color:var(--sz-faint)">'
                   'IOMeter</span>',
                   '<div class="ports">%s</div>%s' % (ports, tail))

    nb = block('<span class="badge live"><i class="dot ok"></i>Signal</span>',
               rows(-22.8, -24.0, -35.4, -35.4, -29.4, -30.0),
               hp('<span class="lim">clear</span>'))
    lb = block('<span class="badge bad"><i class="dot bad"></i>Limiting</span>',
               rows(-2.4, -3.6, -28.8, -28.8, -17.4, -18.0, xlim=True),
               hp('<span class="lim on">engaged</span>')
               + '<div class="strip bad" style="margin-top:12px">%s<span>XLR 1 and 2 are in '
                 'the limiter. Leveling backs the trim off until the outputs are clear.'
                 '</span></div>' % ICON["alert"])
    sb = block('<span class="badge off"><i class="dot idle"></i>No signal</span>',
               rows(None, None, None, None, None, None),
               hp('<span class="lim">clear</span>'))
    foot = ('<p class="hint" style="padding:0 4px">Fed by the device&rsquo;s own IOMeter stream, '
            'which also carries the per-output limiter flags. The float scale is not documented '
            '&mdash; the dB readouts stay provisional until they are calibrated against Cortex '
            'Control&rsquo;s meter.</p>')
    body = ('<div style="padding:20px;display:grid;gap:12px">'
            '<div class="eyebrow" style="padding:0 4px">Output meter &middot; docked under the '
            'rack</div>%s%s%s%s</div>' % (nb, lb, sb, foot))
    return doc(body, 1120, 660)


# ------------------------------------------------------------------ write ----
FILES = {
    "Main.dc.html": build_main(),
    "SamplerArmed.dc.html": build_armed(),
    "SamplerRecording.dc.html": build_recording(),
    "SamplerDone.dc.html": build_done(),
    "LevelReport.dc.html": build_report(),
    "LevelRunning.dc.html": build_running(),
    "OutputMeter.dc.html": build_meterstrip(),
    "Help.dc.html": build_help(),
    "SceneLeveling.dc.html": build_scenes(),
}

CANVAS = """{
  "pages": [
    { "id": "page-1", "name": "Reference sampler" },
    { "id": "page-2", "name": "Leveling" },
    { "id": "page-3", "name": "Help" }
  ],
  "artboards": [
    { "file": "Main.dc.html", "title": "Sampler / idle", "x": 0, "y": 0, "w": 1120, "h": 660, "page": "page-1" },
    { "file": "SamplerArmed.dc.html", "title": "Armed", "x": 1240, "y": 0, "w": 680, "h": 360, "page": "page-1" },
    { "file": "SamplerRecording.dc.html", "title": "Recording", "x": 1240, "y": 500, "w": 680, "h": 335, "page": "page-1" },
    { "file": "SamplerDone.dc.html", "title": "Recorded", "x": 1240, "y": 890, "w": 680, "h": 480, "page": "page-1" },
    { "file": "OutputMeter.dc.html", "title": "Output meter / states", "x": 0, "y": 760, "w": 1120, "h": 660, "page": "page-1" },
    { "file": "LevelReport.dc.html", "title": "Level report", "x": 0, "y": 0, "w": 1120, "h": 840, "page": "page-2" },
    { "file": "LevelRunning.dc.html", "title": "Leveling run", "x": 1240, "y": 0, "w": 1120, "h": 960, "page": "page-2" },
    { "file": "SceneLeveling.dc.html", "title": "Leveling by scene", "x": 0, "y": 960, "w": 1120, "h": 720, "page": "page-2" },
    { "file": "Help.dc.html", "title": "How this works", "x": 0, "y": 0, "w": 1120, "h": 880, "page": "page-3" }
  ],
  "annotations": [
    { "id": "note-glass", "x": 0, "y": -150, "w": 470, "page": "page-1",
      "text": "Glass surfaces are the mac build. The Windows build drops backdrop-filter and raises the panel to 92% opacity - same layout, solid fills." },
    { "id": "note-pedal", "x": 1240, "y": -150, "w": 470, "page": "page-1",
      "text": "One control, four states: arm, listening, recording, recorded. Nothing starts or stops by clock - the playing does it. The pedal is a footswitch, so the space bar is the foot: space arms, cancels while listening, and stops while recording. Every state shows the keycap, so the shortcut is never hidden." },
    { "id": "note-corr", "x": 0, "y": -150, "w": 470, "page": "page-2",
      "text": "Correction bars use one hue. Direction and the signed number carry up-or-down, so nothing reads as good-versus-bad." },
    { "id": "note-scenes", "x": 0, "y": 770, "w": 470, "page": "page-2",
      "text": "Leveling by scene writes to the Gain block's per-scene values, which only exist once the parameter is assigned to scenes - so the panel states that plainly rather than hiding it behind a switch that silently does nothing." },
    { "id": "note-help", "x": 0, "y": -150, "w": 470, "page": "page-3",
      "text": "Reached from 'How this works' in the report header. It uses the app's own modal and step components, so it is the same furniture as the setup flow." }
  ],
  "launch": { "view": "canvas", "page": "page-1" }
}
"""

if __name__ == "__main__":
    for name, html in FILES.items():
        with open(os.path.join(HERE, name), "w") as fh:
            fh.write(html)
    with open(os.path.join(HERE, "canvas.json"), "w") as fh:
        fh.write(CANVAS)
    print("wrote", len(FILES) + 1, "files to", HERE)

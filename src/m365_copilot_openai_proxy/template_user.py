from __future__ import annotations

from .template_assets import _FIELD_TIP_CSS, _GLASS_SELECT_CSS, _GLASS_SELECT_JS, _NO_SPIN_CSS, _STILL_DECOR_CSS
from .template_pkce import _USER_PKCE_JS
from .template_user_account_js import _USER_ACCOUNT_JS
from .template_user_config_js import _USER_CONFIG_JS
from .template_user_i18n import _USER_I18N_JS
from .template_user_sessions_js import _USER_SESSIONS_JS

_USER_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ciallo Ms-365 Copilot 代理 · 用户</title>
<style>
:root{--cyan:#60f2ff;--violet:#8c6bff;--pink:#ff5edb;--gold:#ffd76f;--muted:#9aa7d1;--line:rgba(108,137,255,.24);--strong:#eaf0ff;--faint:#8a97c4;--inner:rgba(9,14,34,.66);--inner-border:rgba(108,137,255,.2);--text:#f3f6ff;--card:linear-gradient(180deg,rgba(13,19,45,.82),rgba(7,10,24,.76));--bg:radial-gradient(circle at 18% 12%,rgba(96,242,255,.16),transparent 26%),radial-gradient(circle at 84% 10%,rgba(140,107,255,.2),transparent 24%),radial-gradient(circle at 50% 92%,rgba(255,94,219,.14),transparent 26%),linear-gradient(135deg,#040612 0%,#090d1f 45%,#03050d 100%);--chip:rgba(255,255,255,.06);--chip-border:rgba(255,255,255,.14)}
/* iOS26 Liquid Glass light theme — aligned with admin; dark :root untouched */
body[data-theme="light"]{
--cyan:#007aff;--violet:#5856d6;--pink:#ff2d55;--gold:#ff9f0a;
--muted:#6b6b70;--line:rgba(60,60,67,.12);
--bg:radial-gradient(circle at 16% 10%,rgba(0,122,255,.05),transparent 30%),radial-gradient(circle at 84% 8%,rgba(88,86,214,.04),transparent 28%),radial-gradient(circle at 50% 92%,rgba(0,0,0,.02),transparent 32%),linear-gradient(160deg,#f2f3f7 0%,#e9ebf0 48%,#f4f5f8 100%);
--text:#1c1c1e;--card:linear-gradient(180deg,rgba(255,255,255,.72),rgba(255,255,255,.52));--strong:#000000;--faint:#8e8e93;
--inner:rgba(255,255,255,.62);--inner-border:rgba(120,120,128,.18);--chip:rgba(120,120,128,.1);--chip-border:rgba(120,120,128,.16);--shadow:0 8px 28px rgba(0,0,0,.07);--h1grad:linear-gradient(135deg,#1d1d1f,#3a3a3c 70%,#636366)}

*{box-sizing:border-box}
html{scrollbar-gutter:stable;scrollbar-color:rgba(96,242,255,.45) rgba(8,13,32,.22);scrollbar-width:thin}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:rgba(8,13,32,.22);border-radius:999px}
::-webkit-scrollbar-thumb{background:linear-gradient(180deg,rgba(96,242,255,.58),rgba(140,107,255,.48));border-radius:999px;border:2px solid rgba(8,13,32,.4)}
::-webkit-scrollbar-thumb:hover{background:linear-gradient(180deg,rgba(96,242,255,.78),rgba(255,94,219,.58))}
body{margin:0;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",-apple-system,sans-serif;color:var(--text);line-height:1.5;min-height:100vh;background:var(--bg);position:relative;transition:background .25s,color .25s}
body::before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px);background-size:44px 44px;mask-image:radial-gradient(circle at center,black 45%,transparent 92%);z-index:0}
.orb{position:fixed;width:380px;height:380px;border-radius:50%;filter:blur(16px);background:conic-gradient(from 160deg,var(--cyan),var(--pink),var(--violet),var(--cyan));top:50%;left:50%;transform:translate(-50%,-50%);animation:loginSpin 11s linear infinite,loginPulse 3.8s ease-in-out infinite;opacity:.32;z-index:0;pointer-events:none}
.wrap{position:relative;z-index:1;max-width:900px;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.4rem;margin:0;background:linear-gradient(135deg,#fff,#8deef7 44%,#ffc6f1 78%,#ffe598);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:24px;padding:1.5rem;margin-bottom:10px;backdrop-filter:blur(20px);box-shadow:0 24px 70px rgba(0,0,0,.38);overflow:hidden}
.modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.3);backdrop-filter:blur(18px) saturate(145%);-webkit-backdrop-filter:blur(18px) saturate(145%);display:flex;align-items:center;justify-content:center;z-index:1000;padding:1rem}
.modal-card{position:relative;width:360px;max-width:92vw;border-radius:14px;padding:1.25rem;background:rgba(15,23,42,.3);border:1px solid rgba(96,242,255,.28);box-shadow:0 24px 70px rgba(0,0,0,.36),inset 0 1px 0 rgba(255,255,255,.12);backdrop-filter:blur(22px) saturate(150%);-webkit-backdrop-filter:blur(22px) saturate(150%)}
body[data-theme="light"] .modal-card{background:rgba(255,255,255,.72);border-color:rgba(60,60,67,.12);box-shadow:0 16px 40px rgba(0,0,0,.1);backdrop-filter:blur(28px) saturate(160%)}
.card::before{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;background:linear-gradient(135deg,rgba(96,242,255,.42),transparent 30%,rgba(255,94,219,.32),rgba(255,215,111,.24));-webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:.75;pointer-events:none}
.card:has(details[open])::after{content:"";position:absolute;inset:0;border-radius:inherit;padding:1px;background:linear-gradient(90deg,transparent,rgba(96,242,255,.85),rgba(255,94,219,.58),transparent);background-size:240% 100%;animation:flowBorder 2.4s linear infinite;-webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}
details summary{min-height:42px;display:flex;align-items:center;position:relative;border-radius:14px;padding:.15rem .25rem;transition:background .2s}
details[open] summary{background:linear-gradient(135deg,rgba(96,242,255,.04),rgba(140,107,255,.04))}
@keyframes flowBorder{to{background-position:240% 0}}
.card h2{font-size:1rem;margin:0 0 .8rem;color:var(--strong)}
#login-card{width:380px;max-width:calc(100vw - 32px);margin:8vh auto 1.5rem;text-align:center;padding:2.6rem;border-radius:28px}
#login-card .brand-mark{width:56px;height:56px;margin:0 auto 1rem;border-radius:18px;position:relative;background:linear-gradient(135deg,rgba(96,242,255,.9),rgba(140,107,255,.92));box-shadow:0 0 30px rgba(96,242,255,.4),inset 0 0 22px rgba(255,255,255,.22);overflow:hidden}
#login-card .brand-mark:before,#login-card .brand-mark:after{content:"";position:absolute;inset:12px;border-radius:12px;border:1px solid rgba(255,255,255,.34);animation:userMarkSpin 4.8s linear infinite}
#login-card .brand-mark:after{inset:8px;opacity:.58;animation:userMarkSpinReverse 6.2s linear infinite}
#login-card input{background:rgba(10,16,36,.46)!important;border:1px solid rgba(255,255,255,.14);backdrop-filter:blur(14px);box-shadow:inset 0 1px 0 rgba(255,255,255,.08);-webkit-text-fill-color:#e2e8f0;-webkit-box-shadow:0 0 0 1000px rgba(10,16,36,.46) inset;transition:background-color 0s,color 0s}
#login-card input:focus{border:1px solid transparent!important;background-image:linear-gradient(rgba(10,16,36,.58),rgba(10,16,36,.58)),linear-gradient(90deg,var(--cyan),var(--violet),var(--pink),var(--gold),var(--cyan))!important;background-origin:border-box!important;background-clip:padding-box,border-box!important;background-size:100% 100%,300% 100%!important;animation:fieldFlow 2.2s linear infinite!important}
#login-card input:-webkit-autofill,#login-card input:-webkit-autofill:focus,#login-card input:-webkit-autofill:hover{-webkit-text-fill-color:#e2e8f0!important;background-color:rgba(18,24,48,.72)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.08)!important;-webkit-box-shadow:0 0 0 1000px rgba(18,24,48,.72) inset,inset 0 1px 0 rgba(255,255,255,.08)!important;caret-color:#e2e8f0}
@keyframes userMarkSpin{from{transform:rotate(16deg)}to{transform:rotate(376deg)}}
@keyframes userMarkSpinReverse{from{transform:rotate(-12deg)}to{transform:rotate(-372deg)}}
label{display:block;font-size:.85rem;color:var(--muted);margin:.6rem 0 .3rem}
input,select,textarea{width:100%;background:var(--inner);border:1px solid var(--inner-border);border-radius:10px;color:var(--text);padding:.6rem .7rem;font-size:.9rem;font-family:inherit;transition:border-color .2s,box-shadow .2s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--cyan);box-shadow:0 0 0 3px rgba(96,242,255,.16)}
select:focus{transition:none!important;animation:none!important;box-shadow:0 0 0 2px rgba(96,242,255,.12),inset 0 1px 0 rgba(255,255,255,.08)!important}
textarea{resize:vertical;min-height:70px;font-family:monospace}
#acct-token{height:75px;min-height:75px;max-height:75px;resize:none;overflow:hidden;line-height:1.45;box-sizing:border-box;display:block}
button{color:#050815;border:none;border-radius:10px;padding:.55rem 1rem;font-size:.85rem;font-weight:800;cursor:pointer;margin-top:.6rem;background:linear-gradient(135deg,var(--cyan),#d6fbff 52%,var(--gold));box-shadow:0 10px 24px rgba(96,242,255,.22);transition:transform .18s ease,box-shadow .18s ease;text-shadow:none}
button:hover{transform:translateY(-2px);box-shadow:0 16px 32px rgba(96,242,255,.34)}
button:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn-ghost{background:var(--chip);background-image:none;color:var(--strong);border:1px solid var(--chip-border);box-shadow:none}
/* The sign-in panel markup is shared with /admin, which styles its secondary
   buttons with an inline chip background and has no per-button top margin. */
.pkce-panel button{margin-top:0}
.pkce-panel button[style*="background:var(--chip)"]{background-image:none;color:var(--strong);border:1px solid var(--chip-border);box-shadow:none}
.pkce-panel input{margin-top:0;border-radius:6px}
.compact-action{width:58px;margin:0;padding:.2rem .55rem!important;font-size:.75rem!important;text-align:center;display:inline-flex;align-items:center;justify-content:center}
.call-param-box{background:var(--inner);border:1px solid var(--inner-border);border-radius:10px;color:var(--text);padding:.6rem .7rem;font-size:.9rem;box-shadow:inset 0 1px 0 rgba(255,255,255,.08)}
.call-param-row{display:grid;grid-template-columns:72px minmax(0,1fr) 58px;align-items:center;gap:.5rem;font-size:.8rem;color:var(--muted);margin-bottom:.4rem}
.call-param-row:last-child{margin-bottom:0}
.call-param-row code{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#a5b4fc}
.account-main>.row button,.account-main .action-row button,.account-action{width:180px;justify-content:center}
.account-icon-btn{width:34px!important;height:34px!important;min-width:34px!important;padding:0!important;display:inline-flex!important;align-items:center;justify-content:center;border-radius:12px;border:1px solid transparent;box-shadow:inset 0 1px 0 rgba(255,255,255,.16);cursor:pointer}
.account-icon-btn svg{width:17px;height:17px;display:block;flex:0 0 auto;stroke-width:2.4}
.account-icon-btn-pass{color:#fde047!important;background:rgba(250,204,21,.22)!important;border-color:rgba(250,204,21,.55)!important}
.account-icon-btn-out{color:#7dd3fc!important;background:rgba(56,189,248,.22)!important;border-color:rgba(56,189,248,.55)!important}
.account-icon-btn:hover{filter:brightness(1.12)}
body[data-theme="light"] .account-icon-btn-pass{color:#a16207!important;background:rgba(250,204,21,.28)!important;border-color:rgba(202,138,4,.55)!important}
body[data-theme="light"] .account-icon-btn-out{color:#0369a1!important;background:rgba(14,165,233,.2)!important;border-color:rgba(2,132,199,.5)!important}
.account-main select{width:180px!important;min-height:38px;background-color:var(--inner);border:1px solid var(--inner-border);color:var(--text);box-shadow:inset 0 1px 0 rgba(255,255,255,.08);transition:border-color .2s,box-shadow .2s}
.account-main select:focus{border-color:var(--cyan);box-shadow:0 0 0 2px rgba(96,242,255,.12),inset 0 1px 0 rgba(255,255,255,.1)!important;animation:none!important;transition:none!important}
select option{transition:none!important}
select option:checked{background:#1e40af;color:#fff}
@keyframes userSelectGlow{50%{box-shadow:0 0 0 3px rgba(96,242,255,.22),0 0 30px rgba(255,94,219,.2),inset 0 1px 0 rgba(255,255,255,.14)}}
.account-main select option{background:#10162f;color:#f3f6ff}
body[data-theme="light"] .account-main select option{background:#fff;color:#1c1c1e}
""" + _GLASS_SELECT_CSS + _NO_SPIN_CSS + _FIELD_TIP_CSS + """
.account-main .glass-select.open{z-index:2000}
.account-main .tone-select+.glass-select .glass-select-menu{left:0;right:auto;width:100%;max-width:100%;min-width:100%;overflow-x:hidden;overflow-y:auto}
.account-main textarea{margin-top:.65rem}
.action-row{margin-top:.8rem;margin-bottom:.15rem}
.row{display:flex;gap:.5rem;align-items:center}
.login-row{align-items:stretch;margin-top:.6rem}
.login-row #login-btn{width:100%;margin:0}
.login-row #login-msg{position:absolute;left:2.6rem;right:2.6rem;bottom:1.15rem;text-align:center}
.row>*{margin-top:0}
.pill{display:inline-block;font-size:.75rem;padding:.15rem .5rem;border-radius:99px;background:rgba(255,255,255,.08);color:#cbd5e1}
.pill.ok{background:rgba(6,95,70,.6);color:#d1fae5}
.pill.bad{background:rgba(127,29,29,.6);color:#fee2e2}
.msg{font-size:.8rem;margin-left:.5rem;opacity:0;transition:opacity .2s;color:#86efac}
#tone-msg{display:inline-flex;align-items:center;justify-content:center;min-width:42px;height:18px;margin-left:0;padding:0 .45rem;border-radius:999px;background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.18);font-size:.72rem;font-weight:700;line-height:1;color:#86efac;box-shadow:inset 0 1px 0 rgba(255,255,255,.08);transform:translateY(1px);transition:opacity .22s ease}
.hint{font-size:.8rem;color:var(--muted);margin-bottom:.4rem}
.section-title{display:flex;flex-direction:column;align-items:flex-start;gap:.35rem;margin:1rem 0 .45rem;font-size:1rem;color:var(--strong);font-weight:700;letter-spacing:.01em}
.section-title:before{content:"";display:block;width:46px;height:1px;border-radius:99px;background:linear-gradient(90deg,var(--cyan),var(--violet),transparent);box-shadow:0 0 10px rgba(96,242,255,.35)}
input:focus,textarea:focus{border:1px solid transparent!important;background-image:linear-gradient(var(--inner),var(--inner)),linear-gradient(90deg,var(--cyan),var(--violet),var(--pink),var(--gold),var(--cyan))!important;background-origin:border-box!important;background-clip:padding-box,border-box!important;background-size:100% 100%,300% 100%!important;background-position:0 0,0 0!important;box-shadow:0 0 0 3px rgba(96,242,255,.12),0 0 24px rgba(96,242,255,.2),inset 0 1px 0 rgba(255,255,255,.08)!important;animation:fieldFlow 2.2s linear infinite!important;outline:none}
select:focus{border-color:var(--cyan)!important;background-image:none!important;animation:none!important;transition:none!important;box-shadow:0 0 0 2px rgba(96,242,255,.12),inset 0 1px 0 rgba(255,255,255,.08)!important;outline:none}
@keyframes fieldFlow{to{background-position:0 0,300% 0}}
.qs-link{color:var(--cyan);font-weight:700;text-decoration:none;padding:.02rem .28rem;border-radius:6px;background:linear-gradient(135deg,rgba(96,242,255,.12),rgba(140,107,255,.12));border:1px solid rgba(96,242,255,.28);transition:box-shadow .18s,background .18s}
.qs-link:hover{text-decoration:none;background:linear-gradient(135deg,rgba(96,242,255,.22),rgba(255,94,219,.18));box-shadow:0 0 14px rgba(96,242,255,.28)}
body[data-theme="light"] .qs-link{color:#007aff;border-color:rgba(0,122,255,.22);background:linear-gradient(135deg,rgba(0,122,255,.08),rgba(88,86,214,.06))}
body[data-theme="light"] .hint,body[data-theme="light"] label,body[data-theme="light"] .call-param-row,body[data-theme="light"] .status-line{color:#6b6b70}
body[data-theme="light"] code,body[data-theme="light"] .call-param-row code{color:#5856d6}
body[data-theme="light"] .pill{background:rgba(120,120,128,.12);color:#3a3a3c}
body[data-theme="light"] .pill.ok{background:rgba(220,252,231,.88);color:#166534}
body[data-theme="light"] .pill.bad{background:rgba(254,226,226,.9);color:#991b1b}
body[data-theme="light"] .msg{color:#15803d}
body[data-theme="light"] .api-row>span:first-child{color:var(--text)}
body[data-theme="light"] .account-side{background:linear-gradient(180deg,rgba(255,255,255,.78),rgba(242,243,247,.62));border-color:rgba(60,60,67,.12);box-shadow:inset 0 1px 0 rgba(255,255,255,.9),0 10px 28px rgba(0,0,0,.05)}
body[data-theme="light"] .status-line,body[data-theme="light"] .status-line:first-child{border-color:rgba(60,60,67,.12)}
.api-info{margin-top:.75rem;padding:.75rem;background:var(--inner);border:1px solid var(--inner-border);border-radius:10px;font-family:monospace;font-size:.8rem;line-height:1.6}
.api-grp{font-weight:700;color:var(--strong);margin:.5rem 0 .25rem;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:.78rem}
.api-grp:first-child{margin-top:0}
.api-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.12rem 0}
.api-row>span:first-child{color:#f3f6ff;white-space:pre}
.api-row>span:last-child{color:var(--faint);text-align:right;font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:.74rem}
.account-card{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:10px;align-items:start;min-height:600px;overflow:visible}
.account-card:has(.glass-select.open){z-index:2000}
.user-default-grid{display:grid;grid-template-columns:repeat(4,minmax(0,180px));gap:1rem;align-items:end;margin-top:.25rem}
.user-config-field{position:relative;display:flex;flex-direction:column;gap:.35rem;color:var(--strong);font-size:.86rem;font-weight:800;min-width:0}
body[data-lang="en"] .user-config-field,body[data-lang="en"] .user-media-suffix .user-config-label{font-size:.72rem;line-height:1.2;font-weight:700}
/* English labels are long enough to need an ellipsis, but the rule must skip a
   label wrapped in a .field-row: `display:block` would collapse the row and
   `overflow:hidden` would clip the tip bubble out of existence. */
body[data-lang="en"] .user-config-field>span:not(.field-row),body[data-lang="en"] .field-row>span:not(.field-tip),body[data-lang="en"] .user-config-label{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
body[data-lang="en"] .user-default-grid{grid-template-columns:repeat(4,minmax(0,1fr));gap:.75rem}
body[data-lang="en"] .user-config-field input,body[data-lang="en"] .user-default-grid .glass-select-trigger{font-size:.78rem!important}
body[data-lang="en"] button{font-size:.72rem!important;letter-spacing:0}
body[data-lang="en"] .compact-action{font-size:.7rem!important}
body[data-lang="en"] .section-title{font-size:.9rem}
body[data-lang="en"] .account-action{font-size:.72rem!important;min-width:0}
body[data-lang="en"] .account-icon-btn{width:34px!important;height:34px!important;min-width:34px!important;padding:0!important;font-size:0!important}
body[data-lang="en"] .account-main .account-action{font-size:.78rem!important;padding:.45rem .8rem!important;white-space:nowrap}
body[data-lang="en"] .pill{max-width:100%;overflow:hidden;text-overflow:ellipsis}
body[data-lang="en"] .status-line{font-size:.72rem}
body[data-lang="en"] .pill{font-size:.7rem}
body[data-lang="en"] h1{font-size:1.15rem}
body[data-lang="en"] .card h2{font-size:.95rem}

.user-config-field input{width:100%;height:38px;box-sizing:border-box;padding:9px 14px;background:rgba(96,242,255,.08);border:1px solid rgba(96,242,255,.45);border-radius:14px;color:var(--strong);font-size:.86rem;font-weight:700;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 8px 20px rgba(0,0,0,.16)}
.user-default-grid .glass-select{width:100%!important;min-width:0!important;height:38px!important;margin-left:0!important}
.user-default-grid .glass-select-trigger{height:38px!important;width:100%!important;box-sizing:border-box!important;padding:9px 34px 9px 14px!important;border-radius:14px!important;font-size:.86rem!important;font-weight:700!important}
.mode-profile-card:has(.glass-select.open){overflow:visible;z-index:2000}
.mode-profile-card .user-default-grid .glass-select-menu{left:0;right:auto;width:100%;min-width:100%;max-width:100%}
/* One 180px column is too narrow to read three explanations in, and unlike the
   admin grid this one never reflows, so a fixed wider bubble stays in the card. */
.user-default-grid .field-tip-bubble{right:auto;width:290px}
/* The caveat row that replaced the two prose hints. Not a grid field, so it has
   to establish its own positioning context -- the bubble is absolutely positioned
   against its containing block (same contract as .user-config-field /
   .runtime-field-label). inline-flex keeps the label at its natural width so the
   `!` icon sits right beside it instead of being pushed to the far edge by
   .field-tip's margin-left:auto, and the bubble is wider than a grid field's
   because it carries three measured caveats rather than one. */
.user-notice{position:relative;display:inline-flex;align-items:center;margin-bottom:.5rem;font-size:.8rem;font-weight:800;color:var(--strong)}
.user-notice .field-row{width:auto}
.user-notice .field-tip{margin-left:.35rem}
.user-notice .field-tip-bubble{right:auto;width:min(560px,78vw)}
.user-media-suffix{margin-top:1.1rem}
.user-media-suffix .user-config-label{font-size:.86rem;font-weight:800;color:var(--strong)}
.user-media-suffix textarea{width:100%;box-sizing:border-box;min-height:60px;padding:9px 14px;background:rgba(96,242,255,.08);border:1px solid rgba(96,242,255,.45);border-radius:14px;color:var(--strong);font-size:.85rem;font-family:monospace;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 8px 20px rgba(0,0,0,.16);resize:vertical;scrollbar-width:none;-ms-overflow-style:none}
.user-media-suffix textarea::-webkit-scrollbar{display:none}
.account-side{position:sticky;top:10px;background:linear-gradient(180deg,rgba(96,242,255,.09),rgba(140,107,255,.08));border:1px solid rgba(96,242,255,.22);border-radius:18px;padding:1rem;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 12px 32px rgba(0,0,0,.22);overflow:hidden}
.account-side:before{content:"";position:absolute;inset:-40%;background:conic-gradient(from 180deg,transparent,rgba(96,242,255,.22),transparent,rgba(255,94,219,.16),transparent);animation:spin 8s linear infinite;opacity:.55;pointer-events:none}
.account-side>*{position:relative;z-index:1}
.status-grid{display:grid;gap:0;margin-top:.1rem}
.status-line{display:flex;justify-content:space-between;gap:.8rem;font-size:.78rem;color:var(--muted);border-bottom:1px solid rgba(255,255,255,.08);padding:.5rem 0}
.status-line:first-child{border-top:1px solid rgba(255,255,255,.08)}
.status-line b{color:var(--strong);font-weight:700;text-align:right;word-break:break-word}
/* The session list is as long as the store allows (1000 rows), so it scrolls
   instead of stretching the card: a `.card` taller than the compositor's max
   texture (16384px in Chrome) silently stops painting its `backdrop-filter`,
   and the whole viewport went flat grey-white from ~600 rows on. Same 520px
   cap the admin session table uses. */
#my-sessions-content{max-height:520px;overflow:auto;border-radius:8px;scrollbar-gutter:stable}
.status-mark{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;padding:0;border-radius:50%;font-size:.55rem;font-weight:900;color:#050815;border:none;background:linear-gradient(135deg,var(--cyan),#d6fbff 52%,var(--gold));box-shadow:0 4px 10px rgba(96,242,255,.24),inset 0 1px 0 rgba(255,255,255,.4);line-height:1;position:relative;overflow:hidden}
.status-mark:before{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,.32),transparent 55%);pointer-events:none}
.status-mark:after{display:none}
.status-mark.ok{background:linear-gradient(135deg,var(--cyan),#d6fbff 52%,var(--gold));color:#050815}
.status-mark.bad{background:linear-gradient(135deg,#64748b,#475569);color:#f8fafc;box-shadow:0 4px 10px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.18)}
@keyframes statusBreath{50%{box-shadow:inset 0 1px 0 rgba(255,255,255,.55),inset 0 -9px 16px rgba(0,0,0,.14),0 0 26px rgba(255,255,255,.16)}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes loginSpin{to{transform:translate(-50%,-50%) rotate(360deg)}}
@keyframes loginPulse{50%{scale:1.08;opacity:.48}}
@media(max-width:760px){.account-card{grid-template-columns:1fr;min-height:auto}.account-side{position:relative;top:auto}}
.hidden{display:none}
a{color:var(--cyan);text-decoration:none}
a:hover{text-decoration:underline}
code{color:#a5b4fc}


/* iOS26 light — component overrides (user page; dark base rules untouched) */
body[data-theme="light"]{scrollbar-color:rgba(0,122,255,.28) rgba(120,120,128,.08)}
body[data-theme="light"]::-webkit-scrollbar-track{background:rgba(120,120,128,.08)}
body[data-theme="light"]::-webkit-scrollbar-thumb{background:linear-gradient(180deg,rgba(0,122,255,.4),rgba(88,86,214,.32));border-color:rgba(255,255,255,.5)}
body[data-theme="light"]::-webkit-scrollbar-thumb:hover{background:linear-gradient(180deg,rgba(0,122,255,.55),rgba(88,86,214,.42))}
body[data-theme="light"]::before{background:linear-gradient(rgba(60,60,67,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(60,60,67,.05) 1px,transparent 1px);background-size:44px 44px;opacity:.55}
body[data-theme="light"] .orb{opacity:.1;filter:blur(28px);background:conic-gradient(from 160deg,rgba(0,122,255,.55),rgba(88,86,214,.45),rgba(255,45,85,.28),rgba(0,122,255,.55))}
body[data-theme="light"] h1{background:var(--h1grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
body[data-theme="light"] .card{border-radius:22px;backdrop-filter:blur(28px) saturate(160%);-webkit-backdrop-filter:blur(28px) saturate(160%);box-shadow:var(--shadow)}
body[data-theme="light"] .card::before{background:linear-gradient(135deg,rgba(255,255,255,.75),transparent 42%,rgba(0,122,255,.12),rgba(88,86,214,.08));opacity:.55}
body[data-theme="light"] .card:has(details[open])::after{background:linear-gradient(90deg,transparent,rgba(0,122,255,.45),rgba(88,86,214,.28),transparent);animation:none;opacity:.7}
body[data-theme="light"] details[open] summary{background:linear-gradient(135deg,rgba(0,122,255,.05),rgba(88,86,214,.04))}
body[data-theme="light"] button{color:#fff;background:linear-gradient(180deg,#0a84ff 0%,#007aff 100%);box-shadow:0 4px 14px rgba(0,122,255,.28),inset 0 1px 0 rgba(255,255,255,.28);text-shadow:none;border-radius:12px;font-weight:700}
body[data-theme="light"] button:hover{transform:translateY(-1px);box-shadow:0 8px 20px rgba(0,122,255,.32),inset 0 1px 0 rgba(255,255,255,.32)}
body[data-theme="light"] button:active{transform:translateY(0);filter:brightness(.96)}
body[data-theme="light"] .btn-ghost,body[data-theme="light"] button.btn-ghost{color:#1c1c1e!important;background:rgba(120,120,128,.12)!important;border:1px solid rgba(60,60,67,.12)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.7)!important}
body[data-theme="light"] input:focus,body[data-theme="light"] select:focus,body[data-theme="light"] textarea:focus{border:1px solid rgba(0,122,255,.45)!important;background-image:none!important;background:var(--inner)!important;box-shadow:0 0 0 4px rgba(0,122,255,.14)!important;animation:none!important;outline:none}
body[data-theme="light"] #login-card input{background:rgba(255,255,255,.72)!important;border:1px solid rgba(60,60,67,.14)!important;-webkit-text-fill-color:#1c1c1e;-webkit-box-shadow:0 0 0 1000px rgba(255,255,255,.72) inset;box-shadow:inset 0 1px 0 rgba(255,255,255,.9)}
body[data-theme="light"] #login-card input:focus{border:1px solid rgba(0,122,255,.45)!important;background-image:none!important;background:rgba(255,255,255,.86)!important;-webkit-box-shadow:0 0 0 1000px rgba(255,255,255,.86) inset,0 0 0 4px rgba(0,122,255,.14)!important;box-shadow:0 0 0 4px rgba(0,122,255,.14)!important;animation:none!important}
body[data-theme="light"] a{color:#007aff}
body[data-theme="light"] .glass-select.open .glass-select-trigger{border-color:rgba(0,122,255,.4)!important;box-shadow:0 0 0 3px rgba(0,122,255,.12),0 4px 14px rgba(0,0,0,.05)!important}
body[data-theme="light"] .glass-select-menu:before{background:linear-gradient(90deg,rgba(0,122,255,.35),rgba(88,86,214,.25),rgba(0,122,255,.35));animation:none;opacity:.45}
body[data-theme="light"] .glass-select-option:hover{background:rgba(0,122,255,.08)!important;color:#1c1c1e!important}
body[data-theme="light"] .glass-select-option.active{color:#007aff!important;background:rgba(0,122,255,.12)!important;box-shadow:inset 3px 0 0 #007aff!important}
""" + _STILL_DECOR_CSS + """
</style>
</head>
<body>
<div class="orb" aria-hidden="true"></div>
<div class="wrap">
  <div class="top">
    <h1 data-i18n="title">Ciallo Ms-365 Copilot 代理 · 用户</h1>
    <div style="display:flex;gap:.5rem;align-items:center">
      <button class="btn-ghost" id="theme-toggle" onclick="toggleTheme()">&#127769;</button>
      <button class="btn-ghost" id="lang-toggle" onclick="toggleLang()">&#127760; EN</button>
    </div>
  </div>

  <div id="login-card" class="card">
    <div class="brand-mark" aria-hidden="true"></div>
    <h2 data-i18n="login_title">登录</h2>
    <div class="hint" data-i18n="login_hint">输入管理员分配给你的用户名与密码，管理自己的对话模式、提示词与账户 Token。</div>
    <input id="username" type="text" autocomplete="off" data-i18n-ph="username_ph" placeholder="用户名" onkeydown="if(event.key==='Enter')doLogin()">
    <input id="password" type="password" autocomplete="off" data-i18n-ph="password_ph" placeholder="密码" style="margin-top:.5rem" onkeydown="if(event.key==='Enter')doLogin()">
    <div class="row login-row"><button id="login-btn" onclick="doLogin()" data-i18n="login_btn">登录</button><span id="login-msg" class="msg"></span></div>
  </div>

  <div id="app" class="hidden">
    <div class="card">
      <h2 data-i18n="qs_title">快速使用指南</h2>
      <div class="hint" style="line-height:1.7" data-i18n-html="qs_body">1. 安装 <a href="https://gh-proxy.com/https://raw.githubusercontent.com/MurasameCyan/Ciallo-Ms-365-OpenAI-Proxy-Docker/multi/get_token.user.js" target="_blank" rel="noopener" class="qs-link">油猴脚本</a> 并打开 <a href="https://m365.cloud.microsoft/chat" target="_blank" rel="noopener" class="qs-link">M365 Copilot</a>，随意发一条消息触发 WebSocket。<br>2. 在脚本面板点击「一键推送」或 手动「推送/复制 Token」，「推送 Cookie」均可。<br>3. 在账户卡片中复制 Base URL 与 API Key，填入 OpenAI 兼容客户端即可使用。</div>
      <details style="margin-top:.75rem;cursor:pointer">
        <summary style="font-weight:600;color:var(--strong);list-style:none;display:flex;align-items:center;gap:.5rem">
          <span data-i18n="endpoints_title">OpenAI 兼容接口</span>
          <span style="font-size:.7rem;color:var(--faint);margin-left:auto" data-i18n="click_expand">点击展开</span>
        </summary>
        <div class="api-info">
          <div class="api-grp" data-i18n="api_grp_public">公共接口</div>
          <div class="api-row"><span>GET&nbsp; /healthz</span><span data-i18n="api_healthz">健康检查</span></div>
          <div class="api-grp" data-i18n="api_grp_v1">OpenAI 兼容接口</div>
          <div class="api-row"><span>POST /v1/chat/completions</span><span data-i18n="api_chat">OpenAI 兼容对话</span></div>
          <div class="api-row"><span>POST /v1/messages</span><span data-i18n="api_messages">Anthropic 兼容消息</span></div>
          <div class="api-row"><span>GET&nbsp; /v1/models</span><span data-i18n="api_models">模型列表</span></div>
          <div class="api-row"><span>POST /v1/responses</span><span data-i18n="api_responses">Responses 接口</span></div>
        </div>
      </details>
    </div>
    <div class="card account-card">
      <div class="account-main">
        <div style="display:flex;align-items:center;gap:20px;margin-bottom:.75rem">
          <h2 data-i18n="account_title" style="margin:0;height:32px;display:flex;align-items:center;line-height:1">账户控制台</h2>
          <span id="account-console-actions"></span>
        </div>
        <div id="account-info"></div>
        <label class="section-title" data-i18n="call_params_title">调用参数</label>
        <div class="call-param-box">
          <div class="call-param-row"><span>Base URL:</span><code id="base-url"></code><button onclick="copyBaseUrl(this)" class="btn-ghost compact-action" data-i18n="copy_base">复制</button></div>
          <div class="call-param-row"><span>API Key:</span><code id="my-key"></code><button onclick="copyMyKey(this)" class="btn-ghost compact-action" data-i18n="copy_key">复制</button></div>
        </div>
        <div class="row" style="margin-top:.6rem"><button onclick="regenMyKey(this)" data-i18n="regen_my_key">重置 API Key</button><span id="regen-msg" class="msg"></span></div>
        <label class="section-title" data-i18n="manual_update_title">手动更新</label>
        <div class="row action-row"><button onclick="pushToken(this)" data-i18n="push_token_btn">更新 Token</button><span id="token-msg" class="msg"></span></div>
        <textarea id="acct-token" data-i18n-ph="push_token_ph" placeholder="粘贴 access_token 值或完整 wss:// URL。仅推送 Token 可临时使用，推送 Cookie 后才算绑定 Microsoft 账户。&#10;access_token / wss://substrate.office.com/..."></textarea>
        <label class="section-title" data-i18n="pkce_section_title">授权登录 ( M365 Only )</label>
        <div id="pkce-panel"></div>
      </div>
      <div class="account-side" id="account-status-panel"></div>
    </div>

    <div class="card mode-profile-card">
      <details id="mode-profile-details" style="cursor:pointer">
      <summary style="font-size:1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;align-items:center;gap:.5rem">
      <span data-i18n="mode_profile_title">默认配置</span>
      <span id="tone-msg" class="msg"></span>
      <span style="font-size:.7rem;color:#475569;margin-left:auto" data-i18n="click_expand">点击展开</span>
      </summary>
      <div style="margin-top:.75rem">
      <div class="user-notice"><span class="field-row"><span data-i18n="user_notice_label">注意事项</span><span class="field-tip" tabindex="0" role="note"><span class="field-tip-bubble"><span class="tip-line"><b data-i18n="user_notice_m365">M365 精确计算</b><span data-i18n="user_no_interpreter_hint">claude-sonnet-4-6 没有服务端代码执行：不带工具的轮次里，哈希、大数运算这类精确计算会直接答「算不了」，不然它会给一个看起来对的错值。要精确结果就声明一个能执行命令的工具，或改用 claude-sonnet-4-5，实测它既有服务端执行、也认工具调用。</span></span><span class="tip-line"><b data-i18n="user_notice_m365_others">其他 M365 模型</b><span data-i18n="user_other_tones_hint">服务端代码执行：Copilot_自动、Copilot_快速答复、Copilot_深度思考、claude-sonnet-4-5、gpt-5.6、gpt-5.5_Chat、gpt-5.5、gpt-5.4_Chat、gpt-5.4、gpt-5.3_Chat、gpt-5.3、gpt-5.2_Chat 实测都有，claude-fable-5、claude-opus、gpt-6_Chat、gpt-6、gpt-5.2 还没实测；gpt-5.6_Chat 三轮里两轮算对、一轮编了个假哈希，要精确值请另选一个或声明能执行命令的工具。工具调用：只有 claude-sonnet-4-6、claude-sonnet-4-5 实测遵守契约，Copilot_自动、Copilot_深度思考、gpt-5.6、gpt-5.5_Chat、gpt-5.5 实测不遵守——「工具调用规划」保持「自动」时只有这几个会多花一轮判定，其余模型不额外花轮数。</span></span><span class="tip-line"><b data-i18n="user_notice_consumer">个人版出图</b><span data-i18n="user_consumer_image_hint">要出图请选 copilot、copilot-smart、copilot-chat 或 copilot-search，实测这三种模式会真的返回图片。copilot-reasoning / copilot-thinking 会说「已为你生成」却一帧图都不发，copilot-study 只讲不画，copilot-research 给的是网页图搜结果，copilot-coco 会先反问一句——这是上游行为，代理这边没有图可交付。</span></span></span></span></span></div>
      <div class="user-default-grid">
        <label class="user-config-field" style="display:none"><span data-i18n="tone_title">对话模式</span><select id="tone" class="tone-select" onchange="saveTone()"></select></label>
        <label class="user-config-field"><span class="field-row"><span data-i18n="run_permission_label">运行权限</span><span class="field-tip" tabindex="0" role="note"><span class="field-tip-bubble"><span class="tip-line"><b data-i18n="run_permission_inherit">继承全局</b><span id="user-run-permission-default"></span></span><span class="tip-line"><b data-i18n="run_permission_read_only">只读</b><span data-i18n="run_permission_hint_read_only">只放行读取类工具调用，写入、执行类会被丢弃。</span></span><span class="tip-line"><b data-i18n="run_permission_full">完全</b><span data-i18n="run_permission_hint_full">放行客户端声明的全部工具调用。</span></span><span class="tip-line"><span data-i18n="run_permission_hint_ceiling">全局设置是上限：你只能收紧，不能放宽。</span></span></span></span></span><select id="user-run-permission" class="tone-select" onchange="saveTone()"></select></label>
        <label class="user-config-field"><span class="field-row"><span data-i18n="tool_planning_label">工具调用规划</span><span class="field-tip" tabindex="0" role="note"><span class="field-tip-bubble"><span class="tip-line"><b data-i18n="tool_planning_inherit">继承全局</b><span id="user-tool-planning-default"></span></span><span class="tip-line"><b data-i18n="tool_planning_auto">自动</b><span data-i18n="tool_planning_hint_auto">只对实测不遵守契约的模式加一轮路由判定，其余模式不额外花轮数（默认）。</span></span><span class="tip-line"><b data-i18n="tool_planning_native">内联契约</b><span data-i18n="tool_planning_hint_native">契约写进提示词，永不多花轮数；模式不遵守时这一轮就没有工具调用。</span></span><span class="tip-line"><b data-i18n="tool_planning_router">路由模式</b><span data-i18n="tool_planning_hint_router">每轮先判定要不要调工具，判「不需要」也要多花一次上游往返。</span></span><span class="tip-line"><b data-i18n="tool_planning_studio">Studio Agent</b><span data-i18n="tool_planning_hint_studio">m365账户使用自己的 Studio Agent，未就绪或首个输出前不可用时回退Router。首个文本或工具增量发出后若失败，不再重试。</span></span></span></span></span><select id="user-tool-planning" class="tone-select" onchange="saveTone()"></select></label>
        <label class="user-config-field" style="display:none"><span data-i18n="model_alias_label">模型别名</span><input id="user-model-alias" onchange="saveTone()"></label>
        <label class="user-config-field"><span data-i18n="user_time_zone_label">更改时区</span><input id="user-time-zone" onchange="saveTone()"></label>
        <label class="user-config-field"><span data-i18n="ws_idle_timeout_label">对话响应超时分钟</span><input id="user-ws-idle-timeout" type="number" min="0" onchange="saveTone()"></label>
      </div>
      <div class="user-media-suffix">
        <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem"><span class="user-config-label" data-i18n="user_media_suffix_label">媒体后缀名</span></div>
        <div class="hint" data-i18n="user_media_suffix_hint">填写后将强制覆盖全局媒体后缀，仅作用于你自己的 Key。用逗号、空格或换行分隔。留空则跟随全局。</div>
        <textarea id="user-media-suffix" rows="3" onchange="saveTone()" placeholder=""></textarea>
      </div>
      <div class="user-media-suffix">
        <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem"><span class="user-config-label" data-i18n="user_proxy_label">出站代理</span><span id="user-proxy-msg" class="hint" style="opacity:0;transition:opacity .3s"></span></div>
        <div class="hint" data-i18n="user_proxy_hint">仅作用于你绑定的账户。个人版 Copilot 与 M365 按来源 IP 分别风控，两者可能需要不同出口。格式 scheme://host:port，端口必填，支持 http/https/socks4/socks5。留空则跟随全局设置。</div>
        <input id="user-proxy-url" type="text" onchange="saveAccountProxy()" placeholder="socks5h://127.0.0.1:1080" style="width:100%;box-sizing:border-box;padding:9px 14px;background:rgba(96,242,255,.08);border:1px solid rgba(96,242,255,.45);border-radius:14px;color:var(--strong);font-size:.85rem;font-family:monospace">
      </div>
      </div>
      </details>
      <hr style="border:none;border-top:1px solid #334155;margin:1.1rem 0">
      <details id="my-sessions-details" style="cursor:pointer" ontoggle="if(this.open)loadMySessions()">
      <summary style="font-size:1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;align-items:center;gap:.5rem">
      <span data-i18n="my_sessions_title">会话管理</span>
      <span id="sess-msg" class="msg"></span>
      <span style="font-size:.7rem;color:#475569;margin-left:auto" data-i18n="click_expand">点击展开</span>
      </summary>
      <div style="margin-top:.75rem">
      <div class="hint" data-i18n="my_sessions_hint">这里列出你自己的会话，以及它们在 M365 云端对应的对话。删除会同时删掉云端对话，不可恢复。</div>
      <div class="row" style="flex-wrap:wrap;gap:.5rem;margin:.6rem 0 .35rem">
        <input id="my-sess-ttl" type="number" min="0" style="width:150px">
        <input id="my-sess-keep" type="number" min="0" style="width:150px">
        <button class="btn-ghost" onclick="cleanupMySessions(this)" data-i18n="sess_cleanup_btn">执行清理</button>
        <button class="btn-ghost" onclick="loadMySessions()" data-i18n="sess_refresh">刷新</button>
        <span id="my-sessions-warn" class="hidden" style="margin-left:auto;cursor:help" title=""><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#fbbf24" stroke-width="2" stroke-linecap="round" style="display:block"><circle cx="12" cy="12" r="9"></circle><path d="M12 7.5v5"></path><path d="M12 16.3h.01"></path></svg></span>
      </div>
      <div class="hint" data-i18n="sess_cleanup_hint">留空或 0 表示不启用该条件；勾选的会话永不被清理。</div>
      <div id="my-sessions-content"></div>
      </div>
      </details>
      <hr style="border:none;border-top:1px solid #334155;margin:1.1rem 0">
      <details id="tool-prompt-details" style="cursor:pointer">
      <summary style="font-size:1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;align-items:center;gap:.5rem">
      <span data-i18n="tool_prompt_title">提示词增强</span>
      <span style="font-size:.7rem;color:#475569;margin-left:auto" data-i18n="click_expand">点击展开</span>
      </summary>
      <div style="margin-top:.75rem">
      <div class="hint" data-i18n="tool_prompt_hint">追加到工具调用提示词后的自定义指令，仅作用于你自己的 Key。留空则不追加。</div>
      <textarea id="tool-prompt"></textarea>
      <div class="row"><button onclick="saveToolPrompt()" data-i18n="save">保存</button><span id="tool-msg" class="msg"></span></div>
      </div>
      </details>
      <hr style="border:none;border-top:1px solid #334155;margin:1.1rem 0">
      <details id="sys-prompt-details" style="cursor:pointer">
      <summary style="font-size:1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;align-items:center;gap:.5rem">
      <span data-i18n="sys_prompt_title">系统提示词（高级）</span>
      <span style="font-size:.7rem;color:#475569;margin-left:auto" data-i18n="click_expand">点击展开</span>
      </summary>
      <div style="margin-top:.75rem">
      <div class="hint" data-i18n="sys_prompt_hint">覆盖工具调用的基础系统提示词（定义 tool_call 格式与规则）。改错会导致工具调用失效，仅供高级用户调试。留空则使用内置默认。</div>
      <div id="sys-prompt-locked">
      <button onclick="unlockSysPrompt()" style="background:linear-gradient(135deg,#ef4444,#dc2626)" data-i18n="system_prompt_unlock">解锁编辑</button>
      </div>
      <div id="sys-prompt-editor" style="display:none">
      <textarea id="sys-prompt" style="border-color:#7f1d1d"></textarea>
      <div class="row"><button onclick="saveSysPrompt()" data-i18n="save">保存</button><button class="btn-ghost" onclick="resetSysPrompt()" data-i18n="reset">恢复默认</button><span id="sys-msg" class="msg"></span></div>
      </div>
      </div>
      </details>
    </div>
  </div>
</div>

<script>
""" + _USER_I18N_JS + """let lang=localStorage.getItem('lang')||'zh';
let toneOptions=[];
let sysDefault='';
let userTimeZone='';
function t(k){const v=i18n[lang][k];return v==null?k:v}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function getKey(){return sessionStorage.getItem('user_api_key')||''}
function authHeaders(){return {'Content-Type':'application/json','Authorization':'Bearer '+getKey()}}
function applyLang(){
  document.body.setAttribute('data-lang',lang);
  document.documentElement.lang=lang==='zh'?'zh':'en';
  document.title=t('title');
  const btn=document.getElementById('lang-toggle');
  btn.innerHTML=lang==='zh'?'&#127760; EN':'&#127760; 中文';
  document.querySelectorAll('[data-i18n]').forEach(el=>{const k=el.getAttribute('data-i18n');if(i18n[lang][k]!=null)el.textContent=i18n[lang][k]});
  document.querySelectorAll('[data-i18n-ph]').forEach(el=>{const k=el.getAttribute('data-i18n-ph');if(i18n[lang][k]!=null)el.placeholder=i18n[lang][k]});
  document.querySelectorAll('[data-i18n-html]').forEach(el=>{const k=el.getAttribute('data-i18n-html');if(i18n[lang][k]!=null)el.innerHTML=i18n[lang][k]});
  renderToneOptions();
  try{if(typeof renderMySessions==='function')renderMySessions()}catch(e){}
  try{
    if(typeof applyUserLangDynamic==='function' && _userMeCache){applyUserLangDynamic()}
    else if(getKey()){loadMe()}
  }catch(e){}
}
function toggleLang(){lang=lang==='zh'?'en':'zh';localStorage.setItem('lang',lang);applyLang()}
""" + _GLASS_SELECT_JS + """
function applyTheme(){const theme=localStorage.getItem('user_theme')||'dark';document.body.setAttribute('data-theme',theme);const b=document.getElementById('theme-toggle');if(b)b.innerHTML=theme==='light'?'&#9728;':'&#127769;'}
function toggleTheme(){localStorage.setItem('user_theme',(localStorage.getItem('user_theme')||'dark')==='dark'?'light':'dark');applyTheme()}
""" + _USER_CONFIG_JS + """
""" + _USER_ACCOUNT_JS + """
""" + _USER_PKCE_JS + """
""" + _USER_SESSIONS_JS + """
applyTheme();
applyLang();
setInterval(tickUserCountdown,1000);
loadMe();
</script>
</body>
</html>"""

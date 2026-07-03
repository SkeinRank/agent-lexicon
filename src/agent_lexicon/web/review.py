"""Local web proposal inbox for Agent Lexicon.

The inbox is a dependency-free localhost interface for reviewing scout candidates
stored in the SQLite workspace. It uses Python's standard library HTTP server so
local review can run immediately after installing the package.
"""

from __future__ import annotations

import html
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from agent_lexicon.policy import (
    LocalPolicyError,
    PolicyAction,
    PolicyDecision,
    check_local_policy,
    load_local_policy,
)
from agent_lexicon.workspace import (
    ReviewDecisionStatus,
    WorkspaceError,
    WorkspaceReviewItem,
    WorkspaceStore,
    open_workspace,
)


def _accepted_terms(root: str | Path) -> list[dict[str, Any]]:
    """Read the published lexicon for this workspace and return its terms.

    Returns an empty list when no lexicon file exists yet, so the Lexicon tab
    can show an inviting empty state instead of an error.
    """
    try:
        from agent_lexicon.dictionary import dictionary_layout_path
        from agent_lexicon.core import load_lexicon, AgentLexiconLoadError
    except ImportError:
        return []
    layout = dictionary_layout_path(root)
    lexicon_path = Path(layout.lexicon_path)
    if not lexicon_path.exists():
        return []
    try:
        lexicon = load_lexicon(lexicon_path)
    except (AgentLexiconLoadError, OSError, ValueError):
        return []
    terms: list[dict[str, Any]] = []
    for term in lexicon.terms:
        terms.append(
            {
                "id": term.id,
                "canonical": term.canonical,
                "scopes": list(term.scopes),
                "tools": list(term.tools),
                "aliases": [alias.surface for alias in term.aliases],
                "deprecated": bool(term.deprecated),
            }
        )
    terms.sort(key=lambda t: t["id"])
    return terms


class ReviewInboxError(ValueError):
    """Raised when the local proposal inbox cannot be rendered or served."""


_STATUS_LABELS = {
    "unreviewed": "Unreviewed",
    ReviewDecisionStatus.ACCEPTED.value: "Accepted",
    ReviewDecisionStatus.REJECTED.value: "Rejected",
    ReviewDecisionStatus.AMBIGUOUS.value: "Ambiguous",
    ReviewDecisionStatus.NEEDS_SPLIT.value: "Needs split",
}


_CSS = """
:root {
  --bg: #f7f7f5;
  --panel: #ffffff;
  --text: #171717;
  --muted: #6f6f68;
  --subtle: #ecebe7;
  --line: #deddd7;
  --strong: #111111;
  --soft: #fafaf8;
  --accent: #20201d;
  --ok: #f2f8f3;
  --warn: #fff7ea;
  --danger: #fff1f0;
  --radius-lg: 24px;
  --radius-md: 16px;
  --radius-sm: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: inherit; text-decoration: none; }
.shell {
  width: min(1180px, calc(100vw - 48px));
  margin: 0 auto;
  padding: 20px 0 48px;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  margin-bottom: 14px;
}
.eyebrow {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.1;
  letter-spacing: -0.03em;
}
.topbar .meta {
  max-width: 560px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.summary {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pill {
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 999px;
  padding: 7px 11px;
  color: var(--muted);
  font-size: 12px;
}
.grid {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.035);
}
.sidebar { padding: 12px; }
.list-title {
  display: flex;
  justify-content: space-between;
  padding: 8px 10px 12px;
  color: var(--muted);
  font-size: 12px;
}
.item {
  display: block;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  padding: 13px;
  margin-bottom: 8px;
  background: transparent;
}
.item:hover { background: var(--soft); }
.item.active {
  background: var(--soft);
  border-color: var(--line);
}
.item-main {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.surface {
  font-weight: 650;
  letter-spacing: -0.01em;
  word-break: break-word;
}
.priority {
  font-size: 11px;
  padding: 3px 7px;
  border-radius: 999px;
  border: 1px solid var(--line);
}
.priority.important { background: #fef3c7; color: #7a4d00; }
.priority.later { background: var(--soft); color: var(--muted); }
.score {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.meta {
  color: var(--muted);
  font-size: 12px;
  margin-top: 7px;
}
.status {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 8px;
  margin-top: 9px;
  color: var(--muted);
  font-size: 11px;
}
.status.accepted { background: var(--ok); color: #1d5f2f; }
.status.rejected { background: var(--danger); color: #87231d; }
.status.ambiguous, .status.needs_split { background: var(--warn); color: #7a4d00; }
.detail { padding: 24px; }
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}
.detail-title {
  margin: 0;
  font-size: 26px;
  line-height: 1.18;
  letter-spacing: -0.03em;
  word-break: break-word;
}
.kind {
  color: var(--muted);
  font-size: 13px;
  margin-top: 7px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 20px 0;
}
.metric {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--soft);
  padding: 13px;
}
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { margin-top: 4px; font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.section-title {
  margin: 24px 0 10px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.snippet {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-bottom: 10px;
  background: var(--panel);
}
.snippet.positive { border-left: 4px solid #6aa66f; }
.snippet.negative { border-left: 4px solid #d8a34a; }
.snippet-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--soft);
  color: var(--muted);
  font-size: 12px;
  border-bottom: 1px solid var(--line);
}
pre {
  margin: 0;
  padding: 13px 14px;
  overflow-x: auto;
  white-space: pre-wrap;
  color: #252522;
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.actions {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}
textarea {
  width: 100%;
  min-height: 74px;
  resize: vertical;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 12px;
  font: inherit;
  background: var(--soft);
  color: var(--text);
  outline: none;
}
textarea:focus { border-color: var(--strong); background: #fff; }
.button-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
button {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  padding: 10px 12px;
  font: inherit;
  cursor: pointer;
}
button:hover { border-color: var(--strong); }
button:disabled { color: var(--muted); cursor: not-allowed; background: var(--subtle); }
button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.notice {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--soft);
  padding: 12px;
  color: var(--muted);
}
.empty {
  padding: 54px 24px;
  text-align: center;
  color: var(--muted);
}
.empty strong { display: block; color: var(--text); font-size: 18px; margin-bottom: 6px; }
.code {
  display: inline-block;
  margin-top: 14px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--soft);
  color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
@media (max-width: 860px) {
  .shell { width: min(100vw - 28px, 1180px); padding-top: 20px; }
  .topbar { align-items: flex-start; flex-direction: column; }
  .summary { justify-content: flex-start; }
  .grid { grid-template-columns: 1fr; }
  .metrics, .button-row { grid-template-columns: 1fr; }
  .detail-head { flex-direction: column; }
}
.progress-wrap { display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); }
.progress-track { width: 150px; height: 6px; background: var(--subtle); border-radius: 999px; overflow: hidden; }
.progress-bar { height: 100%; width: 0; background: var(--accent); border-radius: 999px; transition: width 0.2s; }
.toolbar { display: flex; gap: 8px; padding: 4px 6px 12px; }
.search { flex: 1; border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 11px; font: inherit; background: var(--soft); color: var(--text); outline: none; }
.search:focus { border-color: var(--strong); background: #fff; }
.filter { border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 8px 10px; font: inherit; background: var(--soft); color: var(--text); cursor: pointer; }
.sidebar-list { max-height: 62vh; overflow-y: auto; }
.cluster-head { display: flex; align-items: center; gap: 8px; padding: 9px 10px; border-radius: var(--radius-sm); cursor: pointer; color: var(--muted); font-size: 12px; }
.cluster-head:hover { background: var(--soft); }
.cluster-head .caret { transition: transform 0.15s; }
.cluster-head.open .caret { transform: rotate(90deg); }
.cluster-count { margin-left: auto; border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; }
.cluster-body { padding-left: 8px; }
.item.done { opacity: 0.5; }
.item .dec-icon { margin-right: 5px; }
.chip { display: inline-block; font-size: 11px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); background: var(--soft); margin: 0 5px 5px 0; }
.chip.accent { border-color: var(--line); color: var(--text); }
.kbd { font: 11px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--soft); border: 1px solid var(--line); border-radius: 5px; padding: 1px 6px; color: var(--muted); }
.hint-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }
.hint-bar .keys { display: flex; gap: 14px; flex-wrap: wrap; }
.scores-toggle { cursor: pointer; color: var(--muted); font-size: 12px; margin-top: 6px; }
.scores-box { margin-top: 10px; max-width: 320px; }
.scores-row { display: flex; justify-content: space-between; padding: 5px 0; font-size: 12px; border-bottom: 1px solid var(--subtle); }
.scores-row span:last-child { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cluster-note { display: inline-flex; align-items: center; gap: 5px; margin-top: 8px; font-size: 12px; color: var(--muted); }
.accept-cluster { margin-left: auto; }
.tabs { display: flex; gap: 4px; }
.tab { border: 0.5px solid transparent; border-radius: 999px; padding: 6px 14px; font-size: 13px; color: var(--muted); cursor: pointer; background: transparent; }
.tab:hover { background: var(--soft); }
.tab.active { background: var(--panel); border-color: var(--line); color: var(--text); }
.actionbar { position: sticky; bottom: 0; z-index: 5; background: var(--panel); border-top: 1px solid var(--line); padding: 14px 24px; margin: 18px -24px -24px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; border-bottom-left-radius: var(--radius-lg); border-bottom-right-radius: var(--radius-lg); }
.lex-term { border: 1px solid var(--line); border-radius: var(--radius-md); padding: 14px 16px; margin-bottom: 10px; background: var(--panel); }
.lex-canonical { font-size: 16px; font-weight: 650; }
.lex-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--muted); }
.lex-alias { display: inline-block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: var(--soft); border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; margin: 4px 4px 0 0; }
.lex-scope { display: inline-block; font-size: 11px; background: #eef; border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; margin-right: 5px; color: #3730a3; }
"""


_APP_JS = r"""
(function(){
  var el = document.getElementById('review-data');
  var DATA = JSON.parse(el.textContent);
  var app = document.getElementById('app');
  var items = DATA.items;
  var lexicon = DATA.lexicon || [];
  var view = 'review';
  var idx = 0;
  var search = '';
  var filter = 'all';
  var collapsed = {};

  items.forEach(function(it){
    if (it.cluster_key && it.cluster_size > 1) {
      if (collapsed[it.cluster_key] === undefined) collapsed[it.cluster_key] = true;
    }
  });
  var sel = DATA.selected;
  if (sel) { for (var i=0;i<items.length;i++){ if(items[i].normalized_surface===sel){ idx=i; break; } } }

  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function isDecided(it){ return it.decision != null; }
  function priorityWord(it){ return it.priority === 'important' ? (it.score >= 0.6 ? 'high' : 'medium') : 'low'; }

  function visibleIndexes(){
    var out = [];
    for (var i=0;i<items.length;i++){
      var it = items[i];
      if (filter === 'unreviewed' && isDecided(it)) continue;
      if (filter === 'important' && it.priority !== 'important') continue;
      if (search && it.surface.toLowerCase().indexOf(search.toLowerCase()) === -1) continue;
      out.push(i);
    }
    return out;
  }

  function groupVisible(vis){
    var groups = [];
    var byKey = {};
    vis.forEach(function(i){
      var it = items[i];
      var key = (it.cluster_key && it.cluster_size > 1) ? it.cluster_key : null;
      if (key === null){ groups.push({key:null, idxs:[i]}); return; }
      if (byKey[key] === undefined){ byKey[key] = {key:key, idxs:[]}; groups.push(byKey[key]); }
      byKey[key].idxs.push(i);
    });
    return groups;
  }

  function reviewedCount(){ var n=0; items.forEach(function(it){ if(isDecided(it)) n++; }); return n; }

  function decIcon(it){
    if (it.decision === 'accepted') return '<span class="dec-icon" style="color:#1d5f2f">\u2713</span>';
    if (it.decision === 'rejected') return '<span class="dec-icon" style="color:#87231d">\u2717</span>';
    if (it.decision) return '<span class="dec-icon" style="color:#7a4d00">\u2022</span>';
    return '';
  }

  function renderSidebar(){
    return renderToolbar() + '<div class="sidebar-list" id="sblist">' + renderListInner() + '</div>';
  }

  function renderToolbar(){
    var h = '<div class="toolbar">';
    h += '<input class="search" id="q" placeholder="Search terms" value="'+esc(search)+'">';
    h += '<select class="filter" id="f">';
    h += '<option value="all"'+(filter==='all'?' selected':'')+'>All</option>';
    h += '<option value="unreviewed"'+(filter==='unreviewed'?' selected':'')+'>Unreviewed</option>';
    h += '<option value="important"'+(filter==='important'?' selected':'')+'>Important</option>';
    h += '</select>';
    h += '</div>';
    return h;
  }

  function renderListInner(){
    var vis = visibleIndexes();
    var groups = groupVisible(vis);
    var h = '';
    if (vis.length === 0){ h += '<div class="meta" style="padding:16px">No terms match.</div>'; }
    groups.forEach(function(g){
      if (g.key === null){
        g.idxs.forEach(function(i){ h += itemRow(i); });
      } else {
        var open = !collapsed[g.key];
        var pending = g.idxs.filter(function(i){ return !isDecided(items[i]); }).length;
        h += '<div class="cluster-head '+(open?'open':'')+'" data-cluster="'+esc(g.key)+'">';
        h += '<span class="caret">\u203a</span>';
        h += '<span style="font-family:ui-monospace,Menlo,monospace">'+esc(g.key)+'</span>';
        h += '<span class="cluster-count">'+pending+' / '+g.idxs.length+'</span>';
        h += '</div>';
        if (open){ h += '<div class="cluster-body">'; g.idxs.forEach(function(i){ h += itemRow(i); }); h += '</div>'; }
      }
    });
    return h;
  }

  function itemRow(i){
    var it = items[i];
    var active = (i === idx) ? ' active' : '';
    var done = isDecided(it) ? ' done' : '';
    return '<a class="item'+active+done+'" data-idx="'+i+'">'
      + '<div class="item-main">'
      + '<span class="surface" style="font-family:ui-monospace,Menlo,monospace;font-size:13px">'+esc(it.surface)+'</span>'
      + '<span class="score">'+decIcon(it)+priorityWord(it)+'</span>'
      + '</div>'
      + '<div class="meta">'+esc(it.kind)+' \u00b7 '+it.occurrences+' uses \u00b7 '+it.documents+' files</div>'
      + '</a>';
  }

  function renderDetail(){
    var it = items[idx];
    if (!it) return '<section class="panel detail"><div class="empty"><strong>Nothing to review</strong><span>Run a scan first.</span></div></section>';
    var chips = it.reasons.map(function(r){ return '<span class="chip accent">'+esc(r)+'</span>'; }).join('');
    var pos = it.positive.map(function(s){ return snippet(s, 'positive'); }).join('');
    var neg = it.negative.map(function(s){ return snippet(s, 'negative'); }).join('');
    var nums = Object.keys(it.nums).map(function(k){ return '<div class="scores-row"><span>'+esc(k)+'</span><span>'+it.nums[k].toFixed(3)+'</span></div>'; }).join('');
    var clusterNote = (it.cluster_key && it.cluster_size > 1) ? '<div class="cluster-note">\u25c8 part of '+esc(it.cluster_key)+' ('+it.cluster_size+' variants)</div>' : '';
    var ro = DATA.readOnly;
    var h = '<section class="panel detail">';
    h += '<div class="detail-head"><div>';
    h += '<h2 class="detail-title" style="font-family:ui-monospace,Menlo,monospace">'+esc(it.surface)+'</h2>';
    h += '<div class="kind">'+esc(it.kind)+' \u00b7 appears '+it.occurrences+' times in '+it.documents+' files \u00b7 '+priorityWord(it)+' priority</div>';
    h += clusterNote;
    h += '</div>';
    h += '<span class="status '+statusClass(it)+'">'+esc(statusLabel(it))+'</span>';
    h += '</div>';
    if (chips) h += '<div style="margin:16px 0 4px">'+chips+'</div>';
    h += '<h3 class="section-title">Where it appears</h3>';
    h += pos || '<div class="meta">No positive evidence stored.</div>';
    if (neg){ h += '<h3 class="section-title">Low-confidence matches</h3>' + neg; }
    h += '<details style="margin-top:12px"><summary class="scores-toggle">Show scores</summary><div class="scores-box">'+nums+'</div></details>';
    if (!ro){
      h += '<div class="actionbar">';
      h += '<button class="primary" data-act="accepted">\u2713 Accept <span class="kbd" style="border-color:currentColor;background:transparent">a</span></button>';
      h += '<button data-act="rejected">\u2717 Reject <span class="kbd">r</span></button>';
      h += '<button data-act="ambiguous">Ambiguous <span class="kbd">m</span></button>';
      h += '<button data-skip="1">Skip <span class="kbd">s</span></button>';
      h += '<button data-undo="1">\u21a9 Undo <span class="kbd">u</span></button>';
      if (it.cluster_key && it.cluster_size > 1) h += '<button class="accept-cluster" data-cluster-accept="'+esc(it.cluster_key)+'">\u2713 Accept cluster <span class="kbd">A</span></button>';
      h += '</div>';
    } else {
      h += '<div class="actionbar"><div class="notice" style="margin:0">Read-only policy mode.</div></div>';
    }
    h += '</section>';
    return h;
  }

  function snippet(s, kind){
    return '<article class="snippet '+kind+'"><div class="snippet-head"><span>'+esc(s.path)+':'+esc(s.start)+'-'+esc(s.end)+'</span><span>'+esc(s.reason)+'</span></div><pre>'+esc(s.text)+'</pre></article>';
  }
  function statusClass(it){ if(it.decision==='accepted')return'accepted'; if(it.decision==='rejected')return'rejected'; if(it.decision)return'ambiguous'; return''; }
  function statusLabel(it){ if(it.decision==='accepted')return'Accepted'; if(it.decision==='rejected')return'Rejected'; if(it.decision==='ambiguous')return'Ambiguous'; if(it.decision)return'Reviewed'; return'Unreviewed'; }

  function renderHeader(){
    var total = items.length, done = reviewedCount();
    var pct = total ? Math.round(done/total*100) : 0;
    var tabs = '<div class="tabs">'
      + '<button class="tab '+(view==='review'?'active':'')+'" data-view="review">Review</button>'
      + '<button class="tab '+(view==='lexicon'?'active':'')+'" data-view="lexicon">Lexicon ('+lexicon.length+')</button>'
      + '</div>';
    var right = view === 'review'
      ? '<div class="progress-wrap"><span>'+done+' of '+total+' reviewed</span><div class="progress-track"><div class="progress-bar" style="width:'+pct+'%"></div></div></div>'
      : '';
    return '<header class="topbar"><div>'
      + '<p class="eyebrow">Agent Lexicon</p><h1>'+(view==='review'?'Review':'Lexicon')+'</h1>'
      + '<div class="meta">Workspace root: '+esc(DATA.root)+'</div>'
      + '<div style="margin-top:8px">'+tabs+'</div></div>'
      + '<div class="summary" style="align-items:center">'
      + right
      + '<span class="pill">policy: '+esc(DATA.policy)+'</span>'
      + '<a class="pill" href="/review-events.jsonl">Export JSONL</a>'
      + '</div></header>';
  }

  function renderLexicon(){
    if (!lexicon.length){
      return '<section class="panel detail" style="max-height:none"><div class="empty"><strong>No accepted terminology yet</strong><span>Accept candidates in the Review tab, then run</span><span class="code">agent-lexicon publish</span></div></section>';
    }
    var q = (search || '').toLowerCase();
    var shown = lexicon.filter(function(t){
      if (!q) return true;
      if (t.canonical.toLowerCase().indexOf(q) > -1) return true;
      if (t.id.toLowerCase().indexOf(q) > -1) return true;
      return t.aliases.some(function(a){ return a.toLowerCase().indexOf(q) > -1; });
    });
    var rows = shown.map(function(t){
      var aliases = t.aliases.length ? t.aliases.map(function(a){ return '<span class="lex-alias">'+esc(a)+'</span>'; }).join('') : '<span class="meta">no aliases</span>';
      var scopes = t.scopes.map(function(s){ return '<span class="lex-scope">'+esc(s)+'</span>'; }).join('');
      var tools = t.tools.length ? '<div class="meta" style="margin-top:8px">tools: '+t.tools.map(esc).join(', ')+'</div>' : '';
      return '<div class="lex-term">'
        + '<div style="display:flex; justify-content:space-between; align-items:baseline; gap:12px;">'
        + '<span class="lex-canonical">'+esc(t.canonical)+(t.deprecated?' <span class="meta">(deprecated)</span>':'')+'</span>'
        + '<span class="lex-id">'+esc(t.id)+'</span></div>'
        + '<div style="margin-top:8px">'+scopes+'</div>'
        + '<div style="margin-top:6px">'+aliases+'</div>'
        + tools
        + '</div>';
    }).join('');
    return '<section class="panel detail" style="max-height:none"><div style="padding:4px 4px 12px"><input class="search" id="lq" placeholder="Search accepted terms" value="'+esc(search)+'" style="max-width:320px"></div>'
      + (shown.length ? rows : '<div class="meta" style="padding:12px">No terms match.</div>')
      + '</section>';
  }

  function render(){
    if (view === 'lexicon'){
      app.innerHTML = renderHeader() + '<section class="grid" style="grid-template-columns:1fr">' + renderLexicon() + '</section>';
      bindTabs();
      var lq = document.getElementById('lq');
      if (lq) lq.oninput = function(){ search = lq.value; render(); var el=document.getElementById('lq'); if(el){el.focus(); el.setSelectionRange(el.value.length, el.value.length);} };
      return;
    }
    app.innerHTML = renderHeader()
      + '<section class="grid"><aside class="panel sidebar">'+renderSidebar()+'</aside>'+renderDetail()+'</section>';
    bind();
    bindTabs();
    var active = app.querySelector('.item.active');
    if (active) active.scrollIntoView({block:'nearest'});
  }

  function bindTabs(){
    app.querySelectorAll('.tab').forEach(function(t){ t.onclick = function(){ view = t.dataset.view; search=''; render(); }; });
  }

  function bind(){
    var q = document.getElementById('q');
    if (q) q.oninput = function(){ search = q.value; var v=visibleIndexes(); if(v.indexOf(idx)===-1 && v.length) idx=v[0]; updateList(); };
    var f = document.getElementById('f');
    if (f) f.onchange = function(){ filter = f.value; var v=visibleIndexes(); if(v.indexOf(idx)===-1 && v.length) idx=v[0]; updateList(); };
    bindList();
    app.querySelectorAll('[data-act]').forEach(function(b){ b.onclick = function(){ decide(idx, b.dataset.act); }; });
    var sk = app.querySelector('[data-skip]'); if (sk) sk.onclick = function(){ next(); };
    var un = app.querySelector('[data-undo]'); if (un) un.onclick = function(){ undo(); };
    var ca = app.querySelector('[data-cluster-accept]'); if (ca) ca.onclick = function(){ acceptCluster(ca.dataset.clusterAccept); };
  }

  function bindList(){
    app.querySelectorAll('.item').forEach(function(a){ a.onclick = function(){ idx = parseInt(a.dataset.idx); render(); }; });
    app.querySelectorAll('.cluster-head').forEach(function(c){ c.onclick = function(){ var k=c.dataset.cluster; collapsed[k]=!collapsed[k]; updateList(); }; });
  }

  function updateList(){
    var listEl = document.getElementById('sblist');
    if (listEl) listEl.innerHTML = renderListInner();
    bindList();
  }

  function post(surface, decision){
    var body = 'surface='+encodeURIComponent(surface)+'&decision='+encodeURIComponent(decision)+'&note=';
    return fetch('/decision', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: body});
  }

  var history = [];

  function decide(i, decision){
    if (DATA.readOnly) return;
    var it = items[i];
    history.push({i:i, prev: it.decision || null});
    it.decision = decision;
    post(it.normalized_surface, decision);
    render();
    setTimeout(next, 100);
  }

  function undo(){
    if (DATA.readOnly || !history.length) return;
    var last = history.pop();
    var it = items[last.i];
    it.decision = last.prev;
    // Re-post the previous decision when there was one; otherwise leave the
    // server's last record (decisions are append-only) and re-open locally.
    if (last.prev) post(it.normalized_surface, last.prev);
    idx = last.i;
    render();
  }

  function acceptCluster(key){
    if (DATA.readOnly) return;
    items.forEach(function(it, i){ if (it.cluster_key === key){ history.push({i:i, prev: it.decision || null}); it.decision='accepted'; post(it.normalized_surface,'accepted'); } });
    render();
    setTimeout(next, 100);
  }

  function next(){ var v=visibleIndexes(); var p=v.indexOf(idx); if(p>-1 && p<v.length-1){ idx=v[p+1]; render(); } else if(p===-1 && v.length){ idx=v[0]; render(); } }
  function prev(){ var v=visibleIndexes(); var p=v.indexOf(idx); if(p>0){ idx=v[p-1]; render(); } }

  document.addEventListener('keydown', function(e){
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (view !== 'review') return;
    var k = e.key;
    if (k==='j'){ next(); e.preventDefault(); }
    else if (k==='k'){ prev(); e.preventDefault(); }
    else if (k==='a'){ decide(idx,'accepted'); e.preventDefault(); }
    else if (k==='r'){ decide(idx,'rejected'); e.preventDefault(); }
    else if (k==='m'){ decide(idx,'ambiguous'); e.preventDefault(); }
    else if (k==='s'){ next(); e.preventDefault(); }
    else if (k==='u'){ undo(); e.preventDefault(); }
    else if (k==='A'){ var it=items[idx]; if(it.cluster_key && it.cluster_size>1) acceptCluster(it.cluster_key); e.preventDefault(); }
  });

  render();
})();
"""


def build_review_inbox_html(
    state: WorkspaceStore,
    *,
    selected_surface: str | None = None,
    limit: int = 100,
    actor: str = "local",
    role: str | None = None,
    policy_mode: str | None = None,
) -> str:
    """Render the local proposal inbox as a complete HTML document."""
    if not isinstance(state, WorkspaceStore):
        raise ReviewInboxError("state must implement WorkspaceStore")
    items = state.list_review_items(limit=limit)
    selected = _select_item(state, items, selected_surface=selected_surface)
    policy = load_local_policy(state.root, mode=policy_mode)
    policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor, role=role)
    return _render_page(items=items, selected=selected, root=str(state.root), policy_decision=policy_decision)


def _is_unreviewed(item: WorkspaceReviewItem) -> bool:
    """True when a review item has no saved decision yet."""
    return getattr(item, "review_decision", None) is None


def run_review_inbox(
    root: str | Path = ".",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    actor: str = "local",
    role: str | None = None,
    policy_mode: str | None = None,
) -> None:
    """Run the local proposal inbox until interrupted."""
    if not host:
        raise ReviewInboxError("host must not be empty")
    if port < 1 or port > 65535:
        raise ReviewInboxError("port must be between 1 and 65535")
    state = open_workspace(root, create=True)
    policy = load_local_policy(state.root, mode=policy_mode)
    policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor, role=role)

    items = state.list_review_items(limit=1000)
    if not items:
        print("Nothing to review yet. Run a scan first, for example:")
        print("  agent-lexicon scan README.md docs src")
        return
    unreviewed = sum(1 for item in items if _is_unreviewed(item))

    handler = _handler_for_state(state, actor=actor, policy_decision=policy_decision)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Review inbox: {url}")
    print(f"{unreviewed} of {len(items)} candidates waiting for review.")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Review inbox stopped.")
    finally:
        server.server_close()


def _handler_for_state(
    state: WorkspaceStore,
    *,
    actor: str = "local",
    policy_decision: PolicyDecision | None = None,
) -> type[BaseHTTPRequestHandler]:
    if policy_decision is None:
        policy = load_local_policy(state.root)
        policy_decision = check_local_policy(policy, PolicyAction.REVIEW_CANDIDATE, actor=actor)
    class ReviewInboxHandler(BaseHTTPRequestHandler):
        server_version = "AgentLexiconReview/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_text("ok\n", status=200)
                return
            if parsed.path == "/review-events.jsonl":
                try:
                    self._send_jsonl(state.export_review_events_jsonl())
                except WorkspaceError as exc:
                    self._send_text(f"Review event export error: {exc}\n", status=500)
                return
            if parsed.path != "/":
                self._send_text("Not found\n", status=404)
                return
            params = parse_qs(parsed.query)
            selected_surface = params.get("surface", [None])[0]
            try:
                content = build_review_inbox_html(
                    state,
                    selected_surface=selected_surface,
                    actor=policy_decision.actor,
                    role=policy_decision.role.value,
                    policy_mode=policy_decision.mode.value,
                )
            except (ReviewInboxError, WorkspaceError, LocalPolicyError) as exc:
                self._send_text(f"Review inbox error: {exc}\n", status=500)
                return
            self._send_html(content)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path != "/decision":
                self._send_text("Not found\n", status=404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8")
            form = parse_qs(payload)
            normalized_surface = form.get("surface", [""])[0]
            decision = form.get("decision", [""])[0]
            note = form.get("note", [""])[0]
            if not policy_decision.is_allowed:
                self._send_text(f"Policy denied review decision: {policy_decision.reason}\n", status=403)
                return
            try:
                state.save_review_decision(normalized_surface, decision, note=note, reviewer=policy_decision.actor)
            except (ValueError, WorkspaceError) as exc:
                self._send_text(f"Invalid review decision: {exc}\n", status=400)
                return
            location = f"/?surface={quote(normalized_surface)}"
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_html(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_text(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_jsonl(self, content: str, *, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="review-events.jsonl"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ReviewInboxHandler


def _select_item(
    state: WorkspaceStore,
    items: tuple[WorkspaceReviewItem, ...],
    *,
    selected_surface: str | None,
) -> WorkspaceReviewItem | None:
    if selected_surface:
        item = state.get_review_item(unquote(selected_surface))
        if item is not None:
            return item
    return items[0] if items else None


_REASON_PHRASES = {
    "code_style_surface": "written like code",
    "high_oov_proxy": "unusual word",
    "high_oov_signal": "unusual word",
    "tokenizer_oov_signal": "unusual word",
    "high_surface_risk": "risky to match",
    "high_jargon_score": "domain jargon",
    "clustered_variants": "has spelling variants",
    "identifier_variants": "has spelling variants",
    "multi_document_signal": "seen across many files",
}


def _humanize_reasons(reasons: Any) -> list[str]:
    if not isinstance(reasons, list):
        return []
    seen: list[str] = []
    for code in reasons:
        phrase = _REASON_PHRASES.get(str(code))
        if phrase and phrase not in seen:
            seen.append(phrase)
    return seen[:4]


def _snippets_as_dicts(snippets: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(snippets, list):
        return result
    for snippet in snippets:
        if not isinstance(snippet, dict):
            continue
        start = snippet.get("start_line", "?")
        result.append(
            {
                "path": str(snippet.get("document_path", "unknown")),
                "start": str(start),
                "end": str(snippet.get("end_line", start)),
                "reason": str(snippet.get("reason", "evidence")),
                "text": str(snippet.get("text", "")),
            }
        )
    return result


def _item_as_dict(item: WorkspaceReviewItem) -> dict[str, Any]:
    cluster = _item_cluster(item)
    cluster_key = str(cluster.get("cluster_key", "") or "")
    priority = _item_priority(item)
    reasons_raw = _item_quality(item).get("priority_reasons", [])
    return {
        "surface": item.surface,
        "normalized_surface": item.normalized_surface,
        "kind": item.candidate_kind,
        "score": round(float(item.score), 3),
        "occurrences": item.occurrence_count,
        "documents": item.document_count,
        "positive_count": item.positive_count,
        "negative_count": item.negative_count,
        "priority": priority,
        "cluster_key": cluster_key,
        "cluster_size": _item_cluster_size(item),
        "reasons": _humanize_reasons(reasons_raw),
        "status": item.review_status,
        "decision": item.review_decision.decision.value if item.review_decision else None,
        "note": item.review_decision.note if item.review_decision else "",
        "nums": {
            "Score": round(float(item.score), 3),
            "Jargon": round(float(item.jargon_score), 3),
            "OOV proxy": round(_item_quality_float(item, "oov_proxy_score"), 3),
            "Surface risk": round(_item_quality_float(item, "surface_risk_score"), 3),
            "Background penalty": round(float(item.background_penalty), 3),
        },
        "positive": _snippets_as_dicts(item.evidence_payload.get("positive_snippets", [])),
        "negative": _snippets_as_dicts(item.evidence_payload.get("negative_snippets", [])),
    }


def _render_page(
    *,
    items: tuple[WorkspaceReviewItem, ...],
    selected: WorkspaceReviewItem | None,
    root: str,
    policy_decision: PolicyDecision,
) -> str:
    payload = {
        "items": [_item_as_dict(item) for item in items],
        "root": root,
        "readOnly": not policy_decision.is_allowed,
        "policy": f"{policy_decision.mode.value} · {policy_decision.role.value}",
        "selected": selected.normalized_surface if selected is not None else "",
        "lexicon": _accepted_terms(root),
    }
    data_json = json.dumps(payload).replace("</", "<\\/")
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Agent Lexicon Review</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<main class="shell" id="app"></main>',
            f'<script id="review-data" type="application/json">{data_json}</script>',
            f"<script>{_APP_JS}</script>",
            "</body>",
            "</html>",
        ]
    )


def _item_quality(item: WorkspaceReviewItem) -> dict[str, Any]:
    metadata = item.candidate_payload.get("metadata", {}) if isinstance(item.candidate_payload, dict) else {}
    quality = metadata.get("quality", {}) if isinstance(metadata, dict) else {}
    return dict(quality) if isinstance(quality, dict) else {}


def _item_cluster(item: WorkspaceReviewItem) -> dict[str, Any]:
    metadata = item.candidate_payload.get("metadata", {}) if isinstance(item.candidate_payload, dict) else {}
    cluster = metadata.get("cluster", {}) if isinstance(metadata, dict) else {}
    return dict(cluster) if isinstance(cluster, dict) else {}


def _item_priority(item: WorkspaceReviewItem) -> str:
    priority = str(_item_quality(item).get("priority", "later"))
    return priority if priority in {"important", "later"} else "later"


def _item_quality_float(item: WorkspaceReviewItem, key: str) -> float:
    try:
        return float(_item_quality(item).get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _item_cluster_size(item: WorkspaceReviewItem) -> int:
    cluster = _item_cluster(item)
    try:
        return max(1, int(cluster.get("candidate_count", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


def _status_class(status: str) -> str:
    return status.replace("_", "-").replace(" ", "-")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)

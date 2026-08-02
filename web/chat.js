/* ============================================================
   Assistant front-end — talks to /chat/api/chat
   Renders the agent's answer + a collapsible reasoning trace.
   ============================================================ */
(function () {
  'use strict';

  // theme toggle (shared)
  (function () {
    var root = document.documentElement, KEY = 'vt-theme';
    var labels = document.querySelectorAll('[data-theme-toggle] .tlabel');
    function sync(t) { root.setAttribute('data-theme', t); labels.forEach(function (el) { el.textContent = t === 'dark' ? 'Light' : 'Dark'; }); }
    var cur = root.getAttribute('data-theme') || localStorage.getItem(KEY) || 'light';
    sync(cur);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        cur = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(KEY, cur); } catch (e) {}
        sync(cur);
      });
    });
  })();

  var win = document.getElementById('window');
  var form = document.getElementById('composer');
  var input = document.getElementById('input');
  var sendBtn = document.getElementById('send');
  var notice = document.getElementById('notice');

  var history = [];      // {role, content}
  var pending = false;

  // avatar + label per agent kind (set by the server after it decides)
  var AGENTS = {
    general:   { who: 'Assistant',  char: '●' },
    math:      { who: 'Math agent', char: '∑' },
    portfolio: { who: 'Portfolio',  char: '❖' },
    tools:     { who: 'Assistant',  char: '⚙' }
  };
  // friendly tool labels for the trace
  var TOOL_LABELS = {
    math_solver: 'Math agent (SymPy)',
    portfolio_search: 'Portfolio search (RAG)',
    datetime: 'Date / time',
    network: 'Network (CIDR / DNS)'
  };

  // --- tiny, safe markdown ---
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function mdToHtml(src) {
    var out = esc(src);
    out = out.replace(/```([\s\S]*?)```/g, function (_, c) { return '<pre><code>' + c.replace(/^\n/, '') + '</code></pre>'; });
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\bhttps?:\/\/[^\s<]+/g, function (u) { return '<a href="' + u + '" target="_blank" rel="noopener">' + u + '</a>'; });
    var parts = out.split(/(<pre>[\s\S]*?<\/pre>)/);
    out = parts.map(function (p) {
      if (p.indexOf('<pre>') === 0) return p;
      return p.split(/\n{2,}/).map(function (para) {
        return para.trim() ? '<p>' + para.replace(/\n/g, '<br>') + '</p>' : '';
      }).join('');
    }).join('');
    return out;
  }

  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  function addMessage(role, agent, html, opts) {
    opts = opts || {};
    var meta = AGENTS[agent] || AGENTS.general;
    var who = role === 'user' ? 'You' : meta.who;
    var avatarChar = role === 'user' ? '›' : meta.char;
    var msg = el('div', 'msg ' + (role === 'user' ? 'user' : 'bot') + (opts.typing ? ' typing' : ''));
    msg.setAttribute('data-agent', agent || 'general');
    msg.appendChild(el('div', 'avatar', avatarChar));
    var bubble = el('div', 'bubble');
    bubble.appendChild(el('div', 'who', who));
    bubble.appendChild(el('div', 'body', html));
    msg.appendChild(bubble);
    win.appendChild(msg);
    win.scrollTop = win.scrollHeight;
    return msg;
  }

  // Build the collapsible "Reasoning" panel from the server trace + citations.
  function renderTrace(bubble, trace, citations) {
    trace = trace || []; citations = citations || [];
    var toolSteps = trace.filter(function (t) { return t.type === 'tool'; });
    if (!toolSteps.length && !citations.length) return;

    var det = el('details', 'trace');
    var n = toolSteps.length;
    det.appendChild(el('summary', null,
      '<span class="tw-ico">⚙</span> Reasoning · ' + n + ' tool call' + (n === 1 ? '' : 's')));

    var ol = el('ol', 'trace-steps');
    toolSteps.forEach(function (t) {
      var li = el('li', t.ok === false ? 'bad' : null);
      if (t.thought) li.appendChild(el('div', 't-thought', esc(t.thought)));
      var label = TOOL_LABELS[t.tool] || t.tool;
      var argStr = '';
      try { argStr = JSON.stringify(t.args || {}); } catch (e) { argStr = ''; }
      li.appendChild(el('div', 't-call',
        '<span class="t-tool">' + esc(label) + '</span>' + (argStr && argStr !== '{}' ? ' <code>' + esc(argStr) + '</code>' : '')));
      if (t.observation) li.appendChild(el('div', 't-obs', esc(t.observation)));
      ol.appendChild(li);
    });
    det.appendChild(ol);

    if (citations.length) {
      var cite = el('div', 'citations', '<span class="c-label">Sources</span> ');
      citations.forEach(function (c) {
        var name = c.title || c.source || 'source';
        cite.appendChild(el('span', 'chip', esc(name)));
      });
      det.appendChild(cite);
    }
    bubble.appendChild(det);
    win.scrollTop = win.scrollHeight;
  }

  function showNotice(text) { notice.textContent = text; notice.hidden = false; }
  function clearNotice() { notice.hidden = true; }

  function autoGrow() { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; }
  input.addEventListener('input', autoGrow);

  function send() {
    if (pending) return;
    var text = input.value.trim();
    if (!text) return;
    clearNotice();
    addMessage('user', null, mdToHtml(text));
    history.push({ role: 'user', content: text });
    input.value = ''; autoGrow();
    pending = true; sendBtn.disabled = true;

    var dots = '<span class="dots"><span>●</span><span>●</span><span>●</span></span>';
    var typing = addMessage('bot', 'general', dots, { typing: true });

    fetch('/chat/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history.slice(-12) })
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    }).then(function (r) {
      typing.remove();
      if (r.ok && r.data.reply) {
        var agent = r.data.agent || 'general';
        var msg = addMessage('bot', agent, mdToHtml(r.data.reply));
        renderTrace(msg.querySelector('.bubble'), r.data.trace, r.data.citations);
        history.push({ role: 'assistant', content: r.data.reply });
      } else {
        var m = (r.data && r.data.message) || 'The assistant is unavailable right now. Please try again.';
        showNotice(m);
      }
    }).catch(function () {
      typing.remove();
      showNotice('Network error — could not reach the assistant. Please try again.');
    }).then(function () {
      pending = false; sendBtn.disabled = false; input.focus();
    });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); send(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  input.focus();
})();

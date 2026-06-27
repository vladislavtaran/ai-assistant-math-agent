/* ============================================================
   Assistant front-end — talks to /chat/api/chat
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

  // client-side mirror of the server's math router (for instant UI feedback)
  var MATH_KW = /\b(solve|calculate|compute|equation|integral|integrate|derivative|differentiate|factor|simplify|expand|probability|matrix|matrices|determinant|polynomial|quadratic|sqrt|square root|logarithm|log|ln|sine|cosine|tangent|sin|cos|tan|percentage|percent|evaluate|prove|theorem|sum of|product of|average|mean|median|variance|standard deviation|factorial|permutation|combination|modulo|gcd|lcm|fraction|geometry|area of|volume of|perimeter|circumference|roots? of|series|limit of)\b/i;
  var MATH_EXPR = /\d\s*[+\-*/×÷=^]\s*\d|[∫∑√π≤≥≠±×÷∞]|\d+\s*%|x\s*\^|\d+!/;
  function looksMath(t) { return MATH_KW.test(t) || MATH_EXPR.test(t); }

  // --- tiny, safe markdown ---
  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function mdToHtml(src) {
    var out = esc(src);
    // fenced code blocks
    out = out.replace(/```([\s\S]*?)```/g, function (_, c) { return '<pre><code>' + c.replace(/^\n/, '') + '</code></pre>'; });
    // inline code
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    // bold
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // links
    out = out.replace(/\bhttps?:\/\/[^\s<]+/g, function (u) { return '<a href="' + u + '" target="_blank" rel="noopener">' + u + '</a>'; });
    // paragraphs / line breaks (skip inside <pre>)
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
    var who = role === 'user' ? 'You' : (agent === 'math' ? 'Math agent' : 'Assistant');
    var avatarChar = role === 'user' ? '›' : (agent === 'math' ? '∑' : '●');
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

  function addHandoff() {
    var h = el('div', 'handoff', '<span class="h-ico">∑</span><span>Math agent is stepping in to solve this</span>');
    win.appendChild(h); win.scrollTop = win.scrollHeight; return h;
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

    // when a math/equation question is asked, announce that the Math agent steps in
    var guess = looksMath(text) ? 'math' : 'general';
    if (guess === 'math') addHandoff();
    var dots = '<span class="dots"><span>●</span><span>●</span><span>●</span></span>';
    var typing = addMessage('bot', guess, dots, { typing: true });

    fetch('/chat/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history.slice(-12) })
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    }).then(function (r) {
      typing.remove();
      if (r.ok && r.data.reply) {
        addMessage('bot', r.data.agent || 'general', mdToHtml(r.data.reply));
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

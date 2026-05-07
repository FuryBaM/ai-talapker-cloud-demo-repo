(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  const dom = {
    useLLM: $('use'),
    showSugTgl: $('showSuggestionsToggle'),
    welcomeView: $('welcomeView'),
    chatView: $('chatView'),
    welcomeInput: $('welcomeInput'),
    welcomeSend: $('welcomeSend'),
    chatInput: $('chatInput'),
    chatSend: $('chatSend'),
    resetChat: $('resetChat'),
    copyChat: $('copyChat'),
    chatList: $('chatList'),
    chatEmpty: $('chatEmpty'),
    welcomeSuggestions: $('welcomeSuggestions'),
    chatSuggestions: $('chatSuggestions'),
    suggestionsWrap: $('suggestionsWrap'),
    chatMeta: $('chatMeta'),
    refreshSuggestions: $('refreshSuggestions'),
    toggleSuggestions: $('toggleSuggestions'),
    toggleIcon: $('toggleIcon'),
    chatCharCount: $('chatCharCount'),
    sessionLabel: $('sessionLabel'),
    msgCountLabel: $('messageCountLabel'),
    overlay: $('settingsOverlay'),
    settingsClose: $('settingsClose'),
    openSettingsBtn: $('openSettings'),
    openSettingsInChat: $('openSettingsInChat'),
    resetFromTop: $('resetFromTop'),
    welcomeTipsButton: $('welcomeTipsButton'),
    showSuggestionsFromComposer: $('showSuggestionsFromComposer'),
    socialDock: $('socialDock'),
    socialToggle: $('socialToggle'),
    chatPage: $('chatPage'),
    spLangs: $('spLangs'),
    replyPreview: $('replyPreview'),
    replyLabel: $('replyLabel'),
    replyText: $('replyText'),
    clearReply: $('clearReply')
  };

  const required = [
    'welcomeView', 'chatView', 'welcomeInput', 'welcomeSend', 'chatInput', 'chatSend',
    'chatList', 'welcomeSuggestions', 'chatSuggestions'
  ];
  const missing = required.filter(name => !dom[name]);
  if (missing.length) {
    console.error('Chat UI initialization stopped. Missing DOM nodes:', missing.join(', '));
    return;
  }

  const DEFAULT_TIPS = [
    'Какие программы подходят для математики?',
    'Какие документы нужны для поступления?',
    'Каковы сроки подачи документов?',
    'Как получить грант на обучение?',
    'Есть ли общежитие для иногородних?'
  ];

  const SUGGESTION_ICONS = ['🎓', '📄', '⏱', '💰', '🏠', '🧭'];


  function normalizeSuggestionQuestions(rawItems, limit = 6) {
    const result = [];
    const seen = new Set();
    const push = value => {
      let text = String(value ?? '')
        .replace(/<[^>]+>/g, '')
        .replace(/^[-*•]+\s*/g, '')
        .replace(/^\d+[.)]\s*/g, '')
        .replace(/^['"]|['"]$/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      if (!text) return;
      if (!text.endsWith('?')) text = text.replace(/[.!:;\s]+$/g, '') + '?';
      const key = text.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      result.push(text);
    };

    const rows = Array.isArray(rawItems) ? rawItems : [rawItems];
    for (const row of rows) {
      let text = String(row ?? '').trim();
      if (!text) continue;
      text = text.replace(/\s+(?=\d+[.)]\s+)/g, '\n');
      text = text.replace(/\s+(?=[-*•]\s+)/g, '\n');
      const lines = text.split(/[\r\n]+/).map(item => item.trim()).filter(Boolean);
      for (const line of lines) {
        const pieces = line.match(/[^?]+\?/g);
        if (pieces && pieces.length) pieces.forEach(push);
        else push(line);
        if (result.length >= limit) return result.slice(0, limit);
      }
    }
    return result.slice(0, limit);
  }

  const state = {
    sessionId: '',
    messages: [],
    tips: [],
    started: false,
    suggestionsVisible: true,
    isSending: false,
    selectedReply: null,
    lastRoute: '',
    lang: 'ru'
  };

  function apiUrl(path) {
    return `${window.API_BASE || ''}${path}`;
  }

  function makeSessionId() {
    return 'session-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function isLLMEnabled() {
    return dom.useLLM ? dom.useLLM.checked : true;
  }

  function autosize(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }

  function resetTA(el) {
    if (!el) return;
    el.value = '';
    el.style.height = '28px';
    updateCharCount();
  }

  function fmtTime(ts) {
    return new Date(ts || Date.now()).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function setDisabled(el, value) {
    if (el) el.disabled = value;
  }

  function bindClick(el, handler) {
    if (el) el.addEventListener('click', handler);
  }

  function bindChange(el, handler) {
    if (el) el.addEventListener('change', handler);
  }


  function focusQuiet(el) {
    if (!el) return;
    try { el.focus({ preventScroll: true }); }
    catch { try { el.focus(); } catch {} }
  }

  function resetLayoutScroll() {
    const nodes = [
      document.documentElement,
      document.body,
      dom.chatPage,
      document.querySelector('.chat-main'),
      dom.welcomeView,
      dom.chatView,
      dom.chatList,
      dom.welcomeSuggestions,
      dom.chatSuggestions,
      dom.suggestionsWrap
    ].filter(Boolean);

    nodes.forEach(node => {
      try { node.scrollLeft = 0; } catch {}
      try {
        if (node !== dom.chatList) node.scrollTop = 0;
      } catch {}
    });

    try { window.scrollTo({ left: 0, top: 0, behavior: 'instant' }); }
    catch { try { window.scrollTo(0, 0); } catch {} }
  }

  function updateCharCount() {
    if (dom.chatCharCount) dom.chatCharCount.textContent = String((dom.chatInput.value || '').length);
  }

  function updateStats() {
    if (dom.sessionLabel) dom.sessionLabel.textContent = state.sessionId ? state.sessionId.slice(0, 16) + '…' : '—';
    if (dom.msgCountLabel) dom.msgCountLabel.textContent = String(state.messages.filter(m => !m.pending).length);
  }

  function savePrefs() {
    try {
      localStorage.setItem('talapker-prefs', JSON.stringify({
        lang: state.lang,
        useLLM: isLLMEnabled(),
        sugVisible: state.suggestionsVisible
      }));
    } catch {}
  }

  function loadPrefs() {
    try {
      const data = JSON.parse(localStorage.getItem('talapker-prefs') || 'null');
      if (!data) return;
      if (data.lang) state.lang = data.lang;
      if (dom.useLLM && typeof data.useLLM === 'boolean') dom.useLLM.checked = data.useLLM;
      if (typeof data.sugVisible === 'boolean') state.suggestionsVisible = data.sugVisible;
      syncLangUI();
      if (dom.showSugTgl) dom.showSugTgl.checked = state.suggestionsVisible;
    } catch {}
  }

  function syncLangUI() {
    if (!dom.spLangs) return;
    dom.spLangs.querySelectorAll('.sp__lang').forEach(button => {
      button.classList.toggle('active', button.dataset.lang === state.lang);
    });
  }

  function openSettings() {
    if (!dom.overlay) return;
    dom.overlay.classList.remove('hidden');
    dom.overlay.classList.add('visible');
    updateStats();
    if (dom.settingsClose) dom.settingsClose.focus();
  }

  function closeSettings() {
    if (!dom.overlay) return;
    dom.overlay.classList.remove('visible');
    setTimeout(() => dom.overlay && dom.overlay.classList.add('hidden'), 200);
  }

  function updateChatMeta() {
    if (!dom.chatMeta) {
      updateStats();
      return;
    }
    const base = state.lastRoute ? `Режим: ${state.lastRoute}` : 'Диалог с памятью по сессии';
    const count = state.messages.filter(m => !m.pending).length;
    dom.chatMeta.textContent = base + (count ? ` · ${count} сообщ.` : '');
    updateStats();
  }

  function setBusy(busy) {
    state.isSending = busy;
    setDisabled(dom.welcomeSend, busy);
    setDisabled(dom.chatSend, busy);
    if (dom.chatSend) dom.chatSend.classList.toggle('is-loading', busy);
    setDisabled(dom.refreshSuggestions, busy);
    if (!busy) updateChatMeta();
  }

  function fallbackCopy(text) {
    const area = Object.assign(document.createElement('textarea'), { value: text });
    area.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(area);
  }

  function copyText(text) {
    if (!text) return Promise.resolve();
    return navigator.clipboard?.writeText
      ? navigator.clipboard.writeText(text).catch(() => fallbackCopy(text))
      : Promise.resolve(fallbackCopy(text));
  }

  function transcriptText() {
    return state.messages
      .filter(m => !m.pending)
      .map(m => {
        const quote = m.replyTo ? ` [ответ на: ${roleLabel(m.replyTo.role)}: ${truncateText(m.replyTo.content, 120)}]` : '';
        return `[${fmtTime(m.ts)}] ${m.role === 'user' ? 'Вы' : 'AI'}${quote}: ${m.content}`;
      })
      .join('\n\n');
  }

  function roleLabel(role) {
    return role === 'ai' || role === 'assistant' ? 'AI' : 'Вы';
  }

  function makeMessageId() {
    return 'msg-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function truncateText(value, limit = 420) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    if (text.length <= limit) return text;
    return text.slice(0, Math.max(0, limit - 1)).trimEnd() + '…';
  }

  function makeReplyPayload(msg) {
    if (!msg || msg.pending) return null;
    return {
      id: msg.id || '',
      role: msg.role === 'ai' ? 'assistant' : 'user',
      content: truncateText(msg.content, 900),
      ts: msg.ts || Date.now()
    };
  }

  function renderReplyPreview() {
    if (!dom.replyPreview) return;
    const reply = state.selectedReply;
    dom.replyPreview.classList.toggle('hidden', !reply);
    dom.replyPreview.classList.toggle('is-active', !!reply);
    if (!reply) {
      if (dom.replyLabel) dom.replyLabel.textContent = '';
      if (dom.replyText) dom.replyText.textContent = '';
      return;
    }
    if (dom.replyLabel) dom.replyLabel.textContent = 'Ответ на сообщение: ' + roleLabel(reply.role);
    if (dom.replyText) dom.replyText.textContent = truncateText(reply.content, 180);
  }

  function setReplyTarget(msg) {
    const payload = makeReplyPayload(msg);
    if (!payload) return;
    state.selectedReply = payload;
    renderReplyPreview();
    focusQuiet(dom.chatInput);
  }

  function clearReplyTarget() {
    state.selectedReply = null;
    renderReplyPreview();
  }

  function jumpToMessage(id) {
    if (!id) return;
    const safeId = window.CSS && CSS.escape ? CSS.escape(id) : String(id).replace(/"/g, '\"');
    const row = dom.chatList?.querySelector(`[data-message-id="${safeId}"]`);
    if (!row) return;
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    row.classList.add('is-highlighted');
    setTimeout(() => row.classList.remove('is-highlighted'), 1100);
  }


  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeMarkdownText(value) {
    return String(value ?? '')
      .replace(/\r\n?/g, '\n')
      .replace(/([^\n])\s+(\d+\.\s+\*\*)/g, '$1\n\n$2')
      .replace(/([^\n])\s+([*-]\s+\*\*)/g, '$1\n$2')
      .trim();
  }

  function renderInlineMarkdown(value) {
    let html = escapeHtml(value);
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    return html;
  }

  function renderAssistantMarkdown(value) {
    const text = normalizeMarkdownText(value);
    if (!text) return '';

    const lines = text.split('\n');
    const html = [];
    let paragraph = [];
    let listType = null;
    let inCode = false;
    let codeLines = [];

    const closeParagraph = () => {
      if (!paragraph.length) return;
      html.push('<p>' + renderInlineMarkdown(paragraph.join(' ').trim()) + '</p>');
      paragraph = [];
    };

    const closeList = () => {
      if (!listType) return;
      html.push(`</${listType}>`);
      listType = null;
    };

    const openList = type => {
      closeParagraph();
      if (listType && listType !== type) closeList();
      if (!listType) {
        listType = type;
        html.push(`<${type}>`);
      }
    };

    for (const rawLine of lines) {
      const line = rawLine.trim();

      if (line.startsWith('```')) {
        if (inCode) {
          html.push('<pre><code>' + escapeHtml(codeLines.join('\n')) + '</code></pre>');
          codeLines = [];
          inCode = false;
        } else {
          closeParagraph();
          closeList();
          inCode = true;
        }
        continue;
      }

      if (inCode) {
        codeLines.push(rawLine);
        continue;
      }

      if (!line) {
        closeParagraph();
        closeList();
        continue;
      }

      const ordered = line.match(/^(\d+)\.\s+(.+)$/);
      if (ordered) {
        openList('ol');
        html.push('<li>' + renderInlineMarkdown(ordered[2]) + '</li>');
        continue;
      }

      const unordered = line.match(/^[-*]\s+(.+)$/);
      if (unordered) {
        openList('ul');
        html.push('<li>' + renderInlineMarkdown(unordered[1]) + '</li>');
        continue;
      }

      closeList();
      paragraph.push(line);
    }

    if (inCode) html.push('<pre><code>' + escapeHtml(codeLines.join('\n')) + '</code></pre>');
    closeParagraph();
    closeList();
    return html.join('');
  }

  function renderMessages() {
    dom.chatList.innerHTML = '';

    if (!state.messages.length) {
      if (dom.chatEmpty) dom.chatList.appendChild(dom.chatEmpty);
      updateStats();
      return;
    }

    state.messages.forEach((msg, i) => {
      const prev = state.messages[i - 1];
      const isFirst = !prev || prev.role !== msg.role;

      const row = document.createElement('div');
      if (!msg.id) msg.id = makeMessageId();
      row.className = `msg-row msg-row--${msg.role}${isFirst ? ' msg-row--first' : ''}`;
      row.dataset.messageId = msg.id;

      const avatar = document.createElement('div');
      avatar.className = isFirst ? 'msg-avatar msg-avatar--' + msg.role : 'msg-avatar msg-avatar--spacer';
      avatar.setAttribute('aria-hidden', 'true');
      if (isFirst) {
        avatar.innerHTML = msg.role === 'user'
          ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
          : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
      }
      row.appendChild(avatar);

      const bubble = document.createElement('div');
      bubble.className = `msg-bubble msg-bubble--${msg.role}${msg.pending ? ' is-pending' : ''}`;

      if (msg.pending) {
        bubble.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
      } else {
        if (msg.replyTo) {
          const quote = document.createElement('button');
          quote.className = 'msg-quote';
          quote.type = 'button';
          quote.title = 'Перейти к выбранному сообщению';
          quote.innerHTML = '<div class="msg-quote__label"></div><div class="msg-quote__text"></div>';
          quote.querySelector('.msg-quote__label').textContent = roleLabel(msg.replyTo.role);
          quote.querySelector('.msg-quote__text').textContent = truncateText(msg.replyTo.content, 180);
          quote.addEventListener('click', () => jumpToMessage(msg.replyTo.id));
          bubble.appendChild(quote);
        }

        const text = document.createElement('div');
        text.className = 'msg-bubble__text';
        if (msg.role === 'ai') {
          text.classList.add('msg-bubble__text--markdown');
          text.innerHTML = renderAssistantMarkdown(msg.content);
        } else {
          text.textContent = msg.content;
        }
        bubble.appendChild(text);

        const copyBtn = document.createElement('button');
        copyBtn.className = 'msg-copy';
        copyBtn.type = 'button';
        copyBtn.title = 'Копировать';
        copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
        copyBtn.addEventListener('click', () => {
          copyText(msg.content);
          copyBtn.classList.add('copied');
          copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
          setTimeout(() => {
            copyBtn.classList.remove('copied');
            copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
          }, 1800);
        });
        bubble.appendChild(copyBtn);

        const replyBtn = document.createElement('button');
        replyBtn.className = 'msg-reply';
        replyBtn.type = 'button';
        replyBtn.title = 'Ответить на это сообщение';
        replyBtn.setAttribute('aria-label', 'Ответить на это сообщение');
        replyBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';
        replyBtn.addEventListener('click', () => setReplyTarget(msg));
        bubble.appendChild(replyBtn);

        if (isFirst || i === state.messages.length - 1) {
          const time = document.createElement('div');
          time.className = 'msg-time';
          time.textContent = fmtTime(msg.ts);
          bubble.appendChild(time);
        }
      }

      row.appendChild(bubble);

      const isLatestAssistant = msg.role === 'ai' && !msg.pending && i === state.messages.length - 1 && state.suggestionsVisible && state.tips.length;
      if (isLatestAssistant) {
        row.classList.add('msg-row--with-suggestions');
        const inline = document.createElement('div');
        inline.className = 'msg-inline-suggestions';
        inline.setAttribute('aria-label', 'Возможные вопросы');
        const label = document.createElement('div');
        label.className = 'msg-inline-suggestions__label';
        label.textContent = 'Возможные вопросы';
        const list = document.createElement('div');
        list.className = 'msg-inline-suggestions__list';
        state.tips.forEach((question, index) => {
          const btn = document.createElement('button');
          btn.className = 'msg-inline-suggestion';
          btn.type = 'button';
          btn.disabled = state.isSending;
          btn.innerHTML = '<span class="msg-inline-suggestion__node"></span><span class="msg-inline-suggestion__text"></span>';
          btn.querySelector('.msg-inline-suggestion__text').textContent = question;
          btn.addEventListener('click', () => send(question, 'chat'));
          list.appendChild(btn);
        });
        inline.append(label, list);
        row.appendChild(inline);
      }

      dom.chatList.appendChild(row);
    });

    updateStats();
    requestAnimationFrame(() => { dom.chatList.scrollTop = dom.chatList.scrollHeight; });
  }

  function renderSuggestionGroup(target, mode) {
    if (!target) return;
    target.innerHTML = '';

    const useInlineChat = mode === 'chat' && state.started;
    target.classList.toggle('hidden', !state.suggestionsVisible || useInlineChat);
    if (!state.suggestionsVisible || useInlineChat) return;

    const tips = normalizeSuggestionQuestions(state.tips.length ? state.tips : DEFAULT_TIPS, 6);
    tips.forEach((question, index) => {
      const btn = document.createElement('button');
      btn.className = 'chip';
      btn.type = 'button';
      btn.disabled = state.isSending;

      const icon = document.createElement('span');
      icon.className = 'chip__icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = SUGGESTION_ICONS[index % SUGGESTION_ICONS.length];

      const label = document.createElement('span');
      label.className = 'chip__text';
      label.textContent = question;

      btn.append(icon, label);
      btn.addEventListener('click', () => send(question, mode));
      target.appendChild(btn);
    });
  }

  function renderSuggestions() {
    renderSuggestionGroup(dom.welcomeSuggestions, 'welcome');

    if (dom.suggestionsWrap) {
      dom.suggestionsWrap.classList.toggle('is-collapsed', !state.suggestionsVisible || state.started);
    }
    if (dom.showSuggestionsFromComposer) {
      dom.showSuggestionsFromComposer.classList.toggle('is-active', state.suggestionsVisible);
      dom.showSuggestionsFromComposer.setAttribute('aria-pressed', state.suggestionsVisible ? 'true' : 'false');
    }
    if (dom.welcomeTipsButton) {
      dom.welcomeTipsButton.classList.toggle('is-active', state.suggestionsVisible);
      dom.welcomeTipsButton.setAttribute('aria-pressed', state.suggestionsVisible ? 'true' : 'false');
    }

    renderSuggestionGroup(dom.chatSuggestions, 'chat');

    if (dom.showSugTgl) dom.showSugTgl.checked = state.suggestionsVisible;
    if (state.started) renderMessages();
    savePrefs();
  }

  function setMode(started) {
    state.started = started;
    document.body.classList.toggle('is-chat-started', started);
    if (dom.chatPage) dom.chatPage.classList.toggle('is-chat-started', started);
    dom.welcomeView.classList.toggle('hidden', started);
    dom.chatView.classList.toggle('hidden', !started);
    if (started) focusQuiet(dom.chatInput);
    else focusQuiet(dom.welcomeInput);
    renderMessages();
    renderSuggestions();
    updateChatMeta();
    requestAnimationFrame(resetLayoutScroll);
  }

  async function loadSuggestions(force = false) {
    if (state.started && !state.suggestionsVisible && !force) return;
    try {
      const res = await fetch(apiUrl('/suggestions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lang: state.lang,
          count: 5,
          use_llm: isLLMEnabled(),
          history: state.started
            ? state.messages.filter(m => !m.pending).map(m => ({ role: m.role === 'ai' ? 'assistant' : m.role, content: m.content }))
            : []
        })
      });
      const data = await res.json();
      state.tips = normalizeSuggestionQuestions(data?.questions || [], 6);
    } catch {
      state.tips = [];
    }
    renderSuggestions();
  }

  async function send(explicitText, mode) {
    if (state.isSending) return;
    const source = mode === 'welcome' ? dom.welcomeInput : dom.chatInput;
    const text = (explicitText ?? source.value).trim();
    if (!text) return;

    resetTA(source);
    if (!state.started) setMode(true);

    const replyTo = state.selectedReply ? { ...state.selectedReply } : null;
    clearReplyTarget();

    const userMessageId = makeMessageId();
    const aiMessageId = makeMessageId();
    state.messages.push(
      { id: userMessageId, role: 'user', content: text, replyTo, ts: Date.now() },
      { id: aiMessageId, role: 'ai', content: '', pending: true, ts: Date.now() }
    );
    renderMessages();
    setBusy(true);

    const responseIndex = state.messages.length - 1;
    try {
      const res = await fetch(apiUrl('/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: state.sessionId,
          message_id: userMessageId,
          message: text,
          lang: state.lang,
          use_llm: isLLMEnabled(),
          reply_to: replyTo
        })
      });
      const data = await res.json();
      state.messages[responseIndex] = { id: aiMessageId, role: 'ai', content: data?.answer || 'Пустой ответ от сервера.', ts: Date.now() };
      state.lastRoute = data?.route ? `${data.route}${data?.profile_complete ? ' · профиль собран' : ''}` : '';
    } catch (err) {
      state.messages[responseIndex] = { id: aiMessageId, role: 'ai', content: `Ошибка: ${err}`, ts: Date.now() };
      state.lastRoute = 'ошибка запроса';
    } finally {
      setBusy(false);
      renderMessages();
      loadSuggestions();
    }
  }

  function bindComposer(textarea, button, mode) {
    if (!textarea || !button) return;
    textarea.addEventListener('input', () => {
      autosize(textarea);
      updateCharCount();
    });
    button.addEventListener('click', () => send(null, mode));
    textarea.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send(null, mode);
      }
    });
  }

  function resetConversation() {
    state.sessionId = makeSessionId();
    state.messages = [];
    state.started = false;
    state.lastRoute = '';
    state.selectedReply = null;
    renderReplyPreview();
    state.suggestionsVisible = true;
    resetTA(dom.welcomeInput);
    resetTA(dom.chatInput);
    setMode(false);
    requestAnimationFrame(resetLayoutScroll);
    loadSuggestions(true);
  }

  function init() {
    loadPrefs();
    bindComposer(dom.welcomeInput, dom.welcomeSend, 'welcome');
    bindComposer(dom.chatInput, dom.chatSend, 'chat');

    if (dom.spLangs) {
      dom.spLangs.addEventListener('click', event => {
        const btn = event.target.closest('.sp__lang');
        if (!btn) return;
        state.lang = btn.dataset.lang || 'ru';
        syncLangUI();
        savePrefs();
        loadSuggestions(true);
      });
    }

    bindClick(dom.openSettingsBtn, openSettings);
    bindClick(dom.openSettingsInChat, openSettings);
    bindClick(dom.settingsClose, closeSettings);
    if (dom.overlay) {
      dom.overlay.addEventListener('click', event => {
        if (event.target === dom.overlay) closeSettings();
      });
    }
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && dom.overlay && !dom.overlay.classList.contains('hidden')) closeSettings();
    });

    bindChange(dom.showSugTgl, () => {
      state.suggestionsVisible = dom.showSugTgl.checked;
      renderSuggestions();
      savePrefs();
    });
    bindChange(dom.useLLM, () => {
      savePrefs();
      loadSuggestions(true);
    });

    bindClick(dom.clearReply, clearReplyTarget);
    bindClick(dom.resetChat, resetConversation);
    bindClick(dom.resetFromTop, resetConversation);
    bindClick(dom.welcomeTipsButton, () => {
      state.suggestionsVisible = !state.suggestionsVisible;
      renderSuggestions();
      if (state.suggestionsVisible) loadSuggestions(true);
    });
    bindClick(dom.showSuggestionsFromComposer, () => {
      state.suggestionsVisible = !state.suggestionsVisible;
      renderSuggestions();
      if (state.suggestionsVisible) loadSuggestions(true);
    });
    bindClick(dom.socialToggle, () => {
      if (!dom.socialDock) return;
      dom.socialDock.classList.toggle('is-open');
      dom.socialToggle.setAttribute('aria-expanded', dom.socialDock.classList.contains('is-open') ? 'true' : 'false');
    });
    document.addEventListener('click', event => {
      if (!dom.socialDock || !dom.socialDock.classList.contains('is-open')) return;
      if (!dom.socialDock.contains(event.target)) dom.socialDock.classList.remove('is-open');
    });
    bindClick(dom.copyChat, () => {
      const text = transcriptText();
      if (!text || !dom.copyChat) return;
      copyText(text);
      const original = dom.copyChat.innerHTML;
      dom.copyChat.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
      setTimeout(() => { if (dom.copyChat) dom.copyChat.innerHTML = original; }, 1800);
    });
    bindClick(dom.refreshSuggestions, () => loadSuggestions(true));
    bindClick(dom.toggleSuggestions, () => {
      state.suggestionsVisible = false;
      renderSuggestions();
    });

    state.sessionId = makeSessionId();
    setMode(false);
    updateCharCount();
    loadSuggestions(true);
  }

  init();
})();

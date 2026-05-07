import { api } from '../core/api.js';
import { state } from '../core/state.js';

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = String(value ?? '');
  return div.innerHTML;
}

function splitList(value) {
  return String(value || '')
    .split(/[\n,]/)
    .map(item => item.trim())
    .filter(Boolean);
}

function formatDate(seconds) {
  if (!seconds) return '—';
  try {
    return new Date(Number(seconds) * 1000).toLocaleString();
  } catch {
    return String(seconds);
  }
}

class AdminAccessManager extends HTMLElement {
  connectedCallback() {
    this.classList.add('admin-access-manager');
    this.renderShell();
    this.bind();
    window.addEventListener('admin-auth-changed', () => this.load());
    if (state.token) this.load();
  }

  renderShell() {
    this.innerHTML = `
      <div class="workspace-card__head">
        <div>
          <h3>Доступы и права API</h3>
          <div class="muted">Пользователи админки, роли, API keys и audit log. Проверка прав выполняется на backend endpoints.</div>
        </div>
        <span class="pill">RBAC</span>
      </div>
      <div class="access-grid">
        <section class="access-panel">
          <h4>Создать администратора</h4>
          <label class="field-label">Логин<input data-access="username" placeholder="personnel_1" /></label>
          <label class="field-label">Роль
            <select data-access="role">
              <option value="main_admin">Главный админ</option>
              <option value="content_admin">Контент-админ</option>
              <option value="section_admin" selected>Обычный админ раздела</option>
              <option value="viewer">Наблюдатель</option>
            </select>
          </label>
          <label class="field-label">Пароль<input data-access="password" placeholder="оставь пустым — backend сгенерирует" type="text" /></label>
          <label class="field-label">Разделы<textarea data-access="sections" placeholder="faq, news, tuition"></textarea></label>
          <label class="field-label">Дополнительные scopes<textarea data-access="scopes" placeholder="entries:update:faq\nsources:reindex"></textarea></label>
          <div class="toolbar toolbar--compact">
            <button class="btn btn--brand" data-access-action="create-user" type="button">Создать</button>
            <button class="btn" data-access-action="reload" type="button">Обновить</button>
          </div>
          <pre class="json-wrap access-result" data-access="result"></pre>
        </section>
        <section class="access-panel access-panel--wide">
          <h4>Администраторы</h4>
          <div class="access-list" data-access="users"></div>
          <h4>API keys</h4>
          <div class="access-list" data-access="keys"></div>
          <h4>Audit log</h4>
          <div class="access-list access-list--audit" data-access="audit"></div>
        </section>
      </div>
    `;
  }

  bind() {
    this.addEventListener('click', event => {
      const button = event.target.closest('[data-access-action]');
      if (!button) return;
      const action = button.dataset.accessAction;
      if (action === 'reload') this.load();
      if (action === 'create-user') this.createUser();
      if (action === 'disable-user') this.disableUser(button.dataset.username);
      if (action === 'create-key') this.createKey(button.dataset.username);
      if (action === 'revoke-key') this.revokeKey(button.dataset.keyId);
    });
  }

  field(name) {
    return this.querySelector(`[data-access="${name}"]`);
  }

  setResult(value) {
    const target = this.field('result');
    if (!target) return;
    target.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  async load() {
    if (!state.token) return;
    try {
      const data = await api('/admin/access/users');
      const audit = await api('/admin/access/audit-log?limit=30').catch(error => ({ events: [{ action: 'audit unavailable', detail: error.message }] }));
      this.renderUsers(data.users || []);
      this.renderKeys(data.api_keys || []);
      this.renderAudit(audit.events || []);
      this.setResult('');
    } catch (error) {
      this.setResult(`Нет доступа к управлению пользователями: ${error.message || error}`);
    }
  }

  async createUser() {
    const payload = {
      username: this.field('username')?.value || '',
      role: this.field('role')?.value || 'section_admin',
      password: this.field('password')?.value || null,
      sections: splitList(this.field('sections')?.value),
      scopes: splitList(this.field('scopes')?.value),
    };
    try {
      const data = await api('/admin/access/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      this.setResult(data);
      await this.load();
    } catch (error) {
      this.setResult(`Ошибка создания: ${error.message || error}`);
    }
  }

  async disableUser(username) {
    if (!username) return;
    try {
      const data = await api(`/admin/access/users/${encodeURIComponent(username)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disabled: true }),
      });
      this.setResult(data);
      await this.load();
    } catch (error) {
      this.setResult(`Ошибка отключения: ${error.message || error}`);
    }
  }

  async createKey(username) {
    if (!username) return;
    try {
      const data = await api(`/admin/access/users/${encodeURIComponent(username)}/api-keys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'web generated key' }),
      });
      this.setResult(data);
      await this.load();
    } catch (error) {
      this.setResult(`Ошибка API key: ${error.message || error}`);
    }
  }

  async revokeKey(keyId) {
    if (!keyId) return;
    try {
      const data = await api(`/admin/access/api-keys/${encodeURIComponent(keyId)}/revoke`, { method: 'POST' });
      this.setResult(data);
      await this.load();
    } catch (error) {
      this.setResult(`Ошибка отзыва: ${error.message || error}`);
    }
  }

  renderUsers(users) {
    const target = this.field('users');
    if (!target) return;
    if (!users.length) {
      target.innerHTML = '<div class="muted">Администраторы не найдены.</div>';
      return;
    }
    target.innerHTML = users.map(user => `
      <article class="access-item ${user.disabled ? 'is-disabled' : ''}">
        <div>
          <strong>${escapeHtml(user.username)}</strong>
          <span class="pill">${escapeHtml(user.role)}</span>
          <div class="muted">sections: ${(user.sections || []).map(escapeHtml).join(', ') || '—'} · expires: ${formatDate(user.expires_at)}</div>
        </div>
        <div class="toolbar toolbar--compact">
          <button class="btn" data-access-action="create-key" data-username="${escapeHtml(user.username)}" type="button">API key</button>
          <button class="danger" data-access-action="disable-user" data-username="${escapeHtml(user.username)}" type="button">Отключить</button>
        </div>
      </article>
    `).join('');
  }

  renderKeys(keys) {
    const target = this.field('keys');
    if (!target) return;
    if (!keys.length) {
      target.innerHTML = '<div class="muted">API keys не созданы.</div>';
      return;
    }
    target.innerHTML = keys.map(key => `
      <article class="access-item ${key.revoked ? 'is-disabled' : ''}">
        <div>
          <strong>${escapeHtml(key.owner_username)}</strong>
          <span class="pill">${escapeHtml(key.key_prefix)}…</span>
          <div class="muted">${escapeHtml(key.name || 'key')} · last: ${formatDate(key.last_used_at)} · expires: ${formatDate(key.expires_at)}</div>
        </div>
        <button class="danger" data-access-action="revoke-key" data-key-id="${escapeHtml(key.key_id)}" type="button">Отозвать</button>
      </article>
    `).join('');
  }

  renderAudit(events) {
    const target = this.field('audit');
    if (!target) return;
    if (!events.length) {
      target.innerHTML = '<div class="muted">Событий нет.</div>';
      return;
    }
    target.innerHTML = events.map(event => `
      <article class="access-audit-row">
        <span>${formatDate(event.created_at)}</span>
        <strong>${escapeHtml(event.actor)}</strong>
        <span>${escapeHtml(event.action)}</span>
        <span>${escapeHtml(event.target || '')}</span>
      </article>
    `).join('');
  }
}

customElements.define('admin-access-manager', AdminAccessManager);

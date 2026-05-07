import { api } from '../core/api.js';
import { state } from '../core/state.js';

class AdminSessionBadge extends HTMLElement {
  connectedCallback() {
    this.classList.add('admin-session-badge');
    window.addEventListener('admin-auth-changed', () => this.refresh());
    this.refresh();
  }

  async refresh() {
    if (!state.token) {
      this.innerHTML = '';
      return;
    }
    try {
      const me = await api('/admin/auth/me');
      state.currentAdmin = me;
      const scopes = Array.isArray(me.scopes) ? me.scopes.length : 0;
      this.innerHTML = `
        <span class="admin-session-badge__name">${this.escape(me.username || 'admin')}</span>
        <span class="pill">${this.escape(me.role || '')}</span>
        <span class="muted">${scopes} прав</span>
      `;
    } catch {
      this.innerHTML = '';
    }
  }

  escape(value) {
    const div = document.createElement('div');
    div.textContent = String(value ?? '');
    return div.innerHTML;
  }
}

customElements.define('admin-session-badge', AdminSessionBadge);

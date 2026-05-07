import { defineComponent, LightComponent } from './component_base.js';

function isTypingTarget(target) {
  return Boolean(target?.closest?.('input, textarea, select, [contenteditable="true"]'));
}

class AdminMenuBar extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this._onClick = this.onClick.bind(this);
    this._onDocumentClick = this.onDocumentClick.bind(this);
    this._onKeyDown = this.onKeyDown.bind(this);
    this.addEventListener('click', this._onClick);
    document.addEventListener('click', this._onDocumentClick);
    document.addEventListener('keydown', this._onKeyDown);
  }

  disconnectedCallback() {
    this.removeEventListener('click', this._onClick);
    document.removeEventListener('click', this._onDocumentClick);
    document.removeEventListener('keydown', this._onKeyDown);
  }

  closeMenus() {
    this.$all('.admin-menu-group.is-open').forEach(group => group.classList.remove('is-open'));
  }

  onClick(event) {
    const trigger = event.target.closest('.admin-menu-trigger');
    if (trigger && this.contains(trigger)) {
      event.stopPropagation();
      const group = trigger.closest('.admin-menu-group');
      const wasOpen = group?.classList.contains('is-open');
      this.closeMenus();
      if (group && !wasOpen) group.classList.add('is-open');
      this.emit('admin-menu-labels-request');
      return;
    }

    const actionButton = event.target.closest('[data-menu-action]');
    if (actionButton && this.contains(actionButton)) {
      this.emit('admin-menu-action', { action: actionButton.dataset.menuAction });
      this.closeMenus();
    }
  }

  onDocumentClick(event) {
    if (!event.target.closest('admin-menu-bar')) this.closeMenus();
  }

  onKeyDown(event) {
    if (event.key === 'Escape') {
      if (this.querySelector('.admin-menu-group.is-open')) {
        event.preventDefault();
        this.closeMenus();
        return;
      }
      if (!isTypingTarget(event.target)) this.emit('admin-menu-action', { action: 'clear-selection' });
      return;
    }

    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === 's') {
      event.preventDefault();
      this.emit('admin-menu-action', { action: event.shiftKey ? 'save-source' : 'save-current' });
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === 'r') {
      event.preventDefault();
      this.emit('admin-menu-action', { action: event.shiftKey ? 'rebuild-all' : 'rebuild-current' });
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === 'j') {
      event.preventDefault();
      this.emit('admin-menu-action', { action: 'merge-selected' });
      return;
    }
    if (event.altKey && key === '3') {
      event.preventDefault();
      this.emit('admin-menu-action', { action: 'toggle-bottom' });
      return;
    }
    if (isTypingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;

    const shortcuts = {
      d: 'mark-body',
      m: 'mark-meta',
      i: 'mark-ignore',
      h: 'mark-heading',
      p: 'mark-draft',
      e: 'edit-block',
      s: 'split-cursor',
      g: 'create-entry',
    };
    if (shortcuts[key]) {
      event.preventDefault();
      this.emit('admin-menu-action', { action: shortcuts[key] });
    }
  }
}

defineComponent('admin-menu-bar', AdminMenuBar);

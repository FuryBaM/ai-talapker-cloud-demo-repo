import { defineComponent, LightComponent } from './component_base.js';

class AdminBottomConsole extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.addEventListener('click', event => {
      const toggle = event.target.closest('#bottomPaneToggle');
      if (toggle && this.contains(toggle)) {
        this.toggleCollapsed();
        return;
      }
      const tab = event.target.closest('[data-bottom-tab]');
      if (tab && this.contains(tab)) {
        this.expand();
        this.emit('admin-bottom-tab', { tab: tab.dataset.bottomTab });
        return;
      }
      if (event.target.closest('#debugBtn')) {
        this.emit('admin-debug-search');
      }
    });
  }

  expand() {
    this.classList.remove('is-collapsed');
  }

  collapse() {
    this.classList.add('is-collapsed');
  }

  toggleCollapsed() {
    this.classList.toggle('is-collapsed');
  }

  setActiveTab(value) {
    this.$all('[data-bottom-tab]').forEach(item => item.classList.toggle('active', item.dataset.bottomTab === value));
    this.$all('[data-bottom-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.bottomPanel !== value));
  }
}

defineComponent('admin-bottom-console', AdminBottomConsole);

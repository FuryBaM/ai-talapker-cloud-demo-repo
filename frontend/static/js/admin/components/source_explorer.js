import { defineComponent, LightComponent } from './component_base.js';

class AdminSourceExplorer extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.addEventListener('click', event => {
      const tab = event.target.closest('[data-explorer-tab]');
      if (tab && this.contains(tab)) {
        this.emit('admin-explorer-tab', { tab: tab.dataset.explorerTab });
        return;
      }
      if (event.target.closest('#loadRegistryBtn')) {
        this.emit('admin-registry-reload');
        return;
      }
      if (event.target.closest('#openUploadModalBtn')) {
        this.emit('admin-upload-open');
      }
    });
    this.$('#sourceSearchInput')?.addEventListener('input', () => this.emit('admin-source-search'));
  }

  setActiveTab(value) {
    this.$all('[data-explorer-tab]').forEach(item => item.classList.toggle('active', item.dataset.explorerTab === value));
    this.$all('[data-explorer-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.explorerPanel !== value));
  }
}

defineComponent('admin-source-explorer', AdminSourceExplorer);

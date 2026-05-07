import { defineComponent, LightComponent } from './component_base.js';

class AdminWorkspaceRouter extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.addEventListener('click', event => {
      const viewerTab = event.target.closest('[data-viewer-tab]');
      if (viewerTab && this.contains(viewerTab)) {
        this.setViewerTab(viewerTab.dataset.viewerTab);
        this.emit('admin-viewer-tab-change', { tab: viewerTab.dataset.viewerTab });
      }
    });
  }

  setActivePanel(value) {
    this.$all('[data-workspace-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.workspacePanel !== value));
  }

  setViewerTab(value) {
    this.$all('[data-viewer-tab]').forEach(item => item.classList.toggle('active', item.dataset.viewerTab === value));
    this.$all('[data-viewer-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.viewerPanel !== value));
    const pill = this.$('#viewerModePill');
    if (pill) pill.textContent = value === 'raw' ? 'разобранный payload' : 'источник';
  }
}

defineComponent('admin-workspace-router', AdminWorkspaceRouter);

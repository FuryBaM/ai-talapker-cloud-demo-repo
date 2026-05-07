import { defineComponent, LightComponent } from './component_base.js';

const BUTTON_ACTIONS = new Map([
  ['saveEntryBtn', 'save'],
  ['reindexEntryBtn', 'reindex'],
  ['clearEntryDraftBtn', 'clear-draft'],
  ['buildDraftFromDocBtn', 'build-from-source'],
  ['loadCuratedEntriesBtn', 'load-curated'],
  ['previewEntriesBtn', 'preview-indexed'],
  ['previewChunksBtn', 'preview-chunks'],
  ['editSelectedEntryBtn', 'edit-selected'],
  ['deleteSelectedEntryBtn', 'delete-selected'],
  ['reindexSelectedSavedEntryBtn', 'reindex-selected'],
]);

class AdminEntryEditor extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.style.display = 'contents';
    this.addEventListener('click', event => this.onClick(event));
    this.addEventListener('change', event => this.onChange(event));
    this.addEventListener('input', event => this.onInput(event));
  }

  onClick(event) {
    const layerTab = event.target.closest('[data-entry-layer-tab]');
    if (layerTab && this.contains(layerTab)) {
      this.emit('admin-entry-layer-tab', { tab: layerTab.dataset.entryLayerTab });
      return;
    }

    const button = event.target.closest('button[id]');
    if (!button || !this.contains(button)) return;
    const action = BUTTON_ACTIONS.get(button.id);
    if (action) this.emit('admin-entry-action', { action });
  }

  onChange(event) {
    if (event.target?.id === 'entrySchemaSelect') this.emit('admin-entry-schema-change');
    if (event.target?.closest?.('#paragraphList') && event.target.matches('input[type="checkbox"]')) {
      this.emit('admin-paragraph-selection-change', {
        value: Number(event.target.value),
        checked: Boolean(event.target.checked),
      });
    }
  }

  onInput(event) {
    if (event.target?.id === 'docEditedText') this.emit('admin-doc-edited-change');
  }
}

defineComponent('admin-entry-editor', AdminEntryEditor);

import { defineComponent, LightComponent } from './component_base.js';

const BUTTON_ACTIONS = new Map([
  ['rebuildAllBtn', 'rebuild-all'],
  ['normalizeOnlyBtn', 'normalize'],
  ['docsOnlyBtn', 'documents'],
  ['chunksOnlyBtn', 'chunks'],
  ['indexOnlyBtn', 'index'],
  ['analyzeSourceBtn', 'analyze-source'],
  ['saveStructureBtn', 'save-structure'],
  ['refreshChunksPreviewBtn', 'refresh-chunks-preview'],
  ['indexSourceFromChunksBtn', 'index'],
  ['openAdvancedDomainsBtn', 'open-domain'],
  ['openAdvancedSchemasBtn', 'open-schema'],
  ['openAdvancedEntryEditorBtn', 'open-entry'],
  ['openAdvancedSavedEntriesBtn', 'open-entries'],
]);

class AdminPipelineActions extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.style.display = 'contents';
    this.addEventListener('click', event => this.onClick(event));
  }

  onClick(event) {
    const pipelineButton = event.target.closest('[data-pipeline-action]');
    if (pipelineButton && this.contains(pipelineButton)) {
      this.emit('admin-pipeline-action', { action: pipelineButton.dataset.pipelineAction });
      return;
    }

    const button = event.target.closest('button[id]');
    if (!button || !this.contains(button)) return;
    const action = BUTTON_ACTIONS.get(button.id);
    if (action) this.emit('admin-pipeline-action', { action });
  }
}

defineComponent('admin-pipeline-actions', AdminPipelineActions);

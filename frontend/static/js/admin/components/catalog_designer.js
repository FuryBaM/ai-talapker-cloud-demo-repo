import { defineComponent, LightComponent } from './component_base.js';

const BUTTON_ACTIONS = new Map([
  ['newDomainBtn', 'domain-new'],
  ['saveDomainBtn', 'domain-save'],
  ['deleteDomainBtn', 'domain-delete'],
  ['saveCatalogBtn', 'catalog-save'],
  ['newSchemaBtn', 'schema-new'],
  ['saveSchemaBtn', 'schema-save'],
  ['deleteSchemaBtn', 'schema-delete'],
  ['saveCatalogBtnSchema', 'catalog-save'],
  ['addSchemaFieldBtn', 'schema-field-add'],
  ['refreshSchemaPreviewBtn', 'schema-preview'],
  ['testSchemaBtn', 'schema-test'],
]);

class AdminCatalogDesigner extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.style.display = 'contents';
    this.addEventListener('click', event => this.onClick(event));
    this.addEventListener('change', event => this.onChange(event));
    this.addEventListener('input', event => this.onInput(event));
  }

  onClick(event) {
    const tab = event.target.closest('[data-schema-designer-tab]');
    if (tab && this.contains(tab)) {
      this.emit('admin-schema-designer-tab', { tab: tab.dataset.schemaDesignerTab });
      return;
    }

    const button = event.target.closest('button[id]');
    if (!button || !this.contains(button)) return;
    const action = BUTTON_ACTIONS.get(button.id);
    if (action) this.emit('admin-catalog-action', { action });
  }

  onChange(event) {
    const target = event.target;
    if (!target || !this.contains(target)) return;
    if (target.id === 'domainSelect') {
      this.emit('admin-domain-select', { value: target.value });
      return;
    }
    if (target.id === 'schemaSelect') {
      this.emit('admin-schema-select', { value: target.value });
      return;
    }
    if (target.name === 'schemaTypeChoice') {
      this.emit('admin-schema-type-change', { value: target.value });
      return;
    }
    if (['schemaName', 'schemaDescription', 'schemaHandler', 'schemaEnabled'].includes(target.id)) {
      this.emit('admin-schema-draft-change', { id: target.id });
    }
  }

  onInput(event) {
    const target = event.target;
    if (!target || !this.contains(target)) return;
    if (['schemaName', 'schemaDescription', 'schemaHandler', 'schemaEnabled'].includes(target.id)) {
      this.emit('admin-schema-inspector-refresh', { id: target.id });
    }
  }
}

defineComponent('admin-catalog-designer', AdminCatalogDesigner);

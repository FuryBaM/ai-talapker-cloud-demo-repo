import { defineComponent, LightComponent } from './component_base.js';

const SOURCE_FIELD_IDS = new Set(['editorClass', 'editorSchema', 'editorLevel', 'editorLang', 'editorNotes']);
const SHEET_CONTROL_IDS = new Set(['sheetSelect', 'headerRowSelect', 'dataStartRowSelect', 'textColumnsSelect', 'tableSelectionMode']);
const XSLX_BUTTON_ACTIONS = new Map([
  ['clearTableSelectionBtn', 'clear-selection'],
  ['useTableInEntryBtn', 'open-entry'],
  ['setHeaderFromSelectionBtn', 'set-header'],
  ['markMetadataFromSelectionBtn', 'mark-metadata'],
  ['setDataBelowHeaderBtn', 'set-data-below-header'],
  ['setDataFromSelectionBtn', 'set-data'],
  ['clearSelectedTableMarksBtn', 'clear-selected-marks'],
  ['clearTableMarksBtn', 'clear-all-marks'],
  ['schemaMarkHeaderBtn', 'set-header'],
  ['schemaMarkFooterBtn', 'mark-footer'],
  ['schemaMarkIgnoreBtn', 'mark-ignore'],
  ['schemaCreateFieldFromColumnBtn', 'schema-field-from-column'],
  ['schemaCreateFieldFromCellBtn', 'schema-field-from-cell'],
]);
const TABLE_DRAFT_BUTTONS = new Map([
  ['buildDraftFromSourceBtn', 'build-from-source'],
  ['useSelectionForTitleBtn', 'title'],
  ['useSelectionForTextBtn', 'text'],
  ['useSelectionForSourceBtn', 'source'],
  ['useSelectionForNoteBtn', 'note'],
]);
const SOURCE_ACTION_BUTTONS = new Map([
  ['saveMappingBtn', 'save-mapping'],
  ['downloadSourceBtn', 'download-source'],
  ['rebuildSourceBtn', 'rebuild-source'],
  ['deleteSourceBtn', 'delete-source'],
]);
const SCHEMA_FIELD_IDS = new Set([
  'schemaInspectorFieldName',
  'schemaInspectorFieldLabel',
  'schemaInspectorFieldType',
  'schemaInspectorFieldSystem',
  'schemaInspectorFieldDestination',
  'schemaInspectorFieldValidation',
  'schemaInspectorFieldRequired',
]);

class AdminInspectorPanel extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.addEventListener('click', event => this.onClick(event));
    this.addEventListener('change', event => this.onChange(event));
    this.addEventListener('input', event => this.onInput(event));
  }

  setContext(contexts = []) {
    const active = new Set(contexts.filter(Boolean));
    this.$all('[data-inspector-context]').forEach(card => {
      const cardContexts = String(card.dataset.inspectorContext || '').split(/\s+/).filter(Boolean);
      card.classList.toggle('hidden', !cardContexts.some(context => active.has(context)));
    });
  }

  onClick(event) {
    const button = event.target.closest('button[id]');
    if (!button || !this.contains(button)) return;

    const sourceAction = SOURCE_ACTION_BUTTONS.get(button.id);
    if (sourceAction) {
      this.emit('admin-source-action', { action: sourceAction });
      return;
    }

    const tableDraftAction = TABLE_DRAFT_BUTTONS.get(button.id);
    if (tableDraftAction) {
      this.emit('admin-table-draft-action', { action: tableDraftAction });
      return;
    }

    const xlsxAction = XSLX_BUTTON_ACTIONS.get(button.id);
    if (xlsxAction) this.emit('admin-xlsx-action', { action: xlsxAction });
  }

  onChange(event) {
    const target = event.target;
    if (!target || !this.contains(target)) return;

    if (SOURCE_FIELD_IDS.has(target.id)) {
      this.emit('admin-source-field-change', { id: target.id });
      return;
    }

    if (SHEET_CONTROL_IDS.has(target.id)) {
      this.emit('admin-sheet-control-change', { id: target.id, value: target.value });
      return;
    }

    if (target.name === 'schemaTypeChoice') {
      this.emit('admin-schema-type-change', { value: target.value });
      return;
    }

    if (SCHEMA_FIELD_IDS.has(target.id)) {
      this.emit('admin-schema-field-change', { id: target.id });
    }
  }

  onInput(event) {
    const target = event.target;
    if (!target || !this.contains(target)) return;
    if (target.id === 'editorNotes') this.emit('admin-source-field-change', { id: target.id });
  }
}

defineComponent('admin-inspector-panel', AdminInspectorPanel);

import { EDUCATION_LEVELS, LANGUAGES, refs, renderSelectOptions, selectedValues, state } from '../core/state.js';
import { renderDomainEditor, renderSchemaEditor, renderSchemaInspector, renderSchemaMappingList, renderXlsxContextInspector } from '../features/mapping/logic.js';
import { renderExtractionWorkspace } from './extraction.js';

export function adminLog(value) {
  refs.adminOutput.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

export function setLoggedIn(isLoggedIn) {
  refs.loginView.classList.toggle('hidden', isLoggedIn);
  refs.adminView.classList.toggle('hidden', !isLoggedIn);
  refs.logoutBtn.classList.toggle('hidden', !isLoggedIn);
}

export function setWorkspaceTab(value) {
  state.workspaceTab = value;
  const visibleTab = ['entry','domain','schema'].includes(value) ? 'advanced' : value;
  document.querySelectorAll('[data-workspace-tab]').forEach(item => item.classList.toggle('active', item.dataset.workspaceTab === visibleTab));
  const workspace = document.querySelector('admin-workspace-router');
  if (workspace?.setActivePanel) workspace.setActivePanel(value);
  else document.querySelectorAll('[data-workspace-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.workspacePanel !== value));
  updateInspectorContext();
}

export function updateInspectorContext() {
  const isSourceMode = Boolean(state.currentSource) && ['viewer', 'structure', 'entries', 'chunks', 'index', 'entry'].includes(state.workspaceTab);
  const sourceType = state.currentParsed?.source_type || '';
  const isXlsxMode = isSourceMode && sourceType === 'xlsx';
  const isTextStructureMode = isSourceMode && !isXlsxMode && state.workspaceTab === 'structure';
  const isXlsxEntryMode = isXlsxMode && state.workspaceTab === 'entry';
  const isSchemaMode = state.workspaceTab === 'schema';
  document.querySelectorAll('[data-inspector-context]').forEach(card => {
    const contexts = String(card.dataset.inspectorContext || '').split(/\s+/).filter(Boolean);
    const show =
      (contexts.includes('source') && isSourceMode) ||
      (contexts.includes('text') && isTextStructureMode) ||
      (contexts.includes('xlsx') && isXlsxMode) ||
      (contexts.includes('xlsx-entry') && isXlsxEntryMode) ||
      (contexts.includes('schema') && isSchemaMode);
    card.classList.toggle('hidden', !show);
  });
  if (isSchemaMode) renderSchemaInspector();
  renderSchemaMappingList();
  renderXlsxContextInspector();
  renderExtractionWorkspace();
}

export function setExplorerTab(value) {
  const requested = String(value || '').trim();
  const safe = requested.replace(/[\\"']/g, '');
  const tab = safe ? document.querySelector('[data-explorer-tab="' + safe + '"]') : null;
  const panel = safe ? document.querySelector('[data-explorer-panel="' + safe + '"]') : null;
  const usable = tab && panel && !tab.classList.contains('hidden');
  const next = usable ? safe : 'sources';
  state.explorerTab = next;
  document.querySelectorAll('[data-explorer-tab]').forEach(item => item.classList.toggle('active', item.dataset.explorerTab === next));
  const explorer = document.querySelector('admin-source-explorer');
  if (explorer?.setActiveTab) explorer.setActiveTab(next);
  else document.querySelectorAll('[data-explorer-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.explorerPanel !== next));
}

export function setBottomTab(value) {
  state.bottomTab = value;
  const bottom = document.querySelector('admin-bottom-console');
  if (bottom?.setActiveTab) bottom.setActiveTab(value);
  else {
    document.querySelectorAll('[data-bottom-tab]').forEach(item => item.classList.toggle('active', item.dataset.bottomTab === value));
    document.querySelectorAll('[data-bottom-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.bottomPanel !== value));
  }
}

export function setEntryLayerTab(value) {
  document.querySelectorAll('[data-entry-layer-tab]').forEach(item => item.classList.toggle('active', item.dataset.entryLayerTab === value));
  document.querySelectorAll('[data-entry-layer-panel]').forEach(item => item.classList.toggle('hidden', item.dataset.entryLayerPanel !== value));
}

export function domainOptions(includeAny = true) {
  const items = state.catalog.domains
    .filter(item => item.enabled !== false)
    .map(item => {
      const value = String(item.name || item.key || item.id || item.value || '').trim();
      return { value, label: String(item.label || item.title || item.name || value) };
    })
    .filter(item => item.value);
  return includeAny ? [{ value: '', label: 'домен: любой' }, ...items] : items;
}

export function schemaOptions(includeAny = true) {
  const items = state.catalog.schemas.filter(item => item.enabled !== false).map(item => ({ value: item.name, label: `${item.name} (${item.handler})` }));
  return includeAny ? [{ value: '', label: 'тип записи: любой' }, ...items] : items;
}



export function destinationLabel(value) {
  return {
    fields: 'поля записи',
    metadata: 'метаданные',
    title: 'заголовок',
    text: 'текст',
    embedding_text: 'текст для эмбеддинга',
  }[value] || value;
}

export function roleLabel(value) {
  return {
    heading_1: 'Заголовок 1',
    heading_2: 'Заголовок 2',
    heading_3: 'Секция',
    body: 'Текст',
    list_item: 'Пункт списка',
    note: 'Примечание',
    ignore: 'Игнорировать',
  }[value] || value;
}

export function tableRoleLabel(value) {
  return {
    header: 'заголовок',
    ignore: 'игнор',
    footer: 'футер',
    meta: 'мета',
    data: 'данные',
  }[value] || value;
}

export function mappingKindLabel(value) {
  return {
    column: 'колонка',
    cell: 'ячейка',
    manual: 'вручную',
  }[value] || value;
}

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

export function indexOptions(items, emptyLabel) {
  return [
    { value: '', label: emptyLabel },
    ...items.map((item, index) => ({ value: String(index), label: item.name || `элемент ${index + 1}` }))
  ];
}

export function syncGlobalDropdowns() {
  const editorClassValue = refs.entryClassSelect?.value || '';
  const editorSchemaValue = refs.entrySchemaSelect?.value || '';
  renderSelectOptions(document.getElementById('uploadClass'), domainOptions(), '');
  renderSelectOptions(document.getElementById('uploadSchema'), schemaOptions(), '');
  renderSelectOptions(document.getElementById('uploadLevel'), EDUCATION_LEVELS, '');
  renderSelectOptions(document.getElementById('uploadLang'), LANGUAGES, '');
  renderSelectOptions(document.getElementById('entriesClass'), domainOptions(), '');
  renderSelectOptions(document.getElementById('entriesSchema'), schemaOptions(), '');
  renderSelectOptions(document.getElementById('debugDomains'), domainOptions(false), [], true);
  renderSelectOptions(document.getElementById('debugSchemas'), schemaOptions(false), [], true);
  renderSelectOptions(document.getElementById('debugLevel'), EDUCATION_LEVELS, '');
  renderSelectOptions(document.getElementById('debugLang'), LANGUAGES, '');
  renderSelectOptions(refs.entryClassSelect, domainOptions(), editorClassValue);
  renderSelectOptions(refs.entrySchemaSelect, schemaOptions(), editorSchemaValue);
  renderSelectOptions(refs.entryLevelSelect, EDUCATION_LEVELS, refs.entryLevelSelect.value || '');
  renderSelectOptions(refs.entryLangSelect, LANGUAGES, refs.entryLangSelect.value || '');
  renderDomainEditor(state.currentDomainIndex);
  renderSchemaEditor(state.currentSchemaIndex);
}

function isEffectivelyBlankColumnCell(value) {
  const text = String(value ?? '').trim();
  return !text || text === 'ø' || text === 'Ø' || text === '∅' || /^null$/i.test(text) || /^undefined$/i.test(text);
}

function meaningfulColumnCount(rows = []) {
  const lastIndex = rows.reduce((max, row) => {
    if (!Array.isArray(row)) return max;
    for (let index = row.length - 1; index >= 0; index -= 1) {
      if (!isEffectivelyBlankColumnCell(row[index])) return Math.max(max, index);
    }
    return max;
  }, -1);
  return Math.max(0, lastIndex + 1);
}

export function currentColumns() {
  if (!state.currentParsed || state.currentParsed.source_type !== 'xlsx') return [];
  const sheets = state.currentParsed.sheets || [];
  const sheet = sheets.find(item => item.sheet_title === state.currentSheetName) || sheets[0];
  if (!sheet) return [];
  const rows = sheet.rows || [];
  const headerIndex = Math.max(0, Number(document.getElementById('headerRowSelect')?.value || 1) - 1);
  const header = rows[headerIndex] || [];
  const count = meaningfulColumnCount(rows);
  return Array.from({ length: count }, (_, index) => {
    const label = String(header[index] ?? '').trim();
    return { value: String(index), label: `${index + 1}: ${label || `Колонка ${index + 1}`}` };
  });
}

export function renderFieldLabelInputs() {
  if (!refs.fieldLabelList) return;
  refs.fieldLabelList.innerHTML = '';
  const selectedColumns = selectedValues(document.getElementById('textColumnsSelect'));
  const columns = currentColumns();
  selectedColumns.forEach(column => {
    const descriptor = columns.find(item => item.value === column);
    const row = document.createElement('div');
    row.className = 'field-row';
    row.innerHTML = `
      <div>${descriptor ? descriptor.label : column}</div>
      <input data-column="${column}" placeholder="метка поля" value="${descriptor ? descriptor.label.split(': ').slice(1).join(': ') : ''}">
    `;
    refs.fieldLabelList.appendChild(row);
  });
}


function normalizeDirtySheetName(sheetName = '') {
  return String(sheetName || '').trim();
}

export function dirtyKeyForSource(sourceId = '', sheetName = '') {
  const id = String(sourceId || '').trim();
  if (!id) return '';
  const sheet = normalizeDirtySheetName(sheetName);
  return sheet ? `${id}::${sheet}` : id;
}

export function currentDirtyKey() {
  if (!state.currentSource?.source_id) return '';
  const isXlsx = state.currentParsed?.source_type === 'xlsx';
  return dirtyKeyForSource(state.currentSource.source_id, isXlsx ? state.currentSheetName : '');
}

export function isDirtyKey(key = '') {
  return Boolean(state.dirtyMappings?.[String(key || '')]);
}

export function isSourceDirty(sourceId = '', sheetName = '') {
  const id = String(sourceId || '').trim();
  if (!id) return false;
  if (sheetName) return isDirtyKey(dirtyKeyForSource(id, sheetName));
  if (isDirtyKey(id)) return true;
  const prefix = `${id}::`;
  return Object.keys(state.dirtyMappings || {}).some(key => key.startsWith(prefix));
}

export function updateDirtyIndicators() {
  const currentKey = currentDirtyKey();
  const saveButton = document.getElementById('saveMappingBtn');
  if (saveButton) {
    const dirty = currentKey && isDirtyKey(currentKey);
    saveButton.classList.toggle('is-dirty', Boolean(dirty));
    saveButton.textContent = dirty ? 'Сохранить привязку *' : 'Сохранить привязку';
    saveButton.title = dirty ? 'Есть несохраненные изменения текущего листа/источника' : '';
  }
  document.querySelectorAll('[data-dirty-key]').forEach(node => {
    const key = node.getAttribute('data-dirty-key') || '';
    const dirty = isDirtyKey(key);
    node.classList.toggle('is-dirty', dirty);
    const mark = node.querySelector('[data-dirty-star]');
    if (mark) mark.classList.toggle('hidden', !dirty);
  });
  document.querySelectorAll('[data-dirty-source-id]').forEach(node => {
    const sourceId = node.getAttribute('data-dirty-source-id') || '';
    const dirty = isSourceDirty(sourceId);
    node.classList.toggle('has-dirty-children', dirty);
    const mark = node.querySelector('[data-workbook-dirty-star]');
    if (mark) mark.classList.toggle('hidden', !dirty);
  });
}

export function markCurrentSourceDirty() {
  const key = currentDirtyKey();
  if (!key) return;
  state.dirtyMappings = { ...(state.dirtyMappings || {}), [key]: true };
  updateDirtyIndicators();
}

export function clearDirtyForSource(sourceId = '', sheetName = null) {
  const id = String(sourceId || '').trim();
  if (!id) return;
  const next = { ...(state.dirtyMappings || {}) };
  if (sheetName) {
    delete next[dirtyKeyForSource(id, sheetName)];
  } else {
    delete next[id];
    const prefix = `${id}::`;
    Object.keys(next).forEach(key => {
      if (key.startsWith(prefix)) delete next[key];
    });
  }
  state.dirtyMappings = next;
  updateDirtyIndicators();
}

export function clearCurrentSourceDirty() {
  if (!state.currentSource?.source_id) return;
  clearDirtyForSource(state.currentSource.source_id);
}

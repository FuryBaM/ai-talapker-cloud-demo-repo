import { api, loginAdmin, uploadForm, downloadSourceFile } from './core/api.js?v=download-v12';
import './components/admin_session_badge.js';
import './components/access_manager.js';
import './components/admin_menu_bar.js';
import './components/source_explorer.js';
import './components/workspace_router.js';
import './components/inspector_panel.js';
import './components/bottom_console.js';
import './components/upload_modal.js';
import './components/entry_editor.js';
import './components/catalog_designer.js';
import './components/pipeline_actions.js';
import './components/training_arena.js';
import { refs, selectedValues, state } from './core/state.js';
import { initializeDocking } from './ui/dock_manager.js';
import {
  adminLog,
  clearCurrentSourceDirty,
  markCurrentSourceDirty,
  updateDirtyIndicators,
  isSourceDirty,
  renderFieldLabelInputs,
  setBottomTab,
  setEntryLayerTab,
  setExplorerTab,
  setLoggedIn,
  setWorkspaceTab,
  syncGlobalDropdowns,
} from './ui/common.js';
import {
  bindCatalogActions,
  renderCatalog,
  renderRegistry,
} from './ui/registry.js';
import {
  bindCuratedEntryActions,
  bindIndexedEntryActions,
  buildCuratedEntryPayload,
  buildDraftFromCurrentSource,
  clearEntryDraft,
  populateEntryDraft,
  renderCuratedEntriesList,
  renderDocPreview,
  renderExtractionWorkspace,
  renderIndexedEntriesList,
  renderSourceEditor,
} from './ui/extraction.js';
import { semanticDocumentMappingPayload } from './ui/text_editor.js?v=tooltip-fix1';
import {
  applyCurrentXlsxSheetMapping,
  applySchemaFieldInspector,
  applySchemaTemplateToCurrentXlsxSheet,
  buildXlsxSourceMappingPayload,
  captureCurrentSheetInspectorState,
  captureCurrentXlsxSheetMapping,
  clearTableMarks,
  clearTableMarksFromSelection,
  collectDomainPayload,
  collectSchemaFields,
  collectSchemaPayload,
  createSchemaFieldFromSelectedColumn,
  createSchemaMetadataFieldFromSelectedCell,
  currentSheetSourceConfig,
  ensureCurrentSheetUsesSelectedSchemaTemplate,
  loadXlsxSheetMappingsFromSource,
  markFooterFromSelection,
  markIgnoreFromSelection,
  markMetadataFromSelection,
  renderDomainEditor,
  renderSchemaEditor,
  renderSchemaFieldsEditor,
  renderSchemaInspector,
  renderSchemaPreview,
  renderSchemaTablePreview,
  schemaMappingPayload,
  setDataBelowHeader,
  setDataFromSelection,
  setHeaderFromSelection,
  setSchemaDesignerTab,
  tableProfilePayload,
} from './features/mapping/logic.js';
import {
  clearExcelSelection,
  fillSelectedTableTextToDraft,
  renderEntryTablePreview,
  renderSheetControls,
  setExcelSelectionMode,
} from './features/mapping/table.js';

async function login() {
  refs.loginStatus.textContent = 'Входим...';
  try {
    const data = await loginAdmin(document.getElementById('loginUsername').value, document.getElementById('loginPassword').value);
    state.token = data.access_token || data.token || data.jwt || '';
    if (!state.token) throw new Error('Сервер не вернул access_token');
    sessionStorage.setItem('admin_jwt', state.token);
    refs.loginStatus.textContent = '';
    setLoggedIn(true);
    window.dispatchEvent(new CustomEvent('admin-auth-changed'));
    try {
      await bootstrapAdmin();
    } catch (error) {
      adminLog(error.message || String(error));
      if (refs.rebuildStatus) refs.rebuildStatus.textContent = `Ошибка загрузки админ-данных: ${error.message || error}`;
    }
  } catch (error) {
    refs.loginStatus.textContent = `Ошибка входа: ${error.message || error}`;
    setLoggedIn(false);
  }
}

function isLogicalSheetSource(source = {}) {
  return String(source.source_id || '').includes('__sheet__') || Boolean(source.source_base_id || source.mapping?.source_base_id || source.mapping?.base_source_id);
}

function looksLikeXlsxSource(source = {}) {
  if (isLogicalSheetSource(source)) return false;
  const path = String(source.path || source.filename || '').toLowerCase();
  return source.source_type === 'xlsx' || source.mapping?.source_type === 'xlsx' || path.endsWith('.xlsx') || path.endsWith('.xls');
}

function xlsxDiscoveryKey(source = {}) {
  const rawPath = String(source.path || source.filename || source.file_path || '').replace(/\\/g, '/').trim().toLowerCase();
  const workbookMatch = rawPath.match(/^(.+?\.(?:xlsx|xlsm|xlsb|xls))(?:$|[\s>#›|:-])/i);
  if (workbookMatch) return `path:${workbookMatch[1]}`;
  if (rawPath) return `path:${rawPath}`;
  return `id:${String(source.source_id || '').trim()}`;
}

function sheetMetasFromParsed(parsed = {}) {
  return (parsed.sheets || []).map(sheet => ({
    sheet_title: sheet.sheet_title,
    rows: Array.isArray(sheet.rows) ? sheet.rows.length : 0,
    columns: Array.isArray(sheet.rows) ? sheet.rows.reduce((max, row) => Math.max(max, Array.isArray(row) ? row.length : 0), 0) : 0,
  })).filter(sheet => sheet.sheet_title);
}

async function discoverXlsxSheetSources() {
  const seen = new Set();
  const xlsxSources = (state.registry || []).filter(source => looksLikeXlsxSource(source)).filter(source => {
    const key = xlsxDiscoveryKey(source);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  for (const source of xlsxSources) {
    if (state.xlsxSourceSheets?.[source.source_id]?.length) continue;
    if (source.mapping?.sheet_mappings && Object.keys(source.mapping.sheet_mappings).length) continue;
    try {
      const preview = await api(`/admin/source-preview/${source.source_id}`);
      const sheets = sheetMetasFromParsed(preview.parsed || {});
      if (!sheets.length) continue;
      state.xlsxSourceSheets = state.xlsxSourceSheets || {};
      state.xlsxSourceSheets[source.source_id] = sheets;
      renderRegistry(openSource);
    } catch (error) {
      adminLog(`Не удалось прочитать листы ${source.source_id}: ${error.message || error}`);
    }
  }
}

async function loadRegistry() {
  const data = await api('/admin/registry');
  state.registry = data.sources || [];
  setExplorerTab('sources');
  renderRegistry(openSource);
  updateDirtyIndicators();
  discoverXlsxSheetSources();
  adminLog(data);
}

async function loadCatalog() {
  const data = await api('/admin/catalog');
  state.catalog = data.catalog || { domains: [], schemas: [] };
  renderCatalog();
  adminLog(data);
}

function openDomainEditor(index) {
  state.currentDomainIndex = Number(index);
  renderCatalog();
  renderDomainEditor(state.currentDomainIndex);
  wireCatalogActions();
  setWorkspaceTab('domain');
}

function openSchemaEditor(index) {
  state.currentSchemaIndex = Number(index);
  renderCatalog();
  renderSchemaEditor(state.currentSchemaIndex);
  wireCatalogActions();
  setWorkspaceTab('schema');
}

function removeDomain(index) {
  state.catalog.domains.splice(index, 1);
  if (state.currentDomainIndex === index) state.currentDomainIndex = null;
  else if (state.currentDomainIndex > index) state.currentDomainIndex -= 1;
  renderCatalog();
  wireCatalogActions();
}

function removeSchema(index) {
  state.catalog.schemas.splice(index, 1);
  if (state.currentSchemaIndex === index) state.currentSchemaIndex = null;
  else if (state.currentSchemaIndex > index) state.currentSchemaIndex -= 1;
  renderCatalog();
  wireCatalogActions();
}

function wireCatalogActions() {
  bindCatalogActions(
    openDomainEditor,
    async index => {
      removeDomain(index);
      await saveCatalog();
    },
    openSchemaEditor,
    async index => {
      removeSchema(index);
      await saveCatalog();
    },
  );
}

async function loadCuratedEntries() {
  const data = await api('/admin/curated-entries');
  state.curatedEntries = data.entries || [];
  renderCuratedEntriesList();
  bindCuratedEntryActions(openCuratedEntry);
}

async function loadIndexedEntries() {
  const data = await api('/admin/entries?limit=200');
  state.indexedEntries = data.entries || [];
  renderIndexedEntriesList();
  bindIndexedEntryActions(openIndexedEntry);
  refs.entriesOutput.textContent = JSON.stringify(data, null, 2);
}

async function bootstrapAdmin() {
  await Promise.all([loadRegistry(), loadCatalog(), loadCuratedEntries(), loadIndexedEntries()]);
  wireCatalogActions();
  try {
    adminLog(await api('/admin/auth/me'));
  } catch {}
}

function hydrateTableProfileFromMapping(source, parsed) {
  state.xlsxTableProfiles = {};
  const mapping = source?.mapping || {};
  const profile = mapping.table_profile || {};
  const sheetName = mapping.sheet_name || parsed?.sheets?.[0]?.sheet_title || '';
  if (!source?.source_id || !sheetName) return;
  state.xlsxTableProfiles[`${source.source_id}::${sheetName}`] = {
    metaRows: Array.isArray(profile.meta_rows) ? profile.meta_rows.map(row => Number(row) - 1).filter(Number.isFinite) : [],
    metaCells: Array.isArray(profile.meta_cells) ? profile.meta_cells.map(cell => ({ row: Number(cell.row) - 1, col: Number(cell.column) - 1 })).filter(cell => Number.isFinite(cell.row) && Number.isFinite(cell.col)) : [],
    dataRows: Array.isArray(profile.data_rows) ? profile.data_rows.map(row => Number(row) - 1).filter(Number.isFinite) : [],
    footerRows: Array.isArray(profile.footer_rows) ? profile.footer_rows.map(row => Number(row) - 1).filter(Number.isFinite) : [],
    ignoredRows: Array.isArray(profile.ignored_rows) ? profile.ignored_rows.map(row => Number(row) - 1).filter(Number.isFinite) : [],
  };
}

async function openSource(sourceId, sheetName = null) {
  const preserveWorkbookState = state.currentSource?.source_id === sourceId && state.currentParsed?.source_type === 'xlsx';
  if (state.currentParsed?.source_type === 'xlsx') captureCurrentXlsxSheetMapping();
  const previousSheetMappings = preserveWorkbookState ? { ...(state.xlsxSheetMappings || {}) } : null;
  const data = await api(`/admin/source-preview/${sourceId}`);
  state.currentSource = data.source;
  state.currentParsed = data.parsed;
  let activeSchemaName = data.source.schema;
  if (data.parsed?.source_type === 'xlsx') {
    const sheetMetas = (data.parsed.sheets || []).map(sheet => ({
      sheet_title: sheet.sheet_title,
      rows: Array.isArray(sheet.rows) ? sheet.rows.length : 0,
      columns: Array.isArray(sheet.rows) ? sheet.rows.reduce((max, row) => Math.max(max, Array.isArray(row) ? row.length : 0), 0) : 0,
    })).filter(sheet => sheet.sheet_title);
    state.xlsxSourceSheets = state.xlsxSourceSheets || {};
    state.xlsxSourceSheets[data.source.source_id] = sheetMetas;
    loadXlsxSheetMappingsFromSource(data.source, data.parsed);
    if (previousSheetMappings) state.xlsxSheetMappings = { ...(state.xlsxSheetMappings || {}), ...previousSheetMappings };
    const sheetExists = sheetName && (data.parsed.sheets || []).some(sheet => sheet.sheet_title === sheetName);
    state.currentSheetName = sheetExists
      ? sheetName
      : (data.source.mapping?.sheet_name || data.parsed?.sheets?.[0]?.sheet_title || '');
    state.currentSheetSourceId = state.currentSheetName ? `${data.source.source_id}__sheet__${state.currentSheetName}` : '';
    state._schemaFieldsControlKey = '';
    state.schemaMappingDraft = { field_map: {} };
    state.schemaFieldDraftValues = {};
    state.currentSchemaFieldIndex = null;
    const sheetEntryType = state.xlsxSheetMappings?.[state.currentSheetName]?.entry_type || '';
    activeSchemaName = sheetEntryType && sheetEntryType !== 'table_facts' ? sheetEntryType : '';
    applyCurrentXlsxSheetMapping();
    ensureCurrentSheetUsesSelectedSchemaTemplate();
  } else {
    state.currentSheetName = '';
    state.currentSheetSourceId = '';
  }
  const sourceSchemaIndex = (state.catalog.schemas || []).findIndex(schema => schema.name === activeSchemaName);
  if (sourceSchemaIndex >= 0) {
    state.currentSchemaIndex = sourceSchemaIndex;
    renderSchemaEditor(sourceSchemaIndex);
    if (data.parsed?.source_type === 'xlsx') applyCurrentXlsxSheetMapping();
  }
  renderRegistry(openSource);
  renderSourceEditor();
  updateDirtyIndicators();
  updateMenuContextLabels();
  setWorkspaceTab('viewer');
  adminLog({ ...data, selected_sheet: state.currentSheetName || null });
}

function firstNonEmpty(...values) {
  return values.map(value => String(value || '').trim()).find(Boolean) || '';
}

function dominantValue(values = []) {
  const counts = new Map();
  values.map(value => String(value || '').trim()).filter(Boolean).forEach(value => {
    counts.set(value, (counts.get(value) || 0) + 1);
  });
  let best = '';
  let bestCount = 0;
  counts.forEach((count, value) => {
    if (count > bestCount) {
      best = value;
      bestCount = count;
    }
  });
  return best;
}

function primaryXlsxSourceConfig(sheetMappings = state.xlsxSheetMappings || {}) {
  const currentSheet = state.currentSheetName ? sheetMappings[state.currentSheetName] : null;
  const mappings = Object.values(sheetMappings || {}).filter(item => item && typeof item === 'object');
  const currentConfig = currentSheet || currentSheetSourceConfig({ readDom: false });
  return {
    class_name: firstNonEmpty(currentConfig.class_name, currentConfig.domain, dominantValue(mappings.map(item => item.class_name || item.domain)), state.currentSource?.class_name),
    schema: firstNonEmpty(currentConfig.schema, currentConfig.entry_type && currentConfig.entry_type !== 'table_facts' ? currentConfig.entry_type : '', dominantValue(mappings.map(item => item.schema || (item.entry_type && item.entry_type !== 'table_facts' ? item.entry_type : ''))), state.currentSource?.schema),
    education_level: firstNonEmpty(currentConfig.education_level, dominantValue(mappings.map(item => item.education_level)), state.currentSource?.education_level),
    language: firstNonEmpty(currentConfig.language, dominantValue(mappings.map(item => item.language)), state.currentSource?.language),
    notes: firstNonEmpty(currentConfig.notes, state.currentSource?.notes),
  };
}

function buildMappingPayload() {
  if (!state.currentSource || !state.currentParsed) return {};
  if (state.currentParsed.source_type === 'xlsx') {
    return buildXlsxSourceMappingPayload();
  }
  return semanticDocumentMappingPayload();
}

async function saveMapping() {
  if (!state.currentSource) return;
  if (state.currentParsed?.source_type === 'xlsx') captureCurrentXlsxSheetMapping();
  const isXlsx = state.currentParsed?.source_type === 'xlsx';
  const mappingPayload = buildMappingPayload();
  const xlsxConfig = isXlsx ? primaryXlsxSourceConfig(mappingPayload.sheet_mappings || state.xlsxSheetMappings || {}) : null;
  const editorConfig = isXlsx ? xlsxConfig : {
    class_name: firstNonEmpty(document.getElementById('editorClass')?.value, mappingPayload.class_name, mappingPayload.domain, state.currentSource?.class_name, state.currentSource?.domain),
    schema: firstNonEmpty(document.getElementById('editorSchema')?.value, mappingPayload.schema, mappingPayload.entry_type, state.currentSource?.schema, state.currentSource?.schema_name),
    education_level: firstNonEmpty(document.getElementById('editorLevel')?.value, mappingPayload.education_level, state.currentSource?.education_level),
    language: firstNonEmpty(document.getElementById('editorLang')?.value, mappingPayload.language, state.currentSource?.language),
    notes: firstNonEmpty(document.getElementById('editorNotes')?.value, mappingPayload.notes, state.currentSource?.notes),
  };
  const payload = {
    source_id: state.currentSource.source_id,
    class_name: editorConfig.class_name || null,
    domain: editorConfig.class_name || null,
    schema: editorConfig.schema || null,
    schema_name: editorConfig.schema || null,
    education_level: editorConfig.education_level || null,
    language: editorConfig.language || null,
    notes: editorConfig.notes || null,
    mapping: mappingPayload,
  };
  const data = await api('/admin/source-mapping', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  state.registry = data.sources || state.registry;
  state.currentSource = state.registry.find(item => item.source_id === payload.source_id) || state.currentSource;
  if (state.currentSource) {
    state.currentSource.class_name = payload.class_name || state.currentSource.class_name || '';
    state.currentSource.domain = payload.domain || state.currentSource.domain || state.currentSource.class_name || '';
    state.currentSource.schema = payload.schema || state.currentSource.schema || '';
    state.currentSource.schema_name = payload.schema_name || state.currentSource.schema_name || state.currentSource.schema || '';
    state.currentSource.education_level = payload.education_level || state.currentSource.education_level || '';
    state.currentSource.language = payload.language || state.currentSource.language || '';
    state.currentSource.notes = payload.notes || state.currentSource.notes || '';
    if (Array.isArray(state.currentSource.items) && state.currentSource.items.length) {
      state.currentSource.items[0] = {
        ...state.currentSource.items[0],
        domain: state.currentSource.domain,
        class_name: state.currentSource.class_name,
        entry_type: state.currentSource.schema || state.currentSource.items[0].entry_type || 'knowledge_entry',
        schema: state.currentSource.schema || state.currentSource.items[0].schema || 'knowledge_entry',
        schema_name: state.currentSource.schema || state.currentSource.items[0].schema_name || 'knowledge_entry',
        education_level: state.currentSource.education_level || null,
        language: state.currentSource.language || null,
        notes: state.currentSource.notes || '',
      };
    }
  }
  clearCurrentSourceDirty();
  renderRegistry(openSource);
  renderSourceEditor();
  adminLog(data);
}

function hasDirtyCurrentSource() {
  return Boolean(state.currentSource?.source_id && isSourceDirty(state.currentSource.source_id));
}

async function saveCurrentDirtySourceBeforeRebuild(label) {
  if (!hasDirtyCurrentSource()) return;
  refs.rebuildStatus.textContent = `Сохраняю текущую привязку перед операцией: ${label}...`;
  refs.jobsStatusMirror.textContent = refs.rebuildStatus.textContent;
  await saveMapping();
}

function countLocalProgramSheetMappings() {
  const mappings = state.currentSource?.mapping?.sheet_mappings || state.currentSource?.mapping?.sheets || state.currentSource?.mapping?.per_sheet || {};
  return Object.values({ ...(mappings || {}), ...(state.xlsxSheetMappings || {}) })
    .filter(item => String(item?.class_name || item?.domain || '').trim() === 'programs')
    .length;
}

async function rebuild(payload, label) {
  await saveCurrentDirtySourceBeforeRebuild(label);
  refs.rebuildStatus.textContent = `${label}...`;
  refs.jobsStatusMirror.textContent = `${label}...`;
  try {
    const data = await api('/rag/rebuild', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const localProgramMappings = countLocalProgramSheetMappings();
    const programWarning = Number(data.programs || 0) === 0 && localProgramMappings > 0 ? ' · программы не распознаны: проверь program_code/schema' : '';
    const summary = `RAG: txt ${data.output_files}, записей ${data.entries_count}, документов ${data.documents_count}, чанков ${data.chunks_count}, программ ${data.programs}${programWarning}`;
    refs.rebuildStatus.textContent = summary;
    refs.jobsStatusMirror.textContent = summary;
    adminLog(data);
  } catch (error) {
    const message = `Ошибка: ${error.message || error}`;
    refs.rebuildStatus.textContent = message;
    refs.jobsStatusMirror.textContent = message;
  }
}

async function previewEntries() {
  const params = new URLSearchParams({ limit: '50' });
  if (document.getElementById('entriesSourceId').value) params.set('source_id', document.getElementById('entriesSourceId').value);
  if (document.getElementById('entriesClass').value) params.set('class_name', document.getElementById('entriesClass').value);
  if (document.getElementById('entriesSchema').value) params.set('schema_name', document.getElementById('entriesSchema').value);
  const data = await api(`/admin/entries?${params.toString()}`);
  state.indexedEntries = data.entries || [];
  renderIndexedEntriesList();
  bindIndexedEntryActions(openIndexedEntry);
  refs.entriesOutput.textContent = JSON.stringify(data, null, 2);
}

async function previewChunks() {
  const params = new URLSearchParams({ limit: '50' });
  if (document.getElementById('chunksSourceId').value) params.set('source_id', document.getElementById('chunksSourceId').value);
  refs.chunksOutput.textContent = JSON.stringify(await api(`/admin/chunks?${params.toString()}`), null, 2);
}

function openCuratedEntry(entryId) {
  const entry = state.curatedEntries.find(item => item.entry_id === entryId);
  if (!entry) return;
  state.currentIndexedEntry = null;
  populateEntryDraft(entry);
  renderCuratedEntriesList();
  bindCuratedEntryActions(openCuratedEntry);
  setWorkspaceTab('entries');
}

function openIndexedEntry(entryId) {
  const entry = state.indexedEntries.find(item => item.entry_id === entryId);
  if (!entry) return;
  state.currentIndexedEntry = entry;
  state.currentCuratedEntry = null;
  refs.selectedEntryOutput.textContent = JSON.stringify(entry, null, 2);
  renderIndexedEntriesList();
  bindIndexedEntryActions(openIndexedEntry);
  setWorkspaceTab('entries');
}

async function saveCuratedEntry() {
  const payload = buildCuratedEntryPayload();
  if (!payload.entry_id || !payload.source_id || !payload.title || !payload.text) {
    adminLog('Для сохранения entry нужны entry_id, source_id, title и text.');
    return;
  }
  const data = await api('/admin/curated-entry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  state.curatedEntries = data.entries || state.curatedEntries;
  populateEntryDraft(data.entry);
  renderCuratedEntriesList();
  bindCuratedEntryActions(openCuratedEntry);
  await loadIndexedEntries();
  adminLog(data);
}

async function deleteSelectedEntry() {
  if (!state.currentCuratedEntry?.entry_id) return;
  const data = await api(`/admin/delete-entry/${state.currentCuratedEntry.entry_id}`, { method: 'POST' });
  state.curatedEntries = data.entries || [];
  state.currentCuratedEntry = null;
  refs.selectedEntryOutput.textContent = '{}';
  clearEntryDraft();
  renderCuratedEntriesList();
  bindCuratedEntryActions(openCuratedEntry);
  await loadIndexedEntries();
  adminLog(data);
}

async function reindexSelectedEntry() {
  const entryId = refs.entryIdInput.value.trim();
  if (!entryId) return;
  const data = await api(`/admin/reindex-entry/${entryId}`, { method: 'POST' });
  refs.rebuildStatus.textContent = `Запись переиндексирована: ${entryId}`;
  refs.jobsStatusMirror.textContent = `Запись переиндексирована: ${entryId}`;
  await loadIndexedEntries();
  adminLog(data);
}

function fillDraftFromSource() {
  const draft = buildDraftFromCurrentSource();
  if (!draft) return;
  populateEntryDraft(draft);
  setWorkspaceTab('entry');
}

function addSchemaField() {
  const existing = collectSchemaFields();
  existing.push({ name: '', label: '', field_type: 'text', required: false, description: '' });
  renderSchemaFieldsEditor(existing);
  wireCatalogActions();
}

function syncOpenCatalogDraftsToState() {
  const schemaName = document.getElementById('schemaName')?.value.trim() || '';
  if (schemaName) {
    const schemaPayload = collectSchemaPayload();
    const schemaIndex = Number.isInteger(state.currentSchemaIndex)
      ? state.currentSchemaIndex
      : (state.catalog.schemas || []).findIndex(schema => schema.name === schemaName);
    if (schemaIndex >= 0) {
      state.catalog.schemas[schemaIndex] = schemaPayload;
      state.currentSchemaIndex = schemaIndex;
    }
  }

  const domainName = document.getElementById('domainName')?.value.trim() || '';
  if (domainName) {
    const domainPayload = collectDomainPayload();
    const domainIndex = Number.isInteger(state.currentDomainIndex)
      ? state.currentDomainIndex
      : (state.catalog.domains || []).findIndex(domain => domain.name === domainName);
    if (domainIndex >= 0) {
      state.catalog.domains[domainIndex] = domainPayload;
      state.currentDomainIndex = domainIndex;
    }
  }
}

async function saveCatalog() {
  syncOpenCatalogDraftsToState();
  const payload = {
    domains: state.catalog.domains,
    schemas: state.catalog.schemas,
  };
  adminLog(await api('/admin/catalog', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
  await loadCatalog();
  wireCatalogActions();
}

function startNewDomain() {
  state.currentDomainIndex = null;
  renderCatalog();
  renderDomainEditor(null);
  wireCatalogActions();
  setWorkspaceTab('domain');
}

async function saveDomainDraft() {
  const payload = collectDomainPayload();
  if (!payload.name) {
    adminLog('Для домена нужно заполнить name.');
    return;
  }
  if (Number.isInteger(state.currentDomainIndex)) {
    state.catalog.domains[state.currentDomainIndex] = payload;
  } else {
    state.catalog.domains.push(payload);
    state.currentDomainIndex = state.catalog.domains.length - 1;
  }
  renderCatalog();
  renderDomainEditor(state.currentDomainIndex);
  wireCatalogActions();
  adminLog({ draft_saved: 'domain', domain: payload });
  await saveCatalog();
}

async function deleteCurrentDomain() {
  if (!Number.isInteger(state.currentDomainIndex)) return;
  removeDomain(state.currentDomainIndex);
  renderDomainEditor(null);
  await saveCatalog();
}

function startNewSchema() {
  state.currentSchemaIndex = null;
  renderCatalog();
  renderSchemaEditor(null);
  wireCatalogActions();
  setWorkspaceTab('schema');
}

async function saveSchemaDraft() {
  const payload = collectSchemaPayload();
  if (!payload.name) {
    adminLog('Для схемы нужно заполнить name.');
    return;
  }
  if (Number.isInteger(state.currentSchemaIndex)) {
    state.catalog.schemas[state.currentSchemaIndex] = payload;
  } else {
    state.catalog.schemas.push(payload);
    state.currentSchemaIndex = state.catalog.schemas.length - 1;
  }
  renderCatalog();
  renderSchemaEditor(state.currentSchemaIndex);
  wireCatalogActions();
  adminLog({ draft_saved: 'schema', schema: payload });
  await saveCatalog();
}

async function deleteCurrentSchema() {
  if (!Number.isInteger(state.currentSchemaIndex)) return;
  removeSchema(state.currentSchemaIndex);
  renderSchemaEditor(null);
  await saveCatalog();
}

function bindDragAndDrop() {
  document.addEventListener('dragstart', event => {
    const sourceItem = event.target.closest('[data-source-id]');
    const domainItem = event.target.closest('[data-domain-index]');
    const schemaItem = event.target.closest('[data-schema-index]');
    if (!event.dataTransfer) return;
    if (sourceItem) {
      const dragSourceId = sourceItem.dataset.logicalSourceId || sourceItem.dataset.sourceId;
      event.dataTransfer.setData('application/x-source-id', dragSourceId);
      event.dataTransfer.setData('application/x-source-base-id', sourceItem.dataset.sourceId);
      if (sourceItem.dataset.sheetName) event.dataTransfer.setData('application/x-sheet-name', sourceItem.dataset.sheetName);
      event.dataTransfer.setData('text/plain', dragSourceId);
      event.dataTransfer.effectAllowed = 'copy';
    } else if (domainItem) {
      event.dataTransfer.setData('application/x-domain-index', domainItem.dataset.domainIndex);
      event.dataTransfer.effectAllowed = 'copy';
    } else if (schemaItem) {
      event.dataTransfer.setData('application/x-schema-index', schemaItem.dataset.schemaIndex);
      event.dataTransfer.effectAllowed = 'copy';
    }
  });

  const bindDropzone = (selector, readType, onDrop) => {
    document.querySelectorAll(selector).forEach(zone => {
      zone.addEventListener('dragover', event => {
        if (!Array.from(event.dataTransfer?.types || []).includes(readType)) return;
        event.preventDefault();
        zone.classList.add('is-drag-over');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-drag-over'));
      zone.addEventListener('drop', event => {
        const value = event.dataTransfer?.getData(readType);
        if (!value) return;
        event.preventDefault();
        zone.classList.remove('is-drag-over');
        onDrop(value);
      });
    });
  };

  bindDropzone('[data-domain-dropzone]', 'application/x-domain-index', value => openDomainEditor(Number(value)));
  bindDropzone('[data-schema-dropzone]', 'application/x-schema-index', value => openSchemaEditor(Number(value)));
  bindDropzone('[data-source-dropzone]', 'application/x-source-id', sourceId => {
    const baseSourceId = String(sourceId || '').split('__sheet__')[0];
    const source = state.registry.find(item => item.source_id === baseSourceId);
    refs.entrySourceIdInput.value = sourceId;
    if (source) {
      const config = state.currentSource?.source_id === baseSourceId && state.currentParsed?.source_type === 'xlsx'
        ? currentSheetSourceConfig()
        : source;
      refs.entryClassSelect.value = config.class_name || refs.entryClassSelect.value || '';
      refs.entrySchemaSelect.value = config.schema || refs.entrySchemaSelect.value || '';
      refs.entryLevelSelect.value = config.education_level || refs.entryLevelSelect.value || '';
      refs.entryLangSelect.value = config.language || refs.entryLangSelect.value || '';
    }
    setWorkspaceTab('entry');
  });
}


function currentDocumentLabels() {
  const sourceType = String(state.currentParsed?.source_type || '').toLowerCase();
  if (sourceType === 'xlsx') {
    return {
      saveCurrent: state.currentSheetName ? 'Сохранить лист' : 'Сохранить текущий лист',
      saveSource: 'Сохранить всю книгу',
      rebuildCurrent: state.currentSheetName ? 'Пересобрать лист' : 'Пересобрать текущий лист',
    };
  }
  if (['docx', 'txt', 'text', 'md'].includes(sourceType)) {
    return {
      saveCurrent: 'Сохранить документ',
      saveSource: 'Сохранить документ целиком',
      rebuildCurrent: 'Пересобрать документ',
    };
  }
  return {
    saveCurrent: 'Сохранить текущий объект',
    saveSource: 'Сохранить весь источник',
    rebuildCurrent: 'Пересобрать текущий объект',
  };
}

function updateMenuContextLabels() {
  const labels = currentDocumentLabels();
  const saveCurrent = document.getElementById('menuSaveCurrentLabel');
  const saveSource = document.getElementById('menuSaveSourceLabel');
  const rebuildCurrent = document.getElementById('menuRebuildCurrentLabel');
  if (saveCurrent) saveCurrent.textContent = labels.saveCurrent;
  if (saveSource) saveSource.textContent = labels.saveSource;
  if (rebuildCurrent) rebuildCurrent.textContent = labels.rebuildCurrent;
}

function closeAdminMenus() {
  document.querySelectorAll('.admin-menu-group.is-open').forEach(group => group.classList.remove('is-open'));
}

function clickFirst(selector) {
  const el = document.querySelector(selector);
  if (!el) return false;
  el.click();
  return true;
}

function focusBlockTextEditor() {
  setWorkspaceTab('structure');
  const textarea = document.getElementById('docBlockTextEditor');
  if (!textarea) return false;
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  return true;
}

function markSelectedTextBlocks(role) {
  setWorkspaceTab('structure');
  return clickFirst(`[data-doc-mark-selected="${role}"]`) || clickFirst(`[data-doc-popup-role="${role}"]`);
}

function clearCurrentSelection() {
  closeAdminMenus();
  const doc = document.querySelector('[data-doc-clear-selection]');
  if (doc) {
    doc.click();
    return;
  }
  const table = document.getElementById('clearTableSelectionBtn');
  if (table) table.click();
}

async function runMenuAction(action) {
  updateMenuContextLabels();
  closeAdminMenus();
  if (!action) return;
  if (action === 'save-current' || action === 'save-source') return saveMapping();
  if (action === 'rebuild-current') return document.getElementById('rebuildSourceBtn')?.click();
  if (action === 'rebuild-all') return rebuild({ rebuild_data: true }, 'Полная пересборка');
  if (action === 'upload-source') return document.getElementById('openUploadModalBtn')?.click();
  if (action === 'download-source') return downloadCurrentSource();
  if (action === 'reload-registry') return loadRegistry();

  if (action === 'edit-block') return focusBlockTextEditor();
  if (action === 'split-cursor') return clickFirst('[data-doc-split-cursor]') || clickFirst('[data-doc-popup-split="cursor"]');
  if (action === 'split-auto') return clickFirst('[data-doc-split-auto]') || clickFirst('[data-doc-popup-split="auto"]');
  if (action === 'merge-selected') return clickFirst('[data-doc-merge-selected]');
  if (action === 'create-entry') return clickFirst('[data-doc-create-entry]') || clickFirst('[data-doc-popup-entry]');
  if (action === 'clear-selection') return clearCurrentSelection();

  if (action === 'mark-body') return markSelectedTextBlocks('body');
  if (action === 'mark-meta') return markSelectedTextBlocks('meta');
  if (action === 'mark-ignore') return markSelectedTextBlocks('ignore');
  if (action === 'mark-heading') return markSelectedTextBlocks('heading_1');
  if (action === 'mark-draft') return markSelectedTextBlocks('draft');

  if (action === 'tab-viewer') return setWorkspaceTab('viewer');
  if (action === 'tab-structure') return setWorkspaceTab('structure');
  if (action === 'tab-entries') return setWorkspaceTab('entries');
  if (action === 'tab-chunks') return setWorkspaceTab('chunks');
  if (action === 'tab-index') return setWorkspaceTab('index');
  if (action === 'tab-advanced') return setWorkspaceTab('advanced');
  if (action === 'tab-training') return setWorkspaceTab('training');
  if (action === 'toggle-bottom') return document.getElementById('bottomPaneToggle')?.click();

  if (action === 'analyze-source') return document.getElementById('analyzeSourceBtn')?.click();
  if (action === 'normalize') return rebuild({ normalize: true }, 'Нормализация');
  if (action === 'build-documents') return rebuild({ documents: true }, 'Сборка документов');
  if (action === 'build-chunks') return rebuild({ chunks: true }, 'Сборка чанков');
  if (action === 'index') return rebuild({ index: true }, 'Индексация');

  if (action === 'show-shortcuts') {
    adminLog(`Горячие клавиши:
Ctrl+S — сохранить документ/лист
Ctrl+Shift+S — сохранить весь источник
Ctrl+R — пересобрать текущий документ/лист
Ctrl+Shift+R — пересобрать всё
D/M/I/H/P — статус выбранного текстового блока
E — редактировать блок
S — разрезать по курсору
G — создать запись
Ctrl+J — объединить выбранные блоки
Esc — снять выбор`);
    return setBottomTab('logs');
  }
  if (action === 'show-markup-help') {
    adminLog('Разметка: статус задает роль блока при сборке RAG. Домены, типы знания, системные поля и уровни наследуются в metadata записи и используются для фильтрации, поиска и сборки embedding_text.');
    return setBottomTab('logs');
  }
}

function isTypingTarget(target) {
  return Boolean(target?.closest?.('input, textarea, select, [contenteditable="true"]'));
}

function bindAdminMenuBar() {
  const bar = document.getElementById('adminMenuBar');
  if (!bar) return;
  bar.addEventListener('admin-menu-action', event => runMenuAction(event.detail?.action));
  bar.addEventListener('admin-menu-labels-request', updateMenuContextLabels);
  updateMenuContextLabels();
}

function syncCurrentInspectorDraft() {
  if (state.currentParsed?.source_type !== 'xlsx') {
    if (state.currentSource) {
      const domain = document.getElementById('editorClass')?.value || '';
      const schema = document.getElementById('editorSchema')?.value || '';
      const level = document.getElementById('editorLevel')?.value || '';
      const lang = document.getElementById('editorLang')?.value || '';
      const notes = document.getElementById('editorNotes')?.value || '';
      state.currentSource.class_name = domain;
      state.currentSource.schema = schema;
      state.currentSource.schema_name = schema;
      state.currentSource.education_level = level;
      state.currentSource.language = lang;
      state.currentSource.notes = notes;
      if (Array.isArray(state.currentSource.items) && state.currentSource.items.length) {
        state.currentSource.items[0] = {
          ...state.currentSource.items[0],
          domain,
          entry_type: schema || state.currentSource.items[0].entry_type || 'knowledge_entry',
          schema: schema || state.currentSource.items[0].schema || 'knowledge_entry',
          education_level: level || null,
          language: lang || null,
          notes,
        };
      }
    }
    markCurrentSourceDirty();
    renderExtractionWorkspace();
    renderRegistry(openSource);
    return;
  }
  markCurrentSourceDirty();
  captureCurrentSheetInspectorState();
  captureCurrentXlsxSheetMapping();
  const sourceConfig = currentSheetSourceConfig();
  refs.entryClassSelect.value = sourceConfig.class_name || '';
  refs.entrySchemaSelect.value = sourceConfig.schema || '';
  refs.entryLevelSelect.value = sourceConfig.education_level || '';
  refs.entryLangSelect.value = sourceConfig.language || '';
  renderExtractionWorkspace();
  renderRegistry(openSource);
}

function handleSheetControlChange(id, value) {
  if (id === 'sheetSelect') {
    const previousSheet = state.currentSheetName;
    const nextSheet = value;
    captureCurrentXlsxSheetMapping(previousSheet);
    state.currentSheetName = nextSheet;
    state._schemaFieldsControlKey = '';
    state.schemaMappingDraft = { field_map: {} };
    state.schemaFieldDraftValues = {};
    state.currentSchemaFieldIndex = null;
    applyCurrentXlsxSheetMapping();
    renderSheetControls();
    renderSourceEditor();
    return;
  }
  if (id === 'headerRowSelect' || id === 'dataStartRowSelect') {
    markCurrentSourceDirty();
    renderSheetControls();
    renderSourceEditor();
    return;
  }
  if (id === 'textColumnsSelect') {
    markCurrentSourceDirty();
    renderFieldLabelInputs();
    renderEntryTablePreview();
    return;
  }
  if (id === 'tableSelectionMode') setExcelSelectionMode(value);
}

function handleXlsxAction(action) {
  if (action === 'clear-selection') return clearExcelSelection();
  if (action === 'open-entry') return setWorkspaceTab('entry');
  if (action === 'set-header') return void (setHeaderFromSelection(), markCurrentSourceDirty());
  if (action === 'mark-metadata') return void (markMetadataFromSelection(), markCurrentSourceDirty());
  if (action === 'set-data-below-header') return void (setDataBelowHeader(), markCurrentSourceDirty());
  if (action === 'set-data') return void (setDataFromSelection(), markCurrentSourceDirty());
  if (action === 'clear-selected-marks') return void (clearTableMarksFromSelection(), markCurrentSourceDirty());
  if (action === 'clear-all-marks') return void (clearTableMarks(), markCurrentSourceDirty());
  if (action === 'mark-footer') return void (markFooterFromSelection(), markCurrentSourceDirty());
  if (action === 'mark-ignore') return void (markIgnoreFromSelection(), markCurrentSourceDirty());
  if (action === 'schema-field-from-column') return void (createSchemaFieldFromSelectedColumn(), markCurrentSourceDirty());
  if (action === 'schema-field-from-cell') return void (createSchemaMetadataFieldFromSelectedCell(), markCurrentSourceDirty());
}

async function downloadCurrentSource() {
  if (!state.currentSource) {
    adminLog({ ok: false, error: 'Источник не выбран' });
    return;
  }
  const result = await downloadSourceFile(state.currentSource.source_id);
  adminLog({ ok: true, message: `Файл скачан: ${result.filename}`, bytes: result.bytes });
}

async function handleSourceAction(action) {
  if (action === 'save-mapping') return saveMapping();
  if (action === 'download-source') return downloadCurrentSource();
  if (action === 'rebuild-source') {
    if (!state.currentSource) return;
    adminLog(await api(`/admin/rebuild-source/${state.currentSource.source_id}`, { method: 'POST' }));
    return;
  }
  if (action === 'delete-source') {
    if (!state.currentSource) return;
    adminLog(await api(`/admin/delete-source/${state.currentSource.source_id}`, { method: 'POST' }));
    state.currentSource = null;
    state.currentParsed = null;
    renderSourceEditor();
    await loadRegistry();
  }
}

async function handleEntryAction(action) {
  if (action === 'save') return saveCuratedEntry();
  if (action === 'reindex') return reindexSelectedEntry();
  if (action === 'load-curated') return loadCuratedEntries();
  if (action === 'clear-draft') return clearEntryDraft();
  if (action === 'build-from-source') return fillDraftFromSource();
  if (action === 'preview-indexed') return previewEntries();
  if (action === 'preview-chunks') return previewChunks();
  if (action === 'edit-selected') {
    const entry = state.currentCuratedEntry || state.currentIndexedEntry;
    if (!entry) return;
    populateEntryDraft(entry);
    if (state.currentIndexedEntry && !state.curatedEntries.some(item => item.entry_id === entry.entry_id)) {
      state.currentCuratedEntry = null;
    }
    return setWorkspaceTab('entry');
  }
  if (action === 'delete-selected') return deleteSelectedEntry();
  if (action === 'reindex-selected') {
    const entryId = state.currentCuratedEntry?.entry_id || state.currentIndexedEntry?.entry_id || refs.entryIdInput.value.trim();
    if (!entryId) return;
    const data = await api(`/admin/reindex-entry/${entryId}`, { method: 'POST' });
    refs.rebuildStatus.textContent = `Запись переиндексирована: ${entryId}`;
    refs.jobsStatusMirror.textContent = `Запись переиндексирована: ${entryId}`;
    await loadIndexedEntries();
    adminLog(data);
  }
}

async function handleCatalogAction(action) {
  if (action === 'domain-new') return startNewDomain();
  if (action === 'domain-save') return saveDomainDraft();
  if (action === 'domain-delete') return deleteCurrentDomain();
  if (action === 'schema-new') return startNewSchema();
  if (action === 'schema-save') return saveSchemaDraft();
  if (action === 'schema-delete') return deleteCurrentSchema();
  if (action === 'schema-field-add') return addSchemaField();
  if (action === 'schema-preview') return renderSchemaPreview();
  if (action === 'schema-test') {
    renderSchemaPreview();
    return setSchemaDesignerTab('preview');
  }
  if (action === 'catalog-save') return saveCatalog();
}

function handleSchemaTypeChange(value) {
  const handler = document.getElementById('schemaHandler');
  if (!handler) return;
  const current = handler.value;
  if (value === 'sectioned_text') handler.value = 'sectioned_text';
  else if (value === 'table_row' && !['program_entry', 'program_tuition_entry', 'dormitory_tuition_entry', 'timeline_entry'].includes(current)) handler.value = 'program_entry';
  else if (value === 'generic_text' && !current.includes('text')) handler.value = 'generic_text';
  renderSchemaFieldsEditor(collectSchemaFields());
  renderSchemaPreview();
}

function handleSchemaDraftChange() {
  renderSchemaFieldsEditor(collectSchemaFields());
  renderSchemaPreview();
}

async function handlePipelineAction(action) {
  if (action === 'save-map') return saveMapping();
  if (action === 'rebuild-source') return document.getElementById('rebuildSourceBtn')?.click();
  if (action === 'build-chunks' || action === 'chunks') return rebuild({ chunks: true }, 'Сборка чанков');
  if (action === 'index') return rebuild({ index: true }, 'Индексация');
  if (action === 'rebuild-all') return rebuild({ rebuild_data: true }, 'Полная пересборка');
  if (action === 'normalize') return rebuild({ normalize: true }, 'Нормализация');
  if (action === 'documents') return rebuild({ documents: true }, 'Сборка документов');
  if (action === 'analyze-source') {
    renderExtractionWorkspace();
    return setWorkspaceTab('structure');
  }
  if (action === 'save-structure') return saveMapping();
  if (action === 'refresh-chunks-preview') {
    renderExtractionWorkspace();
    return setWorkspaceTab('chunks');
  }
  if (action === 'open-domain') return setWorkspaceTab('domain');
  if (action === 'open-schema') return setWorkspaceTab('schema');
  if (action === 'open-entry') return setWorkspaceTab('entry');
  if (action === 'open-entries') return setWorkspaceTab('entries');
}

async function runDebugSearch() {
  refs.debugOutput.textContent = JSON.stringify(await api('/admin/search-debug', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: document.getElementById('debugQuery').value,
      domains: selectedValues(document.getElementById('debugDomains')),
      schemas: selectedValues(document.getElementById('debugSchemas')),
      education_level: document.getElementById('debugLevel').value || null,
      language: document.getElementById('debugLang').value || null
    })
  }), null, 2);
  setBottomTab('debug');
}

function bindEvents() {
  window.addEventListener('ai-talapker-text-editor-change', () => {
    renderDocPreview();
    renderExtractionWorkspace();
  });

  document.addEventListener('admin-explorer-tab', event => setExplorerTab(event.detail?.tab));
  document.addEventListener('admin-registry-reload', () => loadRegistry().catch(error => adminLog(error.message || String(error))));
  document.addEventListener('admin-source-search', () => renderRegistry(openSource));
  document.addEventListener('admin-bottom-tab', event => setBottomTab(event.detail?.tab));
  document.addEventListener('admin-debug-search', () => runDebugSearch().catch(error => adminLog(error.message || String(error))));
  document.addEventListener('admin-viewer-tab-change', () => {});

  document.addEventListener('admin-entry-layer-tab', event => setEntryLayerTab(event.detail?.tab));
  document.addEventListener('admin-entry-action', event => handleEntryAction(event.detail?.action).catch(error => adminLog(error.message || String(error))));
  document.addEventListener('admin-entry-schema-change', () => renderExtractionWorkspace());
  document.addEventListener('admin-doc-edited-change', () => { renderDocPreview(); markCurrentSourceDirty(); });
  document.addEventListener('admin-paragraph-selection-change', event => {
    const value = Number(event.detail?.value);
    const selected = new Set(state.docSelectedParagraphs || []);
    if (event.detail?.checked) selected.add(value);
    else selected.delete(value);
    state.docSelectedParagraphs = Array.from(selected).sort((a, b) => a - b);
    markCurrentSourceDirty();
    renderDocPreview();
  });

  document.addEventListener('admin-source-field-change', syncCurrentInspectorDraft);
  document.addEventListener('admin-sheet-control-change', event => handleSheetControlChange(event.detail?.id, event.detail?.value));
  document.addEventListener('admin-xlsx-action', event => handleXlsxAction(event.detail?.action));
  document.addEventListener('admin-table-draft-action', event => {
    const action = event.detail?.action;
    if (action === 'build-from-source') return fillDraftFromSource();
    return fillSelectedTableTextToDraft(action);
  });
  document.addEventListener('admin-source-action', event => handleSourceAction(event.detail?.action).catch(error => adminLog(error.message || String(error))));

  document.addEventListener('admin-catalog-action', event => handleCatalogAction(event.detail?.action).catch(error => adminLog(error.message || String(error))));
  document.addEventListener('admin-domain-select', event => {
    const value = event.detail?.value;
    if (value === '') startNewDomain();
    else openDomainEditor(Number(value));
  });
  document.addEventListener('admin-schema-select', event => {
    const value = event.detail?.value;
    if (value === '') startNewSchema();
    else openSchemaEditor(Number(value));
  });
  document.addEventListener('admin-schema-designer-tab', event => setSchemaDesignerTab(event.detail?.tab));
  document.addEventListener('admin-schema-type-change', event => handleSchemaTypeChange(event.detail?.value));
  document.addEventListener('admin-schema-draft-change', handleSchemaDraftChange);
  document.addEventListener('admin-schema-inspector-refresh', renderSchemaInspector);
  document.addEventListener('admin-schema-field-change', () => { applySchemaFieldInspector(); markCurrentSourceDirty(); });

  document.addEventListener('admin-pipeline-action', event => handlePipelineAction(event.detail?.action).catch(error => adminLog(error.message || String(error))));

  document.querySelectorAll('[data-workspace-tab]').forEach(tab => {
    tab.onclick = () => setWorkspaceTab(tab.dataset.workspaceTab);
  });
  document.querySelectorAll('.admin-rail [data-explorer-tab]').forEach(tab => {
    tab.onclick = () => setExplorerTab(tab.dataset.explorerTab);
  });

  bindDragAndDrop();

  refs.loginBtn.onclick = login;
  refs.logoutBtn.onclick = () => {
    sessionStorage.removeItem('admin_jwt');
    state.token = '';
    setLoggedIn(false);
    window.dispatchEvent(new CustomEvent('admin-auth-changed'));
    refs.loginStatus.textContent = '';
  };

  document.getElementById('toolbarReloadBtn').onclick = () => bootstrapAdmin().catch(error => adminLog(error.message || String(error)));

  const uploadModal = document.getElementById('uploadModal');

  const fallbackUploadStatus = (message, kind = 'idle') => {
    const target = document.getElementById('uploadStatus');
    if (!target) return;
    target.textContent = message || '';
    target.dataset.kind = kind;
    target.classList.toggle('hidden', !message);
  };

  const setUploadStatus = (message, kind = 'idle') => {
    uploadModal?.setStatus?.(message, kind);
    fallbackUploadStatus(message, kind);
    if (message) adminLog(`[upload] ${message}`);
  };

  const setUploadProgress = percent => {
    const safe = Math.max(0, Math.min(100, Number(percent) || 0));
    uploadModal?.setProgress?.(safe);
    const bar = document.getElementById('uploadProgressBar');
    const wrap = document.getElementById('uploadProgressWrap');
    if (bar) bar.style.width = `${safe}%`;
    if (wrap) wrap.setAttribute('aria-valuenow', String(Math.round(safe)));
  };

  const setUploadBusy = isBusy => {
    uploadModal?.setBusy?.(isBusy);
    ['uploadBtn', 'cancelUploadModalBtn', 'closeUploadModalBtn', 'uploadFile', 'uploadClass', 'uploadSchema', 'uploadLevel', 'uploadLang']
      .forEach(id => document.getElementById(id)?.toggleAttribute('disabled', isBusy));
  };

  const openUploadModal = () => {
    if (uploadModal?.open) uploadModal.open();
    else {
      uploadModal?.classList.remove('hidden');
      document.body.classList.add('modal-open');
    }
    setUploadProgress(0);
    setUploadStatus('Выберите файл и нажмите «Загрузить». PDF/JPG/PNG будут обработаны через OCR.', 'progress');
  };

  const closeUploadModal = () => {
    if (uploadModal?.close) uploadModal.close();
    else {
      uploadModal?.classList.add('hidden');
      document.body.classList.remove('modal-open');
    }
  };

  const refreshUploadFileName = () => {
    if (uploadModal?.refreshFileName) uploadModal.refreshFileName();
    const file = document.getElementById('uploadFile')?.files?.[0];
    const name = document.getElementById('uploadFileName');
    if (name) {
      name.textContent = file
        ? `${file.name}${file.size ? ` · ${(file.size / 1024 / 1024).toFixed(2)} MB` : ''}`
        : 'Файл не выбран';
    }
    setUploadProgress(0);
    setUploadStatus(file ? `Файл выбран: ${file.name}` : 'Файл не выбран.', file ? 'progress' : 'idle');
  };

  const buildUploadForm = () => {
    if (uploadModal?.formData) return uploadModal.formData();
    const file = document.getElementById('uploadFile')?.files?.[0];
    if (!file) return null;
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('class_name', document.getElementById('uploadClass')?.value || 'general');
    form.append('schema_name', document.getElementById('uploadSchema')?.value || 'generic_text');
    form.append('education_level', document.getElementById('uploadLevel')?.value || '');
    form.append('language', document.getElementById('uploadLang')?.value || '');
    return form;
  };

  let uploadInFlight = false;

  const submitUpload = async () => {
    if (uploadInFlight) return;
    const form = buildUploadForm();
    if (!form) {
      setUploadStatus('Выберите файл перед загрузкой.', 'error');
      return;
    }

    uploadInFlight = true;
    setUploadBusy(true);
    setUploadProgress(1);
    setUploadStatus('Запрос создан. Отправляю файл на сервер...', 'progress');

    let serverProcessing = false;
    let elapsedSeconds = 0;
    const timer = window.setInterval(() => {
      elapsedSeconds += 1;
      if (serverProcessing) {
        setUploadStatus(`Файл уже на сервере. Идет обработка/OCR... ${elapsedSeconds} сек.`, 'progress');
      }
    }, 1000);

    try {
      const result = await uploadForm('/admin/upload', form, {
        onProgress: percent => {
          setUploadProgress(percent);
          if (percent >= 100) {
            serverProcessing = true;
            elapsedSeconds = 0;
            setUploadStatus('Файл отправлен. Сервер обрабатывает источник/OCR...', 'progress');
          } else {
            setUploadStatus(`Отправка файла: ${percent}%`, 'progress');
          }
        },
      });

      window.clearInterval(timer);
      setUploadProgress(100);
      setUploadStatus('Сервер ответил. Обновляю реестр источников...', 'progress');
      adminLog(result);
      await loadRegistry();
      setExplorerTab('sources');

      const generated = Array.isArray(result?.generated_files) ? result.generated_files.length : 0;
      const summary = result?.ocr
        ? `OCR завершен. Создано файлов: ${generated}. Страниц: ${result.pages ?? 0}. Табличных строк: ${result.table_rows ?? 0}.`
        : `Источник загружен: ${result?.source_id || 'без id'}.`;
      setUploadStatus(summary, 'ok');
    } catch (error) {
      window.clearInterval(timer);
      const message = error?.message || String(error);
      setUploadStatus(`Ошибка загрузки: ${message}`, 'error');
      adminLog({ ok: false, error: message, status: error?.status, payload: error?.payload });
    } finally {
      uploadInFlight = false;
      setUploadBusy(false);
    }
  };

  document.addEventListener('admin-upload-open', openUploadModal);
  document.addEventListener('admin-upload-submit', event => {
    event.preventDefault();
    submitUpload().catch(error => {
      const message = error?.message || String(error);
      setUploadStatus(`Ошибка загрузки: ${message}`, 'error');
      adminLog({ ok: false, error: message });
    });
  });
  document.getElementById('uploadFile')?.addEventListener('change', refreshUploadFileName);
  document.getElementById('openUploadModalBtn')?.addEventListener('click', openUploadModal);
  document.getElementById('downloadSelectedSourceBtn')?.addEventListener('click', () => downloadCurrentSource().catch(error => adminLog(error.message || String(error))));
  document.getElementById('uploadBtn')?.addEventListener('click', event => {
    event.preventDefault();
    submitUpload().catch(error => {
      const message = error?.message || String(error);
      setUploadStatus(`Ошибка загрузки: ${message}`, 'error');
      adminLog({ ok: false, error: message });
    });
  });
  document.getElementById('cancelUploadModalBtn')?.addEventListener('click', () => { if (!uploadInFlight) closeUploadModal(); });
  document.getElementById('closeUploadModalBtn')?.addEventListener('click', () => { if (!uploadInFlight) closeUploadModal(); });
}
syncGlobalDropdowns();
renderSchemaFieldsEditor([]);
setWorkspaceTab(state.workspaceTab);
setExplorerTab(state.explorerTab);
setBottomTab(state.bottomTab);
setEntryLayerTab('curated');
initializeDocking();
bindAdminMenuBar();
bindEvents();
updateMenuContextLabels();

if (state.token) {
  setLoggedIn(true);
  window.dispatchEvent(new CustomEvent('admin-auth-changed'));
  bootstrapAdmin().catch(error => {
    adminLog(error.message || String(error));
    if (error.status === 401) {
      sessionStorage.removeItem('admin_jwt');
      state.token = '';
      setLoggedIn(false);
      window.dispatchEvent(new CustomEvent('admin-auth-changed'));
    } else if (refs.rebuildStatus) {
      refs.rebuildStatus.textContent = `Ошибка загрузки админ-данных: ${error.message || error}`;
    }
  });
} else {
  setLoggedIn(false);
}

import { refs, selectedValues, state } from '../core/state.js';
import { currentColumns, escapeAttr, escapeHtml, mappingKindLabel, markCurrentSourceDirty, roleLabel, updateInspectorContext } from './common.js';
import { currentLogicalSourceId, currentSourceDisplayName } from './registry.js';
import {
  collectSchemaFieldDraftValues,
  columnHeaderLabel,
  currentDataStartRowIndex,
  currentOutputFields,
  currentSheetSourceConfig,
  dataRowsFromProfile,
  currentSchemaMappingPayload,
  currentXlsxSheetMapping,
  ensureTableProfile,
  getCurrentSheet,
  maxColumnCount,
  metadataRecordsFromProfile,
  renderSourceConfigControls,
  renderSchemaPreview,
  renderSchemaTablePreview,
  selectedOrProfileTableText,
  selectedRowsForDraft,
  selectionMetadata,
  semanticFieldNameForColumn,
  sheetRows,
  stableMetadataFieldName,
  tableProfilePayload,
  tableProfileSummaryText,
  valuesFromSchemaMapping,
} from '../features/mapping/logic.js';
import { renderEntryTablePreview, renderExcelPreview, renderSheetControls, renderSpreadsheetTable } from '../features/mapping/table.js';
import { buildSemanticTextEntriesPreview, semanticDocumentBlocks, initializeSemanticDocumentState, renderSemanticDocPreview, renderSemanticTextStructurePreview, semanticDraftFromCurrentSource } from './text_editor.js';

export function renderDocPreview() {
  renderSemanticDocPreview();
}


function currentSourceDomain() {
  const config = currentSheetSourceConfig();
  return config.class_name || state.currentSource?.domain || 'unassigned';
}

function currentEntryTypeFallback() {
  if (state.currentParsed?.source_type === 'xlsx') {
    return currentXlsxSheetMapping().entry_type || 'table_facts';
  }
  return state.currentParsed?.source_type === 'docx' ? 'sectioned_text' : 'generic_text';
}

function sourceProfileSummaryPayload() {
  if (state.currentParsed?.source_type === 'xlsx') return tableProfilePayload();
  return { blocks: documentStructureBlocks() };
}

function metadataObjectFromProfile() {
  const config = currentSheetSourceConfig();
  const metadata = {
    source_id: currentLogicalSourceId() || null,
    source_base_id: state.currentSource?.source_id || null,
    domain: currentSourceDomain(),
    entry_type: currentEntryTypeFallback(),
    education_level: config.education_level || null,
    language: config.language || null,
  };
  metadataRecordsFromProfile().forEach(record => {
    const key = stableMetadataFieldName({ row: record.row - 1, col: record.column - 1, value: record.value }, currentOutputFields());
    metadata[key] = record.value;
  });
  return metadata;
}

function tableEntryForRow(rowIndex, sequenceIndex = 0) {
  const sheet = getCurrentSheet();
  const row = sheet?.rows?.[rowIndex] || [];
  const mapped = valuesFromSchemaMapping(rowIndex);
  const fields = Object.keys(mapped).length ? mapped : {};
  if (!Object.keys(fields).length) {
    const maxCols = maxColumnCount(sheet?.rows || []);
    for (let col = 0; col < maxCols; col += 1) {
      const value = String(row[col] ?? '').trim();
      if (!value) continue;
      fields[semanticFieldNameForColumn(columnHeaderLabel(col), col)] = value;
    }
  }
  const title = fields.program_group || fields.program_name || fields.name || `${sheet?.sheet_title || 'таблица'} строка ${rowIndex + 1}`;
  const text = Object.entries(fields).map(([key, value]) => `${key}: ${value}`).join('; ');
  return {
    entry_id: `${currentLogicalSourceId() || 'источник'}_row_${rowIndex + 1}`,
    source_id: currentLogicalSourceId() || '',
    domain: currentSourceDomain(),
    entry_type: currentEntryTypeFallback(),
    title,
    text,
    fields,
    metadata: {
      ...metadataObjectFromProfile(),
      sheet: sheet?.sheet_title || null,
      row: rowIndex + 1,
      source_profile: tableProfilePayload(),
    },
    sequence: sequenceIndex + 1,
  };
}

function buildTableEntriesPreview(limit = 25) {
  if (state.currentParsed?.source_type !== 'xlsx') return [];
  const profile = ensureTableProfile();
  const rows = dataRowsFromProfile()
    .filter(row => row >= currentDataStartRowIndex())
    .filter(row => !profile.ignoredRows.includes(row) && !profile.footerRows.includes(row));
  return rows.slice(0, limit).map((rowIndex, index) => tableEntryForRow(rowIndex, index));
}

function documentStructureBlocks() {
  return semanticDocumentBlocks();
}

function buildTextEntriesPreview() {
  return buildSemanticTextEntriesPreview();
}

function buildEntriesPreview() {
  if (!state.currentSource || !state.currentParsed) return [];
  if (state.currentParsed.source_type === 'xlsx') return buildTableEntriesPreview();
  return buildTextEntriesPreview();
}

function chunkTextForEntry(entry) {
  const head = [entry.title, entry.section_path?.length ? entry.section_path.join(' / ') : ''].filter(Boolean).join(' — ');
  const body = entry.text || Object.entries(entry.fields || {}).map(([key, value]) => `${key}: ${value}`).join('; ');
  return [head, body].filter(Boolean).join('\n');
}

function buildChunksPreview() {
  return buildEntriesPreview().map((entry, index) => ({
    chunk_id: `${entry.entry_id}_chunk_1`,
    entry_id: entry.entry_id,
    source_id: entry.source_id,
    domain: entry.domain,
    entry_type: entry.entry_type,
    text: chunkTextForEntry(entry),
    payload: {
      domain: entry.domain,
      entry_type: entry.entry_type,
      source_id: entry.source_id,
      education_level: entry.metadata?.education_level || null,
      language: entry.metadata?.language || null,
      program_code: entry.fields?.program_code || null,
      program_group: entry.fields?.program_group || null,
      row: entry.metadata?.row || null,
      section_path: entry.section_path || [],
      logical_group_id: entry.metadata?.logical_group_id || entry.entry_id,
      expansion_policy: entry.metadata?.expansion_policy || null,
      list_items: entry.metadata?.list_items || [],
      list_count: entry.metadata?.list_count || 0,
    },
    order: index + 1,
  }));
}

function renderTextStructurePreview() {
  renderSemanticTextStructurePreview();
}

function renderTableStructurePreview() {
  const target = refs.sourceStructurePreview;
  if (!target) return;
  const rows = sheetRows();
  if (!rows.length) {
    target.innerHTML = '<div class="status">Нет строк таблицы.</div>';
    return;
  }
  const profile = ensureTableProfile();
  const meta = metadataRecordsFromProfile();
  const mapped = currentSchemaMappingPayload().field_map || {};
  target.innerHTML = `
    <div class="source-structure-grid">
      <div class="structure-card"><strong>Профиль источника</strong><span>${escapeHtml(tableProfileSummaryText())}</span></div>
      <div class="structure-card"><strong>Поля метаданных</strong><span>${meta.length ? meta.map(item => `${escapeHtml(item.address)}: ${escapeHtml(item.value)}`).join('<br>') : 'нет'}</span></div>
      <div class="structure-card"><strong>Привязка колонок</strong><span>${Object.keys(mapped).length ? Object.entries(mapped).map(([name, item]) => `${escapeHtml(name)} ← ${escapeHtml(mappingKindLabel(item.kind))}:${escapeHtml(item.ref)}`).join('<br>') : 'авторежим table_facts'}</span></div>
      <div class="structure-card"><strong>Игнор/футер</strong><span>игнор: ${profile.ignoredRows.length} · футер: ${profile.footerRows.length}</span></div>
    </div>
    <div class="structure-table-host" id="structureTablePreview"></div>`;
  renderSpreadsheetTable(document.getElementById('structureTablePreview'), 'structure');
}

export function renderSourceStructurePreview() {
  if (!refs.sourceStructurePreview) return;
  if (!state.currentSource || !state.currentParsed) {
    refs.sourceStructurePreview.innerHTML = '<div class="status">Выберите источник в проводнике.</div>';
    return;
  }
  if (state.currentParsed.source_type === 'xlsx') renderTableStructurePreview();
  else renderTextStructurePreview();
}

export function renderSourceEntriesPreview() {
  const target = refs.sourceEntriesPreview;
  if (!target) return;
  const entries = buildEntriesPreview();
  if (!entries.length) {
    target.innerHTML = '<div class="status">Нет записей. Проверь структуру и профиль источника.</div>';
    return;
  }
  target.innerHTML = entries.slice(0, 30).map(entry => `
    <article class="entry-preview-item">
      <div class="entry-preview-item__head">
        <strong>${escapeHtml(entry.title || entry.entry_id)}</strong>
        <span class="pill">${escapeHtml(entry.entry_type)}</span>
        <span class="pill">${escapeHtml(entry.domain)}</span>
      </div>
      <div class="entry-preview-text">${escapeHtml((entry.text || '').slice(0, 700))}${(entry.text || '').length > 700 ? '…' : ''}</div>
      <details><summary>JSON</summary><pre class="json-wrap">${escapeHtml(JSON.stringify(entry, null, 2))}</pre></details>
    </article>`).join('');
}

export function renderSourceChunksPreview() {
  const target = refs.sourceChunksPreview;
  if (!target) return;
  const chunks = buildChunksPreview();
  if (!chunks.length) {
    target.innerHTML = '<div class="status">Нет чанков. Сначала проверь записи.</div>';
    return;
  }
  target.innerHTML = chunks.slice(0, 40).map(chunk => `
    <article class="chunk-preview-item">
      <div class="entry-preview-item__head">
        <strong>${escapeHtml(chunk.chunk_id)}</strong>
        <span class="pill">${escapeHtml(chunk.domain)}</span>
        <span class="pill">${escapeHtml(chunk.entry_type)}</span>
      </div>
      <pre class="chunk-text">${escapeHtml(chunk.text)}</pre>
      <details><summary>Payload / служебные данные</summary><pre class="json-wrap">${escapeHtml(JSON.stringify(chunk.payload, null, 2))}</pre></details>
    </article>`).join('');
}

export function renderSourcePipelinePreview() {
  const target = refs.sourcePipelinePreview;
  if (!target) return;
  if (!state.currentSource || !state.currentParsed) {
    target.innerHTML = '<div class="status">Выберите источник.</div>';
    return;
  }
  const entries = buildEntriesPreview();
  const chunks = buildChunksPreview();
  const typed = currentEntryTypeFallback() && !['generic_text','sectioned_text','table_facts'].includes(currentEntryTypeFallback());
  target.innerHTML = `
    <div class="pipeline-step is-ready"><strong>1. Разметка источника</strong><span>${escapeHtml(state.currentParsed.source_type)} · ${state.currentParsed.source_type === 'xlsx' ? tableProfileSummaryText() : `${documentStructureBlocks().length} текстовых блоков`}</span></div>
    <div class="pipeline-step is-ready"><strong>2. Правило извлечения</strong><span>${state.currentParsed.source_type === 'xlsx' ? 'профиль источника + привязка / table_facts' : 'иерархия разделов + роли абзацев'}</span></div>
    <div class="pipeline-step ${typed ? 'is-ready' : 'is-soft'}"><strong>3. Тип записи</strong><span>${escapeHtml(currentEntryTypeFallback())}${typed ? '' : ' · общий режим'}</span></div>
    <div class="pipeline-step ${entries.length ? 'is-ready' : 'is-warn'}"><strong>4. Записи</strong><span>${entries.length} записей в предпросмотре</span></div>
    <div class="pipeline-step ${chunks.length ? 'is-ready' : 'is-warn'}"><strong>5. Чанки / Qdrant</strong><span>${chunks.length} чанков с payload-метаданными</span></div>`;
}

export function renderExtractionWorkspace() {
  renderSourceStructurePreview();
  renderSourceEntriesPreview();
  renderSourceChunksPreview();
  renderSourcePipelinePreview();
}

function setValueIfPresent(id, value) {
  const element = document.getElementById(id);
  if (element) element.value = value ?? '';
}

function setPreviewFallback(error = null) {
  if (!refs.sourcePreview || !state.currentParsed) return;
  if (state.currentParsed.source_type === 'xlsx') {
    const sheet = getCurrentSheet();
    const rows = Array.isArray(sheet?.rows) ? sheet.rows : [];
    refs.sourcePreview.classList.remove('spreadsheet-host');
    refs.sourcePreview.innerHTML = rows.length
      ? `<div class="table-wrap"><table><tbody>${rows.slice(0, 80).map(row => `<tr>${(Array.isArray(row) ? row : []).slice(0, 12).map(cell => `<td>${escapeHtml(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
      : '<div class="status">Нет строк для предпросмотра.</div>';
    return;
  }
  const text = String(state.currentSource?.mapping?.edited_text || state.currentParsed?.text || (state.currentParsed?.paragraphs || []).join('\n\n') || '').trim();
  const note = error ? `<div class="status">Предпросмотр открыт в аварийном режиме: ${escapeHtml(error.message || error)}</div>` : '';
  refs.sourcePreview.classList.remove('spreadsheet-host');
  refs.sourcePreview.innerHTML = `<div class="doc-wrap">${note}${text ? `<pre class="chunk-text">${escapeHtml(text)}</pre>` : 'Нет текста.'}</div>`;
}

export function renderSourceEditor() {
  if (!state.currentSource || !state.currentParsed) {
    refs.sourceEditor?.classList.add('hidden');
    if (refs.sourceStatus) refs.sourceStatus.textContent = 'Выберите источник слева.';
    if (refs.validationOutput) refs.validationOutput.textContent = JSON.stringify({ ready: false, reason: 'источник не выбран' }, null, 2);
    renderExtractionWorkspace();
    updateInspectorContext();
    return;
  }

  refs.sourceEditor?.classList.remove('hidden');
  if (refs.sourceStatus) refs.sourceStatus.textContent = `Источник: ${currentSourceDisplayName()} • ${state.currentParsed.source_type}`;
  if (refs.rawPreview) refs.rawPreview.textContent = JSON.stringify(state.currentParsed, null, 2);

  const sourceConfig = currentSheetSourceConfig({ readDom: false });
  if (refs.validationOutput) {
    refs.validationOutput.textContent = JSON.stringify({
      ready: true,
      source_id: currentLogicalSourceId(),
      source_base_id: state.currentSource.source_id,
      sheet_name: state.currentParsed.source_type === 'xlsx' ? state.currentSheetName : null,
      class_name: sourceConfig.class_name || null,
      source_type: state.currentParsed.source_type
    }, null, 2);
  }

  try {
    if (state.currentParsed.source_type === 'xlsx') {
      refs.xlsxEditor?.classList.remove('hidden');
      refs.docEditor?.classList.add('hidden');
      renderSheetControls();
      renderExcelPreview();
    } else {
      refs.xlsxEditor?.classList.add('hidden');
      refs.docEditor?.classList.remove('hidden');
      setValueIfPresent('docTitle', state.currentSource.mapping?.title || state.currentParsed?.title || state.currentSource.source_id);
      setValueIfPresent('docEditedText', state.currentSource.mapping?.edited_text || state.currentParsed?.text || (state.currentParsed?.paragraphs || []).join('\n\n'));
      state.docSelectedParagraphs = [...((state.currentSource.mapping?.selected_paragraphs || []).map(Number))];
      initializeSemanticDocumentState();
      renderDocPreview();
    }
  } catch (error) {
    console.error('[admin] source preview fallback', error);
    setPreviewFallback(error);
  }

  try {
    renderSourceConfigControls();
    if (refs.entrySourceIdInput) refs.entrySourceIdInput.value = currentLogicalSourceId() || state.currentSource.source_id;
    if (refs.entryClassSelect) refs.entryClassSelect.value = sourceConfig.class_name || '';
    if (refs.entrySchemaSelect) refs.entrySchemaSelect.value = sourceConfig.schema || '';
    if (refs.entryLevelSelect) refs.entryLevelSelect.value = sourceConfig.education_level || '';
    if (refs.entryLangSelect) refs.entryLangSelect.value = sourceConfig.language || '';
    renderEntryTablePreview();
    renderSchemaTablePreview();
    renderSchemaPreview();
    renderExtractionWorkspace();
    updateInspectorContext();
  } catch (error) {
    console.error('[admin] secondary source editor render failed', error);
  }
}

export function buildDraftFromCurrentSource() {
  if (!state.currentSource || !state.currentParsed) return null;
  const base = {
    entry_id: refs.entryIdInput.value.trim() || `${currentLogicalSourceId() || state.currentSource.source_id}_${Date.now()}`,
    source_id: currentLogicalSourceId() || state.currentSource.source_id,
    class_name: refs.entryClassSelect.value || state.currentSource.class_name || '',
    schema: '',
    education_level: refs.entryLevelSelect.value || state.currentSource.education_level || null,
    language: refs.entryLangSelect.value || state.currentSource.language || null,
    source_file: state.currentSource.path || '',
    source_url: state.currentSource.source_url || null,
  };
  if (state.currentParsed.source_type === 'xlsx') {
    const sheet = getCurrentSheet();
    if (!sheet) return null;
    const rows = sheet.rows || [];
    const selectedRows = selectedRowsForDraft();
    const rowIndex = selectedRows[0] ?? Math.max(0, Number(refs.draftRowSelect.value || 1) - 1);
    const row = rows[rowIndex] || [];
    const titleColumn = Number(document.getElementById('titleColumnSelect').value || 0);
    const textColumns = selectedValues(document.getElementById('textColumnsSelect')).map(Number);
    const columns = currentColumns();
    const mappedPieces = textColumns.map(index => {
      const label = columns.find(item => item.value === String(index))?.label?.split(': ').slice(1).join(': ') || '';
      const value = row[index] || '';
      return value ? (label ? `${label}: ${value}` : value) : '';
    }).filter(Boolean);
    const selectionText = selectedOrProfileTableText();
    const schemaFields = { ...valuesFromSchemaMapping(rowIndex), ...collectSchemaFieldDraftValues() };
    const schemaLines = Object.entries(schemaFields).map(([key, value]) => `${key}: ${value}`);
    const text = [schemaLines.join('\n'), selectionText, mappedPieces.join('. ') || row.join(' ').trim()].filter(Boolean)[0] || '';
    return {
      ...base,
      title: (refs.entryTitleInput.value || row[titleColumn] || document.getElementById('mappingTitle').value || `${sheet.sheet_title} row ${rowIndex + 1}`).trim(),
      text,
      embedding_text: refs.entryEmbeddingTextInput.value.trim() || text,
      metadata: {
        sheet_name: sheet.sheet_title,
        row_index: rowIndex + 1,
        record_type: 'curated_excel_selection',
        table_selection: selectionMetadata(),
        fields: schemaFields,
      },
    };
  }
  const semanticEntry = semanticDraftFromCurrentSource();
  return {
    ...base,
    title: semanticEntry?.title || document.getElementById('docTitle').value.trim() || state.currentSource.source_id,
    text: semanticEntry?.text || '',
    embedding_text: refs.entryEmbeddingTextInput.value.trim() || semanticEntry?.text || '',
    metadata: {
      ...(semanticEntry?.metadata || {}),
      record_type: 'semantic_text',
    },
  };
}

export function populateEntryDraft(entry) {
  state.currentCuratedEntry = entry || null;
  refs.entryIdInput.value = entry?.entry_id || '';
  refs.entryTitleInput.value = entry?.title || '';
  refs.entryClassSelect.value = entry?.class_name || refs.entryClassSelect.value || '';
  refs.entrySchemaSelect.value = entry?.schema || refs.entrySchemaSelect.value || '';
  refs.entryLevelSelect.value = entry?.education_level || '';
  refs.entryLangSelect.value = entry?.language || '';
  refs.entrySourceIdInput.value = entry?.source_id || refs.entrySourceIdInput.value || '';
  refs.entryTextInput.value = entry?.text || '';
  refs.entryEmbeddingTextInput.value = entry?.embedding_text || '';
  refs.entryMetadataInput.value = JSON.stringify(entry?.metadata || {}, null, 2);
  refs.selectedEntryOutput.textContent = JSON.stringify(entry || {}, null, 2);
}

export function buildCuratedEntryPayload() {
  let metadata = {};
  try {
    metadata = JSON.parse(refs.entryMetadataInput.value || '{}');
  } catch {
    metadata = { invalid_metadata: true, raw: refs.entryMetadataInput.value };
  }
  return {
    entry_id: refs.entryIdInput.value.trim(),
    source_id: refs.entrySourceIdInput.value.trim(),
    class_name: refs.entryClassSelect.value,
    schema: '',
    title: refs.entryTitleInput.value.trim(),
    text: refs.entryTextInput.value,
    embedding_text: refs.entryEmbeddingTextInput.value.trim() || refs.entryTextInput.value,
    education_level: refs.entryLevelSelect.value || null,
    language: refs.entryLangSelect.value || null,
    metadata,
    source_file: state.currentSource?.path || state.currentCuratedEntry?.source_file || '',
    source_url: state.currentSource?.source_url || state.currentCuratedEntry?.source_url || null,
    enabled: true,
  };
}

export function clearEntryDraft() {
  const sourceConfig = currentSheetSourceConfig();
  populateEntryDraft({
    entry_id: '',
    source_id: currentLogicalSourceId() || state.currentSource?.source_id || '',
    class_name: sourceConfig.class_name || '',
    schema: sourceConfig.schema || '',
    title: '',
    text: '',
    embedding_text: '',
    education_level: sourceConfig.education_level || '',
    language: sourceConfig.language || '',
    metadata: {},
  });
}

function entryTooltip(entry) {
  return [
    `entry_id: ${entry.entry_id || ''}`,
    `source_id: ${entry.source_id || ''}`,
    `domain: ${entry.class_name || ''}`,
    `level: ${entry.education_level || ''}`,
    `language: ${entry.language || ''}`,
  ].join('\n');
}

export function renderCuratedEntriesList() {
  refs.entriesList.innerHTML = '';
  state.curatedEntries.forEach(entry => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `source-item entry-item${state.currentCuratedEntry?.entry_id === entry.entry_id ? ' active' : ''}`;
    item.dataset.entryId = entry.entry_id;
    item.title = entryTooltip(entry);
    item.innerHTML = `
      <div class="source-item__title">${entry.title || entry.entry_id}</div>
      <div class="muted">${entry.entry_id}</div>
      <div class="source-item__meta">
        <span class="pill">${entry.class_name || 'domain: none'}</span>
        <span class="pill">${entry.source_id || 'источник: нет'}</span>
      </div>
    `;
    refs.entriesList.appendChild(item);
  });
}

export function bindCuratedEntryActions(onOpen) {
  refs.entriesList.querySelectorAll('.entry-item[data-entry-id]').forEach(item => {
    item.onclick = () => onOpen(item.dataset.entryId);
  });
}

export function renderIndexedEntriesList() {
  refs.indexedEntriesList.innerHTML = '';
  state.indexedEntries.forEach(entry => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `source-item entry-item${state.currentIndexedEntry?.entry_id === entry.entry_id ? ' active' : ''}`;
    item.dataset.indexedEntryId = entry.entry_id;
    item.title = entryTooltip(entry);
    item.innerHTML = `
      <div class="source-item__title">${entry.title || entry.entry_id}</div>
      <div class="muted">${entry.entry_id}</div>
      <div class="source-item__meta">
        <span class="pill">${entry.class_name || 'domain: none'}</span>
        <span class="pill">${entry.source_id || 'источник: нет'}</span>
      </div>
    `;
    refs.indexedEntriesList.appendChild(item);
  });
}

export function bindIndexedEntryActions(onOpen) {
  refs.indexedEntriesList.querySelectorAll('.entry-item[data-indexed-entry-id]').forEach(item => {
    item.onclick = () => onOpen(item.dataset.indexedEntryId);
  });
}

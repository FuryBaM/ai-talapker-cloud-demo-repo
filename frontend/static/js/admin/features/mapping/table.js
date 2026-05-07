import { refs, renderSelectOptions, selectedValues, state } from '../../core/state.js';
import { currentColumns, escapeAttr, escapeHtml, markCurrentSourceDirty, renderFieldLabelInputs } from '../../ui/common.js';
import { renderExtractionWorkspace } from '../../ui/extraction.js';
import {
  clearTableMarksFromSelection,
  captureCurrentSheetInspectorState,
  columnHeaderLabel,
  createSchemaFieldFromSelectedColumn,
  createSchemaMetadataFieldFromSelectedCell,
  currentDataStartRowIndex,
  currentHeaderRowIndex,
  currentOutputFields,
  currentSchemaMappingPayload,
  currentXlsxSheetMapping,
  deleteSelectedSchemaField,
  ensureTableProfile,
  getColumnWidth,
  getCurrentSheet,
  getSheetKey,
  markFooterFromSelection,
  markIgnoreFromSelection,
  markMetadataFromSelection,
  maxColumnCount,
  normalizeFieldMapping,
  normalizeSelection,
  parseSpreadsheetAddress,
  renderSchemaInspector,
  renderSchemaMappingList,
  renderSchemaPreview,
  renderSchemaTablePreview,
  renderTableStructureSummary,
  resetSelectedSchemaFieldMapping,
  selectedCellKey,
  selectedCellSet,
  selectedOrProfileTableText,
  selectedTableFirstValue,
  mappedSchemaFieldNameFromSelection,
  selectMappedSchemaFieldFromSelection,
  resetMappedSchemaFieldFromSelection,
  deleteMappedSchemaFieldFromSelection,
  selectionMetadata,
  selectionSummaryText,
  setColumnWidth,
  setDataBelowHeader,
  setDataFromSelection,
  setHeaderFromSelection,
  sheetRows,
  spreadsheetColumnName,
  syncSelectionModeControls,
  tableProfileSummaryText,
  toggleCell,
  toggleNumber,
} from './logic.js';

function hideTableContextMenu() {
  const menu = document.getElementById('tableContextMenu');
  if (!menu) return;
  menu.classList.add('hidden');
  menu.innerHTML = '';
}

function compactContextItems(items = []) {
  const seen = new Set();
  return items.filter(item => {
    if (!item || item.visible === false) return false;
    if (item.separator || item.heading) return true;
    const key = item.label || '';
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function focusMappedFieldFromCurrentSelection() {
  return selectMappedSchemaFieldFromSelection();
}

function showTableContextMenu(event, items = []) {
  const menu = document.getElementById('tableContextMenu');
  const visibleItems = compactContextItems(items);
  if (!menu || !visibleItems.length) return;
  event.preventDefault();
  menu.innerHTML = visibleItems.map((item, index) => {
    if (item.separator) return '<div class="context-menu__separator"></div>';
    if (item.heading) return `<div class="context-menu__heading">${escapeHtml(item.heading)}</div>`;
    const className = item.danger ? 'context-menu__danger' : '';
    const disabled = item.disabled ? 'disabled' : '';
    return `<button class="${className}" type="button" data-context-action="${index}" ${disabled}>${escapeHtml(item.label)}</button>`;
  }).join('');
  menu.style.left = `${Math.min(event.clientX, window.innerWidth - 250)}px`;
  menu.style.top = `${Math.min(event.clientY, window.innerHeight - 40 - visibleItems.length * 30)}px`;
  menu.classList.remove('hidden');
  menu.querySelectorAll('[data-context-action]').forEach(button => {
    button.onclick = () => {
      hideTableContextMenu();
      const selected = visibleItems[Number(button.dataset.contextAction)];
      selected?.action?.();
      if (selected && !selected.disabled && !selected.noDirty) markCurrentSourceDirty();
    };
  });
}


document.addEventListener('click', event => {
  if (!event.target.closest('#tableContextMenu')) hideTableContextMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') hideTableContextMenu();
});

function spreadsheetColumnIndexFromName(name = '') {
  const letters = String(name || '').trim().toUpperCase().match(/^[A-Z]+/)?.[0] || '';
  if (!letters) return null;
  let value = 0;
  for (const char of letters) value = value * 26 + (char.charCodeAt(0) - 64);
  return value > 0 ? value - 1 : null;
}

function activeSchemaDisplayName() {
  return currentXlsxSheetMapping().entry_type || 'table_facts';
}

function spreadsheetViewportScrollKey(target, context) {
  const targetId = target?.id || target?.dataset?.spreadsheetScrollId || context || 'table';
  return `${getSheetKey()}::${targetId}::${context || 'viewer'}`;
}

function captureSpreadsheetViewportScroll(target, context) {
  const viewport = target?.querySelector?.('.spreadsheet-viewport');
  if (!viewport) return;
  if (!state.xlsxViewportScrolls || typeof state.xlsxViewportScrolls !== 'object') state.xlsxViewportScrolls = {};
  state.xlsxViewportScrolls[spreadsheetViewportScrollKey(target, context)] = {
    top: viewport.scrollTop || 0,
    left: viewport.scrollLeft || 0,
  };
}

function restoreSpreadsheetViewportScroll(target, context) {
  const viewport = target?.querySelector?.('.spreadsheet-viewport');
  const saved = state.xlsxViewportScrolls?.[spreadsheetViewportScrollKey(target, context)];
  if (!viewport || !saved) return;
  const restore = () => {
    viewport.scrollTop = saved.top || 0;
    viewport.scrollLeft = saved.left || 0;
  };
  restore();
  requestAnimationFrame(restore);
}

function schemaMappingsForViewer() {
  const columns = new Map();
  const cells = new Map();
  const fieldsByName = new Map(currentOutputFields().map(field => [field.name, field]));
  Object.entries(currentSchemaMappingPayload().field_map || {}).forEach(([name, rawMapping]) => {
    const mapping = normalizeFieldMapping(rawMapping);
    const field = fieldsByName.get(name) || { name, label: name, destination: mapping.destination };
    if (mapping.kind === 'column') {
      const col = spreadsheetColumnIndexFromName(mapping.ref);
      if (col !== null) columns.set(col, field);
    }
    if (mapping.kind === 'cell') {
      const cell = parseSpreadsheetAddress(mapping.ref);
      if (cell) cells.set(selectedCellKey(cell.row, cell.col), field);
    }
  });
  return { columns, cells };
}

export function renderSpreadsheetTable(target, context = 'viewer') {
  if (!target) return;
  captureSpreadsheetViewportScroll(target, context);
  target.classList.add('spreadsheet-host');
  normalizeSelection();
  const sheet = getCurrentSheet();
  if (!sheet) {
    target.innerHTML = '<div class="status">Нет данных по выбранному листу.</div>';
    return;
  }
  const rows = sheet.rows || [];
  const maxCols = maxColumnCount(rows);
  const cellSet = selectedCellSet();
  const selectedRows = new Set(state.xlsxSelection.rows.map(Number));
  const selectedCols = new Set(state.xlsxSelection.columns.map(Number));
  const profile = ensureTableProfile();
  const schemaMappings = schemaMappingsForViewer();
  const metaRows = new Set(profile.metaRows.map(Number));
  const metaCells = new Set(profile.metaCells.map(cell => selectedCellKey(cell.row, cell.col)));
  const dataRows = new Set(profile.dataRows.map(Number));
  const footerRows = new Set(profile.footerRows.map(Number));
  const ignoredRows = new Set(profile.ignoredRows.map(Number));
  const headerRow = currentHeaderRowIndex();
  const dataStartRow = currentDataStartRowIndex();
  const renderLimit = 500;
  const visibleRows = rows.slice(0, renderLimit);
  const selectionText = selectionSummaryText();
  const mappedTotal = schemaMappings.columns.size + schemaMappings.cells.size;
  const compactProfileText = `Заголовок: ${headerRow + 1} · Данные: ${dataStartRow + 1}+`;
  const toolbar = document.createElement('div');
  toolbar.className = 'spreadsheet-toolbar spreadsheet-toolbar--compact-status';
  toolbar.innerHTML = `
    <div class="spreadsheet-toolbar__left">
      <strong>${escapeHtml(sheet.sheet_title || 'Лист')}</strong>
      <span class="pill">${rows.length}×${maxCols}</span>
      <span class="pill pill--table-structure">${escapeHtml(compactProfileText)}</span>
      <span class="pill pill--schema-map">${mappedTotal ? `Поля: ${schemaMappings.columns.size}/${schemaMappings.cells.size}` : 'Поля: нет'}</span>
      ${selectionText && !/^ничего/i.test(selectionText) ? `<span class="pill" data-selection-summary>${escapeHtml(selectionText)}</span>` : ''}
    </div>
  `;

  const viewport = document.createElement('div');
  viewport.className = 'spreadsheet-viewport';
  const table = document.createElement('table');
  table.className = 'spreadsheet-table';
  table.dataset.tableContext = context;
  const colgroup = document.createElement('colgroup');
  colgroup.innerHTML = `<col class="spreadsheet-rownum-col" style="width:46px">` + Array.from({ length: maxCols }, (_, col) => `<col data-spreadsheet-col="${col}" style="width:${getColumnWidth(col)}px">`).join('');
  const thead = document.createElement('thead');
  thead.innerHTML = `<tr><th class="spreadsheet-corner">#</th>${Array.from({ length: maxCols }, (_, col) => {
    const mappedField = schemaMappings.columns.get(col);
    const title = mappedField ? `${columnHeaderLabel(col)}\nполе: ${mappedField.name}` : columnHeaderLabel(col);
    return `
    <th class="spreadsheet-col-header ${selectedCols.has(col) ? 'is-selected' : ''} ${mappedField ? 'is-schema-mapped-column' : ''}" data-col-header="${col}" title="${escapeAttr(title)}">
      <span>${spreadsheetColumnName(col)}</span>
      <small>${escapeHtml(columnHeaderLabel(col))}</small>
      ${mappedField ? `<em class="schema-map-badge">${escapeHtml(mappedField.name)}</em>` : ''}
      <i class="col-resizer" data-col-resizer="${col}" aria-hidden="true"></i>
    </th>`;
  }).join('')}</tr>`;
  const tbody = document.createElement('tbody');
  tbody.innerHTML = visibleRows.map((row, rowIndex) => {
    const rowSelected = selectedRows.has(rowIndex);
    const rowIsExplicitMeta = metaRows.has(rowIndex);
    const rowIsImplicitMeta = rowIndex < headerRow;
    const isIgnored = ignoredRows.has(rowIndex);
    const isFooter = !isIgnored && footerRows.has(rowIndex);
    const isHeader = !isIgnored && !isFooter && rowIndex === headerRow;
    const isMeta = !isIgnored && !isFooter && !isHeader && (rowIsExplicitMeta || rowIsImplicitMeta);
    const isData = !isIgnored && !isFooter && !isHeader && !isMeta && (dataRows.has(rowIndex) || (!profile.dataRows.length && rowIndex >= dataStartRow));
    const role = isIgnored ? 'ignore' : (isFooter ? 'footer' : (isHeader ? 'header' : (isMeta ? 'meta' : (isData ? 'data' : 'none'))));
    const rowClasses = [
      rowSelected ? 'is-row-selected' : '',
      `is-role-${role}`,
      isHeader ? 'is-structure-header-row' : '',
      isMeta ? 'is-meta-row' : '',
      isData ? 'is-data-row' : '',
      isFooter ? 'is-footer-row' : '',
      isIgnored ? 'is-ignored-row' : '',
    ].filter(Boolean).join(' ');
    const roleLabels = { header: 'заголовок', ignore: 'игнор', footer: 'футер', meta: 'мета', data: 'данные' };
    const rowRole = roleLabels[role] ? `<small class="spreadsheet-role-badge spreadsheet-role-badge--${role}">${roleLabels[role]}</small>` : '';
    return `<tr class="${rowClasses}">
      <th class="spreadsheet-row-header ${rowSelected ? 'is-selected' : ''}" data-row-header="${rowIndex}"><span>${rowIndex + 1}</span>${rowRole}</th>
      ${Array.from({ length: maxCols }, (_, col) => {
        const key = selectedCellKey(rowIndex, col);
        const selected = rowSelected || selectedCols.has(col) || cellSet.has(key);
        const header = isHeader ? ' is-header-row' : '';
        const meta = metaCells.has(key) || (isMeta && !isHeader) ? ' is-meta-cell' : '';
        const data = isData && !isHeader ? ' is-data-cell' : '';
        const mappedCell = schemaMappings.cells.get(key);
        const mappedColumn = schemaMappings.columns.get(col);
        const schemaCellClass = mappedCell ? ' is-schema-mapped-cell' : (mappedColumn && !isHeader ? ' is-schema-mapped-data-column' : '');
        const rawValue = String(row?.[col] ?? '');
        const title = mappedCell ? `${rawValue}\nполе: ${mappedCell.name}` : rawValue;
        return `<td class="${selected ? 'is-selected' : ''}${header}${meta}${data}${schemaCellClass}" data-cell-row="${rowIndex}" data-cell-col="${col}" title="${escapeAttr(title)}"><span class="spreadsheet-cell-value">${escapeHtml(row?.[col] ?? '')}</span>${mappedCell ? `<em class="schema-map-badge schema-map-badge--cell">${escapeHtml(mappedCell.name)}</em>` : ''}</td>`;
      }).join('')}
    </tr>`;
  }).join('');
  table.appendChild(colgroup);
  table.appendChild(thead);
  table.appendChild(tbody);
  table.addEventListener('click', event => {
    if (event.target.closest('[data-col-resizer]')) return;
    const additive = Boolean(event.ctrlKey || event.metaKey);
    const colHeader = event.target.closest('[data-col-header]');
    if (colHeader) {
      const col = Number(colHeader.dataset.colHeader);
      state.xlsxSelection.cells = [];
      state.xlsxSelection.rows = [];
      state.xlsxSelection.columns = additive ? toggleNumber(state.xlsxSelection.columns, col) : [col];
      captureCurrentSheetInspectorState();
      focusMappedFieldFromCurrentSelection();
      refreshSpreadsheetViews();
      return;
    }
    const rowHeader = event.target.closest('[data-row-header]');
    if (rowHeader) {
      const row = Number(rowHeader.dataset.rowHeader);
      state.xlsxSelection.cells = [];
      state.xlsxSelection.columns = [];
      state.xlsxSelection.rows = additive ? toggleNumber(state.xlsxSelection.rows, row) : [row];
      captureCurrentSheetInspectorState();
      refreshSpreadsheetViews();
      return;
    }
    const cell = event.target.closest('[data-cell-row][data-cell-col]');
    if (!cell) return;
    const row = Number(cell.dataset.cellRow);
    const col = Number(cell.dataset.cellCol);
    if (state.xlsxSelection.mode === 'row') {
      state.xlsxSelection.cells = [];
      state.xlsxSelection.columns = [];
      state.xlsxSelection.rows = additive ? toggleNumber(state.xlsxSelection.rows, row) : [row];
    } else if (state.xlsxSelection.mode === 'column') {
      state.xlsxSelection.cells = [];
      state.xlsxSelection.rows = [];
      state.xlsxSelection.columns = additive ? toggleNumber(state.xlsxSelection.columns, col) : [col];
    } else {
      state.xlsxSelection.rows = [];
      state.xlsxSelection.columns = [];
      state.xlsxSelection.cells = additive ? toggleCell(row, col) || state.xlsxSelection.cells : [{ row, col }];
    }
    captureCurrentSheetInspectorState();
    focusMappedFieldFromCurrentSelection();
    refreshSpreadsheetViews();
  });
  table.addEventListener('contextmenu', event => {
    const colHeader = event.target.closest('[data-col-header]');
    const rowHeader = event.target.closest('[data-row-header]');
    const cell = event.target.closest('[data-cell-row][data-cell-col]');
    if (colHeader) {
      state.xlsxSelection.columns = [Number(colHeader.dataset.colHeader)];
      state.xlsxSelection.cells = [];
      state.xlsxSelection.rows = [];
      captureCurrentSheetInspectorState();
      refreshSpreadsheetViews();
      const mappedFieldName = mappedSchemaFieldNameFromSelection();
      focusMappedFieldFromCurrentSelection();
      showTableContextMenu(event, [
        { heading: mappedFieldName ? `Колонка · ${mappedFieldName}` : 'Колонка' },
        { label: mappedFieldName ? 'Обновить поле' : 'Создать поле', action: createSchemaFieldFromSelectedColumn },
        { label: 'Сбросить привязку', action: resetMappedSchemaFieldFromSelection, disabled: !mappedFieldName },
        { label: 'Удалить поле', action: deleteMappedSchemaFieldFromSelection, danger: true, disabled: !mappedFieldName },
      ]);
      return;
    }
    if (rowHeader) {
      state.xlsxSelection.rows = [Number(rowHeader.dataset.rowHeader)];
      state.xlsxSelection.cells = [];
      state.xlsxSelection.columns = [];
      captureCurrentSheetInspectorState();
      refreshSpreadsheetViews();
      showTableContextMenu(event, [
        { heading: 'Строка' },
        { label: 'Сделать данными', action: setDataFromSelection },
        { label: 'Сделать заголовком', action: setHeaderFromSelection },
        { label: 'Игнорировать', action: markIgnoreFromSelection },
        { label: 'Сбросить статус', action: clearTableMarksFromSelection },
      ]);
      return;
    }
    if (cell) {
      state.xlsxSelection.cells = [{ row: Number(cell.dataset.cellRow), col: Number(cell.dataset.cellCol) }];
      state.xlsxSelection.rows = [];
      state.xlsxSelection.columns = [];
      captureCurrentSheetInspectorState();
      refreshSpreadsheetViews();
      const mappedFieldName = mappedSchemaFieldNameFromSelection();
      focusMappedFieldFromCurrentSelection();
      showTableContextMenu(event, [
        { heading: mappedFieldName ? `Ячейка · ${mappedFieldName}` : 'Ячейка' },
        { label: mappedFieldName ? 'Обновить мета-поле' : 'Создать мета-поле', action: createSchemaMetadataFieldFromSelectedCell },
        { label: 'Сбросить привязку', action: resetMappedSchemaFieldFromSelection, disabled: !mappedFieldName },
        { label: 'Удалить поле', action: deleteMappedSchemaFieldFromSelection, danger: true, disabled: !mappedFieldName },
      ]);
    }
  });
  table.querySelectorAll('[data-col-resizer]').forEach(handle => {
    handle.addEventListener('mousedown', event => {
      event.preventDefault();
      event.stopPropagation();
      const col = Number(handle.dataset.colResizer);
      const startX = event.clientX;
      const startWidth = getColumnWidth(col);
      const colNode = table.querySelector(`col[data-spreadsheet-col="${col}"]`);
      const onMove = moveEvent => {
        const width = Math.max(72, Math.min(720, startWidth + moveEvent.clientX - startX));
        if (colNode) colNode.style.width = `${width}px`;
      };
      const onUp = upEvent => {
        const width = Math.max(72, Math.min(720, startWidth + upEvent.clientX - startX));
        setColumnWidth(col, width);
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        refreshSpreadsheetViews();
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  });
  viewport.appendChild(table);
  target.innerHTML = '';
  target.appendChild(toolbar);
  target.appendChild(viewport);
  restoreSpreadsheetViewportScroll(target, context);
  if (rows.length > renderLimit) {
    const note = document.createElement('div');
    note.className = 'status spreadsheet-limit-note';
    note.textContent = `Показаны первые ${renderLimit} строк из ${rows.length}.`;
    target.appendChild(note);
  }
  syncSelectionModeControls();
  renderTableStructureSummary();
}

export function refreshSpreadsheetViews() {
  if (state.currentParsed?.source_type !== 'xlsx') return;
  renderExcelPreview();
  renderEntryTablePreview();
  renderSchemaTablePreview();
  renderSchemaPreview();
  renderSchemaInspector();
  renderSchemaMappingList();
  renderExtractionWorkspace();
}

export function renderEntryTablePreview() {
  renderSpreadsheetTable(refs.entryTablePreview, 'entry');
}

export function clearExcelSelection() {
  normalizeSelection();
  state.xlsxSelection.cells = [];
  state.xlsxSelection.rows = [];
  state.xlsxSelection.columns = [];
  captureCurrentSheetInspectorState();
  refreshSpreadsheetViews();
}

export function setExcelSelectionMode(mode) {
  normalizeSelection();
  state.xlsxSelection.mode = mode || 'cell';
  captureCurrentSheetInspectorState();
  syncSelectionModeControls();
}

export function fillSelectedTableTextToDraft(target) {
  const text = selectedOrProfileTableText();
  if (!text) return;
  if (target === 'title') refs.entryTitleInput.value = selectedTableFirstValue() || text.split('\n')[0];
  if (target === 'text') refs.entryTextInput.value = refs.entryTextInput.value ? `${refs.entryTextInput.value}\n${text}` : text;
  if (target === 'source') refs.entrySourceIdInput.value = selectedTableFirstValue() || refs.entrySourceIdInput.value;
  if (target === 'note') {
    let metadata = {};
    try { metadata = JSON.parse(refs.entryMetadataInput.value || '{}'); } catch { metadata = {}; }
    metadata.note = text;
    metadata.table_selection = selectionMetadata();
    refs.entryMetadataInput.value = JSON.stringify(metadata, null, 2);
  }
}

export function applySchemaFieldsToDraft() {
  // Deprecated: field extraction is now driven directly by field mappings.
  renderExtractionWorkspace();
}

export function renderSheetControls() {
  const sheets = state.currentParsed?.sheets || [];
  const sheetSelect = document.getElementById('sheetSelect');
  const headerSelect = document.getElementById('headerRowSelect');
  const dataStartSelect = document.getElementById('dataStartRowSelect');
  const titleColumnSelect = document.getElementById('titleColumnSelect');
  const sourceColumnSelect = document.getElementById('sourceColumnSelect');
  const noteColumnSelect = document.getElementById('noteColumnSelect');
  const textColumnsSelect = document.getElementById('textColumnsSelect');
  const oldHeader = headerSelect?.value;
  const oldDataStart = dataStartSelect?.value;
  const oldTitleColumn = titleColumnSelect?.value;
  const oldSourceColumn = sourceColumnSelect?.value;
  const oldNoteColumn = noteColumnSelect?.value;
  const oldTextColumns = selectedValues(textColumnsSelect).map(String);
  const controlKey = getSheetKey();
  const sameControlContext = state._lastSheetControlKey === controlKey;
  const sheetHadOptions = Boolean(sheetSelect?.options?.length);
  const rowsHadOptions = Boolean(headerSelect?.options?.length);
  const columnsHadOptions = Boolean(titleColumnSelect?.options?.length);
  renderSelectOptions(sheetSelect, sheets.map(item => ({ value: item.sheet_title, label: item.sheet_title })), state.currentSheetName || (sheets[0]?.sheet_title || ''));
  state.currentSheetName = sheetSelect.value || sheets[0]?.sheet_title || '';
  if (sheetSelect) {
    sheetSelect.disabled = true;
    sheetSelect.title = 'Лист выбирается в проводнике как отдельный источник';
  }
  const sheetMapping = currentXlsxSheetMapping();
  const rows = (sheets.find(item => item.sheet_title === state.currentSheetName) || sheets[0] || {}).rows || [];
  const rowOptions = rows.slice(0, 500).map((row, index) => ({ value: String(index + 1), label: `Строка ${index + 1}: ${String((row || []).slice(0, 3).filter(Boolean).join(' | ')).slice(0, 72)}` }));
  const headerValue = sameControlContext && rowsHadOptions && oldHeader ? oldHeader : String(sheetMapping.header_row || 1);
  const dataStartValue = sameControlContext && rowsHadOptions && oldDataStart ? oldDataStart : String(sheetMapping.data_start_row || 2);
  renderSelectOptions(headerSelect, rowOptions, headerValue);
  renderSelectOptions(dataStartSelect, rowOptions, dataStartValue);
  renderSelectOptions(refs.draftRowSelect, rowOptions, String(Math.max(1, Number(dataStartSelect.value || 2))));
  const columns = currentColumns();
  renderSelectOptions(titleColumnSelect, columns, sameControlContext && columnsHadOptions && oldTitleColumn !== '' ? oldTitleColumn : String(sheetMapping.title_column ?? 0));
  renderSelectOptions(sourceColumnSelect, [{ value: '', label: 'колонка источника: нет' }, ...columns], sameControlContext && columnsHadOptions ? oldSourceColumn : String(sheetMapping.source_column ?? ''));
  renderSelectOptions(noteColumnSelect, [{ value: '', label: 'колонка примечания: нет' }, ...columns], sameControlContext && columnsHadOptions ? oldNoteColumn : String(sheetMapping.note_column ?? ''));
  renderSelectOptions(textColumnsSelect, columns, sameControlContext && columnsHadOptions && oldTextColumns.length ? oldTextColumns : (sheetMapping.text_columns || []).map(String), true);
  if (!sameControlContext || !sheetHadOptions || !document.getElementById('mappingTitle').value) document.getElementById('mappingTitle').value = sheetMapping.title || '';
  state._lastSheetControlKey = controlKey;
  renderFieldLabelInputs();
}

export function renderExcelPreview() {
  renderSpreadsheetTable(refs.sourcePreview, 'viewer');
}


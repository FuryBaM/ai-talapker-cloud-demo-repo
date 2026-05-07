import { EDUCATION_LEVELS, LANGUAGES, refs, renderSelectOptions, selectedValues, state } from '../../core/state.js';
import { currentColumns, destinationLabel, domainOptions, escapeAttr, escapeHtml, indexOptions, mappingKindLabel, markCurrentSourceDirty, renderFieldLabelInputs, schemaOptions } from '../../ui/common.js';

import { renderExtractionWorkspace } from '../../ui/extraction.js';
import { refreshSpreadsheetViews, renderSheetControls, renderSpreadsheetTable } from './table.js';

const RESERVED_SYSTEM_FIELD_KEYS = new Set([
  'domain',
  'education_level',
  'language',
  'education_area_code',
  'education_area_name',
  'education_area',
  'training_direction_code',
  'training_direction_name',
  'training_direction',
  'program_group_code',
  'program_group_name',
  'program_group',
  'program_code',
  'program_name',
  'program',
  'tuition_price',
]);

function systemFieldOptions() {
  const fields = Array.isArray(state.catalog?.system_fields) ? state.catalog.system_fields : [];
  return [
    { key: '', label: 'нет' },
    ...fields.map(field => ({ key: field.key || '', label: field.label || field.key || '' })).filter(field => field.key),
  ];
}

function inferredSystemField(value = '') {
  const key = String(value || '').trim();
  return RESERVED_SYSTEM_FIELD_KEYS.has(key) ? key : '';
}

function safeLogicalFieldName(name = '', fallback = 'field') {
  const clean = String(name || '').trim();
  if (!RESERVED_SYSTEM_FIELD_KEYS.has(clean)) return clean;
  return uniqueSchemaFieldName(`${clean}_field`, currentOutputFields());
}

function logicalFieldNameFromSemantic(semanticName, fields = currentOutputFields()) {
  const systemKey = inferredSystemField(semanticName);
  const base = systemKey ? `${systemKey}_field` : semanticName;
  return uniqueSchemaFieldName(base, fields);
}

function decodeLegacyPreset(preset = '') {
  const raw = String(preset || '').trim();
  if (!raw) return {};
  const match = raw.match(/^([a-z_]+):([^|]*)(?:\|([a-z_]+))?$/i);
  if (!match) return { source_ref: raw };
  return {
    source_kind: match[1],
    source_ref: match[2] || '',
    destination: match[3] || undefined,
  };
}

function encodeLegacyPreset(source = {}) {
  const kind = String(source.source_kind || 'manual').trim() || 'manual';
  const ref = String(source.source_ref || '').trim();
  const destination = String(source.destination || 'fields').trim() || 'fields';
  if (kind === 'manual' && !ref) return '';
  return `${kind}:${ref}|${destination}`;
}

function normalizeFieldSource(field = {}) {
  const legacy = decodeLegacyPreset(field.preset);
  const kind = field.source_kind || field.source_type || field.source?.kind || legacy.source_kind || 'manual';
  const ref = field.source_ref
    || field.source?.ref
    || (kind === 'column' ? field.source_column : '')
    || (kind === 'cell' ? field.source_cell : '')
    || legacy.source_ref
    || '';
  return {
    source_kind: kind,
    source_ref: ref,
    destination: field.destination || field.output || field.source?.destination || legacy.destination || 'fields',
  };
}

function serializeSchemaField(field = {}) {
  const source = normalizeFieldSource(field);
  const serialized = {
    name: field.name || '',
    label: field.label || field.name || '',
    field_type: field.field_type || field.type || 'text',
    required: Boolean(field.required),
    description: field.description || '',
    system_field: field.system_field || field.system_key || inferredSystemField(field.name) || '',
    source_kind: source.source_kind,
    source_type: source.source_kind,
    source_ref: source.source_ref,
    destination: source.destination,
    output: source.destination,
    preset: encodeLegacyPreset(source),
    source: {
      kind: source.source_kind,
      ref: source.source_ref,
      destination: source.destination,
    },
    validation: field.validation || {},
  };
  if (source.source_kind === 'column') serialized.source_column = source.source_ref;
  if (source.source_kind === 'cell') serialized.source_cell = source.source_ref;
  return serialized;
}

function normalizeSchemaMappingTemplate(schema = {}) {
  const template = schema.mapping_template || schema.mappingTemplate || {};
  const fieldMap = { ...(template.field_map || schema.field_map || {}) };
  (schema.fields || []).forEach(field => {
    const name = String(field?.name || '').trim();
    if (!name || fieldMap[name]) return;
    const source = normalizeFieldSource(field);
    if (source.source_kind && source.source_kind !== 'manual' && source.source_ref) {
      fieldMap[name] = {
        kind: source.source_kind,
        ref: source.source_ref,
        destination: source.destination || field.destination || field.output || 'fields',
      };
    }
  });
  return {
    field_map: Object.fromEntries(Object.entries(fieldMap).map(([name, mapping]) => [name, normalizeFieldMapping(mapping)]).filter(([, mapping]) => mapping.kind && mapping.ref)),
    table_profile: template.table_profile || schema.source_profile || null,
  };
}

export function normalizeFieldMapping(mapping = {}) {
  return {
    kind: mapping.kind || mapping.source_kind || mapping.source_type || 'manual',
    ref: mapping.ref || mapping.source_ref || mapping.source_column || mapping.source_cell || '',
    destination: mapping.destination || mapping.output || 'fields',
  };
}

export function currentSchemaMappingPayload() {
  const template = state.schemaMappingDraft || { field_map: {} };
  const fieldNames = new Set(currentOutputFields().map(field => field.name));
  const fieldMap = {};
  Object.entries(template.field_map || {}).forEach(([name, mapping]) => {
    if (!fieldNames.has(name)) return;
    const normalized = normalizeFieldMapping(mapping);
    if (normalized.kind && normalized.kind !== 'manual' && normalized.ref) fieldMap[name] = normalized;
  });
  return { field_map: fieldMap };
}

export function schemaMappingPayload() {
  return currentSchemaMappingPayload();
}

function mappingForField(fieldName) {
  const mapping = (state.schemaMappingDraft?.field_map || {})[fieldName];
  return mapping ? normalizeFieldMapping(mapping) : { kind: 'manual', ref: '', destination: 'fields' };
}

function setMappingForField(fieldName, mapping) {
  if (!fieldName) return;
  state.schemaMappingDraft = state.schemaMappingDraft || { field_map: {} };
  state.schemaMappingDraft.field_map = { ...(state.schemaMappingDraft.field_map || {}) };
  state.schemaMappingDraft.field_map[fieldName] = normalizeFieldMapping(mapping);
}

function removeMappingForField(fieldName) {
  if (!fieldName || !state.schemaMappingDraft?.field_map) return;
  delete state.schemaMappingDraft.field_map[fieldName];
}

function findMappedFieldName(kind, ref) {
  const normalizedKind = String(kind || '').trim();
  const normalizedRef = String(ref || '').trim().toUpperCase();
  const entry = Object.entries(state.schemaMappingDraft?.field_map || {}).find(([, mapping]) => {
    const source = normalizeFieldMapping(mapping);
    return source.kind === normalizedKind && String(source.ref || '').toUpperCase() === normalizedRef;
  });
  return entry?.[0] || null;
}

function fieldIndexByName(fields, name) {
  return (fields || []).findIndex(field => String(field?.name || '') === String(name || ''));
}

function serializeContractField(field = {}) {
  const rawName = String(field.name || '').trim();
  const legacySystemField = inferredSystemField(rawName);
  return {
    name: rawName,
    label: field.label || field.name || '',
    field_type: field.field_type || field.type || 'text',
    required: Boolean(field.required),
    description: field.description || '',
    system_field: field.system_field || field.system_key || legacySystemField || '',
    destination: field.destination || field.output || normalizeFieldSource(field).destination || 'fields',
    validation: field.validation || {},
  };
}

function currentFieldRows() {

  return Array.from(refs.schemaFieldsList?.querySelectorAll('.schema-field-row:not(.schema-field-row--head)') || []);
}

function currentFieldIndex() {
  const value = Number(state.currentSchemaFieldIndex);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function activeSchemaFields() {
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') return currentOutputFields();
  return collectSchemaFields();
}

export function setCurrentFieldIndex(index) {
  const fields = activeSchemaFields();
  const requested = Number(index);
  const normalized = Number.isInteger(requested) && fields[requested] ? requested : null;
  state.currentSchemaFieldIndex = normalized;
  currentFieldRows().forEach((row, rowIndex) => row.classList.toggle('active', rowIndex === normalized));
  renderSchemaInspector();
  renderSchemaMappingList();
  renderExtractionWorkspace();
}

function tableLikeHandler(handler = '') {
  return ['program_entry', 'program_tuition_entry', 'dormitory_tuition_entry', 'timeline_entry', 'table_row'].includes(handler);
}

function schemaTypeFromHandler(handler = '') {
  if (handler === 'sectioned_text') return 'sectioned_text';
  if (tableLikeHandler(handler)) return 'table_row';
  if (handler.includes('text')) return 'generic_text';
  return 'generic_text';
}

function handlerFromSchemaType(type, currentHandler = '') {
  if (type === 'sectioned_text') return 'sectioned_text';
  if (type === 'table_row') return tableLikeHandler(currentHandler) ? currentHandler : 'program_entry';
  if (type === 'key_value') return currentHandler && !currentHandler.includes('text') ? currentHandler : 'generic_text';
  return currentHandler && currentHandler.includes('text') ? currentHandler : 'generic_text';
}

export function syncSchemaHeader() {
  const handler = document.getElementById('schemaHandler')?.value || 'generic_text';
  const fields = collectSchemaFields();
  const handlerPill = document.getElementById('schemaHandlerPill');
  const fieldCountPill = document.getElementById('schemaFieldCountPill');
  if (handlerPill) handlerPill.textContent = `обработчик: ${handler}`;
  if (fieldCountPill) fieldCountPill.textContent = `${fields.length} полей`;
  document.querySelectorAll('[name="schemaTypeChoice"]').forEach(radio => {
    radio.checked = radio.value === schemaTypeFromHandler(handler);
    radio.closest('.schema-type-card')?.classList.toggle('active', radio.checked);
  });
  const mappingStatus = document.getElementById('schemaMappingStatus');
  if (mappingStatus) {
    mappingStatus.textContent = state.currentParsed?.source_type === 'xlsx'
      ? `${state.currentSource?.source_id || 'источник'} · ${tableProfileSummaryText()}`
      : 'нет xlsx-источника для примера';
  }
}

export function renderSchemaFieldsEditor(fields = []) {
  if (!refs.schemaFieldsList) return;
  refs.schemaFieldsList.innerHTML = '';
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
    state._schemaFieldsControlKey = getSheetKey();
  } else if (state.workspaceTab === 'schema') {
    state._schemaFieldsControlKey = '';
  }
  const header = document.createElement('div');
  header.className = 'schema-field-row schema-field-row--head schema-field-row--contract';
  header.innerHTML = `
    <span>Имя</span>
    <span>Метка</span>
    <span>Тип</span>
    <span>Обязательное</span>
    <span>Назначение</span>
    <span>System</span>
    <span></span>
  `;
  refs.schemaFieldsList.appendChild(header);
  const contractFields = fields.map(serializeContractField);
  if (!contractFields.length) {
    const empty = document.createElement('div');
    empty.className = 'status schema-fields-empty';
    empty.textContent = 'Нет полей. В документе выдели заголовок/колонку/ячейку и создай привязку либо нажми + Добавить поле.';
    refs.schemaFieldsList.appendChild(empty);
    state.currentSchemaFieldIndex = null;
    syncSchemaHeader();
    renderSchemaInspector();
    renderSchemaMappingList();
    return;
  }
  contractFields.forEach((field, index) => {
    const row = document.createElement('div');
    row.className = `schema-field-row schema-field-row--contract${state.currentSchemaFieldIndex === index ? ' active' : ''}`;
    row.dataset.schemaFieldIndex = String(index);
    row.dataset.schemaFieldName = field.name;
    const systemOptions = systemFieldOptions().map(option => `<option value="${escapeAttr(option.key)}" ${field.system_field === option.key ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('');
    row.innerHTML = `
      <input data-schema-field="${index}" data-prop="name" placeholder="program_group" value="${escapeAttr(field.name)}">
      <input data-schema-field="${index}" data-prop="label" placeholder="Группа образовательных программ" value="${escapeAttr(field.label)}">
      <select data-schema-field="${index}" data-prop="field_type">
        <option value="text" ${field.field_type === 'text' ? 'selected' : ''}>текст</option>
        <option value="number" ${field.field_type === 'number' ? 'selected' : ''}>число</option>
        <option value="date" ${field.field_type === 'date' ? 'selected' : ''}>дата</option>
        <option value="enum" ${field.field_type === 'enum' ? 'selected' : ''}>список</option>
        <option value="url" ${field.field_type === 'url' ? 'selected' : ''}>ссылка</option>
      </select>
      <label class="schema-field-required"><input data-schema-field="${index}" data-prop="required" type="checkbox" ${field.required ? 'checked' : ''}> обязательное</label>
      <select data-schema-field="${index}" data-prop="system_field">${systemOptions}</select>
      <select data-schema-field="${index}" data-prop="destination">
        <option value="fields" ${field.destination === 'fields' ? 'selected' : ''}>поля</option>
        <option value="metadata" ${field.destination === 'metadata' ? 'selected' : ''}>метаданные</option>
        <option value="title" ${field.destination === 'title' ? 'selected' : ''}>заголовок</option>
        <option value="text" ${field.destination === 'text' ? 'selected' : ''}>текст</option>
        <option value="embedding_text" ${field.destination === 'embedding_text' ? 'selected' : ''}>эмбеддинг</option>
      </select>
      <button class="danger danger--inline schema-field-remove" data-schema-field-remove="${index}" type="button">×</button>
      <input class="schema-field-hidden-description" data-schema-field="${index}" data-prop="description" type="hidden" value="${escapeAttr(field.description)}">
      <input class="schema-field-hidden-validation" data-schema-field="${index}" data-prop="validation" type="hidden" value='${escapeAttr(JSON.stringify(field.validation || {}))}'>
    `;
    refs.schemaFieldsList.appendChild(row);
  });
  refs.schemaFieldsList.querySelectorAll('[data-schema-field-index]').forEach(row => {
    row.onclick = event => {
      if (event.target.closest('[data-schema-field-remove]')) return;
      setCurrentFieldIndex(Number(row.dataset.schemaFieldIndex));
    };
  });
  refs.schemaFieldsList.querySelectorAll('[data-schema-field-remove]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const fieldsNow = collectSchemaFields();
      if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') markCurrentSourceDirty();
      const removed = fieldsNow.splice(Number(button.dataset.schemaFieldRemove), 1)[0];
      removeMappingForField(removed?.name);
      state.currentSchemaFieldIndex = null;
      if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
        setCurrentOutputFields(fieldsNow);
      } else {
        renderSchemaFieldsEditor(fieldsNow);
      }
      renderSchemaPreview();
      refreshSpreadsheetViews();
    };
  });
  refs.schemaFieldsList.querySelectorAll('[data-prop]').forEach(input => {
    input.onchange = () => {
      if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') markCurrentSourceDirty();
      const row = input.closest('[data-schema-field-index]');
      const oldName = row?.dataset.schemaFieldName || '';
      let effectiveName = oldName;
      if (input.dataset.prop === 'name') {
        const nextName = input.value.trim();
        const systemKey = inferredSystemField(nextName);
        if (systemKey) {
          const systemSelect = row.querySelector('[data-prop="system_field"]');
          if (systemSelect && !systemSelect.value) systemSelect.value = systemKey;
          input.value = safeLogicalFieldName(nextName);
        }
        const effectiveNextName = input.value.trim();
        if (oldName && effectiveNextName && oldName !== effectiveNextName && state.schemaMappingDraft?.field_map?.[oldName]) {
          state.schemaMappingDraft.field_map[effectiveNextName] = state.schemaMappingDraft.field_map[oldName];
          delete state.schemaMappingDraft.field_map[oldName];
          row.dataset.schemaFieldName = effectiveNextName;
        }
        effectiveName = effectiveNextName || oldName;
      }
      if (input.dataset.prop === 'destination' && effectiveName) {
        const mapping = mappingForField(effectiveName);
        if (mapping.kind && mapping.kind !== 'manual' && mapping.ref) {
          setMappingForField(effectiveName, { ...mapping, destination: input.value || 'fields' });
        }
      }
      if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
        const sheetName = xlsxSheetNameFallback();
        if (sheetName) {
          if (!state.xlsxSheetMappings || typeof state.xlsxSheetMappings !== 'object') state.xlsxSheetMappings = {};
          state.xlsxSheetMappings[sheetName] = {
            ...currentXlsxSheetMapping(),
            fields: collectSchemaFields(),
          };
        }
      }
      syncSchemaHeader();
      renderSchemaInspector();
      renderSchemaPreview();
      renderSchemaMappingList();
    };
  });
  if (currentFieldIndex() !== null && !currentFieldRows()[currentFieldIndex()]) {
    state.currentSchemaFieldIndex = null;
  }
  syncSchemaHeader();
  renderSchemaInspector();
  renderSchemaMappingList();
  renderExtractionWorkspace();
}

export function collectSchemaFields() {
  const rows = Array.from(refs.schemaFieldsList?.querySelectorAll('.schema-field-row:not(.schema-field-row--head)') || []);
  return rows.map(row => {
    const get = prop => row.querySelector(`[data-prop="${prop}"]`);
    const validationRaw = get('validation')?.value || '{}';
    let validation = {};
    try {
      validation = JSON.parse(validationRaw || '{}');
    } catch {
      validation = { invalid: true, raw: validationRaw };
    }
    return serializeContractField({
      name: get('name')?.value.trim() || '',
      label: get('label')?.value.trim() || get('name')?.value.trim() || '',
      field_type: get('field_type')?.value || 'text',
      required: Boolean(get('required')?.checked),
      description: get('description')?.value.trim() || '',
      system_field: get('system_field')?.value || '',
      destination: get('destination')?.value || 'fields',
      validation,
    });
  }).filter(field => field.name);
}

export function currentOutputFields() {
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
    const sheetName = xlsxSheetNameFallback();
    const fromState = state.xlsxSheetMappings?.[sheetName]?.fields;
    if (Array.isArray(fromState)) return fromState.map(serializeContractField).filter(field => field.name);
    const fromMapping = currentXlsxSheetMapping().fields || [];
    return Array.isArray(fromMapping) ? fromMapping.map(serializeContractField).filter(field => field.name) : [];
  }
  return collectSchemaFields();
}

function setCurrentOutputFields(fields = []) {
  if (state.currentParsed?.source_type !== 'xlsx') {
    renderSchemaFieldsEditor(fields);
    return;
  }
  const sheetName = xlsxSheetNameFallback();
  if (!sheetName) return;
  if (!state.xlsxSheetMappings || typeof state.xlsxSheetMappings !== 'object') state.xlsxSheetMappings = {};
  const base = currentXlsxSheetMapping();
  state.xlsxSheetMappings[sheetName] = {
    ...base,
    fields: fields.map(serializeContractField).filter(field => field.name),
  };
  renderSchemaFieldsEditor(state.xlsxSheetMappings[sheetName].fields);
  renderSchemaMappingList();
  renderExtractionWorkspace();
}

function commitSchemaFieldState(fields = activeSchemaFields(), touchedIndex = null) {
  const nextFields = fields.map(serializeContractField).filter(field => field.name);
  state.currentSchemaFieldIndex = Number.isInteger(touchedIndex) && nextFields[touchedIndex] ? touchedIndex : null;
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
    setCurrentOutputFields(nextFields);
  } else {
    renderSchemaFieldsEditor(nextFields);
  }
  renderSchemaInspector();
  renderSchemaMappingList();
  renderSchemaPreview();
  renderSchemaFieldMapper();
  renderExtractionWorkspace();
}


function selectedColumnDescriptor() {
  normalizeSelection();
  const col = state.xlsxSelection.columns[0] ?? state.xlsxSelection.cells[0]?.col;
  if (col === undefined || col === null) return null;
  return {
    index: Number(col),
    letter: spreadsheetColumnName(col),
    header: columnHeaderLabel(col),
  };
}

function selectedCellDescriptor() {
  normalizeSelection();
  const cell = state.xlsxSelection.cells[0];
  if (!cell) return null;
  const row = Number(cell.row);
  const col = Number(cell.col);
  const rows = sheetRows();
  return {
    row,
    col,
    address: `${spreadsheetColumnName(col)}${row + 1}`,
    value: String((rows[row] || [])[col] ?? '').trim(),
  };
}

export function slugifyFieldName(value, fallback = 'field') {
  const translit = String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[аә]/g, 'a').replace(/[б]/g, 'b').replace(/[в]/g, 'v')
    .replace(/[гғ]/g, 'g').replace(/[д]/g, 'd').replace(/[еёэ]/g, 'e')
    .replace(/[ж]/g, 'zh').replace(/[з]/g, 'z').replace(/[иі]/g, 'i')
    .replace(/[й]/g, 'y').replace(/[кқ]/g, 'k').replace(/[л]/g, 'l')
    .replace(/[м]/g, 'm').replace(/[нң]/g, 'n').replace(/[оө]/g, 'o')
    .replace(/[п]/g, 'p').replace(/[р]/g, 'r').replace(/[с]/g, 's')
    .replace(/[т]/g, 't').replace(/[уұү]/g, 'u').replace(/[ф]/g, 'f')
    .replace(/[хһ]/g, 'h').replace(/[ц]/g, 'ts').replace(/[ч]/g, 'ch')
    .replace(/[ш]/g, 'sh').replace(/[щ]/g, 'shch').replace(/[ы]/g, 'y')
    .replace(/[ьъ]/g, '').replace(/[ю]/g, 'yu').replace(/[я]/g, 'ya');
  const slug = translit.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48);
  return slug || fallback;
}


function normalizeStableFieldName(value, fallback = 'field') {
  const slug = slugifyFieldName(value, fallback).replace(/^_+|_+$/g, '').slice(0, 56);
  return slug || fallback;
}

function uniqueSchemaFieldName(base, fields = collectSchemaFields(), ignoreIndex = -1) {
  const normalizedBase = normalizeStableFieldName(base, 'field');
  const used = new Set((fields || [])
    .map((field, index) => index === ignoreIndex ? '' : String(field?.name || '').trim())
    .filter(Boolean));
  if (!used.has(normalizedBase)) return normalizedBase;
  let counter = 2;
  while (used.has(`${normalizedBase}_${counter}`)) counter += 1;
  return `${normalizedBase}_${counter}`;
}

export function semanticFieldNameFromHeader(label, colIndex) {
  const raw = String(label || '').trim();
  if (/^№|номер$/i.test(raw)) return 'row_number';
  if (/группа.*образователь/i.test(raw)) return 'program_group';
  if (/^код$/i.test(raw) || /код.*(оп|образователь|программ)/i.test(raw) || /^шифр$/i.test(raw) || /^code$/i.test(raw)) return 'program_code';
  if (/наимен.*(образователь|программ|групп)/i.test(raw) || /^образовательная программа$/i.test(raw)) return 'program_name';
  if (/очн.*пол.*грант/i.test(raw)) return 'full_time_grant_score';
  if (/очн.*пол.*плат/i.test(raw)) return 'full_time_paid_score';
  if (/сокращ.*грант/i.test(raw)) return 'shortened_grant_score';
  if (/сокращ.*плат/i.test(raw)) return 'shortened_paid_score';
  if (/учебн.*год/i.test(raw) || /year/i.test(raw)) return 'academic_year';
  return slugifyFieldName(raw, `field_${spreadsheetColumnName(colIndex).toLowerCase()}`);
}

function columnSampleValues(colIndex, limit = 24) {
  const rows = sheetRows();
  const col = Number(colIndex);
  if (!Number.isFinite(col)) return [];
  return dataRowsFromProfile()
    .filter(row => row >= currentDataStartRowIndex())
    .slice(0, limit)
    .map(row => String((rows[row] || [])[col] ?? '').trim())
    .filter(Boolean);
}

function looksLikeProgramCode(value = '') {
  const clean = String(value || '').trim();
  return /^(?:[A-ZА-Я]d{2,4}|d[A-ZА-Я]d{2,4}|[A-ZА-Я]{1,3}d{2,4})/i.test(clean);
}

export function semanticFieldNameForColumn(label, colIndex) {
  const raw = String(label || '').trim();
  const lower = raw.toLowerCase();
  const samples = columnSampleValues(colIndex);
  const programCodeHits = samples.filter(looksLikeProgramCode).length;
  const programCodeRatio = samples.length ? programCodeHits / samples.length : 0;

  if (/^№|номер$/i.test(raw)) {
    return programCodeRatio >= 0.5 ? 'program_code' : 'row_number';
  }
  if (/^код$/i.test(raw) || /код.*(оп|образователь|программ)/i.test(raw) || /^шифр$/i.test(raw) || /^code$/i.test(raw)) {
    return 'program_code';
  }
  if (/группа.*образователь/i.test(raw)) return 'program_group';
  if (/наимен.*(образователь|программ|групп)/i.test(raw) || lower === 'образовательная программа') return 'program_name';
  return semanticFieldNameFromHeader(raw, colIndex);
}

export function stableMetadataFieldName(cell, fields = collectSchemaFields()) {
  const row = Number(cell?.row);
  const value = String(cell?.value || '').trim();
  const lower = value.toLowerCase();
  let base = 'metadata_field';
  if (/https?:\/\//i.test(value) || /^источник\s*:/i.test(value) || lower.includes('источник')) base = 'source_url';
  else if (/^примеч/i.test(value) || lower.includes('note')) base = 'note';
  else if (Number.isFinite(row) && row < currentHeaderRowIndex()) base = row === 0 ? 'document_title' : `metadata_row_${row + 1}`;
  else if (/учебн.*год/i.test(value) || /20\d{2}\s*[-–]\s*20\d{2}/.test(value)) base = 'academic_year';
  return uniqueSchemaFieldName(base, fields);
}

function schemaFieldLabelForCell(cell) {
  const value = String(cell?.value || '').trim();
  if (!value) return String(cell?.address || 'Метаданные');
  if (/^источник\s*:/i.test(value)) return 'Источник';
  if (/^примеч/i.test(value)) return 'Примечание';
  if (Number.isFinite(Number(cell?.row)) && Number(cell.row) === 0) return 'Заголовок документа';
  return value;
}

function schemaColumnFieldIndex(fields, colIndex) {
  const letter = spreadsheetColumnName(colIndex).toUpperCase();
  const mappedName = findMappedFieldName('column', letter);
  return mappedName ? fieldIndexByName(fields, mappedName) : -1;
}

function schemaCellFieldIndex(fields, address) {
  const mappedName = findMappedFieldName('cell', String(address || '').toUpperCase());
  return mappedName ? fieldIndexByName(fields, mappedName) : -1;
}

function upsertSchemaFieldList(nextFields, touchedIndex = null) {
  const fields = nextFields.map(serializeContractField).filter(field => field.name);
  state.currentSchemaFieldIndex = Number.isInteger(touchedIndex) ? touchedIndex : null;
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') {
    setCurrentOutputFields(fields);
  } else {
    renderSchemaFieldsEditor(fields);
    if (Number.isInteger(touchedIndex)) setCurrentFieldIndex(touchedIndex);
  }
  renderSchemaPreview();
  renderSchemaFieldMapper();
}
export function parseSpreadsheetAddress(address = '') {
  const match = String(address || '').trim().toUpperCase().match(/^([A-Z]+)(\d+)$/);
  if (!match) return null;
  let col = 0;
  for (const char of match[1]) col = col * 26 + (char.charCodeAt(0) - 64);
  const row = Number(match[2]);
  if (!Number.isFinite(row) || row < 1 || col < 1) return null;
  return { row: row - 1, col: col - 1, address: `${match[1]}${row}` };
}

function addMetadataCellToProfile(cell) {
  const normalized = Number.isFinite(Number(cell?.row)) && Number.isFinite(Number(cell?.col))
    ? { row: Number(cell.row), col: Number(cell.col) }
    : parseSpreadsheetAddress(cell?.address || cell?.source_ref || '');
  if (!normalized) return;
  const profile = ensureTableProfile();
  profile.metaRows = uniqueSortedNumbers([...profile.metaRows, normalized.row]);
  profile.metaCells = mergeCells(profile.metaCells, [normalized]);
}

function syncTableProfileFromMapping() {
  if (state.currentParsed?.source_type !== 'xlsx') return;
  Object.values(state.schemaMappingDraft?.field_map || {}).forEach(rawMapping => {
    const mapping = normalizeFieldMapping(rawMapping);
    if (mapping.kind !== 'cell') return;
    if (!['metadata', 'title', 'text', 'embedding_text'].includes(mapping.destination)) return;
    const cell = parseSpreadsheetAddress(mapping.ref);
    if (cell) addMetadataCellToProfile(cell);
  });
}

function inferColumnFieldType(colIndex) {
  const rows = sheetRows();
  const col = Number(colIndex);
  if (!Number.isFinite(col)) return 'text';
  const candidates = dataRowsFromProfile()
    .filter(row => row >= currentDataStartRowIndex())
    .slice(0, 24)
    .map(row => String((rows[row] || [])[col] ?? '').trim())
    .filter(Boolean);
  if (!candidates.length) return 'text';
  const numeric = candidates.filter(value => /^-?\d+(?:[,.]\d+)?$/.test(value.replace(/\s+/g, ''))).length;
  return numeric / candidates.length >= 0.75 ? 'number' : 'text';
}

function upsertSchemaColumnFieldsFromHeader() {
  const rows = sheetRows();
  const headerRow = rows[currentHeaderRowIndex()] || [];
  if (!headerRow.length || !refs.schemaFieldsList) return;
  const fields = currentOutputFields();
  const hasColumnMappings = Object.values(state.schemaMappingDraft?.field_map || {}).some(raw => normalizeFieldMapping(raw).kind === 'column');
  const reusableFieldIndexes = fields
    .map((field, index) => ({ field, index }))
    .filter(({ field }) => (field.destination || 'fields') === 'fields')
    .map(({ index }) => index);
  let changed = false;
  let firstTouchedIndex = null;
  headerRow.forEach((rawHeader, col) => {
    if (isEffectivelyBlankCell(rawHeader)) return;
    const label = String(rawHeader ?? '').trim();
    const letter = spreadsheetColumnName(col);
    const existingMappedName = findMappedFieldName('column', letter);
    const reusableIndex = !hasColumnMappings && reusableFieldIndexes[col] !== undefined ? reusableFieldIndexes[col] : -1;
    const index = existingMappedName ? fieldIndexByName(fields, existingMappedName) : reusableIndex;
    if (index >= 0) {
      const existing = fields[index];
      fields[index] = {
        ...existing,
        label: existing.label || label,
        field_type: existing.field_type || inferColumnFieldType(col),
        destination: existing.destination || 'fields',
        system_field: existing.system_field || inferredSystemField(existing.name),
        validation: existing.validation || {},
      };
      setMappingForField(fields[index].name, { kind: 'column', ref: letter, destination: fields[index].destination || 'fields' });
      firstTouchedIndex ??= index;
      changed = true;
    } else {
      const semanticName = semanticFieldNameForColumn(label, col);
      const name = logicalFieldNameFromSemantic(semanticName, fields);
      fields.push({
        name,
        label,
        field_type: inferColumnFieldType(col),
        required: true,
        description: '',
        system_field: inferredSystemField(semanticName),
        destination: 'fields',
        validation: {},
      });
      setMappingForField(name, { kind: 'column', ref: letter, destination: 'fields' });
      firstTouchedIndex ??= fields.length - 1;
      changed = true;
    }
  });
  if (!changed) return;
  upsertSchemaFieldList(fields, firstTouchedIndex);
}

export function createSchemaFieldFromSelectedColumn() {
  markCurrentSourceDirty();
  const column = selectedColumnDescriptor();
  if (!column) return;
  const fields = currentOutputFields();
  const existingMappedName = findMappedFieldName('column', column.letter);
  const index = existingMappedName ? fieldIndexByName(fields, existingMappedName) : -1;
  let touchedIndex = index;
  if (index >= 0) {
    const existing = fields[index];
    fields[index] = {
      ...existing,
      label: existing.label || column.header,
      field_type: existing.field_type || inferColumnFieldType(column.index),
      destination: existing.destination || 'fields',
      system_field: existing.system_field || inferredSystemField(existing.name),
      validation: existing.validation || {},
    };
    setMappingForField(fields[index].name, { kind: 'column', ref: column.letter, destination: fields[index].destination || 'fields' });
  } else {
    const semanticName = semanticFieldNameForColumn(column.header, column.index);
    const name = logicalFieldNameFromSemantic(semanticName, fields);
    fields.push({
      name,
      label: column.header,
      field_type: inferColumnFieldType(column.index),
      required: false,
      description: '',
      system_field: inferredSystemField(semanticName),
      destination: 'fields',
      validation: {},
    });
    setMappingForField(name, { kind: 'column', ref: column.letter, destination: 'fields' });
    touchedIndex = fields.length - 1;
  }
  state.currentSchemaFieldIndex = touchedIndex;
  upsertSchemaFieldList(fields, touchedIndex);
  setSchemaDesignerTab('fields');
  refreshSpreadsheetViews();
}

function upsertSchemaMetadataFieldsFromCells(cells = []) {
  const rows = sheetRows();
  const fields = currentOutputFields();
  let touchedIndex = null;
  cells.forEach(rawCell => {
    const row = Number(rawCell.row);
    const col = Number(rawCell.col);
    if (!Number.isFinite(row) || !Number.isFinite(col)) return;
    const cell = {
      row,
      col,
      address: `${spreadsheetColumnName(col)}${row + 1}`,
      value: String((rows[row] || [])[col] ?? '').trim(),
    };
    if (!cell.value) return;
    addMetadataCellToProfile(cell);
    const existingMappedName = findMappedFieldName('cell', cell.address);
    const index = existingMappedName ? fieldIndexByName(fields, existingMappedName) : -1;
    if (index >= 0) {
      const existing = fields[index];
      fields[index] = {
        ...existing,
        label: existing.label || schemaFieldLabelForCell(cell),
        field_type: existing.field_type || (/^Источник[:\s]|https?:\/\//i.test(cell.value) ? 'url' : 'text'),
        destination: existing.destination || 'metadata',
        validation: existing.validation || {},
      };
      setMappingForField(fields[index].name, { kind: 'cell', ref: cell.address, destination: fields[index].destination || 'metadata' });
      touchedIndex = index;
    } else {
      const name = stableMetadataFieldName(cell, fields);
      fields.push({
        name,
        label: schemaFieldLabelForCell(cell),
        field_type: /^Источник[:\s]|https?:\/\//i.test(cell.value) ? 'url' : 'text',
        required: false,
        description: '',
        destination: 'metadata',
        validation: {},
      });
      setMappingForField(name, { kind: 'cell', ref: cell.address, destination: 'metadata' });
      touchedIndex = fields.length - 1;
    }
  });
  if (touchedIndex !== null) upsertSchemaFieldList(fields, touchedIndex);
  return touchedIndex;
}

export function createSchemaMetadataFieldFromSelectedCell() {
  markCurrentSourceDirty();
  const cell = selectedCellDescriptor();
  if (!cell) return;
  const touchedIndex = upsertSchemaMetadataFieldsFromCells([{ row: cell.row, col: cell.col }]);
  if (touchedIndex !== null) {
    state.currentSchemaFieldIndex = touchedIndex;
    setSchemaDesignerTab('fields');
  }
  refreshSpreadsheetViews();
}

function schemaValidationIssues(fields = collectSchemaFields()) {
  const issues = [];
  const names = new Set();
  if (!document.getElementById('schemaName')?.value.trim()) issues.push('Имя схемы не заполнено.');
  fields.forEach((field, index) => {
    if (names.has(field.name)) issues.push(`Дублирующееся поле: ${field.name}`);
    names.add(field.name);
    if (field.required && !field.name) issues.push(`Поле ${index + 1} обязательное, но без имени.`);
    if (field.validation?.invalid) issues.push(`У поля ${field.name || index + 1} некорректный JSON валидации.`);
  });
  return issues;
}

export function renderSchemaInspector() {
  const fields = activeSchemaFields();
  const selectedIndex = currentFieldIndex();
  const field = selectedIndex === null ? null : fields[selectedIndex];
  const status = document.getElementById('schemaInspectorStatus');
  const summary = document.getElementById('schemaInspectorSummary');
  const validation = document.getElementById('schemaValidationList');
  const selection = document.getElementById('schemaSelectionSummary');
  if (status) status.textContent = state.currentSchemaIndex === null ? 'новая схема' : 'редактирование';
  if (summary) {
    const name = document.getElementById('schemaName')?.value.trim() || 'схема без имени';
    const handler = document.getElementById('schemaHandler')?.value || 'generic_text';
    const mappings = Object.keys(currentSchemaMappingPayload().field_map || {}).length;
    summary.innerHTML = `<strong>${escapeHtml(name)}</strong><br>обработчик: ${escapeHtml(handler)} · полей: ${fields.length} · привязок: ${mappings}<br>${escapeHtml(document.getElementById('schemaDescription')?.value || 'Нет описания')}`;
  }
  if (validation) {
    const issues = schemaValidationIssues(fields);
    validation.innerHTML = issues.length
      ? issues.map(issue => `<div class="schema-validation-item schema-validation-item--warn">• ${escapeHtml(issue)}</div>`).join('')
      : '<div class="schema-validation-item">• Черновик схемы структурно корректен.</div>';
  }
  if (selection) selection.textContent = selectionSummaryText();
  const empty = document.getElementById('schemaFieldInspectorEmpty');
  const form = document.getElementById('schemaFieldInspectorForm');
  const pill = document.getElementById('schemaFieldInspectorPill');
  if (pill) pill.textContent = field ? `поле ${selectedIndex + 1}` : 'нет поля';
  if (!field) {
    empty?.classList.remove('hidden');
    form?.classList.add('hidden');
    renderXlsxContextInspector();
    return;
  }
  empty?.classList.add('hidden');
  form?.classList.remove('hidden');
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = Boolean(value);
    else el.value = value ?? '';
  };
  renderSelectOptions(document.getElementById('schemaInspectorFieldSystem'), systemFieldOptions().map(option => ({ value: option.key, label: option.label })), field.system_field || inferredSystemField(field.name));
  setValue('schemaInspectorFieldName', field.name);
  setValue('schemaInspectorFieldLabel', field.label);
  setValue('schemaInspectorFieldType', field.field_type || 'text');
  setValue('schemaInspectorFieldDestination', field.destination || 'fields');
  const mapping = mappingForField(field.name);
  const mappingSummary = document.getElementById('schemaInspectorFieldMapping');
  if (mappingSummary) mappingSummary.textContent = mapping.kind && mapping.ref ? `${mappingKindLabel(mapping.kind)}: ${mapping.ref} → ${destinationLabel(mapping.destination || field.destination || 'fields')}` : 'не привязано';
  setValue('schemaInspectorFieldValidation', JSON.stringify(field.validation || {}));
  setValue('schemaInspectorFieldRequired', field.required);
  renderXlsxContextInspector();
}

export function applySchemaFieldInspector() {
  if (state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema') markCurrentSourceDirty();
  const index = currentFieldIndex();
  if (index === null) return;
  const fields = activeSchemaFields();
  const field = fields[index];
  if (!field) return;
  const get = id => document.getElementById(id);
  let validation = {};
  try {
    validation = JSON.parse(get('schemaInspectorFieldValidation')?.value || '{}');
  } catch {
    validation = { invalid: true, raw: get('schemaInspectorFieldValidation')?.value || '' };
  }
  const oldName = field.name;
  const requestedName = get('schemaInspectorFieldName')?.value.trim() || field.name;
  const requestedSystem = inferredSystemField(requestedName);
  const nextName = requestedSystem ? safeLogicalFieldName(requestedName) : requestedName;
  if (requestedSystem && get('schemaInspectorFieldName')) get('schemaInspectorFieldName').value = nextName;
  const nextDestination = get('schemaInspectorFieldDestination')?.value || 'fields';
  fields[index] = {
    ...field,
    name: nextName,
    label: get('schemaInspectorFieldLabel')?.value.trim() || nextName || field.label,
    field_type: get('schemaInspectorFieldType')?.value || 'text',
    system_field: get('schemaInspectorFieldSystem')?.value || requestedSystem || field.system_field || '',
    destination: nextDestination,
    required: Boolean(get('schemaInspectorFieldRequired')?.checked),
    validation,
  };
  if (oldName && nextName && oldName !== nextName && state.schemaMappingDraft?.field_map?.[oldName]) {
    state.schemaMappingDraft.field_map[nextName] = state.schemaMappingDraft.field_map[oldName];
    delete state.schemaMappingDraft.field_map[oldName];
  }
  const mapping = mappingForField(nextName);
  if (mapping.kind && mapping.kind !== 'manual' && mapping.ref) {
    setMappingForField(nextName, { ...mapping, destination: nextDestination });
  }
  commitSchemaFieldState(fields, index);
}

export function selectSchemaFieldByName(fieldName) {
  const fields = activeSchemaFields();
  const index = fieldIndexByName(fields, fieldName);
  if (index < 0) return false;
  state.currentSchemaFieldIndex = index;
  if (!(state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema')) {
    setCurrentFieldIndex(index);
  } else {
    renderSchemaInspector();
    renderSchemaMappingList();
    renderSchemaFieldMapper();
    renderExtractionWorkspace();
  }
  return true;
}

export function mappedSchemaFieldNameFromSelection() {
  normalizeSelection();
  let fieldName = null;
  const col = state.xlsxSelection.columns[0];
  if (Number.isFinite(Number(col))) fieldName = findMappedFieldName('column', spreadsheetColumnName(Number(col)));
  if (!fieldName && state.xlsxSelection.cells[0]) {
    const cell = state.xlsxSelection.cells[0];
    fieldName = findMappedFieldName('cell', `${spreadsheetColumnName(Number(cell.col))}${Number(cell.row) + 1}`);
  }
  return fieldName || null;
}

export function selectMappedSchemaFieldFromSelection() {
  const fieldName = mappedSchemaFieldNameFromSelection();
  return fieldName ? selectSchemaFieldByName(fieldName) : false;
}

export function resetMappedSchemaFieldFromSelection() {
  const fieldName = mappedSchemaFieldNameFromSelection();
  if (!fieldName) return false;
  resetSchemaFieldMappingByName(fieldName);
  return true;
}

export function deleteMappedSchemaFieldFromSelection() {
  const fieldName = mappedSchemaFieldNameFromSelection();
  if (!fieldName) return false;
  deleteSchemaFieldByName(fieldName);
  return true;
}

export function bindSelectionToSelectedSchemaField() {
  const index = currentFieldIndex();
  if (index === null) return;
  const fields = activeSchemaFields();
  const field = fields[index];
  if (!field?.name) return;
  normalizeSelection();
  const col = state.xlsxSelection.columns[0];
  const cell = state.xlsxSelection.cells[0];
  if (Number.isFinite(Number(col))) {
    setMappingForField(field.name, {
      kind: 'column',
      ref: spreadsheetColumnName(Number(col)),
      destination: field.destination || 'fields',
    });
  } else if (cell && Number.isFinite(Number(cell.row)) && Number.isFinite(Number(cell.col))) {
    const ref = `${spreadsheetColumnName(Number(cell.col))}${Number(cell.row) + 1}`;
    setMappingForField(field.name, {
      kind: 'cell',
      ref,
      destination: field.destination || 'metadata',
    });
    if (['metadata', 'title', 'text', 'embedding_text'].includes(field.destination || 'metadata')) {
      addMetadataCellToProfile({ row: Number(cell.row), col: Number(cell.col), address: ref });
    }
  } else {
    return;
  }
  commitSchemaFieldState(fields, index);
  refreshSpreadsheetViews();
}

export function resetSchemaFieldMappingByName(fieldName) {
  if (!fieldName) return;
  markCurrentSourceDirty();
  removeMappingForField(fieldName);
  if (state.schemaFieldDraftValues && Object.prototype.hasOwnProperty.call(state.schemaFieldDraftValues, fieldName)) {
    delete state.schemaFieldDraftValues[fieldName];
  }
  const fields = activeSchemaFields();
  const index = fieldIndexByName(fields, fieldName);
  commitSchemaFieldState(fields, index >= 0 ? index : null);
  refreshSpreadsheetViews();
}

export function resetSelectedSchemaFieldMapping() {
  const fields = activeSchemaFields();
  const index = currentFieldIndex();
  const field = index === null ? null : fields[index];
  if (!field?.name) return;
  resetSchemaFieldMappingByName(field.name);
}

export function deleteSchemaFieldByName(fieldName) {
  if (!fieldName) return;
  markCurrentSourceDirty();
  const fields = activeSchemaFields();
  const index = fieldIndexByName(fields, fieldName);
  if (index < 0) return;
  const removed = fields.splice(index, 1)[0];
  removeMappingForField(removed?.name);
  if (state.schemaFieldDraftValues && removed?.name) delete state.schemaFieldDraftValues[removed.name];
  commitSchemaFieldState(fields, null);
  refreshSpreadsheetViews();
}

export function deleteSelectedSchemaField() {
  const fields = activeSchemaFields();
  const index = currentFieldIndex();
  const field = index === null ? null : fields[index];
  if (!field?.name) return;
  deleteSchemaFieldByName(field.name);
}

export function setSchemaDesignerTab(value) {
  document.querySelectorAll('[data-schema-designer-tab]').forEach(tab => tab.classList.toggle('active', tab.dataset.schemaDesignerTab === value));
  document.querySelectorAll('[data-schema-designer-panel]').forEach(panel => panel.classList.toggle('hidden', panel.dataset.schemaDesignerPanel !== value));
  if (value === 'mapping') renderSchemaTablePreview();
  if (value === 'preview') renderSchemaPreview();
}

export function renderSchemaTablePreview() {
  const target = document.getElementById('schemaTablePreview');
  const hint = document.getElementById('schemaNoSourceHint');
  if (!target) return;
  if (state.currentParsed?.source_type === 'xlsx') {
    target.classList.remove('hidden');
    hint?.classList.add('hidden');
    renderSpreadsheetTable(target, 'schema');
  } else {
    target.innerHTML = '';
    target.classList.add('hidden');
    hint?.classList.remove('hidden');
  }
  syncSchemaHeader();
}

export function renderSchemaPreview() {
  const output = document.getElementById('schemaPreviewOutput');
  if (!output) return;
  const fields = collectSchemaFields();
  const payload = {
    schema: {
      name: document.getElementById('schemaName')?.value.trim() || null,
      handler: document.getElementById('schemaHandler')?.value || null,
      enabled: document.getElementById('schemaEnabled')?.checked !== false,
      fields,
      mapping_template: currentSchemaMappingPayload(),
    },
    source_sample: state.currentSource ? {
      source_id: state.currentSource.source_id,
      source_type: state.currentParsed?.source_type || null,
      sheet: getCurrentSheet()?.sheet_title || null,
      table_profile: state.currentParsed?.source_type === 'xlsx' ? tableProfilePayload() : null,
      selection: state.currentParsed?.source_type === 'xlsx' ? selectionMetadata() : null,
    } : null,
    validation: schemaValidationIssues(fields),
  };
  output.textContent = JSON.stringify(payload, null, 2);
}

export function renderDomainEditor(index = state.currentDomainIndex) {
  const domains = state.catalog.domains || [];
  const normalizedIndex = index === null || index === undefined || index === '' ? null : Number(index);
  const domain = Number.isInteger(normalizedIndex) ? domains[normalizedIndex] : null;
  state.currentDomainIndex = domain ? normalizedIndex : null;
  renderSelectOptions(document.getElementById('domainSelect'), indexOptions(domains, 'Новый домен'), domain ? String(normalizedIndex) : '');
  renderSelectOptions(document.getElementById('domainDefaultSchema'), schemaOptions(), domain?.default_schema || '');
  document.getElementById('domainName').value = domain?.name || '';
  document.getElementById('domainDescription').value = domain?.description || '';
  document.getElementById('domainEnabled').checked = domain?.enabled !== false;
  const mode = document.getElementById('domainEditorMode');
  if (mode) mode.textContent = domain ? 'редактирование' : 'новый';
  const deleteButton = document.getElementById('deleteDomainBtn');
  if (deleteButton) deleteButton.disabled = !domain;
}

export function collectDomainPayload() {
  return {
    name: document.getElementById('domainName').value.trim(),
    description: document.getElementById('domainDescription').value.trim(),
    default_schema: document.getElementById('domainDefaultSchema').value || null,
    enabled: document.getElementById('domainEnabled').checked,
  };
}

export function renderSchemaEditor(index = state.currentSchemaIndex) {
  const schemas = state.catalog.schemas || [];
  const normalizedIndex = index === null || index === undefined || index === '' ? null : Number(index);
  const schema = Number.isInteger(normalizedIndex) ? schemas[normalizedIndex] : null;
  state.currentSchemaIndex = schema ? normalizedIndex : null;
  state.currentSchemaFieldIndex = null;
  const schemaName = document.getElementById('schemaName');
  const schemaDescription = document.getElementById('schemaDescription');
  const schemaHandler = document.getElementById('schemaHandler');
  const schemaEnabled = document.getElementById('schemaEnabled');
  state.schemaMappingDraft = normalizeSchemaMappingTemplate(schema || {});
  if (schemaName) schemaName.value = schema?.name || '';
  if (schemaDescription) schemaDescription.value = schema?.description || '';
  if (schemaHandler) schemaHandler.value = schema?.handler || 'generic_text';
  if (schemaEnabled) schemaEnabled.checked = schema?.enabled !== false;
  renderSchemaFieldsEditor(Array.isArray(schema?.fields) ? schema.fields.map(serializeContractField) : []);
  const mode = document.getElementById('schemaEditorMode');
  if (mode) mode.textContent = schema ? 'редактирование' : 'новый';
  const deleteButton = document.getElementById('deleteSchemaBtn');
  if (deleteButton) deleteButton.disabled = !schema;
  syncSchemaHeader();
  renderSchemaTablePreview();
  renderSchemaPreview();
  renderSchemaInspector();
  renderSchemaMappingList();
  renderExtractionWorkspace();
}

export function collectSchemaPayload() {
  return {
    name: document.getElementById('schemaName')?.value.trim() || '',
    handler: document.getElementById('schemaHandler')?.value || 'generic_text',
    description: document.getElementById('schemaDescription')?.value.trim() || '',
    enabled: document.getElementById('schemaEnabled')?.checked !== false,
    fields: collectSchemaFields(),
    mapping_template: currentSchemaMappingPayload(),
  };
}

export function renderSourceConfigControls() {
  if (!state.currentSource) return;
  const isXlsx = state.currentParsed?.source_type === 'xlsx';
  const sourceItem = Array.isArray(state.currentSource.items) && state.currentSource.items.length ? state.currentSource.items[0] : {};
  const config = isXlsx ? ensureCurrentSheetInspectorState() : {
    class_name: state.currentSource.class_name || sourceItem.domain || sourceItem.class_name || '',
    schema: state.currentSource.schema || state.currentSource.schema_name || sourceItem.entry_type || sourceItem.schema || sourceItem.schema_name || '',
    education_level: state.currentSource.education_level || sourceItem.education_level || '',
    language: state.currentSource.language || sourceItem.language || '',
    notes: state.currentSource.notes || sourceItem.notes || '',
  };
  state._sourceInspectorControlKey = isXlsx ? getSheetKey() : (state.currentSource.source_id || 'source');
  renderSelectOptions(document.getElementById('editorClass'), domainOptions(), config.class_name || '');
  renderSelectOptions(document.getElementById('editorSchema'), [{ value: '', label: 'Авто' }, ...schemaOptions(false)], config.schema || '');
  renderSelectOptions(document.getElementById('editorLevel'), EDUCATION_LEVELS, config.education_level || '');
  renderSelectOptions(document.getElementById('editorLang'), LANGUAGES, config.language || '');
  document.getElementById('editorNotes').value = config.notes || '';
}

export function getCurrentSheet() {
  const sheets = state.currentParsed?.sheets || [];
  return sheets.find(item => item.sheet_title === state.currentSheetName) || sheets[0] || null;
}

export function getSheetKeyFor(sheetName = '') {
  const activeSheet = xlsxSheetNameFallback(sheetName) || 'sheet';
  return `${state.currentSource?.source_id || 'источник'}::${activeSheet}`;
}

export function getSheetKey() {
  return getSheetKeyFor();
}

export function normalizeSelection() {
  if (!state.xlsxSelection || typeof state.xlsxSelection !== 'object') {
    state.xlsxSelection = { mode: 'cell', cells: [], rows: [], columns: [] };
  }
  state.xlsxSelection.mode = state.xlsxSelection.mode || 'cell';
  state.xlsxSelection.cells = Array.isArray(state.xlsxSelection.cells) ? state.xlsxSelection.cells : [];
  state.xlsxSelection.rows = Array.isArray(state.xlsxSelection.rows) ? state.xlsxSelection.rows.map(Number).filter(Number.isFinite) : [];
  state.xlsxSelection.columns = Array.isArray(state.xlsxSelection.columns) ? state.xlsxSelection.columns.map(Number).filter(Number.isFinite) : [];
}

export function spreadsheetColumnName(index) {
  let value = Number(index) + 1;
  let label = '';
  while (value > 0) {
    const mod = (value - 1) % 26;
    label = String.fromCharCode(65 + mod) + label;
    value = Math.floor((value - mod) / 26);
  }
  return label;
}

export function spreadsheetColumnIndexFromName(name = '') {
  const letters = String(name || '').trim().toUpperCase().match(/^[A-Z]+/)?.[0] || '';
  if (!letters) return null;
  let value = 0;
  for (const ch of letters) {
    value = value * 26 + (ch.charCodeAt(0) - 64);
  }
  return value > 0 ? value - 1 : null;
}

export function sheetRows() {
  return getCurrentSheet()?.rows || [];
}

function isEffectivelyBlankCell(value) {
  const text = String(value ?? '').trim();
  return !text || text === 'ø' || text === 'Ø' || text === '∅' || /^null$/i.test(text) || /^undefined$/i.test(text);
}

export function lastMeaningfulColumnIndex(rows = sheetRows()) {
  return rows.reduce((max, row) => {
    if (!Array.isArray(row)) return max;
    for (let index = row.length - 1; index >= 0; index -= 1) {
      if (!isEffectivelyBlankCell(row[index])) return Math.max(max, index);
    }
    return max;
  }, -1);
}

export function maxColumnCount(rows = sheetRows()) {
  return Math.max(0, lastMeaningfulColumnIndex(rows) + 1);
}

function headerIndex() {
  return Math.max(0, Number(document.getElementById('headerRowSelect')?.value || 1) - 1);
}

function dataStartIndex() {
  return Math.max(0, Number(document.getElementById('dataStartRowSelect')?.value || 2) - 1);
}

export function columnHeaderLabel(index) {
  const rows = sheetRows();
  const header = rows[headerIndex()] || [];
  const value = header[index];
  return isEffectivelyBlankCell(value) ? `Колонка ${index + 1}` : String(value ?? '').trim();
}

function isSameCell(a, b) {
  return Number(a.row) === Number(b.row) && Number(a.col) === Number(b.col);
}

function uniqueSortedNumbers(values) {
  return Array.from(new Set(values.map(Number).filter(Number.isFinite))).sort((a, b) => a - b);
}

export function toggleNumber(values, value) {
  const normalized = uniqueSortedNumbers(values);
  const number = Number(value);
  return normalized.includes(number) ? normalized.filter(item => item !== number) : [...normalized, number].sort((a, b) => a - b);
}

export function toggleCell(row, col) {
  normalizeSelection();
  const cell = { row: Number(row), col: Number(col) };
  const exists = state.xlsxSelection.cells.some(item => isSameCell(item, cell));
  state.xlsxSelection.cells = exists
    ? state.xlsxSelection.cells.filter(item => !isSameCell(item, cell))
    : [...state.xlsxSelection.cells, cell].sort((a, b) => a.row - b.row || a.col - b.col);
}

export function selectedCellKey(row, col) {
  return `${Number(row)}:${Number(col)}`;
}

export function selectedCellSet() {
  normalizeSelection();
  return new Set(state.xlsxSelection.cells.map(cell => selectedCellKey(cell.row, cell.col)));
}

function ensureColumnWidths() {
  if (!state.xlsxColumnWidths || typeof state.xlsxColumnWidths !== 'object') state.xlsxColumnWidths = {};
  const key = getSheetKey();
  if (!state.xlsxColumnWidths[key]) state.xlsxColumnWidths[key] = {};
  return state.xlsxColumnWidths[key];
}

export function getColumnWidth(index) {
  const widths = ensureColumnWidths();
  return Number(widths[index] || 180);
}

export function setColumnWidth(index, width) {
  const widths = ensureColumnWidths();
  widths[index] = Math.max(72, Math.min(720, Math.round(Number(width) || 180)));
}

function removeNumbers(values = [], removals = []) {
  const removalSet = new Set((removals || []).map(Number).filter(Number.isFinite));
  return uniqueSortedNumbers(values).filter(value => !removalSet.has(Number(value)));
}

function normalizeCellList(cells = []) {
  const map = new Map();
  (Array.isArray(cells) ? cells : []).forEach(cell => {
    const row = Number(cell.row);
    const col = Number(cell.col ?? cell.column);
    if (!Number.isFinite(row) || !Number.isFinite(col)) return;
    map.set(selectedCellKey(row, col), { row, col });
  });
  return Array.from(map.values()).sort((a, b) => a.row - b.row || a.col - b.col);
}

function removeCellsInRows(cells = [], rows = []) {
  const rowSet = new Set((rows || []).map(Number).filter(Number.isFinite));
  if (!rowSet.size) return normalizeCellList(cells);
  return normalizeCellList(cells).filter(cell => !rowSet.has(Number(cell.row)));
}

function normalizeTableProfile(profile) {
  const safe = profile && typeof profile === 'object' ? profile : {};
  const ignoredRows = uniqueSortedNumbers(safe.ignoredRows || []);
  const footerRows = removeNumbers(safe.footerRows || [], ignoredRows);
  const metaRows = removeNumbers(safe.metaRows || [], [...ignoredRows, ...footerRows]);
  const dataRows = removeNumbers(safe.dataRows || [], [...ignoredRows, ...footerRows, ...metaRows]);
  return {
    metaCells: removeCellsInRows(safe.metaCells || [], [...ignoredRows, ...footerRows]),
    metaRows,
    dataRows,
    footerRows,
    ignoredRows,
  };
}

export function ensureTableProfile() {
  if (!state.xlsxTableProfiles || typeof state.xlsxTableProfiles !== 'object') state.xlsxTableProfiles = {};
  const key = getSheetKey();
  if (!state.xlsxTableProfiles[key]) state.xlsxTableProfiles[key] = { metaCells: [], metaRows: [], dataRows: [], footerRows: [], ignoredRows: [] };
  state.xlsxTableProfiles[key] = normalizeTableProfile(state.xlsxTableProfiles[key]);
  return state.xlsxTableProfiles[key];
}

function xlsxSheetNameFallback(sheetName = '') {
  return String(sheetName || state.currentSheetName || getCurrentSheet()?.sheet_title || '').trim();
}

function cloneXlsxSelection(selection = {}) {
  const safe = selection && typeof selection === 'object' ? selection : {};
  const rawCells = safe.cells || safe.selected_cells || [];
  const rawRows = safe.rows || safe.selected_rows || [];
  const rawColumns = safe.columns || safe.selected_columns || [];
  return {
    mode: safe.mode || safe.selection_mode || 'cell',
    cells: Array.isArray(rawCells) ? rawCells.map(cell => ({
      row: Number(cell.row),
      col: Number(cell.col ?? cell.column),
    })).filter(cell => Number.isFinite(cell.row) && Number.isFinite(cell.col)) : [],
    rows: Array.isArray(rawRows) ? rawRows.map(Number).filter(Number.isFinite) : [],
    columns: Array.isArray(rawColumns) ? rawColumns.map(item => typeof item === 'object' ? Number(item.index ?? item.column ?? item.col) : Number(item)).filter(Number.isFinite) : [],
  };
}

function sheetMappingForInspectorSeed(sheetName = '') {
  const name = xlsxSheetNameFallback(sheetName);
  const raw = state.xlsxSheetMappings?.[name] || {};
  return raw && typeof raw === 'object' ? raw : {};
}

function sourceInspectorDomValues(fallback = {}, key = getSheetKey()) {
  const canReadDom = state._sourceInspectorControlKey === key;
  const readSelect = (id, fallbackValue = '') => {
    const node = document.getElementById(id);
    if (!canReadDom || !node || !node.options?.length) return fallbackValue ?? '';
    return node.value ?? fallbackValue ?? '';
  };
  const readInput = (id, fallbackValue = '') => {
    const node = document.getElementById(id);
    if (!canReadDom || !node) return fallbackValue ?? '';
    return node.value ?? fallbackValue ?? '';
  };
  return {
    class_name: readSelect('editorClass', fallback.class_name || fallback.domain || ''),
    schema: readSelect('editorSchema', fallback.schema || (fallback.entry_type && fallback.entry_type !== 'table_facts' ? fallback.entry_type : '') || ''),
    education_level: readSelect('editorLevel', fallback.education_level || ''),
    language: readSelect('editorLang', fallback.language || ''),
    notes: readInput('editorNotes', fallback.notes || fallback.note || ''),
  };
}

export function ensureCurrentSheetInspectorState(sheetNameOverride = '') {
  if (!state.xlsxSheetInspectorStates || typeof state.xlsxSheetInspectorStates !== 'object') state.xlsxSheetInspectorStates = {};
  const sheetName = xlsxSheetNameFallback(sheetNameOverride);
  const key = getSheetKeyFor(sheetName);
  if (!state.xlsxSheetInspectorStates[key]) {
    const mapping = sheetMappingForInspectorSeed(sheetName);
    state.xlsxSheetInspectorStates[key] = {
      sheet_name: sheetName,
      class_name: mapping.class_name || mapping.domain || state.currentSource?.class_name || '',
      schema: mapping.schema || (mapping.entry_type && mapping.entry_type !== 'table_facts' ? mapping.entry_type : '') || state.currentSource?.schema || '',
      education_level: mapping.education_level || state.currentSource?.education_level || '',
      language: mapping.language || state.currentSource?.language || '',
      notes: mapping.notes || state.currentSource?.notes || '',
      selection: cloneXlsxSelection(mapping.table_selection || {}),
    };
  }
  state.xlsxSheetInspectorStates[key].sheet_name = sheetName;
  return state.xlsxSheetInspectorStates[key];
}

export function captureCurrentSheetInspectorState(sheetNameOverride = null) {
  if (!state.currentSource || state.currentParsed?.source_type !== 'xlsx') return null;
  const sheetName = xlsxSheetNameFallback(sheetNameOverride || state.currentSheetName);
  if (!sheetName) return null;
  const key = getSheetKeyFor(sheetName);
  const existing = ensureCurrentSheetInspectorState(sheetName);
  normalizeSelection();
  const values = sourceInspectorDomValues(existing, key);
  const next = {
    ...existing,
    ...values,
    sheet_name: sheetName,
    selection: cloneXlsxSelection(state.xlsxSelection),
  };
  state.xlsxSheetInspectorStates[key] = next;
  return next;
}

export function applyCurrentSheetInspectorState() {
  if (state.currentParsed?.source_type !== 'xlsx') return null;
  const inspector = ensureCurrentSheetInspectorState();
  state.xlsxSelection = cloneXlsxSelection(inspector.selection || {});
  normalizeSelection();
  return inspector;
}

export function currentSheetSourceConfig(options = {}) {
  if (state.currentParsed?.source_type !== 'xlsx') {
    return {
      class_name: state.currentSource?.class_name || state.currentSource?.domain || '',
      schema: state.currentSource?.schema || '',
      education_level: state.currentSource?.education_level || '',
      language: state.currentSource?.language || '',
      notes: state.currentSource?.notes || '',
    };
  }
  const inspector = ensureCurrentSheetInspectorState();
  const key = getSheetKey();
  const values = options.readDom === false ? inspector : sourceInspectorDomValues(inspector, key);
  return {
    ...inspector,
    ...values,
    class_name: values.class_name || '',
    schema: values.schema || inspector.schema || '',
    education_level: values.education_level || '',
    language: values.language || '',
    notes: values.notes || '',
  };
}

function tableProfileStateFromPayload(profile = {}) {
  const safe = profile && typeof profile === 'object' ? profile : {};
  return normalizeTableProfile({
    metaRows: Array.isArray(safe.meta_rows) ? safe.meta_rows.map(row => Number(row) - 1) : [],
    metaCells: Array.isArray(safe.meta_cells) ? safe.meta_cells.map(cell => ({ row: Number(cell.row) - 1, col: Number(cell.column) - 1 })) : [],
    dataRows: Array.isArray(safe.data_rows) ? safe.data_rows.map(row => Number(row) - 1) : [],
    footerRows: Array.isArray(safe.footer_rows) ? safe.footer_rows.map(row => Number(row) - 1) : [],
    ignoredRows: Array.isArray(safe.ignored_rows) ? safe.ignored_rows.map(row => Number(row) - 1) : [],
  });
}

function domainByName(name = '') {
  const clean = String(name || '').trim();
  if (!clean) return null;
  return (state.catalog?.domains || []).find(domain => String(domain?.name || '') === clean) || null;
}

function schemaByNameLoose(name = '') {
  const clean = String(name || '').trim();
  if (!clean) return null;
  return (state.catalog?.schemas || []).find(schema => String(schema?.name || '') === clean) || null;
}

function autoSchemaForDomain(domainName = '') {
  const domain = domainByName(domainName);
  if (domain?.default_schema && schemaByNameLoose(domain.default_schema)) return domain.default_schema;
  const clean = String(domainName || '').trim().toLowerCase();
  const schemas = (state.catalog?.schemas || []).filter(schema => schema?.enabled !== false);
  if (clean === 'programs') {
    const programSchema = schemas.find(schema => /program/i.test(String(schema?.name || '')) || /program/i.test(String(schema?.handler || '')));
    if (programSchema?.name) return programSchema.name;
  }
  return domain?.default_schema || '';
}

function resolveEntrySchemaForMapping(mapping = {}) {
  const domain = mapping.class_name || mapping.domain || '';
  const explicit = mapping.schema || (mapping.entry_type && mapping.entry_type !== 'table_facts' ? mapping.entry_type : '') || '';
  return explicit || autoSchemaForDomain(domain) || '';
}

function normalizeSheetMappingPayload(sheetName, raw = {}) {
  const mapping = raw && typeof raw === 'object' ? raw : {};
  const schemaFieldMap = mapping.schema_field_map || mapping.field_map || mapping.mapping_template?.field_map || {};
  const domainName = mapping.class_name || mapping.domain || null;
  const resolvedSchema = resolveEntrySchemaForMapping(mapping);
  return {
    ...mapping,
    source_type: 'xlsx',
    extraction_mode: mapping.extraction_mode || 'table',
    sheet_name: xlsxSheetNameFallback(sheetName || mapping.sheet_name),
    entry_type: resolvedSchema || mapping.entry_type || 'table_facts',
    class_name: domainName,
    domain: domainName,
    schema: resolvedSchema,
    education_level: mapping.education_level || null,
    language: mapping.language || null,
    notes: mapping.notes || '',
    header_row: Number(mapping.header_row || mapping.table_profile?.header_row || 1),
    data_start_row: Number(mapping.data_start_row || mapping.table_profile?.data_start_row || 2),
    title: mapping.title || '',
    title_column: Number(mapping.title_column ?? 0),
    text_columns: Array.isArray(mapping.text_columns) ? mapping.text_columns.map(Number).filter(Number.isFinite) : [],
    source_column: mapping.source_column === undefined ? null : mapping.source_column,
    note_column: mapping.note_column === undefined ? null : mapping.note_column,
    field_labels: Array.isArray(mapping.field_labels) ? mapping.field_labels : [],
    fields: Array.isArray(mapping.fields) ? mapping.fields.map(serializeContractField).filter(field => field.name) : [],
    table_profile: mapping.table_profile || mapping.source_profile || null,
    source_profile: mapping.source_profile || mapping.table_profile || null,
    field_map: Object.fromEntries(Object.entries(schemaFieldMap).map(([name, m]) => [name, normalizeFieldMapping(m)]).filter(([, m]) => m.kind && m.kind !== 'manual' && m.ref)),
    schema_field_map: Object.fromEntries(Object.entries(schemaFieldMap).map(([name, m]) => [name, normalizeFieldMapping(m)]).filter(([, m]) => m.kind && m.kind !== 'manual' && m.ref)),
    table_selection: cloneXlsxSelection(mapping.table_selection || {
      mode: mapping.selection_mode,
      cells: mapping.selected_cells,
      rows: mapping.selected_rows,
      columns: mapping.selected_columns,
    }),
  };
}

function legacyMappingHasXlsxData(mapping = {}) {
  return Boolean(
    mapping.sheet_name ||
    mapping.header_row ||
    mapping.data_start_row ||
    mapping.table_profile ||
    mapping.source_profile ||
    mapping.schema_field_map ||
    mapping.field_map ||
    mapping.text_columns ||
    mapping.title_column !== undefined
  );
}

export function loadXlsxSheetMappingsFromSource(source = state.currentSource, parsed = state.currentParsed) {
  state.xlsxSheetMappings = {};
  if (!source || parsed?.source_type !== 'xlsx') return;
  const mapping = source.mapping || {};
  const sheetNames = (parsed.sheets || []).map(sheet => sheet.sheet_title).filter(Boolean);
  const rawSheetMappings = mapping.sheet_mappings || mapping.sheets || mapping.per_sheet || {};
  const hasSheetMappings = Boolean(Object.keys(rawSheetMappings || {}).length);
  Object.entries(rawSheetMappings || {}).forEach(([name, raw]) => {
    const sheetName = xlsxSheetNameFallback(raw?.sheet_name || name);
    if (sheetName) state.xlsxSheetMappings[sheetName] = normalizeSheetMappingPayload(sheetName, raw);
  });
  if (!hasSheetMappings && legacyMappingHasXlsxData(mapping)) {
    const legacySheet = xlsxSheetNameFallback(mapping.sheet_name || sheetNames[0]);
    if (legacySheet && !state.xlsxSheetMappings[legacySheet]) {
      state.xlsxSheetMappings[legacySheet] = normalizeSheetMappingPayload(legacySheet, mapping);
    }
  }
}

function emptyXlsxSheetMapping(sheetName) {
  const emptySheet = normalizeSheetMappingPayload(sheetName, { sheet_name: sheetName });
  emptySheet.entry_type = 'table_facts';
  emptySheet.schema = '';
  emptySheet.fields = [];
  emptySheet.field_map = {};
  emptySheet.schema_field_map = {};
  return emptySheet;
}

export function currentXlsxSheetMapping() {
  const sheetName = xlsxSheetNameFallback();
  const fromState = state.xlsxSheetMappings?.[sheetName];
  if (fromState) return normalizeSheetMappingPayload(sheetName, fromState);

  const legacy = state.currentSource?.mapping || {};
  const sheetNames = (state.currentParsed?.sheets || []).map(sheet => sheet.sheet_title).filter(Boolean);
  const hasPerSheetMappings = Boolean(legacy.sheet_mappings || legacy.sheets || legacy.per_sheet);
  const legacySheetName = xlsxSheetNameFallback(legacy.sheet_name || sheetNames[0] || sheetName);

  if (!legacyMappingHasXlsxData(legacy)) return emptyXlsxSheetMapping(sheetName);

  // Old files could store one Excel mapping at source level. Apply that legacy
  // payload only to its own sheet. Never let its fields leak into another sheet.
  if (hasPerSheetMappings) return emptyXlsxSheetMapping(sheetName);
  if (legacy.sheet_name && legacy.sheet_name !== sheetName) return emptyXlsxSheetMapping(sheetName);
  if (!legacy.sheet_name && sheetNames.length > 1 && sheetName !== legacySheetName) return emptyXlsxSheetMapping(sheetName);

  return normalizeSheetMappingPayload(sheetName, legacy);
}

export function applyCurrentXlsxSheetMapping() {
  if (state.currentParsed?.source_type !== 'xlsx') return;
  const sheetName = xlsxSheetNameFallback();
  const mapping = currentXlsxSheetMapping();
  const normalized = normalizeSheetMappingPayload(sheetName, {
    ...mapping,
    fields: Array.isArray(mapping.fields) ? mapping.fields : [],
    field_map: mapping.schema_field_map || mapping.field_map || {},
    schema_field_map: mapping.schema_field_map || mapping.field_map || {},
  });
  if (sheetName) {
    if (!state.xlsxSheetMappings || typeof state.xlsxSheetMappings !== 'object') state.xlsxSheetMappings = {};
    state.xlsxSheetMappings[sheetName] = normalized;
  }
  state._schemaFieldsControlKey = '';
  state.schemaMappingDraft = { field_map: { ...(normalized.schema_field_map || normalized.field_map || {}) } };
  state.currentSchemaFieldIndex = null;
  renderSchemaFieldsEditor(Array.isArray(normalized.fields) ? normalized.fields.map(serializeContractField).filter(field => field.name) : []);
  if (normalized.table_profile || normalized.source_profile) {
    state.xlsxTableProfiles[getSheetKey()] = tableProfileStateFromPayload(normalized.table_profile || normalized.source_profile);
  } else if (!state.xlsxTableProfiles[getSheetKey()]) {
    state.xlsxTableProfiles[getSheetKey()] = { metaCells: [], metaRows: [], dataRows: [], footerRows: [], ignoredRows: [] };
  }
  applyCurrentSheetInspectorState();
  const setValue = (id, value) => {
    const node = document.getElementById(id);
    if (node && value !== undefined && value !== null) node.value = String(value);
  };
  setValue('headerRowSelect', normalized.header_row || 1);
  setValue('dataStartRowSelect', normalized.data_start_row || 2);
  setValue('mappingTitle', normalized.title || '');
  setValue('titleColumnSelect', normalized.title_column ?? 0);
  setValue('sourceColumnSelect', normalized.source_column ?? '');
  setValue('noteColumnSelect', normalized.note_column ?? '');
  const schemaSelect = document.getElementById('editorSchema');
  if (schemaSelect) schemaSelect.value = normalized.entry_type && normalized.entry_type !== 'table_facts' ? normalized.entry_type : '';
}

function schemaByName(schemaName = '') {
  const name = String(schemaName || '').trim();
  return (state.catalog.schemas || []).find(schema => String(schema?.name || '') === name) || null;
}

function sheetMappingHasExtraction(mapping = {}) {
  return Boolean(
    (Array.isArray(mapping.fields) && mapping.fields.length) ||
    Object.keys(mapping.schema_field_map || {}).length ||
    mapping.table_profile ||
    mapping.source_profile
  );
}

function currentTableProfilePayloadNoSync() {
  const profile = ensureTableProfile();
  return {
    header_row: headerIndex() + 1,
    data_start_row: dataStartIndex() + 1,
    meta_rows: profile.metaRows.map(row => Number(row) + 1),
    meta_cells: profile.metaCells.map(cell => ({
      row: Number(cell.row) + 1,
      column: Number(cell.col) + 1,
      address: `${spreadsheetColumnName(cell.col)}${Number(cell.row) + 1}`,
    })),
    data_rows: profile.dataRows.map(row => Number(row) + 1),
    footer_rows: profile.footerRows.map(row => Number(row) + 1),
    ignored_rows: profile.ignoredRows.map(row => Number(row) + 1),
    metadata: metadataRecordsFromProfile(),
  };
}

export function applySchemaTemplateToCurrentXlsxSheet(schemaName, options = {}) {
  if (state.currentParsed?.source_type !== 'xlsx') return false;
  const schema = schemaByName(schemaName);
  const sheetName = xlsxSheetNameFallback();
  if (!schema || !sheetName) return false;

  if (!state.xlsxSheetMappings || typeof state.xlsxSheetMappings !== 'object') state.xlsxSheetMappings = {};
  const existing = currentXlsxSheetMapping();
  const template = normalizeSchemaMappingTemplate(schema);
  const schemaFields = Array.isArray(schema.fields)
    ? schema.fields.map(serializeContractField).filter(field => field.name)
    : [];
  const force = options.force !== false;
  const existingProfile = existing.source_profile || existing.table_profile || currentTableProfilePayloadNoSync();
  const templateProfile = template.table_profile || schema.source_profile || null;
  const nextProfile = force && templateProfile ? templateProfile : existingProfile;
  const nextFieldMap = force || !Object.keys(existing.schema_field_map || {}).length
    ? { ...(template.field_map || {}) }
    : { ...(existing.schema_field_map || {}) };
  const nextFields = force || !Array.isArray(existing.fields) || !existing.fields.length
    ? schemaFields
    : existing.fields.map(serializeContractField).filter(field => field.name);

  const nextMapping = normalizeSheetMappingPayload(sheetName, {
    ...existing,
    source_type: 'xlsx',
    extraction_mode: 'table',
    sheet_name: sheetName,
    entry_type: schema.name,
    schema: schema.name,
    fields: nextFields,
    schema_field_map: nextFieldMap,
    source_profile: nextProfile,
    table_profile: nextProfile,
  });

  state.xlsxSheetMappings[sheetName] = nextMapping;
  state.schemaMappingDraft = { field_map: { ...(nextMapping.schema_field_map || {}) } };
  if (nextProfile) state.xlsxTableProfiles[getSheetKey()] = tableProfileStateFromPayload(nextProfile);
  applyCurrentXlsxSheetMapping();
  return true;
}

export function ensureCurrentSheetUsesSelectedSchemaTemplate() {
  if (state.currentParsed?.source_type !== 'xlsx') return false;
  const mapping = currentXlsxSheetMapping();
  const schemaName = mapping.entry_type;
  if (!schemaName || schemaName === 'table_facts') return false;
  if (sheetMappingHasExtraction(mapping)) return false;
  return applySchemaTemplateToCurrentXlsxSheet(schemaName, { force: true });
}

export function captureCurrentXlsxSheetMapping(sheetNameOverride = null) {
  if (!state.currentSource || state.currentParsed?.source_type !== 'xlsx') return null;
  const sheetName = xlsxSheetNameFallback(sheetNameOverride || document.getElementById('sheetSelect')?.value);
  if (!sheetName) return null;
  if (!state.xlsxSheetMappings || typeof state.xlsxSheetMappings !== 'object') state.xlsxSheetMappings = {};
  const fieldLabels = Array.from(refs.fieldLabelList?.querySelectorAll('input') || []).map(input => ({
    column: Number(input.dataset.column),
    label: input.value.trim(),
  })).filter(item => Number.isFinite(item.column) && item.label);
  const inspectorState = captureCurrentSheetInspectorState(sheetName);
  const mapping = normalizeSheetMappingPayload(sheetName, {
    ...(state.xlsxSheetMappings[sheetName] || {}),
    source_type: 'xlsx',
    extraction_mode: 'table',
    entry_type: inspectorState?.schema || state.xlsxSheetMappings[sheetName]?.entry_type || 'table_facts',
    class_name: inspectorState?.class_name || null,
    domain: inspectorState?.class_name || null,
    schema: inspectorState?.schema || '',
    education_level: inspectorState?.education_level || null,
    language: inspectorState?.language || null,
    notes: inspectorState?.notes || '',
    table_selection: inspectorState?.selection || cloneXlsxSelection(state.xlsxSelection),
    sheet_name: sheetName,
    header_row: Number(document.getElementById('headerRowSelect')?.value || 1),
    data_start_row: Number(document.getElementById('dataStartRowSelect')?.value || 2),
    title: document.getElementById('mappingTitle')?.value.trim() || '',
    title_column: Number(document.getElementById('titleColumnSelect')?.value || 0),
    text_columns: selectedValues(document.getElementById('textColumnsSelect')).map(Number).filter(Number.isFinite),
    source_column: document.getElementById('sourceColumnSelect')?.value === '' ? null : Number(document.getElementById('sourceColumnSelect')?.value),
    note_column: document.getElementById('noteColumnSelect')?.value === '' ? null : Number(document.getElementById('noteColumnSelect')?.value),
    field_labels: fieldLabels,
    fields: currentOutputFields(),
    source_profile: tableProfilePayload(),
    table_profile: tableProfilePayload(),
    field_map: schemaMappingPayload().field_map,
    schema_field_map: schemaMappingPayload().field_map,
  });
  state.xlsxSheetMappings[sheetName] = mapping;
  return mapping;
}

export function buildXlsxSourceMappingPayload() {
  const current = captureCurrentXlsxSheetMapping() || currentXlsxSheetMapping();
  const sheetMappings = { ...(state.xlsxSheetMappings || {}) };
  if (current?.sheet_name) sheetMappings[current.sheet_name] = current;
  const normalizedSheetMappings = Object.fromEntries(
    Object.entries(sheetMappings || {}).map(([name, mapping]) => [name, normalizeSheetMappingPayload(name, mapping)])
  );
  const sheetSources = Object.entries(normalizedSheetMappings).map(([name, mapping]) => ({
    source_id: state.currentSource?.source_id ? `${state.currentSource.source_id}__sheet__${slugifyFieldName(name, 'sheet')}` : '',
    source_base_id: state.currentSource?.source_id || '',
    sheet_name: name,
    ...mapping,
  }));
  return {
    ...normalizeSheetMappingPayload(current?.sheet_name || state.currentSheetName, current),
    sheet_mappings: normalizedSheetMappings,
    sheets: normalizedSheetMappings,
    per_sheet: normalizedSheetMappings,
    sheet_sources: sheetSources,
  };
}

export function currentHeaderRowIndex() {
  return headerIndex();
}

export function currentDataStartRowIndex() {
  return dataStartIndex();
}

function selectedRowsOrCellRows() {
  normalizeSelection();
  const rows = [];
  rows.push(...state.xlsxSelection.rows);
  rows.push(...state.xlsxSelection.cells.map(cell => cell.row));
  return uniqueSortedNumbers(rows);
}

function selectedCellsOrRowsAsMetaCells() {
  normalizeSelection();
  const rows = sheetRows();
  const maxCols = maxColumnCount(rows);
  const cells = [];
  state.xlsxSelection.cells.forEach(cell => cells.push({ row: Number(cell.row), col: Number(cell.col) }));
  state.xlsxSelection.rows.forEach(row => {
    for (let col = 0; col < maxCols; col += 1) {
      const value = String((rows[row] || [])[col] ?? '').trim();
      if (value) cells.push({ row: Number(row), col });
    }
  });
  return cells.filter(cell => Number.isFinite(cell.row) && Number.isFinite(cell.col));
}

function cellListKey(cell) {
  return selectedCellKey(cell.row, cell.col);
}

function mergeCells(a, b) {
  const map = new Map();
  [...(a || []), ...(b || [])].forEach(cell => {
    if (Number.isFinite(Number(cell.row)) && Number.isFinite(Number(cell.col))) {
      map.set(cellListKey(cell), { row: Number(cell.row), col: Number(cell.col) });
    }
  });
  return Array.from(map.values()).sort((x, y) => x.row - y.row || x.col - y.col);
}

function selectedHeaderCandidate() {
  const rows = selectedRowsOrCellRows();
  if (rows.length) return rows[0];
  return currentHeaderRowIndex();
}

export function tableProfileSummaryText() {
  const profile = ensureTableProfile();
  const header = currentHeaderRowIndex() + 1;
  const dataStart = currentDataStartRowIndex() + 1;
  const metaRowsCount = profile.metaRows.length;
  const metaFieldsCount = profile.metaCells.length;
  const data = profile.dataRows.length ? profile.dataRows.map(row => row + 1).join(', ') : `${dataStart}…`;
  const footer = profile.footerRows.length ? ` · футер: ${profile.footerRows.map(row => row + 1).join(', ')}` : '';
  const ignored = profile.ignoredRows.length ? ` · игнор: ${profile.ignoredRows.length}` : '';
  return `заголовки: строка ${header} · данные: ${data} · мета-строк: ${metaRowsCount} · мета-полей: ${metaFieldsCount}${footer}${ignored}`;
}

export function metadataRecordsFromProfile() {
  const rows = sheetRows();
  const profile = ensureTableProfile();
  const cells = [...profile.metaCells];
  profile.metaRows.forEach(rowIndex => {
    const row = rows[rowIndex] || [];
    for (let col = 0; col < row.length; col += 1) {
      if (String(row[col] ?? '').trim()) cells.push({ row: rowIndex, col });
    }
  });
  return mergeCells([], cells).map(cell => ({
    row: cell.row + 1,
    column: cell.col + 1,
    address: `${spreadsheetColumnName(cell.col)}${cell.row + 1}`,
    value: String((rows[cell.row] || [])[cell.col] ?? '').trim(),
  })).filter(item => item.value);
}

export function dataRowsFromProfile() {
  const rows = sheetRows();
  const profile = ensureTableProfile();
  if (profile.dataRows.length) return profile.dataRows.filter(row => row >= 0 && row < rows.length);
  const start = currentDataStartRowIndex();
  const excluded = new Set([
    currentHeaderRowIndex(),
    ...profile.metaRows,
    ...profile.footerRows,
    ...profile.ignoredRows,
  ].map(Number).filter(Number.isFinite));
  return rows.map((_, index) => index).filter(index => index >= start && !excluded.has(index));
}

function rowTextFromHeaders(rowIndex) {
  const rows = sheetRows();
  const row = rows[rowIndex] || [];
  const maxCols = maxColumnCount(rows);
  const parts = [];
  for (let col = 0; col < maxCols; col += 1) {
    const value = String(row[col] ?? '').trim();
    if (!value) continue;
    parts.push(`${columnHeaderLabel(col)}: ${value}`);
  }
  return parts.join('; ');
}

function columnTextForField(colIndex) {
  const rows = sheetRows();
  const col = Number(colIndex);
  if (!Number.isFinite(col)) return '';
  const candidateRows = selectedRowsForDraft().length ? selectedRowsForDraft() : dataRowsFromProfile();
  const values = candidateRows
    .filter(rowIndex => rowIndex >= currentDataStartRowIndex())
    .map(rowIndex => String((rows[rowIndex] || [])[col] ?? '').trim())
    .filter(Boolean);
  const label = columnHeaderLabel(col);
  return values.length ? `${label}: ${values.join('\n')}` : '';
}

export function selectedOrProfileTableText() {
  const selected = selectedTableText();
  if (selected) return selected;
  const rows = dataRowsFromProfile().filter(row => row >= currentDataStartRowIndex());
  return rows.map(row => `Строка ${row + 1}: ${rowTextFromHeaders(row)}`).filter(line => !line.endsWith(': ')).join('\n');
}

export function renderTableStructureSummary() {
  const summary = document.getElementById('tableStructureSummary');
  if (summary) summary.textContent = tableProfileSummaryText();
  const selectionSummary = document.getElementById('inspectorSelectionSummary');
  if (selectionSummary) selectionSummary.textContent = selectionSummaryText();
  const target = document.getElementById('tableMetaList');
  if (target) {
    const meta = metadataRecordsFromProfile();
    const profile = ensureTableProfile();
    const dataRows = profile.dataRows.length ? profile.dataRows.map(row => `строка ${row + 1}`).join(', ') : `со строки ${currentDataStartRowIndex() + 1}`;
    target.innerHTML = `
      <div class="table-meta-list__line"><strong>Метаданные</strong><span>${meta.length ? meta.map(item => `${escapeHtml(item.address)}=${escapeHtml(item.value)}`).join(' · ') : 'нет'}</span></div>
      <div class="table-meta-list__line"><strong>Строки данных</strong><span>${escapeHtml(dataRows)}</span></div>
      <div class="table-meta-list__line"><strong>Футер</strong><span>${escapeHtml(profile.footerRows.length ? profile.footerRows.map(row => `строка ${row + 1}`).join(', ') : 'нет')}</span></div>
      <div class="table-meta-list__line"><strong>Игнор</strong><span>${escapeHtml(profile.ignoredRows.length ? profile.ignoredRows.map(row => `строка ${row + 1}`).join(', ') : 'нет')}</span></div>
    `;
  }
  renderXlsxContextInspector();
}

function clearRowsFromAllProfileRoles(profile, rows = [], options = {}) {
  const normalizedRows = uniqueSortedNumbers(rows);
  if (!normalizedRows.length) return normalizedRows;
  profile.metaRows = removeNumbers(profile.metaRows, normalizedRows);
  profile.dataRows = removeNumbers(profile.dataRows, normalizedRows);
  profile.footerRows = removeNumbers(profile.footerRows, normalizedRows);
  profile.ignoredRows = removeNumbers(profile.ignoredRows, normalizedRows);
  if (options.clearMetaCells !== false) {
    profile.metaCells = removeCellsInRows(profile.metaCells, normalizedRows);
  }
  return normalizedRows;
}

function setRowsToTableRole(role, rows = []) {
  const profile = ensureTableProfile();
  const normalizedRows = clearRowsFromAllProfileRoles(profile, rows, { clearMetaCells: true });
  if (!normalizedRows.length) return profile;
  if (role === 'meta') profile.metaRows = uniqueSortedNumbers([...profile.metaRows, ...normalizedRows]);
  if (role === 'data') profile.dataRows = uniqueSortedNumbers([...profile.dataRows, ...normalizedRows]);
  if (role === 'footer') profile.footerRows = uniqueSortedNumbers([...profile.footerRows, ...normalizedRows]);
  if (role === 'ignore') profile.ignoredRows = uniqueSortedNumbers([...profile.ignoredRows, ...normalizedRows]);
  state.xlsxTableProfiles[getSheetKey()] = normalizeTableProfile(profile);
  return state.xlsxTableProfiles[getSheetKey()];
}

function clearHeaderConflictMarks(row) {
  const profile = ensureTableProfile();
  clearRowsFromAllProfileRoles(profile, [row], { clearMetaCells: true });
  state.xlsxTableProfiles[getSheetKey()] = normalizeTableProfile(profile);
}

export function clearTableMarksFromSelection() {
  const profile = ensureTableProfile();
  normalizeSelection();
  const rows = selectedRowsOrCellRows();
  const cells = normalizeCellList(state.xlsxSelection.cells || []);
  if (!rows.length && !cells.length) return;
  clearRowsFromAllProfileRoles(profile, rows, { clearMetaCells: true });
  if (cells.length) {
    const cellKeys = new Set(cells.map(cell => selectedCellKey(cell.row, cell.col)));
    profile.metaCells = normalizeCellList(profile.metaCells).filter(cell => !cellKeys.has(selectedCellKey(cell.row, cell.col)));
  }
  state.xlsxTableProfiles[getSheetKey()] = normalizeTableProfile(profile);
  refreshSpreadsheetViews();
}

export function setHeaderFromSelection() {
  const row = selectedHeaderCandidate();
  const rows = sheetRows();
  const headerSelect = document.getElementById('headerRowSelect');
  const dataSelect = document.getElementById('dataStartRowSelect');
  if (!rows.length || !headerSelect || !dataSelect) return;
  clearHeaderConflictMarks(row);
  headerSelect.value = String(row + 1);
  dataSelect.value = String(Math.min(rows.length, row + 2));
  renderSheetControls();
  upsertSchemaColumnFieldsFromHeader();
  refreshSpreadsheetViews();
}

export function markMetadataFromSelection() {
  const profile = ensureTableProfile();
  normalizeSelection();
  let selectedRows = uniqueSortedNumbers(state.xlsxSelection.rows || []);
  let selectedCells = selectedCellsOrRowsAsMetaCells();
  if (!selectedRows.length && !selectedCells.length) {
    selectedRows = uniqueSortedNumbers(Array.from({ length: currentHeaderRowIndex() }, (_, index) => index));
    selectedCells = selectedRowsOrProfileCells(selectedRows);
  }
  if (selectedRows.length) setRowsToTableRole('meta', selectedRows);
  const nextProfile = ensureTableProfile();
  nextProfile.metaCells = mergeCells(nextProfile.metaCells, selectedCells);
  state.xlsxTableProfiles[getSheetKey()] = normalizeTableProfile(nextProfile);
  upsertSchemaMetadataFieldsFromCells(selectedCells);
  refreshSpreadsheetViews();
}

function selectedRowsOrProfileCells(rows = []) {
  const tableRows = sheetRows();
  const maxCols = maxColumnCount(tableRows);
  const cells = [];
  rows.forEach(row => {
    for (let col = 0; col < maxCols; col += 1) {
      const value = String((tableRows[row] || [])[col] ?? '').trim();
      if (value) cells.push({ row: Number(row), col });
    }
  });
  return cells;
}

function upsertFooterFieldForSelection(rows = []) {
  const selected = uniqueSortedNumbers(rows);
  if (!selected.length) return;
  const fields = currentOutputFields();
  const sourceRef = selected.map(row => `строка ${row + 1}`).join(', ');
  const existingName = findMappedFieldName('footer_zone', sourceRef) || 'footer_notes';
  let existingIndex = fieldIndexByName(fields, existingName);
  if (existingIndex < 0) existingIndex = fieldIndexByName(fields, 'footer_notes');
  const nextField = {
    name: existingIndex >= 0 ? fields[existingIndex].name : uniqueSchemaFieldName('footer_notes', fields),
    label: 'Примечания футера',
    field_type: 'text',
    required: false,
    description: 'Строки, отмеченные как футер таблицы или нижние метаданные.',
    destination: 'metadata',
    validation: {},
  };
  if (existingIndex >= 0) fields[existingIndex] = { ...fields[existingIndex], ...nextField };
  else fields.push(nextField);
  setMappingForField(nextField.name, { kind: 'footer_zone', ref: sourceRef, destination: 'metadata' });
  upsertSchemaFieldList(fields, existingIndex >= 0 ? existingIndex : fields.length - 1);
}

export function markFooterFromSelection() {
  const profile = ensureTableProfile();
  const rows = selectedRowsOrCellRows();
  const selected = rows.length ? rows : uniqueSortedNumbers(sheetRows().map((_, index) => index).filter(index => index > currentDataStartRowIndex()));
  setRowsToTableRole('footer', selected);
  upsertFooterFieldForSelection(selected);
  refreshSpreadsheetViews();
}


export function markIgnoreFromSelection() {
  const profile = ensureTableProfile();
  const rows = selectedRowsOrCellRows();
  setRowsToTableRole('ignore', rows);
  refreshSpreadsheetViews();
}

export function setDataBelowHeader() {
  const rows = sheetRows();
  const header = currentHeaderRowIndex();
  const dataStart = Math.min(rows.length, header + 2);
  const dataSelect = document.getElementById('dataStartRowSelect');
  if (dataSelect) dataSelect.value = String(dataStart);
  const profile = ensureTableProfile();
  profile.dataRows = [];
  renderSheetControls();
  refreshSpreadsheetViews();
}

export function setDataFromSelection() {
  const profile = ensureTableProfile();
  const rows = selectedRowsOrCellRows().filter(row => row > currentHeaderRowIndex());
  const targetRows = rows.length ? rows : dataRowsFromProfile().filter(row => row > currentHeaderRowIndex());
  setRowsToTableRole('data', targetRows);
  refreshSpreadsheetViews();
}

export function clearTableMarks() {
  const profile = ensureTableProfile();
  profile.metaCells = [];
  profile.metaRows = [];
  profile.dataRows = [];
  profile.footerRows = [];
  profile.ignoredRows = [];
  refreshSpreadsheetViews();
}

export function syncSelectionModeControls() {
  normalizeSelection();
  document.querySelectorAll('[data-table-selection-mode], #tableSelectionMode').forEach(select => {
    if (select && select.value !== state.xlsxSelection.mode) select.value = state.xlsxSelection.mode;
  });
}

export function selectionSummaryText() {
  normalizeSelection();
  const parts = [];
  if (state.xlsxSelection.cells.length) parts.push(`${state.xlsxSelection.cells.length} ячеек`);
  if (state.xlsxSelection.rows.length) parts.push(`${state.xlsxSelection.rows.length} строк`);
  if (state.xlsxSelection.columns.length) parts.push(`${state.xlsxSelection.columns.length} столбцов`);
  return parts.length ? parts.join(' · ') : 'ничего не выбрано';
}

function rowRoleForInspector(rowIndex) {
  const row = Number(rowIndex);
  if (!Number.isFinite(row)) return '';
  if (row === currentHeaderRowIndex()) return 'header';
  const profile = ensureTableProfile();
  if ((profile.metaRows || []).map(Number).includes(row)) return 'meta';
  if ((profile.dataRows || []).map(Number).includes(row)) return 'data';
  if ((profile.footerRows || []).map(Number).includes(row)) return 'footer';
  if ((profile.ignoredRows || []).map(Number).includes(row)) return 'ignore';
  return '';
}

function rowRoleLabelForInspector(role) {
  return {
    header: 'заголовок',
    meta: 'мета',
    data: 'данные',
    footer: 'футер',
    ignore: 'игнор',
  }[role] || 'без статуса';
}

export function currentXlsxSelectionContext() {
  normalizeSelection();
  const rows = state.xlsxSelection.rows || [];
  const columns = state.xlsxSelection.columns || [];
  const cells = state.xlsxSelection.cells || [];
  const sheetTitle = state.currentSheetName || 'лист';
  const noSelection = !rows.length && !columns.length && !cells.length;
  if (noSelection) {
    return {
      kind: 'none',
      title: `Лист: ${sheetTitle}`,
      pill: 'лист',
      detail: `${tableProfileSummaryText()} · ${currentOutputFields().length} полей`,
      fieldName: null,
      field: null,
    };
  }
  if (rows.length === 1 && !columns.length && !cells.length) {
    const row = Number(rows[0]);
    const role = rowRoleForInspector(row);
    const preview = String((sheetRows()[row] || []).filter(value => String(value ?? '').trim()).slice(0, 4).join(' | ')).slice(0, 140);
    return {
      kind: 'row',
      row,
      title: `Строка ${row + 1}`,
      pill: rowRoleLabelForInspector(role),
      detail: preview || 'Пустая строка.',
      fieldName: null,
      field: null,
    };
  }
  if (columns.length === 1 && !rows.length && !cells.length) {
    const col = Number(columns[0]);
    const ref = spreadsheetColumnName(col);
    const fieldName = findMappedFieldName('column', ref);
    const field = fieldName ? currentOutputFields().find(item => item.name === fieldName) || null : null;
    const header = columnHeaderLabel(col);
    return {
      kind: 'column',
      col,
      ref,
      title: `Колонка ${ref}`,
      pill: fieldName ? 'поле' : 'нет поля',
      detail: fieldName ? `${fieldName} · ${destinationLabel(field?.destination || mappingForField(fieldName).destination || 'fields')}` : `Заголовок: ${header}`,
      fieldName,
      field,
    };
  }
  if (cells.length === 1 && !rows.length && !columns.length) {
    const cell = cells[0];
    const row = Number(cell.row);
    const col = Number(cell.col);
    const ref = `${spreadsheetColumnName(col)}${row + 1}`;
    const fieldName = findMappedFieldName('cell', ref);
    const field = fieldName ? currentOutputFields().find(item => item.name === fieldName) || null : null;
    const value = String((sheetRows()[row] || [])[col] ?? '').trim();
    return {
      kind: 'cell',
      row,
      col,
      ref,
      title: `Ячейка ${ref}`,
      pill: fieldName ? 'мета-поле' : 'ячейка',
      detail: fieldName ? `${fieldName} · ${destinationLabel(field?.destination || mappingForField(fieldName).destination || 'metadata')}` : (value || 'Пустая ячейка.'),
      fieldName,
      field,
    };
  }
  return {
    kind: 'mixed',
    title: 'Смешанный выбор',
    pill: 'выбор',
    detail: selectionSummaryText(),
    fieldName: mappedSchemaFieldNameFromSelection(),
    field: null,
  };
}

function contextModeList(value = '') {
  return String(value || '').split(/\s+/).map(item => item.trim()).filter(Boolean);
}

function showForXlsxContext(element, context) {
  const modes = contextModeList(element.dataset.xlsxContextSection);
  if (!modes.length) return true;
  return modes.includes('all')
    || modes.includes(context.kind)
    || (context.kind === 'none' && modes.includes('sheet'))
    || (context.fieldName && modes.includes('field'));
}

function updateXlsxActionVisibility(context) {
  document.querySelectorAll('[data-xlsx-action-context]').forEach(element => {
    const modes = contextModeList(element.dataset.xlsxActionContext);
    const show = !modes.length
      || modes.includes('all')
      || modes.includes(context.kind)
      || (context.kind === 'none' && modes.includes('sheet'))
      || (context.fieldName && modes.includes('field'));
    element.classList.toggle('hidden', !show);
  });
}

export function renderXlsxContextInspector() {
  if (state.currentParsed?.source_type !== 'xlsx') return;
  const context = currentXlsxSelectionContext();
  const root = document.getElementById('inspectorPane');
  if (root) root.dataset.xlsxSelectionKind = context.kind;
  const title = document.getElementById('xlsxContextTitle');
  const detail = document.getElementById('xlsxContextDetail');
  const pill = document.getElementById('xlsxContextPill');
  if (title) title.textContent = context.title;
  if (detail) detail.textContent = context.detail || '';
  if (pill) pill.textContent = context.pill || context.kind;
  document.querySelectorAll('[data-xlsx-context-section]').forEach(element => {
    element.classList.toggle('hidden', !showForXlsxContext(element, context));
  });
  updateXlsxActionVisibility(context);
}

function selectedTableRecords() {
  normalizeSelection();
  const rows = sheetRows();
  const maxCols = maxColumnCount(rows);
  const header = rows[headerIndex()] || [];
  const result = [];
  const addCell = (rowIndex, colIndex) => {
    if (rowIndex < 0 || colIndex < 0 || rowIndex >= rows.length || colIndex >= maxCols) return;
    const row = rows[rowIndex] || [];
    const raw = row[colIndex];
    const value = String(raw ?? '').trim();
    if (!value) return;
    result.push({
      row: rowIndex,
      col: colIndex,
      address: `${spreadsheetColumnName(colIndex)}${rowIndex + 1}`,
      header: String(header[colIndex] ?? '').trim() || `Колонка ${colIndex + 1}`,
      value,
    });
  };
  state.xlsxSelection.rows.forEach(rowIndex => {
    for (let col = 0; col < maxCols; col += 1) addCell(rowIndex, col);
  });
  const firstData = dataStartIndex();
  state.xlsxSelection.columns.forEach(colIndex => {
    for (let row = firstData; row < rows.length; row += 1) addCell(row, colIndex);
  });
  state.xlsxSelection.cells.forEach(cell => addCell(Number(cell.row), Number(cell.col)));
  const seen = new Set();
  return result.filter(item => {
    const key = selectedCellKey(item.row, item.col);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => a.row - b.row || a.col - b.col);
}

export function selectedTableText() {
  const records = selectedTableRecords();
  if (!records.length) return '';
  const byRow = new Map();
  records.forEach(record => {
    if (!byRow.has(record.row)) byRow.set(record.row, []);
    byRow.get(record.row).push(record);
  });
  return Array.from(byRow.entries()).map(([rowIndex, items]) => {
    const line = items.map(item => `${item.header}: ${item.value}`).join('; ');
    return byRow.size > 1 ? `Строка ${rowIndex + 1}: ${line}` : line;
  }).join('\n');
}

export function selectedTableFirstValue() {
  return selectedTableRecords()[0]?.value || '';
}

export function selectionMetadata() {
  normalizeSelection();
  const profile = ensureTableProfile();
  return {
    sheet_name: getCurrentSheet()?.sheet_title || state.currentSheetName || null,
    header_row: headerIndex() + 1,
    data_start_row: dataStartIndex() + 1,
    selected_cells: state.xlsxSelection.cells.map(cell => ({ row: Number(cell.row) + 1, column: Number(cell.col) + 1, address: `${spreadsheetColumnName(cell.col)}${Number(cell.row) + 1}` })),
    selected_rows: state.xlsxSelection.rows.map(row => Number(row) + 1),
    selected_columns: state.xlsxSelection.columns.map(col => ({ index: Number(col) + 1, letter: spreadsheetColumnName(col), header: columnHeaderLabel(col) })),
    table_profile: {
      header_row: headerIndex() + 1,
      data_start_row: dataStartIndex() + 1,
      meta_rows: profile.metaRows.map(row => Number(row) + 1),
      meta_cells: profile.metaCells.map(cell => ({ row: Number(cell.row) + 1, column: Number(cell.col) + 1, address: `${spreadsheetColumnName(cell.col)}${Number(cell.row) + 1}` })),
      data_rows: profile.dataRows.map(row => Number(row) + 1),
      footer_rows: profile.footerRows.map(row => Number(row) + 1),
      ignored_rows: profile.ignoredRows.map(row => Number(row) + 1),
      metadata: metadataRecordsFromProfile(),
    },
  };
}
function collectSchemaFieldDraftValuesFromDom() {
  state.schemaFieldDraftValues = {};
}

function currentSchemaDefinition() {
  const schemaName = state.currentParsed?.source_type === 'xlsx'
    ? (document.getElementById('editorSchema')?.value || currentXlsxSheetMapping().entry_type || state.currentSource?.schema || '')
    : (refs.entrySchemaSelect?.value || state.currentSource?.schema || '');
  return (state.catalog.schemas || []).find(schema => schema.name === schemaName) || null;
}

function schemaFieldsForCurrentDraft() {
  if (state.currentParsed?.source_type === 'xlsx') {
    const fields = currentOutputFields();
    if (fields.length) return fields;
  }
  const schema = currentSchemaDefinition();
  if (Array.isArray(schema?.fields) && schema.fields.length) return schema.fields;
  return [
    { name: 'title', label: 'Заголовок', field_type: 'text' },
    { name: 'text', label: 'Текст', field_type: 'text' },
  ];
}

export function renderSchemaFieldMapper() {
  if (refs.schemaFieldMapper) refs.schemaFieldMapper.innerHTML = '';
}

export function renderSchemaMappingList() {
  const target = document.getElementById('schemaMappingList');
  if (!target) return;
  const fields = currentOutputFields();
  const isXlsxSourceInspector = state.currentParsed?.source_type === 'xlsx' && state.workspaceTab !== 'schema';
  const context = isXlsxSourceInspector ? currentXlsxSelectionContext() : null;
  if (!fields.length) {
    target.innerHTML = '<div class="inspector-note">Полей нет. Для листа: выбери строку заголовков или колонку/ячейку и создай поле.</div>';
    renderXlsxContextInspector();
    return;
  }
  if (context?.kind === 'row') {
    target.innerHTML = '<div class="inspector-note">Для строки показываются только статусы таблицы. Поля редактируются через колонку, ячейку или список листа без выделения.</div>';
    renderXlsxContextInspector();
    return;
  }
  if (context?.kind === 'mixed') {
    target.innerHTML = '<div class="inspector-note">Смешанный выбор используется только для текста/черновика. Для поля выбери одну колонку или одну ячейку.</div>';
    renderXlsxContextInspector();
    return;
  }
  let visibleFields = fields.map((field, index) => ({ field, index }));
  if ((context?.kind === 'column' || context?.kind === 'cell') && context.fieldName) {
    visibleFields = visibleFields.filter(item => item.field.name === context.fieldName);
  } else if (context?.kind === 'column') {
    target.innerHTML = '<div class="inspector-note">У выбранной колонки еще нет поля. Нажми «Создать поле из колонки».</div>';
    renderXlsxContextInspector();
    return;
  } else if (context?.kind === 'cell') {
    target.innerHTML = '<div class="inspector-note">У выбранной ячейки еще нет мета-поля. Нажми «Создать мета-поле из ячейки».</div>';
    renderXlsxContextInspector();
    return;
  }
  target.innerHTML = visibleFields.map(({ field, index }) => {
    const mapping = mappingForField(field.name);
    const hasMapping = mapping.kind && mapping.kind !== 'manual' && mapping.ref;
    const selected = currentFieldIndex() === index;
    const summary = hasMapping
      ? `${mappingKindLabel(mapping.kind)}: ${mapping.ref} → ${destinationLabel(mapping.destination || field.destination || 'fields')}`
      : 'логика не задана';
    return `<div class="mapping-line mapping-line--field${selected ? ' is-active' : ''}${hasMapping ? '' : ' is-empty'}" data-mapping-field-name="${escapeAttr(field.name)}">
      <div class="mapping-line__main">
        <strong>${escapeHtml(field.name)}</strong>
        <span>${escapeHtml(field.label || '')}</span>
        <code>${escapeHtml(summary)}</code>
      </div>
      <div class="mapping-line__actions">
        <button class="btn btn--mini" data-schema-map-reset="${escapeAttr(field.name)}" type="button" ${hasMapping ? '' : 'disabled'}>Сброс</button>
        <button class="danger danger--mini" data-schema-map-delete="${escapeAttr(field.name)}" type="button">Удалить</button>
      </div>
    </div>`;
  }).join('');
  target.querySelectorAll('[data-mapping-field-name]').forEach(row => {
    row.onclick = event => {
      if (event.target.closest('button')) return;
      selectSchemaFieldByName(row.dataset.mappingFieldName);
    };
  });
  target.querySelectorAll('[data-schema-map-reset]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      resetSchemaFieldMappingByName(button.dataset.schemaMapReset);
    };
  });
  target.querySelectorAll('[data-schema-map-delete]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      deleteSchemaFieldByName(button.dataset.schemaMapDelete);
    };
  });
  renderXlsxContextInspector();
}

export function tableProfilePayload() {

  syncTableProfileFromMapping();
  return selectionMetadata().table_profile;
}

export function valuesFromSchemaMapping(rowIndex) {
  const sheet = getCurrentSheet();
  const rows = sheet?.rows || [];
  const row = rows[rowIndex] || [];
  const values = {};
  currentOutputFields().forEach(field => {
    const mapping = mappingForField(field.name);
    let value = '';
    if (mapping.kind === 'column') {
      const col = spreadsheetColumnIndexFromName(mapping.ref);
      if (col !== null) value = String(row[col] ?? '').trim();
    } else if (mapping.kind === 'cell') {
      const cell = parseSpreadsheetAddress(mapping.ref);
      if (cell) value = String((rows[cell.row] || [])[cell.col] ?? '').trim();
    } else if (mapping.kind === 'footer_zone') {
      const profile = ensureTableProfile();
      value = profile.footerRows.map(r => rowTextFromHeaders(r)).filter(Boolean).join('\n');
    }
    if (value) values[field.name] = value;
  });
  return values;
}

export function collectSchemaFieldDraftValues() {
  return {};
}

export function selectedRowsForDraft() {
  normalizeSelection();
  if (state.xlsxSelection.rows.length) return state.xlsxSelection.rows;
  if (state.xlsxSelection.cells.length) return uniqueSortedNumbers(state.xlsxSelection.cells.map(cell => cell.row));
  const profile = ensureTableProfile();
  if (profile.dataRows.length) return profile.dataRows;
  return [];
}

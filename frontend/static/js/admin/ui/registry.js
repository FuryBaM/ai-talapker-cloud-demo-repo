import { EDUCATION_LEVELS, refs, state } from '../core/state.js';
import { dirtyKeyForSource, escapeHtml, isSourceDirty, syncGlobalDropdowns } from './common.js';
import { collectSchemaFields, renderSchemaFieldsEditor, renderSchemaInspector, renderSchemaPreview, setCurrentFieldIndex, slugifyFieldName, syncSchemaHeader } from '../features/mapping/logic.js';

function isXlsxSource(source = {}) {
  const path = String(source.path || source.filename || '').toLowerCase();
  return source.source_type === 'xlsx' || source.mapping?.source_type === 'xlsx' || path.endsWith('.xlsx') || path.endsWith('.xls');
}


function normalizedLooseText(value = '') {
  return String(value || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/\s+/g, ' ')
    .toLowerCase();
}

function normalizedWorkbookPathKey(source = {}) {
  const raw = String(
    source.path ||
    source.filename ||
    source.file_path ||
    source.mapping?.path ||
    source.mapping?.filename ||
    ''
  ).trim();
  if (!raw) return '';
  const slashPath = raw.replace(/\\/g, '/').replace(/\s+/g, ' ').trim();
  const workbookMatch = slashPath.match(/^(.+?\.(?:xlsx|xlsm|xlsb|xls))(?:$|[\s>#›|:-])/i);
  if (workbookMatch) return workbookMatch[1].toLowerCase();
  return slashPath.toLowerCase();
}

function sheetKey(value = '') {
  const raw = String(value || '').trim();
  if (!raw) return '';
  return slugifyFieldName(raw, 'sheet').toLowerCase();
}

function preferSheetTitle(current = '', incoming = '') {
  const a = String(current || '').trim();
  const b = String(incoming || '').trim();
  if (!a) return b;
  if (!b) return a;
  const aLooksSlug = /^[a-z0-9_]+$/.test(a) && a === sheetKey(a);
  const bLooksSlug = /^[a-z0-9_]+$/.test(b) && b === sheetKey(b);
  if (aLooksSlug && !bLooksSlug) return b;
  if (!aLooksSlug && bLooksSlug) return a;
  if (b.length > a.length && b.includes('-')) return b;
  return a;
}

function logicalSheetInfo(source = {}) {
  const sourceId = String(source.source_id || '').trim();
  const marker = '__sheet__';
  const markerIndex = sourceId.indexOf(marker);
  const mapping = source.mapping || {};

  if (markerIndex >= 0) {
    const baseSourceId = sourceId.slice(0, markerIndex).trim();
    const suffix = sourceId.slice(markerIndex + marker.length).trim();
    if (!baseSourceId) return null;
    const sheetName = String(
      mapping.sheet_name ||
      mapping.sheet_title ||
      source.sheet_name ||
      source.sheet_title ||
      source.title ||
      suffix ||
      'sheet'
    ).trim();
    return { baseSourceId, sheetName, suffix };
  }

  const explicitBase = String(source.source_base_id || mapping.source_base_id || mapping.base_source_id || '').trim();
  const explicitSheet = String(source.sheet_name || source.sheet_title || mapping.sheet_name || mapping.sheet_title || '').trim();
  if (explicitBase && explicitSheet) return { baseSourceId: explicitBase, sheetName: explicitSheet, suffix: sheetKey(explicitSheet) };

  return null;
}

function normalizedPathKey(source = {}) {
  return normalizedWorkbookPathKey(source);
}

function normalizedSourceIdKey(sourceId = '') {
  const id = String(sourceId || '').trim();
  if (!id) return '';
  return id.includes('__sheet__') ? id.split('__sheet__')[0] : id;
}

function sourceDedupKey(source = {}) {
  const path = normalizedPathKey(source);
  if (path) return `path:${path}`;
  const mappingBase = String(source.source_base_id || source.mapping?.source_base_id || source.mapping?.base_source_id || '').trim();
  if (mappingBase) return `id:${mappingBase}`;
  return `id:${normalizedSourceIdKey(source.source_id)}`;
}

function ensureSourceMapping(source) {
  source.mapping = { ...(source.mapping || {}) };
  source.mapping.sheet_mappings = { ...(source.mapping.sheet_mappings || source.mapping.sheets || source.mapping.per_sheet || {}) };
  return source.mapping;
}

function mergePlainMissing(target = {}, source = {}) {
  Object.entries(source || {}).forEach(([key, value]) => {
    if (key === 'mapping' || key === '_extra_sheet_metas' || key === '_merged_source_ids') return;
    if (value === undefined || value === null || value === '') return;
    if (target[key] === undefined || target[key] === null || target[key] === '') target[key] = value;
  });
  return target;
}

function mergeSheetMapping(targetMapping, sheetName, mapping = {}) {
  const canonical = sheetKey(mapping?.sheet_name || mapping?.sheet_title || sheetName);
  if (!canonical) return;
  const existingKey = Object.keys(targetMapping.sheet_mappings || {}).find(name => sheetKey(name) === canonical);
  const preferredName = preferSheetTitle(existingKey || '', mapping?.sheet_name || mapping?.sheet_title || sheetName || existingKey || 'sheet');
  const previous = existingKey ? targetMapping.sheet_mappings[existingKey] || {} : {};
  if (existingKey && existingKey !== preferredName) delete targetMapping.sheet_mappings[existingKey];
  targetMapping.sheet_mappings[preferredName] = {
    ...previous,
    ...(mapping || {}),
    sheet_name: preferredName,
  };
}

function cloneRegistrySource(source = {}) {
  const clone = { ...source };
  clone.mapping = { ...(source.mapping || {}) };
  clone.mapping.sheet_mappings = {};
  const sourceMappings = source.mapping?.sheet_mappings || source.mapping?.sheets || source.mapping?.per_sheet || {};
  Object.entries(sourceMappings || {}).forEach(([name, mapping]) => mergeSheetMapping(clone.mapping, name, mapping));
  clone._extra_sheet_metas = Array.isArray(source._extra_sheet_metas) ? [...source._extra_sheet_metas] : [];
  clone._merged_source_ids = [String(source.source_id || '').trim(), normalizedSourceIdKey(source.source_id)].filter(Boolean);
  clone._merged_source_ids = Array.from(new Set(clone._merged_source_ids));
  return clone;
}

function mergeSourceRecord(target, source = {}) {
  mergePlainMissing(target, source);
  const targetMapping = ensureSourceMapping(target);
  const sourceMappings = source.mapping?.sheet_mappings || source.mapping?.sheets || source.mapping?.per_sheet || {};
  Object.entries(sourceMappings || {}).forEach(([name, mapping]) => mergeSheetMapping(targetMapping, name, mapping));
  const extraMetas = Array.isArray(source._extra_sheet_metas) ? source._extra_sheet_metas : [];
  target._extra_sheet_metas = [...(target._extra_sheet_metas || []), ...extraMetas];
  const ids = new Set([
    ...(target._merged_source_ids || []),
    String(target.source_id || '').trim(),
    String(source.source_id || '').trim(),
    normalizedSourceIdKey(target.source_id),
    normalizedSourceIdKey(source.source_id),
    String(source.source_base_id || source.mapping?.source_base_id || source.mapping?.base_source_id || '').trim(),
  ].filter(Boolean));
  target._merged_source_ids = Array.from(ids);
  return target;
}

function attachLogicalSheetSource(workbookSource, sheetSource = {}, info = null) {
  const sheetInfo = info || logicalSheetInfo(sheetSource);
  if (!workbookSource || !sheetInfo?.sheetName) return;
  const mapping = sheetSource.mapping || {};
  const sheetName = String(mapping.sheet_name || mapping.sheet_title || sheetInfo.sheetName).trim();
  const targetMapping = ensureSourceMapping(workbookSource);
  mergeSheetMapping(targetMapping, sheetName, mapping);
  workbookSource._extra_sheet_metas = workbookSource._extra_sheet_metas || [];
  workbookSource._extra_sheet_metas.push({
    sheet_title: sheetName,
    rows: Number(mapping.row_count || mapping.rows || sheetSource.rows || 0),
    columns: Number(mapping.column_count || mapping.columns || sheetSource.columns || 0),
  });
  workbookSource._merged_source_ids = Array.from(new Set([
    ...(workbookSource._merged_source_ids || []),
    String(sheetSource.source_id || '').trim(),
    sheetInfo.baseSourceId,
  ].filter(Boolean)));
}

function registerSourceLookups(source, maps) {
  const ids = new Set([
    String(source.source_id || '').trim(),
    normalizedSourceIdKey(source.source_id),
    ...(source._merged_source_ids || []),
    String(source.source_base_id || source.mapping?.source_base_id || source.mapping?.base_source_id || '').trim(),
  ].filter(Boolean));
  ids.forEach(id => {
    if (!maps.bySourceId.has(id)) maps.bySourceId.set(id, source);
  });
  const pathKey = normalizedPathKey(source);
  if (pathKey && !maps.byPath.has(pathKey)) maps.byPath.set(pathKey, source);
}

function normalizedRegistry() {
  const raw = Array.isArray(state.registry) ? state.registry : [];
  const ordered = [];
  const byKey = new Map();
  const maps = { bySourceId: new Map(), byPath: new Map() };
  const logicalSheetSources = [];

  raw.forEach(source => {
    const info = logicalSheetInfo(source);
    if (info) {
      logicalSheetSources.push({ source, info });
      return;
    }

    const key = sourceDedupKey(source);
    let existing = byKey.get(key);
    if (!existing) {
      existing = cloneRegistrySource(source);
      byKey.set(key, existing);
      ordered.push(existing);
    } else {
      mergeSourceRecord(existing, source);
    }
    registerSourceLookups(existing, maps);
    registerSourceLookups(source, maps);
  });

  logicalSheetSources.forEach(({ source, info }) => {
    const target = maps.bySourceId.get(info.baseSourceId) || maps.byPath.get(normalizedPathKey(source));
    if (target) {
      attachLogicalSheetSource(target, source, info);
      registerSourceLookups(target, maps);
      return;
    }

    const pseudoKey = `logical:${info.baseSourceId || normalizedPathKey(source)}`;
    let pseudo = byKey.get(pseudoKey);
    if (!pseudo) {
      pseudo = cloneRegistrySource({
        ...source,
        source_id: info.baseSourceId,
        mapping: { source_type: 'xlsx', sheet_mappings: {} },
      });
      byKey.set(pseudoKey, pseudo);
      ordered.push(pseudo);
      registerSourceLookups(pseudo, maps);
    }
    attachLogicalSheetSource(pseudo, source, info);
    registerSourceLookups(pseudo, maps);
  });

  return ordered;
}

function cachedSheetMetasForSource(source = {}) {
  const sourceIds = Array.from(new Set([
    String(source.source_id || '').trim(),
    normalizedSourceIdKey(source.source_id),
    ...(source._merged_source_ids || []),
  ].filter(Boolean)));
  const cacheSheets = sourceIds.flatMap(id => Array.isArray(state.xlsxSourceSheets?.[id]) ? state.xlsxSourceSheets[id] : []);
  const fromMapping = source.mapping?.sheet_mappings || source.mapping?.sheets || source.mapping?.per_sheet || {};
  const fromExtra = Array.isArray(source._extra_sheet_metas) ? source._extra_sheet_metas : [];
  const byKey = new Map();

  const putSheet = (sheet = {}, mapping = null) => {
    const name = String(sheet.sheet_title || sheet.sheet_name || sheet.name || mapping?.sheet_name || mapping?.sheet_title || '').trim();
    const key = sheetKey(name);
    if (!key) return;
    const existing = byKey.get(key) || {};
    const title = preferSheetTitle(existing.sheet_title || '', name);
    byKey.set(key, {
      sheet_title: title,
      rows: Number(existing.rows || sheet.rows || sheet.row_count || mapping?.row_count || mapping?.rows || 0),
      columns: Number(existing.columns || sheet.columns || sheet.column_count || mapping?.column_count || mapping?.columns || 0),
    });
  };

  cacheSheets.forEach(sheet => putSheet(sheet));
  fromExtra.forEach(sheet => putSheet(sheet));
  Object.entries(fromMapping || {}).forEach(([name, mapping]) => putSheet({ sheet_title: mapping?.sheet_name || mapping?.sheet_title || name }, mapping));

  return Array.from(byKey.values());
}

function isCurrentWorkbook(source = {}) {
  const currentId = String(state.currentSource?.source_id || '').trim();
  if (!currentId) return false;
  if (currentId === String(source.source_id || '').trim()) return true;
  return Array.isArray(source._merged_source_ids) && source._merged_source_ids.includes(currentId);
}

function isCurrentSheetSource(source = {}, sheetTitle = '') {
  return isCurrentWorkbook(source) && state.currentParsed?.source_type === 'xlsx' && sheetKey(state.currentSheetName || '') === sheetKey(sheetTitle || '');
}

function sheetMappingForSource(source = {}, sheetTitle = '') {
  const title = String(sheetTitle || '').trim();
  if (!title) return {};
  const titleKey = sheetKey(title);
  if (isCurrentWorkbook(source)) {
    const currentEntry = Object.entries(state.xlsxSheetMappings || {}).find(([name]) => sheetKey(name) === titleKey);
    if (currentEntry) return currentEntry[1] || {};
  }
  const mappings = source.mapping?.sheet_mappings || source.mapping?.sheets || source.mapping?.per_sheet || {};
  return mappings[title] || Object.entries(mappings).find(([name, mapping]) => sheetKey(mapping?.sheet_name || mapping?.sheet_title || name) === titleKey)?.[1] || {};
}

function sheetConfigForSource(source = {}, sheetTitle = '') {
  const mapping = sheetMappingForSource(source, sheetTitle);
  return {
    class_name: mapping.class_name || mapping.domain || source.class_name || '',
    schema: mapping.schema || (mapping.entry_type && mapping.entry_type !== 'table_facts' ? mapping.entry_type : '') || source.schema || '',
    education_level: mapping.education_level || source.education_level || '',
    language: mapping.language || source.language || '',
    notes: mapping.notes || source.notes || '',
  };
}

function sheetLogicalSourceId(sourceId = '', sheetTitle = '') {
  const cleanSource = String(sourceId || 'source').trim() || 'source';
  return `${cleanSource}__sheet__${slugifyFieldName(sheetTitle || 'sheet', 'sheet')}`;
}

export function currentLogicalSourceId() {
  if (state.currentParsed?.source_type === 'xlsx' && state.currentSheetName && state.currentSource?.source_id) {
    return sheetLogicalSourceId(state.currentSource.source_id, state.currentSheetName);
  }
  return state.currentSource?.source_id || '';
}

export function currentSourceDisplayName() {
  if (state.currentParsed?.source_type === 'xlsx' && state.currentSheetName && state.currentSource?.source_id) {
    return `${state.currentSource.source_id} / ${state.currentSheetName}`;
  }
  return state.currentSource?.source_id || '';
}

export function filteredRegistry() {
  const q = (refs.sourceSearchInput.value || '').trim().toLowerCase();
  const registry = normalizedRegistry();
  if (!q) return registry;
  return registry.filter(source => {
    const sheets = isXlsxSource(source) ? cachedSheetMetasForSource(source) : [];
    return String(source.source_id || '').toLowerCase().includes(q) ||
      String(source.path || '').toLowerCase().includes(q) ||
      String(source.class_name || '').toLowerCase().includes(q) ||
      String(source.schema || '').toLowerCase().includes(q) ||
      sheets.some(sheet => {
        const config = sheetConfigForSource(source, sheet.sheet_title);
        return String(sheet.sheet_title || '').toLowerCase().includes(q) ||
          String(config.class_name || '').toLowerCase().includes(q) ||
          String(config.education_level || '').toLowerCase().includes(q) ||
          String(config.language || '').toLowerCase().includes(q);
      });
  });
}

function sourceTooltip(source, sheet = null) {
  if (sheet) {
    const config = sheetConfigForSource(source, sheet.sheet_title);
    return [
      `${source.source_id} / ${sheet.sheet_title}`,
      source.path ? `файл: ${source.path}` : '',
      `лист: ${sheet.sheet_title}`,
      sheet.rows ? `строк: ${sheet.rows}` : '',
      sheet.columns ? `столбцов: ${sheet.columns}` : '',
      config.class_name ? `домен: ${config.class_name}` : '',
      config.education_level ? `уровень: ${educationLevelDisplay(config.education_level)}` : '',
      config.language ? `язык: ${config.language}` : '',
      config.notes ? `примечание: ${config.notes}` : '',
    ].filter(Boolean).join('\n');
  }
  return [
    source.source_id,
    source.path ? `путь: ${source.path}` : '',
    isXlsxSource(source) ? 'Excel-книга: листы открываются как отдельные источники' : '',
    source.class_name ? `домен: ${source.class_name}` : '',
    source.education_level ? `уровень: ${educationLevelDisplay(source.education_level)}` : '',
    source.language ? `язык: ${source.language}` : '',
    source.notes ? `примечание: ${source.notes}` : '',
  ].filter(Boolean).join('\n');
}

function compactParts(...parts) {
  return parts.map(part => String(part || '').trim()).filter(Boolean).join(' · ');
}

function educationLevelDisplay(value = '') {
  const clean = String(value || '').trim();
  if (!clean) return '';
  const item = EDUCATION_LEVELS.find(level => level.value === clean);
  return item && item.value ? item.label : clean;
}

function sourceShortName(source = {}) {
  const raw = String(source.path || source.filename || source.source_id || '').replace(/\\/g, '/');
  return raw.split('/').filter(Boolean).pop() || String(source.source_id || 'source');
}

function renderSourceButton({ source, sheet = null, active = false, openSource, childCount = 0 }) {
  const item = document.createElement('button');
  item.className = `source-item source-item--file source-tree-row${sheet ? ' source-item--sheet source-tree-row--sheet' : ''}${isXlsxSource(source) && !sheet ? ' source-item--workbook source-tree-row--workbook' : ''}${active ? ' active' : ''}`;
  item.type = 'button';
  item.draggable = true;
  item.dataset.sourceId = source.source_id;
  const dirtyKey = dirtyKeyForSource(source.source_id, sheet ? sheet.sheet_title : '');
  const dirty = sheet ? isSourceDirty(source.source_id, sheet.sheet_title) : isSourceDirty(source.source_id);
  item.dataset.dirtyKey = dirtyKey;
  if (!sheet) item.dataset.dirtySourceId = source.source_id;
  item.classList.toggle('is-dirty', Boolean(sheet && dirty));
  item.classList.toggle('has-dirty-children', Boolean(!sheet && dirty));
  if (sheet) {
    item.dataset.sheetName = sheet.sheet_title;
    item.dataset.logicalSourceId = sheetLogicalSourceId(source.source_id, sheet.sheet_title);
  }
  item.title = sourceTooltip(source, sheet);

  const sheetConfig = sheet ? sheetConfigForSource(source, sheet.sheet_title) : null;
  const title = sheet ? sheet.sheet_title : sourceShortName(source);
  const fallbackTitle = sheet ? sheet.sheet_title : source.source_id;
  const icon = sheet ? '└' : (isXlsxSource(source) ? (childCount ? '▾' : '▦') : '•');

  const pathLine = sheet
    ? compactParts(
        sheet.rows && sheet.columns ? `${sheet.rows}×${sheet.columns}` : '',
        sheetConfig?.class_name,
        educationLevelDisplay(sheetConfig?.education_level),
        sheetConfig?.language
      )
    : compactParts(
        source.path && sourceShortName(source) !== source.source_id ? source.source_id : '',
        childCount ? `${childCount} лист.` : '',
        !isXlsxSource(source) ? (source.class_name || 'источник') : '',
        educationLevelDisplay(source.education_level),
        source.language
      );

  item.innerHTML = `
    <span class="source-item__icon source-tree-row__icon" aria-hidden="true">${icon}</span>
    <span class="source-item__body source-tree-row__body">
      <span class="source-tree-row__main">
        <span class="source-item__title" title="${escapeHtml(fallbackTitle)}">${escapeHtml(title)}</span>
        <span class="source-dirty-star ${dirty ? '' : 'hidden'}" ${sheet ? 'data-dirty-star' : 'data-workbook-dirty-star'} title="Несохраненные изменения">*</span>
      </span>
      ${pathLine ? `<span class="source-item__path">${escapeHtml(pathLine)}</span>` : ''}
    </span>
  `;
  item.onclick = () => openSource(source.source_id, sheet?.sheet_title || null);
  return item;
}

export function renderRegistry(openSource) {
  const sourceListEl = refs.sourceList || document.getElementById('sourceList');
  if (!sourceListEl) return;
  sourceListEl.innerHTML = '';
  sourceListEl.classList.add('source-list--tree');

  const filtered = filteredRegistry();
  filtered.forEach(source => {
    const sheets = isXlsxSource(source) ? cachedSheetMetasForSource(source) : [];
    const showWorkbookAsParent = isXlsxSource(source) && sheets.length;
    const parentActive = isCurrentWorkbook(source) && (!showWorkbookAsParent || !state.currentSheetName);

    if (!showWorkbookAsParent) {
      sourceListEl.appendChild(renderSourceButton({
        source,
        active: parentActive,
        openSource,
      }));
      return;
    }

    const group = document.createElement('div');
    group.className = `source-tree-group${isCurrentWorkbook(source) ? ' source-tree-group--active' : ''}`;
    group.appendChild(renderSourceButton({
      source,
      active: parentActive,
      childCount: sheets.length,
      openSource: (sourceId, sheetName) => openSource(sourceId, sheetName || sheets[0]?.sheet_title || null),
    }));

    const children = document.createElement('div');
    children.className = 'source-tree-children';
    sheets.forEach(sheet => {
      children.appendChild(renderSourceButton({
        source,
        sheet,
        active: isCurrentSheetSource(source, sheet.sheet_title),
        openSource,
      }));
    });
    group.appendChild(children);
    sourceListEl.appendChild(group);
  });
}

export function renderCatalog() {
  refs.domainsList.innerHTML = '';
  refs.schemasList.innerHTML = '';
  const domainEditorList = document.getElementById('domainEditorList');
  if (domainEditorList) domainEditorList.innerHTML = '';

  const renderDomainNode = (domain, index) => {
    const node = document.createElement('div');
    node.setAttribute('role', 'button');
    node.tabIndex = 0;
    node.draggable = true;
    node.className = `source-item catalog-item catalog-edit-item${state.currentDomainIndex === index ? ' active' : ''}`;
    node.dataset.domainIndex = String(index);
    node.title = [
      `домен: ${domain.name}`,
      domain.description ? `описание: ${domain.description}` : '',
      domain.default_schema ? `тип записи по умолчанию: ${domain.default_schema}` : '',
      `включено: ${domain.enabled !== false ? 'да' : 'нет'}`,
    ].filter(Boolean).join('\n');
    node.innerHTML = `
      <span class="catalog-item__main">
        <span class="source-item__title">${escapeHtml(domain.name)}</span>
        <span class="muted">${escapeHtml(domain.description || 'Нет описания')}</span>
        <span class="source-item__meta">
          <span class="pill">${domain.enabled !== false ? 'включено' : 'отключено'}</span>
          ${domain.default_schema ? `<span class="pill">по умолчанию: ${escapeHtml(domain.default_schema)}</span>` : ''}
        </span>
      </span>
      <span class="catalog-item__actions">
        <span class="pill catalog-action-pill">Изменить</span>
        <button class="danger danger--inline" data-domain-remove="${index}" type="button">Удалить</button>
      </span>
    `;
    return node;
  };

  const renderSchemaNode = (schema, index) => {
    const fields = Array.isArray(schema.fields) ? schema.fields : [];
    const node = document.createElement('div');
    node.setAttribute('role', 'button');
    node.tabIndex = 0;
    node.draggable = true;
    node.className = `source-item catalog-item catalog-edit-item${state.currentSchemaIndex === index ? ' active' : ''}`;
    node.dataset.schemaIndex = String(index);
    node.title = [
      `тип записи: ${schema.name}`,
      `обработчик: ${schema.handler || ''}`,
      schema.description ? `описание: ${schema.description}` : '',
      fields.length ? `поля: ${fields.map(field => field.name).filter(Boolean).join(', ')}` : '',
      `включено: ${schema.enabled !== false ? 'да' : 'нет'}`,
    ].filter(Boolean).join('\n');
    node.innerHTML = `
      <span class="catalog-item__main">
        <span class="source-item__title">${escapeHtml(schema.name)}</span>
        <span class="muted">${escapeHtml(schema.description || 'Нет описания')}</span>
        <span class="source-item__meta">
          <span class="pill">${escapeHtml(schema.handler || 'обработчик: нет')}</span>
          <span class="pill">${schema.enabled !== false ? 'включено' : 'отключено'}</span>
          ${fields.length ? `<span class="pill">${fields.length} полей</span>` : ''}
        </span>
      </span>
      <span class="catalog-item__actions">
        <span class="pill catalog-action-pill">Изменить</span>
        <button class="danger danger--inline" data-schema-remove="${index}" type="button">Удалить</button>
      </span>
    `;
    return node;
  };

  state.catalog.domains.forEach((domain, index) => {
    const node = renderDomainNode(domain, index);
    refs.domainsList.appendChild(node);
    if (domainEditorList) domainEditorList.appendChild(renderDomainNode(domain, index));
  });

  state.catalog.schemas.forEach((schema, index) => {
    const node = renderSchemaNode(schema, index);
    refs.schemasList.appendChild(node);
  });
  syncGlobalDropdowns();
}

export function bindCatalogActions(onOpenDomain, onRemoveDomain, onOpenSchema, onRemoveSchema) {
  document.querySelectorAll('[data-domain-index]').forEach(item => {
    const open = () => onOpenDomain(Number(item.dataset.domainIndex));
    item.onclick = event => {
      if (event.target.closest('[data-domain-remove]')) return;
      open();
    };
    item.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    };
  });
  document.querySelectorAll('[data-schema-index]').forEach(item => {
    const open = () => onOpenSchema(Number(item.dataset.schemaIndex));
    item.onclick = event => {
      if (event.target.closest('[data-schema-remove]')) return;
      open();
    };
    item.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    };
  });
  document.querySelectorAll('[data-domain-remove]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      onRemoveDomain(Number(button.dataset.domainRemove));
    };
  });
  document.querySelectorAll('[data-schema-remove]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      onRemoveSchema(Number(button.dataset.schemaRemove));
    };
  });
  refs.schemaFieldsList?.querySelectorAll('[data-schema-field-index]').forEach(row => {
    row.onclick = event => {
      if (event.target.closest('[data-schema-field-remove]')) return;
      setCurrentFieldIndex(Number(row.dataset.schemaFieldIndex));
    };
  });
  refs.schemaFieldsList?.querySelectorAll('[data-schema-field-remove]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const idx = Number(button.dataset.schemaFieldRemove);
      const fields = collectSchemaFields();
      fields.splice(idx, 1);
      state.currentSchemaFieldIndex = null;
      renderSchemaFieldsEditor(fields);
      bindCatalogActions(onOpenDomain, onRemoveDomain, onOpenSchema, onRemoveSchema);
    };
  });
  refs.schemaFieldsList?.querySelectorAll('[data-prop]').forEach(input => {
    input.onchange = () => {
      syncSchemaHeader();
      renderSchemaInspector();
      renderSchemaPreview();
    };
  });
}


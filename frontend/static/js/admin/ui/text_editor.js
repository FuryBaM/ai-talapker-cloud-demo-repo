import { EDUCATION_LEVELS, LANGUAGES, refs, state } from '../core/state.js';
import { escapeAttr, escapeHtml, markCurrentSourceDirty } from './common.js';
import { currentLogicalSourceId } from './registry.js';

const KNOWLEDGE_TYPES = [
  { value: 'definition', label: 'определение' },
  { value: 'requirement', label: 'требование' },
  { value: 'eligibility', label: 'кто имеет право' },
  { value: 'procedure_step', label: 'шаг процедуры' },
  { value: 'required_document', label: 'документ' },
  { value: 'document_list', label: 'список' },
  { value: 'deadline', label: 'срок' },
  { value: 'result', label: 'результат' },
  { value: 'exception', label: 'исключение' },
  { value: 'contact', label: 'контакт' },
  { value: 'price', label: 'стоимость' },
  { value: 'program', label: 'программа' },
  { value: 'benefit', label: 'льгота' },
  { value: 'condition', label: 'условие' },
  { value: 'note', label: 'примечание' }
];

const BLOCK_ROLES = [
  { value: 'heading_1', label: 'глава' },
  { value: 'heading_2', label: 'подглава' },
  { value: 'heading_3', label: 'секция' },
  { value: 'body', label: 'данные' },
  { value: 'list_item', label: 'пункт списка' },
  { value: 'meta', label: 'мета' },
  { value: 'note', label: 'примечание' },
  { value: 'ignore', label: 'игнор' },
  { value: 'draft', label: 'проверить' }
];

const LOGICAL_ENTRY_TYPES = [
  { value: '', label: 'авто' },
  { value: 'linked_text', label: 'связанный текст' },
  { value: 'document_list', label: 'список' },
  { value: 'sectioned_text', label: 'секция текста' },
  { value: 'generic_text', label: 'обычный текст' }
];

function sourceDocKey() {
  return state.currentSource?.source_id || 'current_document';
}

function sourcePrimaryItem() {
  return Array.isArray(state.currentSource?.items) && state.currentSource.items.length ? state.currentSource.items[0] : {};
}

function sourceConfig() {
  const item = sourcePrimaryItem();
  return {
    class_name: document.getElementById('editorClass')?.value || state.currentSource?.class_name || state.currentSource?.domain || item.domain || item.class_name || '',
    schema: document.getElementById('editorSchema')?.value || state.currentSource?.schema || state.currentSource?.schema_name || item.entry_type || item.schema || item.schema_name || '',
    education_level: document.getElementById('editorLevel')?.value || state.currentSource?.education_level || item.education_level || '',
    language: document.getElementById('editorLang')?.value || state.currentSource?.language || item.language || '',
    notes: document.getElementById('editorNotes')?.value || state.currentSource?.notes || item.notes || '',
  };
}

function currentSourceDomain() {
  return sourceConfig().class_name || 'unassigned';
}

function currentEntryTypeFallback() {
  const schema = sourceConfig().schema;
  if (schema) return schema;
  return state.currentParsed?.source_type === 'docx' ? 'sectioned_text' : 'generic_text';
}

function asArray(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (value === null || value === undefined || value === '') return [];
  return [value];
}

function clampNumber(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.max(min, Math.min(max, number));
}

function rememberBlockCaret(blockId, start, end = start, source = 'unknown') {
  const doc = ensureSemanticDocState();
  const block = doc.blocks.find(item => item.id === blockId);
  if (!block) return null;
  const length = String(block.text || '').length;
  const safeStart = clampNumber(start, 0, length);
  const safeEnd = clampNumber(end, safeStart, length);
  doc.caret = { blockId, start: safeStart, end: safeEnd, source };
  return doc.caret;
}

function clearBlockCaret(blockId) {
  const doc = ensureSemanticDocState();
  if (doc.caret?.blockId === blockId) doc.caret = null;
}

function selectedOptions(select) {
  return Array.from(select?.selectedOptions || []).map(option => option.value).filter(Boolean);
}

function roleLabel(role) {
  return BLOCK_ROLES.find(item => item.value === role)?.label || role || 'данные';
}

function inferParagraphRole(text = '', index = 0) {
  const value = String(text || '').trim();
  if (!value) return 'ignore';
  if (/^(глава|раздел|section)\b/i.test(value)) return 'heading_1';
  if (/^\d+(?:\.\d+){0,4}\.?\s+\S/.test(value) && value.length <= 160) {
    const depth = (value.match(/\./g) || []).length + 1;
    return depth <= 1 ? 'heading_1' : (depth === 2 ? 'heading_2' : 'heading_3');
  }
  if (value.length <= 90 && !/[.!?;:]$/.test(value)) return index === 0 ? 'heading_1' : 'heading_2';
  return 'body';
}

function normalizeBlock(block = {}, index = 0) {
  const id = String(block.id || block.block_id || `p_${index + 1}`);
  const originalText = String(block.original_text ?? block.source_text ?? block.text ?? '').trim();
  return {
    id,
    source_index: Number.isFinite(Number(block.source_index)) ? Number(block.source_index) : index,
    original_text: originalText,
    text: String(block.edited_text ?? block.text ?? originalText).trim(),
    role: block.role || inferParagraphRole(originalText, index),
    level: block.level || null,
    domains: asArray(block.domains || block.domain),
    primary_domain: block.primary_domain || '',
    knowledge_types: asArray(block.knowledge_types || block.knowledge_type),
    system_fields: asArray(block.system_fields || block.system_marks),
    education_levels: asArray(block.education_levels || block.education_level),
    language: block.language || '',
    merge_with_previous: Boolean(block.merge_with_previous),
    links_to: asArray(block.links_to),
    entry_ids: asArray(block.entry_ids),
    status: block.status || '',
  };
}

function parsedParagraphs() {
  const parsed = state.currentParsed || {};
  const paragraphs = Array.isArray(parsed.paragraphs) ? parsed.paragraphs : [];
  if (paragraphs.length) return paragraphs.map(item => String(item || '').trim()).filter(Boolean);
  return String(parsed.text || '').split(/\n{2,}/).map(item => item.trim()).filter(Boolean);
}

function ensureSemanticDocState() {
  const key = sourceDocKey();
  if (!state.docTextEditor || typeof state.docTextEditor !== 'object') state.docTextEditor = {};
  if (state.docTextEditor[key]) return state.docTextEditor[key];

  const mapping = state.currentSource?.mapping || {};
  let blocks = Array.isArray(mapping.document_blocks) ? mapping.document_blocks : [];
  if (!blocks.length && Array.isArray(mapping.text_blocks)) blocks = mapping.text_blocks;
  if (!blocks.length) {
    blocks = parsedParagraphs().map((text, index) => normalizeBlock({ id: `p_${index + 1}`, text, original_text: text, role: inferParagraphRole(text, index) }, index));
    const legacy = Array.isArray(mapping.document_structure) ? mapping.document_structure : [];
    legacy.forEach(item => {
      const idx = Number(item.index);
      if (!Number.isFinite(idx) || !blocks[idx]) return;
      blocks[idx] = { ...blocks[idx], ...normalizeBlock({ ...blocks[idx], ...item, id: blocks[idx].id }, idx) };
    });
  } else {
    blocks = blocks.map((block, index) => normalizeBlock(block, index));
  }
  const entries = Array.isArray(mapping.logical_entries)
    ? mapping.logical_entries.map((entry, index) => ({
      entry_id: String(entry.entry_id || `entry_${index + 1}`),
      title: entry.title || `Запись ${index + 1}`,
      block_ids: asArray(entry.block_ids || entry.parts || entry.blocks),
      domains: asArray(entry.domains || entry.domain),
      knowledge_types: asArray(entry.knowledge_types || entry.knowledge_type),
      education_levels: asArray(entry.education_levels || entry.education_level),
      language: entry.language || '',
      notes: entry.notes || '',
      entry_type: entry.entry_type || entry.schema || '',
      logical_group_id: entry.logical_group_id || entry.group_id || '',
    }))
    : [];
  state.docTextEditor[key] = { blocks, entries, selectedBlockIds: [], selectedEntryId: '', caret: null };
  syncBlockEntryIds(state.docTextEditor[key]);
  return state.docTextEditor[key];
}

export function initializeSemanticDocumentState() {
  const key = sourceDocKey();
  if (!state.docTextEditor || typeof state.docTextEditor !== 'object') state.docTextEditor = {};
  delete state.docTextEditor[key];
  return ensureSemanticDocState();
}

function selectedBlocks(doc = ensureSemanticDocState()) {
  const ids = new Set(doc.selectedBlockIds || []);
  return doc.blocks.filter(block => ids.has(block.id));
}

function blockLevel(block) {
  if (block.level) return Number(block.level);
  if (block.role === 'heading_1') return 1;
  if (block.role === 'heading_2') return 2;
  if (block.role === 'heading_3') return 3;
  return null;
}

function isListLikeText(value = '') {
  return /^\s*(?:[-•*–—]\s+|\d+[.)]\s+|[а-яa-z]\)\s+)/i.test(String(value || ''));
}

function inferSplitContinuationRole(block, afterText = '') {
  if (isListLikeText(afterText)) return 'list_item';
  if (block.role?.startsWith('heading')) return 'body';
  return block.role || 'body';
}

function inferLogicalEntryType(blocks = [], preferred = '') {
  const explicit = String(preferred || '').trim();
  if (explicit) return explicit;
  const usable = blocks.filter(block => block?.role !== 'ignore' && cleanText(block?.text));
  if (!usable.length) return 'linked_text';
  const hasListRole = usable.some(block => block.role === 'list_item' || isListLikeText(block.text));
  const hasListKnowledge = usable.some(block => asArray(block.knowledge_types).some(type => ['document_list', 'required_document'].includes(type)));
  const listTitle = usable.find(block => block.role?.startsWith('heading'))?.text || '';
  if (hasListRole || hasListKnowledge || /(?:перечень|список|пакет)\s+(?:документ|документов)|необходимые\s+документы/i.test(listTitle)) return 'document_list';
  return 'linked_text';
}

function logicalEntryTypeLabel(value = '') {
  return LOGICAL_ENTRY_TYPES.find(item => item.value === value)?.label || value || 'авто';
}

function sectionPathForBlock(target, blocks = ensureSemanticDocState().blocks) {
  const path = [];
  for (const block of blocks) {
    if (block.id === target.id) break;
    const level = blockLevel(block);
    if (!level || block.role === 'ignore') continue;
    path.splice(level - 1);
    path[level - 1] = block.text;
  }
  return path.filter(Boolean);
}

export function semanticDocumentBlocks() {
  const doc = ensureSemanticDocState();
  return doc.blocks.map((block, index) => ({
    ...block,
    index,
    level: blockLevel(block),
    section_path: sectionPathForBlock(block, doc.blocks),
  }));
}

function notifyChanged() {
  markCurrentSourceDirty();
  window.dispatchEvent(new CustomEvent('ai-talapker-text-editor-change'));
}

function captureSemanticScroll(target = refs.sourceStructurePreview) {
  return {
    targetTop: target?.scrollTop || 0,
    blocksTop: target?.querySelector('.doc-semantic-blocks')?.scrollTop || 0,
    blocksLeft: target?.querySelector('.doc-semantic-blocks')?.scrollLeft || 0,
    outlineTop: target?.querySelector('.doc-semantic-outline')?.scrollTop || 0,
    inspectorTop: document.getElementById('docSemanticInspectorCard')?.scrollTop || 0,
  };
}

function restoreSemanticScroll(snapshot = {}, options = {}) {
  requestAnimationFrame(() => {
    const target = refs.sourceStructurePreview;
    const blocks = target?.querySelector('.doc-semantic-blocks');
    const outline = target?.querySelector('.doc-semantic-outline');
    const inspector = document.getElementById('docSemanticInspectorCard');
    if (target) target.scrollTop = snapshot.targetTop || 0;
    if (outline) outline.scrollTop = snapshot.outlineTop || 0;
    if (inspector) inspector.scrollTop = snapshot.inspectorTop || 0;
    if (blocks) {
      if (options.scrollToBlockId) {
        const block = blocks.querySelector(`[data-doc-block-id="${CSS.escape(options.scrollToBlockId)}"]`);
        if (block) {
          const nextTop = block.offsetTop - blocks.offsetTop - Math.max(24, blocks.clientHeight * 0.22);
          blocks.scrollTop = Math.max(0, nextTop);
        }
      } else {
        blocks.scrollTop = snapshot.blocksTop || 0;
        blocks.scrollLeft = snapshot.blocksLeft || 0;
      }
    }
  });
}

function semanticTextInspectorCard() {
  return document.getElementById('docSemanticInspectorCard');
}

function helpTooltip(text, label = '?') {
  const safe = escapeAttr(text);
  return '<span class="inspector-help" tabindex="0" role="note" aria-label="' + safe + '" title="' + safe + '" data-help-text="' + safe + '">' + escapeHtml(label) + '</span>';
}

let inspectorHelpPortalBound = false;
let inspectorHelpPortal = null;

function ensureInspectorHelpPortalEvents() {
  if (inspectorHelpPortalBound) return;
  inspectorHelpPortalBound = true;

  const removeTooltip = () => {
    if (!inspectorHelpPortal) return;
    inspectorHelpPortal.remove();
    inspectorHelpPortal = null;
  };

  const showTooltip = target => {
    const text = target?.dataset?.helpText || target?.getAttribute('aria-label') || '';
    if (!text) return;
    removeTooltip();

    const tooltip = document.createElement('div');
    tooltip.className = 'inspector-tooltip-portal';
    tooltip.textContent = text;
    document.body.appendChild(tooltip);
    inspectorHelpPortal = tooltip;

    const rect = target.getBoundingClientRect();
    const pad = 12;
    const width = Math.min(300, Math.max(220, window.innerWidth - pad * 2));
    tooltip.style.width = `${width}px`;

    const measured = tooltip.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(pad, Math.min(left, window.innerWidth - width - pad));

    let top = rect.bottom + 8;
    if (top + measured.height + pad > window.innerHeight) top = rect.top - measured.height - 8;
    top = Math.max(pad, top);

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  };

  document.addEventListener('pointerover', event => {
    const target = event.target.closest?.('.inspector-help');
    if (target) showTooltip(target);
  });
  document.addEventListener('pointerout', event => {
    const target = event.target.closest?.('.inspector-help');
    if (target && !target.contains(event.relatedTarget)) removeTooltip();
  });
  document.addEventListener('focusin', event => {
    const target = event.target.closest?.('.inspector-help');
    if (target) showTooltip(target);
  });
  document.addEventListener('focusout', event => {
    const target = event.target.closest?.('.inspector-help');
    if (target) removeTooltip();
  });
  window.addEventListener('scroll', removeTooltip, true);
  window.addEventListener('resize', removeTooltip);
}

function renderGlobalSemanticTextInspector(doc, selected) {
  ensureInspectorHelpPortalEvents();
  const card = semanticTextInspectorCard();
  if (!card) return;
  const body = card.querySelector('#docSemanticInspectorBody') || card;
  body.innerHTML = `${renderInspector(doc, selected)}
    <details class="doc-entry-panel" ${doc.entries.length ? 'open' : ''}>
      <summary>Логические записи ${helpTooltip('Связывает выбранные блоки в одну RAG-запись. При поиске по одному блоку ассистент получает всю связанную запись.')}</summary>
      ${renderSelectedEntryEditor(doc)}
      <div class="doc-entry-list">${renderEntryList(doc)}</div>
    </details>`;
  bindSemanticTextEvents(card);
}

function setSelectedBlocks(ids, append = false, options = {}) {
  const doc = ensureSemanticDocState();
  const current = new Set(append ? (doc.selectedBlockIds || []) : []);
  ids.forEach(id => current.has(id) && append ? current.delete(id) : current.add(id));
  doc.selectedBlockIds = Array.from(current);
  doc.scrollToBlockId = options.scrollToBlockId || '';
  renderSemanticTextStructurePreview();
}

function updateBlock(blockId, patch, rerender = true) {
  const doc = ensureSemanticDocState();
  const index = doc.blocks.findIndex(block => block.id === blockId);
  if (index < 0) return;
  doc.blocks[index] = { ...doc.blocks[index], ...patch };
  markCurrentSourceDirty();
  if (rerender) notifyChanged();
}

function updateBlocksByIds(ids, patch, rerender = true) {
  const doc = ensureSemanticDocState();
  const idSet = new Set(asArray(ids));
  if (!idSet.size) return;
  doc.blocks = doc.blocks.map(block => idSet.has(block.id) ? { ...block, ...patch } : block);
  markCurrentSourceDirty();
  if (rerender) notifyChanged();
}

function updateSelectedBlocks(patch) {
  const doc = ensureSemanticDocState();
  updateBlocksByIds(doc.selectedBlockIds || [], patch);
}

function updateEntry(entryId, patch, rerender = true) {
  const doc = ensureSemanticDocState();
  const index = doc.entries.findIndex(entry => entry.entry_id === entryId);
  if (index < 0) return;
  const next = { ...doc.entries[index], ...patch };
  if ('title' in patch) next.title = String(patch.title || '').trim();
  doc.entries[index] = next;
  markCurrentSourceDirty();
  if (rerender) notifyChanged();
}

function uniqueBlockId(base) {
  const doc = ensureSemanticDocState();
  const used = new Set(doc.blocks.map(block => block.id));
  let candidate = base;
  let i = 2;
  while (used.has(candidate)) candidate = `${base}_${i++}`;
  return candidate;
}

function captureTextareaCaret(textarea, blockId) {
  if (!textarea || !blockId) return null;
  const doc = ensureSemanticDocState();
  const index = doc.blocks.findIndex(item => item.id === blockId);
  if (index >= 0 && doc.blocks[index].text !== textarea.value) {
    doc.blocks[index] = { ...doc.blocks[index], text: textarea.value };
    markCurrentSourceDirty();
  }
  return rememberBlockCaret(blockId, textarea.selectionStart ?? 0, textarea.selectionEnd ?? textarea.selectionStart ?? 0, 'textarea');
}

function caretForBlock(block, text) {
  const doc = ensureSemanticDocState();
  const textarea = document.getElementById('docBlockTextEditor');
  if (textarea && selectedBlocks(doc)[0]?.id === block.id) {
    captureTextareaCaret(textarea, block.id);
    text.value = String(textarea.value || '');
  } else {
    text.value = String(block.text || '');
  }
  const caret = doc.caret?.blockId === block.id ? doc.caret : null;
  if (!caret) return -1;
  return clampNumber(caret.start, 0, text.value.length);
}

function splitSelectedBlock() {
  const doc = ensureSemanticDocState();
  const block = selectedBlocks(doc)[0];
  if (!block) return;
  const textBox = { value: String(block.text || '') };
  let cut = caretForBlock(block, textBox);
  const text = textBox.value;
  if (cut <= 0 || cut >= text.length) {
    const sentence = text.slice(0, Math.ceil(text.length / 2)).lastIndexOf('. ');
    cut = sentence > 20 ? sentence + 1 : Math.ceil(text.length / 2);
  }
  const before = text.slice(0, cut).trim();
  const after = text.slice(cut).trim();
  if (!before || !after) return;
  const idx = doc.blocks.findIndex(item => item.id === block.id);
  const first = { ...block, text: before };
  const second = { ...block, id: uniqueBlockId(`${block.id}_part`), original_text: after, text: after, role: inferSplitContinuationRole(block, after) };
  doc.blocks.splice(idx, 1, first, second);
  doc.selectedBlockIds = [second.id];
  doc.caret = { blockId: second.id, start: 0, end: 0, source: 'split' };
  notifyChanged();
}

function splitSelectedBlockByLines() {
  const doc = ensureSemanticDocState();
  const block = selectedBlocks(doc)[0];
  if (!block) return;
  const parts = String(block.text || '').split(/\n{1,}|(?<=[.!?])\s+(?=[А-ЯA-ZӘІҢҒҮҰҚӨ])/).map(item => item.trim()).filter(Boolean);
  if (parts.length < 2) return;
  const idx = doc.blocks.findIndex(item => item.id === block.id);
  const next = parts.map((text, index) => ({
    ...block,
    id: index === 0 ? block.id : uniqueBlockId(`${block.id}_part_${index + 1}`),
    original_text: text,
    text,
    role: index === 0 ? block.role : inferSplitContinuationRole(block, text),
  }));
  doc.blocks.splice(idx, 1, ...next);
  doc.selectedBlockIds = next.map(item => item.id);
  doc.caret = next.length ? { blockId: next[0].id, start: 0, end: 0, source: 'split_auto' } : null;
  notifyChanged();
}

function mergeSelectedBlocks() {
  const doc = ensureSemanticDocState();
  const ids = new Set(doc.selectedBlockIds || []);
  const selected = doc.blocks.filter(block => ids.has(block.id));
  if (selected.length < 2) return;
  const first = selected[0];
  const mergedText = selected.map(block => block.text).filter(Boolean).join('\n\n');
  doc.blocks = doc.blocks.filter(block => !ids.has(block.id) || block.id === first.id).map(block => block.id === first.id ? { ...block, text: mergedText, original_text: selected.map(item => item.original_text || item.text).join('\n\n') } : block);
  doc.selectedBlockIds = [first.id];
  doc.caret = { blockId: first.id, start: mergedText.length, end: mergedText.length, source: 'merge' };
  notifyChanged();
}

function createEntryFromSelected(entryType = '') {
  const doc = ensureSemanticDocState();
  const blocks = selectedBlocks(doc).filter(block => block.role !== 'ignore');
  if (!blocks.length) return;
  const resolvedEntryType = inferLogicalEntryType(blocks, entryType);
  const entryId = uniqueEntryId(resolvedEntryType === 'document_list' ? 'list' : 'entry');
  const title = blocks.find(block => block.role?.startsWith('heading'))?.text || blocks[0].text.slice(0, 70) || `Запись ${doc.entries.length + 1}`;
  const domains = firstNonEmptyArray(blocks.map(block => block.domains), [currentSourceDomain()]);
  const knowledgeTypes = firstNonEmptyArray(blocks.map(block => block.knowledge_types), resolvedEntryType === 'document_list' ? ['document_list'] : []);
  doc.entries.push({
    entry_id: entryId,
    title,
    block_ids: blocks.map(block => block.id),
    domains,
    knowledge_types: knowledgeTypes,
    education_levels: [],
    language: '',
    entry_type: resolvedEntryType,
    logical_group_id: entryId,
  });
  doc.selectedEntryId = entryId;
  doc.blocks = doc.blocks.map(block => blocks.some(item => item.id === block.id) ? { ...block, entry_ids: Array.from(new Set([...(block.entry_ids || []), entryId])) } : block);
  notifyChanged();
}

function createListEntryFromSelected() {
  const doc = ensureSemanticDocState();
  const ids = new Set(doc.selectedBlockIds || []);
  doc.blocks = doc.blocks.map(block => ids.has(block.id) && !block.role?.startsWith('heading')
    ? { ...block, role: 'list_item', knowledge_types: Array.from(new Set([...(block.knowledge_types || []), 'document_list'])) }
    : block);
  createEntryFromSelected('document_list');
}

function uniqueEntryId(prefix) {
  const doc = ensureSemanticDocState();
  const used = new Set(doc.entries.map(entry => entry.entry_id));
  const base = `${state.currentSource?.source_id || 'source'}_${prefix}_${doc.entries.length + 1}`;
  let candidate = base;
  let i = 2;
  while (used.has(candidate)) candidate = `${base}_${i++}`;
  return candidate;
}

function firstNonEmptyArray(arrays, fallback = []) {
  for (const item of arrays) {
    const values = asArray(item);
    if (values.length) return values;
  }
  return fallback;
}

function deleteSelectedEntry() {
  const doc = ensureSemanticDocState();
  const id = doc.selectedEntryId;
  if (!id) return;
  doc.entries = doc.entries.filter(entry => entry.entry_id !== id);
  doc.blocks = doc.blocks.map(block => ({ ...block, entry_ids: (block.entry_ids || []).filter(entryId => entryId !== id) }));
  doc.selectedEntryId = '';
  notifyChanged();
}

function optionText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value).trim();
  if (typeof value === 'object') {
    return String(value.label || value.title || value.name || value.key || value.id || value.value || value.code || '').trim();
  }
  return String(value || '').trim();
}

function catalogValue(item = {}) {
  return optionText(item.key) || optionText(item.id) || optionText(item.value) || optionText(item.name) || optionText(item);
}

function catalogLabel(item = {}, fallback = '') {
  return optionText(item.label) || optionText(item.title) || optionText(item.name) || optionText(item.display_name) || optionText(item.key) || optionText(item.id) || optionText(item.value) || fallback;
}

function domainOptions() {
  const domains = Array.isArray(state.catalog?.domains) ? state.catalog.domains : [];
  const values = domains
    .filter(item => item && item.enabled !== false)
    .map(item => {
      const value = catalogValue(item);
      return { value, label: catalogLabel(item, value) || value };
    })
    .filter(item => item.value && item.label);
  return values.length ? values : ['programs','tuition','scores','timeline','contacts','housing','benefits','documents','university_info'].map(value => ({ value, label: value }));
}

function systemFieldOptions() {
  const fields = Array.isArray(state.catalog?.system_fields) ? state.catalog.system_fields : [];
  return fields
    .filter(field => field && (!field.applies_to?.length || field.applies_to.includes('paragraph') || field.applies_to.includes('block')))
    .map(field => {
      const value = optionText(field.key) || optionText(field.id) || optionText(field.value) || optionText(field.name);
      return { value, label: catalogLabel(field, value) || value };
    })
    .filter(field => field.value && field.label);
}

function optionByValue(options = []) {
  const map = {};
  options.forEach(item => {
    const value = String(item.value || '');
    if (!value) return;
    map[value] = item;
    map[value.toLowerCase()] = item;
  });
  return map;
}

function normalizePickerValues(values, options = []) {
  const allowed = new Map(options.map(item => [String(item.value).toLowerCase(), String(item.value)]));
  const result = [];
  asArray(values).forEach(value => {
    const raw = String(value || '').trim();
    if (!raw) return;
    const normalized = allowed.get(raw.toLowerCase()) || raw;
    if (allowed.size && !allowed.has(raw.toLowerCase())) return;
    if (!result.includes(normalized)) result.push(normalized);
  });
  return result;
}

function pickerOptionsAttr(options = []) {
  return escapeAttr(encodeURIComponent(JSON.stringify(options.map(item => ({
    value: String(item.value || '').trim(),
    label: String(item.label || item.value || '').trim() || String(item.value || '').trim()
  })).filter(item => item.value && item.label))));
}

function pickerValuesAttr(values = []) {
  return escapeAttr(JSON.stringify(asArray(values).map(String)));
}

function renderMultiPickerChips(options, values) {
  const byValue = optionByValue(options);
  if (!values.length) return '<span class="multi-picker__empty">не задано</span>';
  return values.map(value => {
    const label = byValue[String(value)]?.label || value;
    return `<span class="multi-chip"><span>${escapeHtml(label)}</span><button type="button" title="Убрать" data-multi-remove="${escapeAttr(value)}">×</button></span>`;
  }).join('');
}

function renderMultiPickerMenu(options, values, placeholder = '+ добавить') {
  const selected = new Set(values.map(value => String(value).toLowerCase()));
  const rows = options.map(item => {
    const value = String(item.value || '').trim();
    const label = String(item.label || value).trim() || value;
    if (!value || !label) return '';
    return `<label class="multi-picker__option" title="${escapeAttr(label)}">
      <input class="multi-picker__check" type="checkbox" value="${escapeAttr(value)}" ${selected.has(value.toLowerCase()) ? 'checked' : ''}>
      <span class="multi-picker__option-label">${escapeHtml(label)}</span>
    </label>`;
  }).filter(Boolean).join('') || '<div class="multi-picker__empty-row">нет значений</div>';
  const title = values.length ? `выбрано: ${values.length}` : placeholder;
  return `<div class="multi-picker__control">
    <button class="multi-picker__button" type="button" data-multi-toggle="true">${escapeHtml(title)}</button>
    <div class="multi-picker__menu" hidden>
      <div class="multi-picker__menu-head">
        <span>${escapeHtml(placeholder.replace(/^\+\s*/, ''))}</span>
        <button type="button" data-multi-clear="true">очистить</button>
      </div>
      <div class="multi-picker__options">${rows}</div>
    </div>
  </div>`;
}

function multiDropdownHtml(id, options, values, placeholder = '+ добавить') {
  const normalizedOptions = options.map(item => {
    const value = optionText(item.value) || optionText(item.key) || optionText(item.id) || optionText(item.name) || optionText(item);
    const label = catalogLabel(item, value) || value;
    return { value, label };
  }).filter(item => item.value && item.label);
  const normalizedValues = normalizePickerValues(values, normalizedOptions);
  return `<div class="multi-picker" data-multi-picker="${escapeAttr(id)}" data-options="${pickerOptionsAttr(normalizedOptions)}" data-placeholder="${escapeAttr(placeholder)}">
    <input type="hidden" id="${escapeAttr(id)}" value="${pickerValuesAttr(normalizedValues)}">
    <div class="multi-picker__chips">${renderMultiPickerChips(normalizedOptions, normalizedValues)}</div>
    ${renderMultiPickerMenu(normalizedOptions, normalizedValues, placeholder)}
  </div>`;
}

function pickerValues(target, id) {
  const raw = target?.querySelector(`#${CSS.escape(id)}`)?.value || '[]';
  try {
    const parsed = JSON.parse(raw);
    return asArray(parsed).map(String).filter(Boolean);
  } catch (_) {
    return [];
  }
}

function pickerOptions(picker) {
  try {
    return JSON.parse(decodeURIComponent(picker.dataset.options || '%5B%5D')) || [];
  } catch (_) {
    return [];
  }
}

function setPickerValues(picker, values) {
  const options = pickerOptions(picker);
  const normalized = normalizePickerValues(values, options);
  const hidden = picker.querySelector('input[type="hidden"]');
  if (hidden) hidden.value = JSON.stringify(normalized);
  const chips = picker.querySelector('.multi-picker__chips');
  if (chips) chips.innerHTML = renderMultiPickerChips(options, normalized);
  const control = picker.querySelector('.multi-picker__control');
  if (control) {
    const placeholder = picker.dataset.placeholder || '+ добавить';
    const menuWasOpen = !control.querySelector('.multi-picker__menu')?.hidden;
    control.outerHTML = renderMultiPickerMenu(options, normalized, placeholder);
    const nextMenu = picker.querySelector('.multi-picker__menu');
    if (nextMenu) nextMenu.hidden = !menuWasOpen;
  }
  return normalized;
}

function checkedPickerValues(picker) {
  return Array.from(picker.querySelectorAll('.multi-picker__check:checked'))
    .map(input => input.value)
    .filter(Boolean);
}

function closeOtherPickers(current) {
  document.querySelectorAll('.multi-picker__menu').forEach(menu => {
    if (!current || !current.contains(menu)) menu.hidden = true;
  });
}

function bindMultiPickers(target, callbacks = {}) {
  target.querySelectorAll('[data-multi-picker]').forEach(picker => {
    const id = picker.dataset.multiPicker;
    if (picker.closest('[data-doc-context-popover]') && !callbacks[id]) return;
    const emit = values => {
      const normalized = setPickerValues(picker, values);
      if (callbacks[id]) callbacks[id](normalized);
    };
    picker.onchange = event => {
      if (!event.target.classList?.contains('multi-picker__check')) return;
      emit(checkedPickerValues(picker));
    };
    picker.onclick = event => {
      const toggle = event.target.closest('[data-multi-toggle]');
      if (toggle) {
        event.preventDefault();
        const menu = picker.querySelector('.multi-picker__menu');
        if (!menu) return;
        const shouldOpen = menu.hidden;
        closeOtherPickers(picker);
        menu.hidden = !shouldOpen;
        return;
      }
      const clear = event.target.closest('[data-multi-clear]');
      if (clear) {
        event.preventDefault();
        emit([]);
        return;
      }
      const remove = event.target.closest('[data-multi-remove]');
      if (remove) {
        event.preventDefault();
        emit(pickerValues(target, id).filter(value => value !== remove.dataset.multiRemove));
      }
    };
  });
}

function singleSelectHtml(id, options, value) {
  return `<select id="${escapeAttr(id)}">${options.map(item => `<option value="${escapeAttr(item.value)}" ${String(item.value) === String(value || '') ? 'selected' : ''}>${escapeHtml(item.label)}</option>`).join('')}</select>`;
}

function contextTargetBlocks(doc) {
  const menu = doc.contextMenu || {};
  const ids = asArray(menu.blockIds?.length ? menu.blockIds : menu.blockId).filter(Boolean);
  return ids.length ? ids : asArray(doc.selectedBlockIds || []);
}

function roleHelpText(value) {
  return ({
    body: 'Обычный фактовый текст, который может попасть в RAG.',
    list_item: 'Пункт списка. Несколько пунктов надо связать как список, чтобы RAG подтягивал полный перечень.',
    meta: 'Служебная информация: название, источник, пометки. Обычно не основной ответ пользователю.',
    heading_1: 'Крупная глава документа.',
    heading_2: 'Подглава внутри главы.',
    heading_3: 'Секция или заголовок логического блока.',
    ignore: 'Исключить блок из RAG-сборки.',
    draft: 'Пометить как требующий проверки. Используй для сомнительных блоков перед финальной сборкой.'
  })[value] || '';
}

function renderRoleButtons(prefix = 'popup') {
  const compactRoles = [
    { value: 'body', label: 'Данные' },
    { value: 'list_item', label: 'Пункт списка' },
    { value: 'meta', label: 'Мета' },
    { value: 'heading_1', label: 'Глава' },
    { value: 'heading_2', label: 'Подглава' },
    { value: 'heading_3', label: 'Секция' },
    { value: 'ignore', label: 'Игнор' },
    { value: 'draft', label: 'На проверку' },
  ];
  return compactRoles.map(item => `<button class="doc-popover-chip" type="button" title="${escapeAttr(roleHelpText(item.value))}" data-doc-popup-role="${escapeAttr(item.value)}">${escapeHtml(item.label)}</button>`).join('');
}

function renderBlockContextPopup(doc) {
  const menu = doc.contextMenu || null;
  if (!menu?.blockId) return '';
  const block = doc.blocks.find(item => item.id === menu.blockId);
  if (!block) return '';
  const ids = contextTargetBlocks(doc);
  const selected = doc.blocks.filter(item => ids.includes(item.id));
  const mergedValues = key => {
    const first = selected[0]?.[key] || [];
    return first.filter(value => selected.every(block => asArray(block[key]).includes(value)));
  };
  const left = Math.max(8, Math.min(Number(menu.x || 0), window.innerWidth - 360));
  const top = Math.max(8, Math.min(Number(menu.y || 0), window.innerHeight - 520));
  return `<div class="doc-context-popover" style="left:${left}px;top:${top}px" data-doc-context-popover="true">
    <div class="doc-context-popover__head">
      <strong>${escapeHtml(ids.length > 1 ? `Выбрано: ${ids.length}` : `Блок ${block.id}`)}</strong>
      <button type="button" title="Закрыть" data-doc-popup-close="true">×</button>
    </div>
    <div class="doc-context-popover__section">
      <div class="doc-popover-title">Статус</div>
      <div class="doc-popover-grid">${renderRoleButtons()}</div>
    </div>
    <div class="doc-context-popover__section">
      <div class="doc-popover-title">Метки</div>
      <label class="field-label field-label--compact">Домены${multiDropdownHtml('docPopupDomains', domainOptions(), mergedValues('domains'), '+ домен')}</label>
      <label class="field-label field-label--compact">Типы знания${multiDropdownHtml('docPopupKnowledgeTypes', KNOWLEDGE_TYPES, mergedValues('knowledge_types'), '+ тип')}</label>
      <label class="field-label field-label--compact">Системные поля${multiDropdownHtml('docPopupSystemFields', systemFieldOptions(), mergedValues('system_fields'), '+ системное поле')}</label>
      <label class="field-label field-label--compact">Уровни${multiDropdownHtml('docPopupLevels', EDUCATION_LEVELS.filter(item => item.value), mergedValues('education_levels'), '+ уровень')}</label>
    </div>
    <div class="doc-context-popover__section">
      <div class="doc-popover-title">Текст</div>
      <div class="doc-popover-actions">
        <button class="btn" type="button" data-doc-popup-split="cursor">Разрезать по курсору</button>
        <button class="btn" type="button" data-doc-popup-split="auto">Разрезать авто</button>
        <button class="btn" type="button" data-doc-popup-list-entry="true">Связать как список</button>
        <button class="btn btn--brand" type="button" data-doc-popup-entry="true">Связать в RAG-запись</button>
      </div>
      
    </div>
    <div class="doc-context-popover__section">
      <div class="doc-popover-title">Сброс</div>
      <div class="doc-popover-actions">
        <button class="btn" type="button" data-doc-popup-clear="domains">Домены</button>
        <button class="btn" type="button" data-doc-popup-clear="knowledge_types">Типы</button>
        <button class="btn" type="button" data-doc-popup-clear="system_fields">Системные</button>
        <button class="btn" type="button" data-doc-popup-clear="education_levels">Уровни</button>
      </div>
    </div>
  </div>`;
}

function renderOutline(blocks) {
  const headings = blocks.filter(block => blockLevel(block));
  if (!headings.length) return '<div class="muted">Заголовки не найдены.</div>';
  return headings.map(block => {
    const level = blockLevel(block) || 1;
    return `<button class="doc-outline-item doc-outline-item--l${level}" type="button" data-doc-jump="${escapeAttr(block.id)}"><span>${escapeHtml(block.text.slice(0, 80))}</span></button>`;
  }).join('');
}

function textPreview(value = '', limit = 160) {
  const clean = cleanText(value);
  if (!clean) return '';
  return clean.length > limit ? `${clean.slice(0, Math.max(0, limit - 1))}…` : clean;
}

function sourceTreeTitle() {
  return state.currentParsed?.title
    || state.currentSource?.title
    || state.currentSource?.source_id
    || currentLogicalSourceId()
    || 'Источник';
}

function entryMetaLine(entry = {}) {
  const parts = [];
  if (Array.isArray(entry.section_path) && entry.section_path.length) parts.push(entry.section_path.join(' › '));
  const blockCount = asArray(entry.metadata?.block_ids || []).length;
  if (blockCount) parts.push(`${blockCount} блок.`);
  return parts.join(' · ');
}

function renderProgramEntryTree(entry, index, blockById, selectedEntryId = '') {
  const blockIds = asArray(entry.metadata?.block_ids || entry.block_ids);
  const realEntry = ensureSemanticDocState().entries.find(item => item.entry_id === entry.entry_id);
  const selected = selectedEntryId && selectedEntryId === entry.entry_id;
  const title = entry.title || `Запись ${index + 1}`;
  const meta = entryMetaLine(entry);
  const blocks = blockIds.map(id => blockById[id]).filter(Boolean);
  const blockRows = blocks.slice(0, 4).map(block => `<button class="doc-program-block" type="button" data-doc-jump="${escapeAttr(block.id)}">${escapeHtml(textPreview(block.text, 82))}</button>`).join('');
  const hidden = blocks.length > 4 ? `<div class="doc-program-more">+${blocks.length - 4} блок.</div>` : '';
  return `<div class="doc-program-entry ${selected ? 'active' : ''}">
    <button class="doc-program-entry__main" type="button" data-doc-program-blocks="${escapeAttr(JSON.stringify(blockIds))}" ${realEntry ? `data-doc-program-entry="${escapeAttr(entry.entry_id)}"` : ''}>
      <strong>${escapeHtml(title)}</strong>
      ${meta ? `<span>${escapeHtml(meta)}</span>` : ''}
      <em>${escapeHtml(textPreview(entry.text, 145))}</em>
    </button>
    ${blockRows || hidden ? `<div class="doc-program-blocks">${blockRows}${hidden}</div>` : ''}
  </div>`;
}

function renderDocumentContextTree(blocks) {
  const visible = blocks.filter(block => block.role !== 'ignore' && cleanText(block.text));
  if (!visible.length) return '<div class="muted">Нет видимых блоков.</div>';
  let rows = '';
  visible.forEach(block => {
    const level = blockLevel(block);
    if (level) {
      rows += `<button class="doc-context-node doc-context-node--l${level}" type="button" data-doc-jump="${escapeAttr(block.id)}"><strong>${escapeHtml(textPreview(block.text, 94))}</strong></button>`;
      return;
    }
    const depth = Math.min(3, Math.max(1, (block.section_path || []).length + 1));
    rows += `<button class="doc-context-node doc-context-node--leaf doc-context-node--l${depth}" type="button" data-doc-jump="${escapeAttr(block.id)}">${escapeHtml(textPreview(block.text, 86))}</button>`;
  });
  return rows;
}

function renderProgramTree(blocks, doc) {
  const blockById = Object.fromEntries(blocks.map(block => [block.id, block]));
  const entries = buildSemanticTextEntriesPreview();
  const entriesHtml = entries.length
    ? entries.map((entry, index) => renderProgramEntryTree(entry, index, blockById, doc.selectedEntryId)).join('')
    : '<div class="muted">Записи еще не собираются. Разметь блоки или свяжи их в RAG-запись.</div>';
  return `<div class="doc-program-tree">
    <div class="doc-program-source">
      <div class="doc-program-source__label">Источник</div>
      <strong>${escapeHtml(textPreview(sourceTreeTitle(), 120))}</strong>
    </div>
    <details class="doc-program-section" open>
      <summary>Логические RAG-записи</summary>
      <div class="doc-program-list">${entriesHtml}</div>
    </details>
    <details class="doc-program-section">
      <summary>Контекст документа</summary>
      <div class="doc-context-tree">${renderDocumentContextTree(blocks)}</div>
    </details>
  </div>`;
}

function entriesForBlock(doc, blockId) {
  return (doc.entries || []).filter(entry => asArray(entry.block_ids).includes(blockId));
}

function shortEntryTitle(entry) {
  const title = String(entry.title || entry.entry_id || 'логическая запись').trim();
  return title.length > 44 ? `${title.slice(0, 41)}…` : title;
}

function unlinkBlockFromEntry(entryId, blockId) {
  const doc = ensureSemanticDocState();
  const entry = doc.entries.find(item => item.entry_id === entryId);
  if (!entry || !blockId) return;
  entry.block_ids = asArray(entry.block_ids).filter(id => id !== blockId);
  doc.blocks = doc.blocks.map(block => block.id === blockId
    ? { ...block, entry_ids: asArray(block.entry_ids).filter(id => id !== entryId) }
    : block);
  doc.entries = doc.entries.filter(item => asArray(item.block_ids).length > 0);
  if (doc.selectedEntryId === entryId && !doc.entries.some(item => item.entry_id === entryId)) doc.selectedEntryId = '';
  notifyChanged();
}

function syncBlockEntryIds(doc = ensureSemanticDocState()) {
  const entryByBlock = new Map();
  (doc.entries || []).forEach(entry => {
    asArray(entry.block_ids).forEach(blockId => {
      if (!entryByBlock.has(blockId)) entryByBlock.set(blockId, []);
      entryByBlock.get(blockId).push(entry.entry_id);
    });
  });
  doc.blocks = doc.blocks.map(block => ({
    ...block,
    entry_ids: Array.from(new Set([...(block.entry_ids || []), ...(entryByBlock.get(block.id) || [])])).filter(entryId => doc.entries.some(entry => entry.entry_id === entryId)),
  }));
}

function blockStatusClass(block) {
  return [
    block.role === 'ignore' ? 'is-ignored' : '',
    block.role?.startsWith('heading') ? 'is-heading' : '',
    block.role === 'meta' ? 'is-meta' : '',
    block.role === 'list_item' ? 'is-list-item' : '',
    block.role === 'draft' ? 'is-draft' : '',
  ].filter(Boolean).join(' ');
}

function renderBlockCard(block, selectedIds) {
  const doc = ensureSemanticDocState();
  const selected = selectedIds.has(block.id);
  const path = sectionPathForBlock(block).join(' / ');
  const showPath = Boolean(path) && block.role !== 'list_item' && !entriesForBlock(doc, block.id).length;
  const logicalEntries = entriesForBlock(doc, block.id);
  const entryChips = logicalEntries.map(entry => `<span class="pill pill--entry" title="Логическая запись: ${escapeAttr(entry.title || entry.entry_id)}"><span>↔ ${escapeHtml(shortEntryTitle(entry))}</span><button type="button" title="Убрать связь с логической записью" data-doc-unlink-entry="${escapeAttr(entry.entry_id)}" data-doc-unlink-block="${escapeAttr(block.id)}">×</button></span>`);
  const chips = [
    ...(block.domains || []).map(item => `<span class="pill pill--soft">${escapeHtml(item)}</span>`),
    ...(block.knowledge_types || []).map(item => `<span class="pill">${escapeHtml(KNOWLEDGE_TYPES.find(k => k.value === item)?.label || item)}</span>`),
    ...(block.system_fields || []).map(item => `<span class="pill pill--system">${escapeHtml(item)}</span>`),
    ...entryChips,
  ].join('');
  const caret = ensureSemanticDocState().caret?.blockId === block.id ? ensureSemanticDocState().caret : null;
  const caretBadge = caret && caret.start > 0 && caret.start < String(block.text || '').length
    ? `<span class="pill pill--cursor" title="Позиция разрезания сохранена">курсор: ${caret.start}</span>`
    : '';
  return `<article class="doc-semantic-block ${blockStatusClass(block)} ${selected ? 'is-selected' : ''}" data-doc-block-id="${escapeAttr(block.id)}">
    <label class="doc-block-check"><input type="checkbox" data-doc-select-block="${escapeAttr(block.id)}" ${selected ? 'checked' : ''}></label>
    <div class="doc-semantic-block__body">
      <div class="doc-semantic-block__head">
        <span class="mono">${escapeHtml(block.id)}</span>
        <span class="pill">${escapeHtml(roleLabel(block.role))}</span>
        ${caretBadge}
        ${showPath ? `<span class="muted doc-section-path">${escapeHtml(path)}</span>` : ''}
      </div>
      <div class="doc-semantic-block__text" data-doc-text-region="true">${escapeHtml(block.text)}</div>
      ${chips ? `<div class="doc-semantic-block__chips">${chips}</div>` : ''}
    </div>
  </article>`;
}

function renderSelectedEntryEditor(doc) {
  const entry = doc.selectedEntryId ? doc.entries.find(item => item.entry_id === doc.selectedEntryId) : null;
  if (!entry) {
    return `<div class="inspector-hintline">Выберите запись из списка ${helpTooltip('После выбора логической записи можно изменить название, тип, домены, уровни, язык и примечание.')}</div>`;
  }
  return `<div class="doc-entry-editor" data-doc-entry-editor="${escapeAttr(entry.entry_id)}">
    <div class="workspace-card__head workspace-card__head--compact">
      <h4>Связь / RAG-запись</h4>
      <span class="pill">${asArray(entry.block_ids).length} блок.</span>
    </div>
    <label class="field-label">Название связи<input id="docEntryTitleEditor" value="${escapeAttr(entry.title || '')}" placeholder="Например: Перечень документов для поступления в докторантуру" /></label>
    <label class="field-label">Тип RAG-записи${singleSelectHtml('docEntryTypeEditor', LOGICAL_ENTRY_TYPES, entry.entry_type || inferLogicalEntryType(asArray(entry.block_ids).map(id => doc.blocks.find(block => block.id === id)).filter(Boolean)))}</label>
    <label class="field-label">Домены${multiDropdownHtml('docEntryDomains', domainOptions(), entry.domains || [], '+ домен')}</label>
    <label class="field-label">Типы знания${multiDropdownHtml('docEntryKnowledgeTypes', KNOWLEDGE_TYPES, entry.knowledge_types || [], '+ тип знания')}</label>
    <div class="form-grid form-grid--two">
      <label class="field-label">Уровни${multiDropdownHtml('docEntryLevels', EDUCATION_LEVELS.filter(item => item.value), entry.education_levels || [], '+ уровень')}</label>
      <label class="field-label">Язык${singleSelectHtml('docEntryLang', LANGUAGES, entry.language || '')}</label>
    </div>
    <label class="field-label">Примечание<textarea id="docEntryNotesEditor" rows="3" placeholder="Комментарий к логической связи">${escapeHtml(entry.notes || '')}</textarea></label>
    <button class="danger danger--small" type="button" data-doc-delete-entry="true">Удалить логическую запись целиком</button>
  </div>`;
}

function renderEntryList(doc) {
  if (!doc.entries.length) return '<div class="muted">Логические записи еще не созданы. Выдели несколько блоков и нажми “Связать в запись”.</div>';
  const blockById = Object.fromEntries(doc.blocks.map(block => [block.id, block]));
  return doc.entries.map(entry => {
    const blockLabels = asArray(entry.block_ids).map(id => blockById[id]?.id || id).join(', ');
    return `<div class="doc-entry-item ${doc.selectedEntryId === entry.entry_id ? 'active' : ''}" data-doc-entry-box="${escapeAttr(entry.entry_id)}">
      <button class="doc-entry-item__main" type="button" title="Выбрать и изменить название связи" data-doc-entry-id="${escapeAttr(entry.entry_id)}">
        <strong>${escapeHtml(entry.title || entry.entry_id)}</strong>
        <span>${escapeHtml(logicalEntryTypeLabel(entry.entry_type || ''))} · ${asArray(entry.block_ids).length} блок. · ${escapeHtml(blockLabels)}</span>
      </button>
      <button class="doc-entry-item__remove" type="button" title="Удалить логическую запись" data-doc-delete-entry-id="${escapeAttr(entry.entry_id)}">×</button>
    </div>`;
  }).join('');
}


function renderInspector(doc, selected) {
  if (!selected.length) {
    return `<div class="doc-inspector-empty">
      <h4>Документ ${helpTooltip('Выделите блок, чтобы редактировать текст, роль, темы и системные метки. Несоседние блоки связываются через логическую запись.')}</h4>
      <div class="metric-grid metric-grid--compact">
        <span><strong>${doc.blocks.length}</strong><small>блоков</small></span>
        <span><strong>${doc.entries.length}</strong><small>записей</small></span>
        <span><strong>${doc.blocks.filter(b => b.role === 'ignore').length}</strong><small>игнор</small></span>
      </div>
    </div>`;
  }
  if (selected.length > 1) {
    return `<div class="doc-inspector-selected">
      <h4>Выбрано блоков: ${selected.length} ${helpTooltip('Разметку можно делать через правый клик по выбранным блокам. Здесь остается точное массовое редактирование полей.')}</h4>
      <label class="field-label">Роль${singleSelectHtml('docBulkRole', [{ value: '', label: 'не менять' }, ...BLOCK_ROLES], '')}</label>
      <label class="field-label">Домены${multiDropdownHtml('docBulkDomains', domainOptions(), [], '+ домен')}</label>
      <label class="field-label">Типы знания${multiDropdownHtml('docBulkKnowledgeTypes', KNOWLEDGE_TYPES, [], '+ тип')}</label>
      <label class="field-label">Системные поля${multiDropdownHtml('docBulkSystemFields', systemFieldOptions(), [], '+ системное поле')}</label>
      <details class="inspector-inline-details">
        <summary>Действия с выбранными</summary>
        <div class="toolbar toolbar--compact toolbar--relaxed">
          <button class="btn" type="button" data-doc-merge-selected="true">Склеить текст физически</button>
          <button class="btn" type="button" data-doc-create-list-entry="true">Связать как список</button>
          <button class="btn btn--brand" type="button" data-doc-create-entry="true">Связать в RAG-запись</button>
        </div>
      </details>
    </div>`;
  }
  const block = selected[0];
  const linkedEntries = entriesForBlock(doc, block.id);
  const linksHtml = linkedEntries.length ? `<div class="doc-linked-entries">${linkedEntries.map(entry => `<div class="doc-linked-entry"><span>↔ ${escapeHtml(shortEntryTitle(entry))}</span><button type="button" data-doc-unlink-entry="${escapeAttr(entry.entry_id)}" data-doc-unlink-block="${escapeAttr(block.id)}">Убрать связь</button></div>`).join('')}</div>` : '<div class="muted">Блок не входит в логическую запись.</div>';
  return `<div class="doc-inspector-selected">
    <div class="workspace-card__head workspace-card__head--compact"><h4>Блок ${escapeHtml(block.id)}</h4><span class="pill">${escapeHtml(roleLabel(block.role))}</span></div>
    <details class="inspector-inline-details" ${linkedEntries.length ? 'open' : ''}>
      <summary>Логические связи</summary>
      ${linksHtml}
    </details>
    <label class="field-label">Роль${singleSelectHtml('docBlockRole', BLOCK_ROLES, block.role)}</label>
    <label class="field-label">Текст<textarea id="docBlockTextEditor">${escapeHtml(block.text)}</textarea></label>
    <label class="field-label">Домены${multiDropdownHtml('docBlockDomains', domainOptions(), block.domains, '+ домен')}</label>
    <label class="field-label">Типы знания${multiDropdownHtml('docBlockKnowledgeTypes', KNOWLEDGE_TYPES, block.knowledge_types, '+ тип знания')}</label>
    <label class="field-label">Системные поля${multiDropdownHtml('docBlockSystemFields', systemFieldOptions(), block.system_fields, '+ системное поле')}</label>
    <div class="form-grid form-grid--two">
      <label class="field-label">Уровни${multiDropdownHtml('docBlockLevels', EDUCATION_LEVELS.filter(item => item.value), block.education_levels, '+ уровень')}</label>
      <label class="field-label">Язык${singleSelectHtml('docBlockLang', LANGUAGES, block.language)}</label>
    </div>
    <details class="inspector-inline-details">
      <summary>Сборка записи</summary>
      <label class="check-row"><input id="docBlockMergePrev" type="checkbox" ${block.merge_with_previous ? 'checked' : ''}> склеивать с предыдущим блоком при сборке RAG-записи</label>
    </details>
    <details class="inspector-inline-details">
      <summary>Действия текста</summary>
      <div class="toolbar toolbar--compact toolbar--relaxed">
        <button class="btn" type="button" data-doc-split-cursor="true">Разрезать по сохраненному курсору</button>
        <button class="btn" type="button" data-doc-split-auto="true">Разрезать авто</button>
        <button class="btn" type="button" data-doc-create-list-entry="true">Связать как список</button>
        <button class="btn btn--brand" type="button" data-doc-create-entry="true">Связать в RAG-запись</button>
      </div>
    </details>
  </div>`;
}

export function renderSemanticTextStructurePreview() {
  const target = refs.sourceStructurePreview;
  if (!target) return;
  const scrollSnapshot = captureSemanticScroll(target);
  const doc = ensureSemanticDocState();
  const blocks = semanticDocumentBlocks();
  if (!blocks.length) {
    target.innerHTML = '<div class="status">Нет текстовых блоков для разметки.</div>';
    renderGlobalSemanticTextInspector(doc, []);
    return;
  }
  const selectedIds = new Set(doc.selectedBlockIds || []);
  const selected = selectedBlocks(doc);
  const scrollToBlockId = doc.scrollToBlockId || '';
  doc.scrollToBlockId = '';
  target.innerHTML = `<div class="structure-summary-row">
      <span class="pill">блоков: ${blocks.length}</span>
      <span class="pill">записей: ${doc.entries.length}</span>
      <span class="pill">домен: ${escapeHtml(currentSourceDomain())}</span>
      <span class="pill">режим: semantic text</span>
    </div>
    <div class="doc-semantic-editor">
      <aside class="doc-semantic-outline">
        <div class="doc-panel-title">RAG-превью</div>
        ${renderProgramTree(blocks, doc)}
      </aside>
      <section class="doc-semantic-main">
        <div class="doc-semantic-toolbar doc-semantic-toolbar--select-only">
          <label class="doc-select-all-toggle" title="Выбрать все блоки"><input type="checkbox" data-doc-select-all-toggle="true" ${selected.length === blocks.length ? 'checked' : ''}> Все</label>
          <button class="btn btn--micro" type="button" data-doc-clear-selection="true" title="Снять выбор">Снять</button>
        </div>
        <div class="doc-semantic-blocks">${blocks.map(block => renderBlockCard(block, selectedIds)).join('')}</div>
      </section>
    </div>
    ${renderBlockContextPopup(doc)}`;
  renderGlobalSemanticTextInspector(doc, selected);
  bindSemanticTextEvents(target);
  restoreSemanticScroll(scrollSnapshot, { scrollToBlockId });
}


function captureSelectionCaretFromBlockText(textRegion) {
  const card = textRegion?.closest?.('[data-doc-block-id]');
  const blockId = card?.dataset?.docBlockId;
  if (!blockId) return false;
  const selection = window.getSelection?.();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false;
  const range = selection.getRangeAt(0);
  if (!textRegion.contains(range.startContainer) || !textRegion.contains(range.endContainer)) return false;
  const before = range.cloneRange();
  before.selectNodeContents(textRegion);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  const end = start + range.toString().length;
  rememberBlockCaret(blockId, start, end, 'selection');
  return true;
}

function restoreTextareaCaret(textarea, blockId) {
  const caret = ensureSemanticDocState().caret;
  if (!textarea || caret?.blockId !== blockId) return;
  const start = clampNumber(caret.start, 0, textarea.value.length);
  const end = clampNumber(caret.end ?? start, start, textarea.value.length);
  requestAnimationFrame(() => {
    try { textarea.setSelectionRange(start, end); } catch (_) { /* noop */ }
  });
}

function bindSemanticTextEvents(target) {
  target.querySelectorAll('[data-doc-select-block]').forEach(input => {
    input.onchange = event => setSelectedBlocks([event.currentTarget.dataset.docSelectBlock], true);
  });
  target.querySelectorAll('[data-doc-block-id]').forEach(card => {
    card.onclick = event => {
      if (event.target.closest('input,select,textarea,button')) return;
      const doc = ensureSemanticDocState();
      doc.contextMenu = null;
      setSelectedBlocks([card.dataset.docBlockId], event.ctrlKey || event.metaKey);
    };
    card.oncontextmenu = event => {
      event.preventDefault();
      const doc = ensureSemanticDocState();
      const blockId = card.dataset.docBlockId;
      const current = new Set(doc.selectedBlockIds || []);
      if (!current.has(blockId)) doc.selectedBlockIds = [blockId];
      const ids = current.has(blockId) && current.size > 1 ? Array.from(current) : [blockId];
      doc.contextMenu = { blockId, blockIds: ids, x: event.clientX, y: event.clientY };
      renderSemanticTextStructurePreview();
    };
  });
  target.querySelectorAll('[data-doc-text-region]').forEach(textRegion => {
    textRegion.addEventListener('mouseup', () => captureSelectionCaretFromBlockText(textRegion));
    textRegion.addEventListener('keyup', () => captureSelectionCaretFromBlockText(textRegion));
  });
  target.querySelectorAll('[data-doc-jump]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      setSelectedBlocks([button.dataset.docJump], false, { scrollToBlockId: button.dataset.docJump });
    };
  });
  target.querySelectorAll('[data-doc-program-blocks]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const doc = ensureSemanticDocState();
      let ids = [];
      try { ids = JSON.parse(button.dataset.docProgramBlocks || '[]') || []; } catch (_) { ids = []; }
      ids = asArray(ids).filter(id => doc.blocks.some(block => block.id === id));
      doc.selectedBlockIds = ids;
      doc.selectedEntryId = button.dataset.docProgramEntry || '';
      doc.scrollToBlockId = ids[0] || '';
      renderSemanticTextStructurePreview();
    };
  });
  target.querySelector('[data-doc-select-all]')?.addEventListener('click', () => {
    const doc = ensureSemanticDocState();
    doc.selectedBlockIds = doc.blocks.map(block => block.id);
    renderSemanticTextStructurePreview();
  });
  target.querySelector('[data-doc-select-all-toggle]')?.addEventListener('change', event => {
    const doc = ensureSemanticDocState();
    doc.selectedBlockIds = event.currentTarget.checked ? doc.blocks.map(block => block.id) : [];
    renderSemanticTextStructurePreview();
  });
  target.querySelector('[data-doc-clear-selection]')?.addEventListener('click', () => {
    ensureSemanticDocState().selectedBlockIds = [];
    renderSemanticTextStructurePreview();
  });
  target.querySelectorAll('[data-doc-mark-selected]').forEach(button => {
    button.onclick = () => updateSelectedBlocks({ role: button.dataset.docMarkSelected });
  });
  target.querySelectorAll('[data-doc-role-quick]').forEach(button => {
    button.onclick = () => {
      const block = selectedBlocks()[0];
      if (block) updateBlock(block.id, { role: button.dataset.docRoleQuick });
    };
  });
  target.querySelector('[data-doc-split-cursor]')?.addEventListener('click', splitSelectedBlock);
  target.querySelector('[data-doc-split-auto]')?.addEventListener('click', splitSelectedBlockByLines);
  target.querySelector('[data-doc-merge-selected]')?.addEventListener('click', mergeSelectedBlocks);
  target.querySelectorAll('[data-doc-create-entry]').forEach(button => button.onclick = () => createEntryFromSelected());
  target.querySelectorAll('[data-doc-create-list-entry]').forEach(button => button.onclick = createListEntryFromSelected);
  bindContextPopoverEvents(target);
  target.querySelector('[data-doc-delete-entry]')?.addEventListener('click', deleteSelectedEntry);
  target.querySelectorAll('[data-doc-delete-entry-id]').forEach(button => {
    button.onclick = event => {
      event.stopPropagation();
      const doc = ensureSemanticDocState();
      doc.selectedEntryId = button.dataset.docDeleteEntryId;
      deleteSelectedEntry();
    };
  });
  target.querySelectorAll('[data-doc-unlink-entry]').forEach(button => {
    button.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      unlinkBlockFromEntry(button.dataset.docUnlinkEntry, button.dataset.docUnlinkBlock);
    };
  });
  target.querySelectorAll('[data-doc-entry-id]').forEach(button => {
    button.onclick = () => {
      const doc = ensureSemanticDocState();
      doc.selectedEntryId = button.dataset.docEntryId;
      const entry = doc.entries.find(item => item.entry_id === doc.selectedEntryId);
      if (entry) doc.selectedBlockIds = [...entry.block_ids];
      renderSemanticTextStructurePreview();
    };
  });
  const selectedNow = selectedBlocks();
  const block = selectedNow[0];
  const docNow = ensureSemanticDocState();
  const selectedEntry = docNow.selectedEntryId ? docNow.entries.find(entry => entry.entry_id === docNow.selectedEntryId) : null;
  const pickerCallbacks = {};
  if (selectedNow.length > 1) {
    pickerCallbacks.docBulkDomains = values => updateSelectedBlocks({ domains: values });
    pickerCallbacks.docBulkKnowledgeTypes = values => updateSelectedBlocks({ knowledge_types: values });
    pickerCallbacks.docBulkSystemFields = values => updateSelectedBlocks({ system_fields: values });
  } else if (block) {
    pickerCallbacks.docBlockDomains = values => updateBlock(block.id, { domains: values }, false);
    pickerCallbacks.docBlockKnowledgeTypes = values => updateBlock(block.id, { knowledge_types: values }, false);
    pickerCallbacks.docBlockSystemFields = values => updateBlock(block.id, { system_fields: values }, false);
    pickerCallbacks.docBlockLevels = values => updateBlock(block.id, { education_levels: values }, false);
  }
  if (selectedEntry) {
    pickerCallbacks.docEntryDomains = values => updateEntry(selectedEntry.entry_id, { domains: values }, false);
    pickerCallbacks.docEntryKnowledgeTypes = values => updateEntry(selectedEntry.entry_id, { knowledge_types: values }, false);
    pickerCallbacks.docEntryLevels = values => updateEntry(selectedEntry.entry_id, { education_levels: values }, false);
  }
  bindMultiPickers(target, pickerCallbacks);
  if (selectedNow.length > 1) {
    const bulkRole = target.querySelector('#docBulkRole');
    if (bulkRole) bulkRole.onchange = () => {
      if (bulkRole.value) updateSelectedBlocks({ role: bulkRole.value });
    };
  }
  if (block) {
    const role = target.querySelector('#docBlockRole');
    if (role) role.onchange = () => updateBlock(block.id, { role: role.value });
    const text = target.querySelector('#docBlockTextEditor');
    if (text) {
      const capture = () => captureTextareaCaret(text, block.id);
      text.oninput = () => capture();
      text.onclick = capture;
      text.onkeyup = capture;
      text.onselect = capture;
      text.onpointerup = capture;
      text.onblur = () => { capture(); notifyChanged(); };
      text.onfocus = () => restoreTextareaCaret(text, block.id);
      restoreTextareaCaret(text, block.id);
    }
    const lang = target.querySelector('#docBlockLang');
    if (lang) lang.onchange = () => updateBlock(block.id, { language: lang.value });
    const merge = target.querySelector('#docBlockMergePrev');
    if (merge) merge.onchange = () => updateBlock(block.id, { merge_with_previous: merge.checked });
  }
  if (selectedEntry) {
    const entryTitle = target.querySelector('#docEntryTitleEditor');
    if (entryTitle) {
      entryTitle.oninput = () => updateEntry(selectedEntry.entry_id, { title: entryTitle.value }, false);
      entryTitle.onblur = () => notifyChanged();
      entryTitle.onkeydown = event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          entryTitle.blur();
        }
      };
    }
    const entryType = target.querySelector('#docEntryTypeEditor');
    if (entryType) entryType.onchange = () => updateEntry(selectedEntry.entry_id, { entry_type: entryType.value });
    const entryLang = target.querySelector('#docEntryLang');
    if (entryLang) entryLang.onchange = () => updateEntry(selectedEntry.entry_id, { language: entryLang.value });
    const entryNotes = target.querySelector('#docEntryNotesEditor');
    if (entryNotes) {
      entryNotes.oninput = () => updateEntry(selectedEntry.entry_id, { notes: entryNotes.value }, false);
      entryNotes.onblur = () => notifyChanged();
    }
  }
  target.querySelector('[data-doc-bulk-apply]')?.addEventListener('click', () => {
    const patch = {};
    const role = target.querySelector('#docBulkRole')?.value || '';
    const domains = pickerValues(target, 'docBulkDomains');
    const types = pickerValues(target, 'docBulkKnowledgeTypes');
    const systemFields = pickerValues(target, 'docBulkSystemFields');
    if (role) patch.role = role;
    if (domains.length) patch.domains = domains;
    if (types.length) patch.knowledge_types = types;
    if (systemFields.length) patch.system_fields = systemFields;
    if (Object.keys(patch).length) updateSelectedBlocks(patch);
  });
}

function bindContextPopoverEvents(target) {
  const doc = ensureSemanticDocState();
  const popover = target.querySelector('[data-doc-context-popover]');
  if (!popover) return;
  const ids = contextTargetBlocks(doc);
  popover.querySelector('[data-doc-popup-close]')?.addEventListener('click', () => {
    doc.contextMenu = null;
    renderSemanticTextStructurePreview();
  });
  popover.querySelectorAll('[data-doc-popup-role]').forEach(button => {
    button.onclick = () => {
      updateBlocksByIds(ids, { role: button.dataset.docPopupRole });
    };
  });
  popover.querySelectorAll('[data-doc-popup-clear]').forEach(button => {
    button.onclick = () => updateBlocksByIds(ids, { [button.dataset.docPopupClear]: [] });
  });
  popover.querySelector('[data-doc-popup-split="cursor"]')?.addEventListener('click', () => {
    doc.selectedBlockIds = [doc.contextMenu.blockId];
    doc.contextMenu = null;
    splitSelectedBlock();
  });
  popover.querySelector('[data-doc-popup-split="auto"]')?.addEventListener('click', () => {
    doc.selectedBlockIds = [doc.contextMenu.blockId];
    doc.contextMenu = null;
    splitSelectedBlockByLines();
  });
  popover.querySelector('[data-doc-popup-list-entry]')?.addEventListener('click', () => {
    doc.selectedBlockIds = [...ids];
    doc.contextMenu = null;
    createListEntryFromSelected();
  });
  popover.querySelector('[data-doc-popup-entry]')?.addEventListener('click', () => {
    doc.selectedBlockIds = [...ids];
    doc.contextMenu = null;
    createEntryFromSelected();
  });
  bindMultiPickers(popover, {
    docPopupDomains: values => updateBlocksByIds(ids, { domains: values }),
    docPopupKnowledgeTypes: values => updateBlocksByIds(ids, { knowledge_types: values }),
    docPopupSystemFields: values => updateBlocksByIds(ids, { system_fields: values }),
    docPopupLevels: values => updateBlocksByIds(ids, { education_levels: values }),
  });
}

export function renderSemanticDocPreview() {
  if (!refs.sourcePreview) return;
  refs.sourcePreview.classList.remove('spreadsheet-host');
  const doc = ensureSemanticDocState();
  const selected = selectedBlocks(doc);
  const blocks = selected.length ? selected : doc.blocks.filter(block => block.role !== 'ignore');
  const wrap = document.createElement('div');
  wrap.className = 'doc-wrap';
  wrap.innerHTML = blocks.slice(0, 120).map(block => `<div class="doc-preview-block doc-preview-block--${escapeAttr(block.role)}"><strong>${escapeHtml(roleLabel(block.role))}</strong><p>${escapeHtml(block.text)}</p></div>`).join('') || 'Нет текста.';
  refs.sourcePreview.innerHTML = '';
  refs.sourcePreview.appendChild(wrap);
}

function buildEntryFromBlocks({ entryId, title, blocks, domains, knowledgeTypes, entryType = '', logicalGroupId = '', explicit = false }) {
  const config = sourceConfig();
  const usable = blocks.filter(block => block.role !== 'ignore' && cleanText(block.text));
  if (!usable.length) return null;
  const first = usable[0];
  const sectionPath = first.section_path || sectionPathForBlock(first);
  const finalDomains = asArray(domains).length ? asArray(domains) : firstNonEmptyArray(usable.map(block => block.domains), [config.class_name || 'unassigned']);
  const inferredEntryType = inferLogicalEntryType(usable, entryType);
  const resolvedEntryType = !entryType && inferredEntryType === 'linked_text' ? currentEntryTypeFallback() : inferredEntryType;
  const finalKnowledge = asArray(knowledgeTypes).length ? asArray(knowledgeTypes) : firstNonEmptyArray(usable.map(block => block.knowledge_types), resolvedEntryType === 'document_list' ? ['document_list'] : []);
  const finalSystemFields = firstNonEmptyArray(usable.map(block => block.system_fields), []);
  const level = firstNonEmptyArray(usable.map(block => block.education_levels), [config.education_level].filter(Boolean));
  const language = usable.find(block => block.language)?.language || config.language || null;
  const entryTitle = title || sectionPath[sectionPath.length - 1] || first.text.slice(0, 90) || state.currentParsed?.title || state.currentSource?.source_id;
  const listItems = resolvedEntryType === 'document_list'
    ? usable.filter(block => !block.role?.startsWith('heading') && block.role !== 'meta' && block.role !== 'note').map(block => cleanText(block.text)).filter(Boolean)
    : [];
  const text = listItems.length
    ? `${entryTitle}\n${listItems.map(item => `- ${item.replace(/^[-•*–—]\s+|^\d+[.)]\s+|^[а-яa-z]\)\s+/i, '')}`).join('\n')}`
    : usable.map(block => {
      if (block.role === 'meta') return `Метаданные: ${block.text}`;
      if (block.role === 'note') return `Примечание: ${block.text}`;
      return block.text;
    }).join('\n\n').trim();
  const groupId = logicalGroupId || (explicit ? entryId : '');
  return {
    entry_id: entryId,
    source_id: currentLogicalSourceId() || state.currentSource?.source_id || '',
    domain: finalDomains[0] || 'unassigned',
    domains: finalDomains,
    entry_type: resolvedEntryType,
    title: entryTitle,
    section_path: sectionPath,
    text,
    fields: {},
    metadata: {
      source_id: state.currentSource?.source_id || null,
      source_base_id: state.currentSource?.source_id || null,
      domain: finalDomains[0] || 'unassigned',
      domains: finalDomains,
        entry_type: resolvedEntryType,
      schema: resolvedEntryType,
      logical_group_id: groupId || null,
      expansion_policy: resolvedEntryType === 'document_list' ? 'full_logical_group' : 'entry_context',
      list_items: listItems,
      list_count: listItems.length,
      education_level: level[0] || null,
      education_levels: level,
      language,
      knowledge_types: finalKnowledge,
      system_marks: finalSystemFields,
      system_fields: Object.fromEntries(finalSystemFields.map(key => [key, usable.map(block => block.text).join('\n\n')])),
      block_ids: usable.map(block => block.id),
      source_indexes: usable.map(block => block.source_index),
      section_path: sectionPath,
      logical_entry: explicit,
    },
  };
}

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

export function buildSemanticTextEntriesPreview() {
  const doc = ensureSemanticDocState();
  const blocks = semanticDocumentBlocks();
  const byId = Object.fromEntries(blocks.map(block => [block.id, block]));
  const explicitEntries = doc.entries.map((entry, index) => buildEntryFromBlocks({
    entryId: entry.entry_id || `${state.currentSource?.source_id || 'источник'}_logical_${index + 1}`,
    title: entry.title,
    blocks: (entry.block_ids || []).map(id => byId[id]).filter(Boolean),
    domains: entry.domains,
    knowledgeTypes: entry.knowledge_types,
    entryType: entry.entry_type,
    logicalGroupId: entry.logical_group_id || entry.entry_id,
    explicit: true,
  })).filter(Boolean);
  if (explicitEntries.length) return explicitEntries;

  const entries = [];
  const path = [];
  let current = null;
  const flush = () => {
    if (!current || !current.blocks.length) return;
    const entry = buildEntryFromBlocks({
      entryId: `${state.currentSource?.source_id || 'источник'}_section_${entries.length + 1}`,
      title: current.title,
      blocks: current.blocks,
      domains: current.domains,
      knowledgeTypes: current.knowledgeTypes,
      explicit: false,
    });
    if (entry) entries.push(entry);
    current = null;
  };
  blocks.forEach(block => {
    if (block.role === 'ignore' || !cleanText(block.text)) return;
    const level = blockLevel(block);
    if (level) {
      flush();
      path.splice(level - 1);
      path[level - 1] = block.text;
      current = { title: block.text, blocks: [], domains: block.domains, knowledgeTypes: block.knowledge_types };
      return;
    }
    if (!current) current = { title: path[path.length - 1] || state.currentParsed?.title || state.currentSource?.source_id, blocks: [], domains: [], knowledgeTypes: [] };
    if (block.merge_with_previous && entries.length && !current.blocks.length) {
      current = { title: entries[entries.length - 1].title, blocks: [], domains: [], knowledgeTypes: [] };
    }
    current.blocks.push(block);
  });
  flush();
  if (!entries.length) {
    const entry = buildEntryFromBlocks({ entryId: `${state.currentSource?.source_id || 'источник'}_text_1`, title: state.currentParsed?.title || state.currentSource?.source_id || 'Текстовая запись', blocks, domains: [], knowledgeTypes: [] });
    if (entry) entries.push(entry);
  }
  return entries;
}

export function semanticDocumentMappingPayload() {
  const doc = ensureSemanticDocState();
  const config = sourceConfig();
  return {
    source_type: state.currentParsed?.source_type || 'text',
    extraction_mode: 'semantic_text',
    class_name: config.class_name || '',
    domain: config.class_name || '',
    schema: config.schema || '',
    education_level: config.education_level || '',
    language: config.language || '',
    notes: config.notes || '',
    entry_type: config.schema || (state.currentParsed?.source_type === 'docx' ? 'sectioned_text' : 'generic_text'),
    title: document.getElementById('docTitle')?.value.trim() || state.currentParsed?.title || state.currentSource?.source_id || '',
    edited_text: doc.blocks.filter(block => block.role !== 'ignore').map(block => block.text).join('\n\n'),
    selected_paragraphs: doc.selectedBlockIds.map(id => doc.blocks.findIndex(block => block.id === id)).filter(index => index >= 0),
    document_blocks: doc.blocks.map(block => ({
      id: block.id,
      source_index: block.source_index,
      original_text: block.original_text,
      text: block.text,
      edited_text: block.text !== block.original_text ? block.text : undefined,
      role: block.role,
      level: blockLevel(block),
      domains: block.domains || [],
      primary_domain: block.primary_domain || '',
      knowledge_types: block.knowledge_types || [],
      system_fields: block.system_fields || [],
      education_levels: block.education_levels || [],
      language: block.language || '',
      merge_with_previous: Boolean(block.merge_with_previous),
      links_to: block.links_to || [],
      entry_ids: block.entry_ids || [],
    })),
    document_structure: doc.blocks.map((block, index) => ({
      index,
      block_id: block.id,
      role: block.role,
      level: blockLevel(block),
      merge_with_previous: Boolean(block.merge_with_previous),
      domains: block.domains || [],
      knowledge_types: block.knowledge_types || [],
      system_fields: block.system_fields || [],
    })),
    logical_entries: doc.entries.map(entry => ({ ...entry })),
    document_tree: semanticDocumentBlocks().filter(block => blockLevel(block)).map(block => ({ id: block.id, title: block.text, level: blockLevel(block), section_path: sectionPathForBlock(block) })),
  };
}

export function semanticDraftFromCurrentSource() {
  const doc = ensureSemanticDocState();
  const selected = selectedBlocks(doc);
  const entry = doc.selectedEntryId ? doc.entries.find(item => item.entry_id === doc.selectedEntryId) : null;
  const byId = Object.fromEntries(semanticDocumentBlocks().map(block => [block.id, block]));
  const blocks = entry ? entry.block_ids.map(id => byId[id]).filter(Boolean) : (selected.length ? selected : semanticDocumentBlocks().filter(block => block.role !== 'ignore'));
  return buildEntryFromBlocks({
    entryId: refs.entryIdInput?.value.trim() || `${state.currentSource?.source_id || 'source'}_${Date.now()}`,
    title: document.getElementById('docTitle')?.value.trim() || entry?.title || state.currentParsed?.title || state.currentSource?.source_id,
    blocks,
    domains: entry?.domains || [],
    knowledgeTypes: entry?.knowledge_types || [],
    entryType: entry?.entry_type || '',
    logicalGroupId: entry?.logical_group_id || entry?.entry_id || '',
    explicit: Boolean(entry),
  });
}

export const refs = {
  loginView: document.getElementById('loginView'),
  adminView: document.getElementById('adminView'),
  logoutBtn: document.getElementById('logoutBtn'),
  loginBtn: document.getElementById('loginBtn'),
  loginStatus: document.getElementById('loginStatus'),
  adminOutput: document.getElementById('adminOutput'),
  sourceList: document.getElementById('sourceList'),
  sourceStatus: document.getElementById('sourceStatus'),
  sourceEditor: document.getElementById('sourceEditor'),
  sourcePreview: document.getElementById('sourcePreview'),
  rawPreview: document.getElementById('rawPreview'),
  sourceStructurePreview: document.getElementById('sourceStructurePreview'),
  sourceEntriesPreview: document.getElementById('sourceEntriesPreview'),
  sourceChunksPreview: document.getElementById('sourceChunksPreview'),
  sourcePipelinePreview: document.getElementById('sourcePipelinePreview'),
  xlsxEditor: document.getElementById('xlsxEditor'),
  docEditor: document.getElementById('docEditor'),
  paragraphPickerWrap: document.getElementById('paragraphPickerWrap'),
  paragraphList: document.getElementById('paragraphList'),
  fieldLabelList: document.getElementById('fieldLabelList'),
  entryTablePreview: document.getElementById('entryTablePreview'),
  schemaFieldMapper: document.getElementById('schemaFieldMapper'),
  tableSelectionMode: document.getElementById('tableSelectionMode'),
  schemaFieldsList: document.getElementById('schemaFieldsList'),
  rebuildStatus: document.getElementById('rebuildStatus'),
  jobsStatusMirror: document.getElementById('jobsStatusMirror'),
  validationOutput: document.getElementById('validationOutput'),
  domainsList: document.getElementById('domainsList'),
  schemasList: document.getElementById('schemasList'),
  entriesOutput: document.getElementById('entriesOutput'),
  entriesList: document.getElementById('entriesList'),
  indexedEntriesList: document.getElementById('indexedEntriesList'),
  selectedEntryOutput: document.getElementById('selectedEntryOutput'),
  chunksOutput: document.getElementById('chunksOutput'),
  debugOutput: document.getElementById('debugOutput'),
  sourceSearchInput: document.getElementById('sourceSearchInput'),
  draftRowSelect: document.getElementById('draftRowSelect'),
  entryIdInput: document.getElementById('entryIdInput'),
  entryTitleInput: document.getElementById('entryTitleInput'),
  entryClassSelect: document.getElementById('entryClassSelect'),
  entrySchemaSelect: document.getElementById('entrySchemaSelect'),
  entryLevelSelect: document.getElementById('entryLevelSelect'),
  entryLangSelect: document.getElementById('entryLangSelect'),
  entrySourceIdInput: document.getElementById('entrySourceIdInput'),
  entryTextInput: document.getElementById('entryTextInput'),
  entryEmbeddingTextInput: document.getElementById('entryEmbeddingTextInput'),
  entryMetadataInput: document.getElementById('entryMetadataInput'),
};

export const EDUCATION_LEVELS = [
  { value: '', label: 'уровень: любой' },
  { value: 'bachelor', label: 'бакалавриат' },
  { value: 'master', label: 'магистратура' },
  { value: 'phd', label: 'докторантура' },
  { value: 'military_department', label: 'военная кафедра' },
  { value: 'college', label: 'колледж' }
];

export const LANGUAGES = [
  { value: '', label: 'язык: любой' },
  { value: 'ru', label: 'русский' },
  { value: 'kk', label: 'қазақша' },
  { value: 'en', label: 'английский' }
];

export const state = {
  token: sessionStorage.getItem('admin_jwt') || '',
  currentAdmin: null,
  registry: [],
  catalog: { domains: [], schemas: [] },
  curatedEntries: [],
  indexedEntries: [],
  currentSource: null,
  currentParsed: null,
  currentSheetName: '',
  currentCuratedEntry: null,
  currentIndexedEntry: null,
  currentDomainIndex: null,
  currentSchemaIndex: null,
  docSelectedParagraphs: [],
  docBlockAnnotations: {},
  docTextEditor: {},
  xlsxSelection: { mode: 'cell', cells: [], rows: [], columns: [] },
  xlsxSheetInspectorStates: {},
  xlsxColumnWidths: {},
  xlsxTableProfiles: {},
  xlsxSheetMappings: {},
  xlsxSourceSheets: {},
  currentSheetSourceId: '',
  dirtyMappings: {},
  schemaFieldDraftValues: {},
  schemaMappingDraft: { field_map: {}, table_profile: null },
  explorerTab: 'sources',
  workspaceTab: 'viewer',
  bottomTab: 'logs',
};

export function apiUrl(path) {
  return `${window.API_BASE}${path}`;
}

export function renderSelectOptions(select, items, currentValue = '', multiple = false) {
  if (!select) return;
  select.innerHTML = '';
  items.forEach(item => {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.label;
    option.selected = multiple ? Array.isArray(currentValue) && currentValue.includes(item.value) : String(item.value) === String(currentValue || '');
    select.appendChild(option);
  });
}

export function selectedValues(select) {
  return Array.from(select?.selectedOptions || []).map(option => option.value).filter(Boolean);
}

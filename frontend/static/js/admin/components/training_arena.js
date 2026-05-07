import { apiUrl, state } from '../core/state.js';
import { api } from '../core/api.js';
import { defineComponent, LightComponent } from './component_base.js';
import { escapeHtml, escapeAttr, adminLog } from '../ui/common.js';

function optionHtml(items, current = '', empty = '') {
  const rows = empty ? [`<option value="">${escapeHtml(empty)}</option>`] : [];
  items.forEach(item => {
    const value = String(item.value ?? item.model_id ?? item.dataset_id ?? '');
    const label = String(item.label ?? item.name ?? value);
    rows.push(`<option value="${escapeAttr(value)}" ${String(current) === value ? 'selected' : ''}>${escapeHtml(label)}</option>`);
  });
  return rows.join('');
}

function formatDate(timestamp) {
  const value = Number(timestamp || 0);
  if (!value) return '—';
  try { return new Date(value * 1000).toLocaleString(); } catch { return String(value); }
}

function itemStatusLabel(value) {
  return { draft: 'черновик', approved: 'принято', rejected: 'отклонено' }[value] || value || 'черновик';
}

function pct(value) {
  const n = Number(value || 0);
  return Math.round(Math.max(0, Math.min(n, 1)) * 100);
}

class AdminTrainingArena extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.models = [];
    this.datasets = [];
    this.jobs = [];
    this.activeDataset = null;
    this.activeTab = 'dataset';
    this.candidates = [];
    this.busy = false;
    this.error = '';
    this._jobPoll = 0;
    this.addEventListener('click', event => this.onClick(event));
    this.addEventListener('change', event => this.onChange(event));
    this.render();
    this.loadAll().catch(error => this.fail(error));
  }

  disconnectedCallback() {
    if (this._jobPoll) clearInterval(this._jobPoll);
  }

  ensureJobPolling() {
    const running = this.jobs.some(job => job.status === 'running');
    if (running && !this._jobPoll) {
      this._jobPoll = setInterval(() => this.loadJobs({ silent: true }).catch(() => {}), 2500);
    }
    if (!running && this._jobPoll) {
      clearInterval(this._jobPoll);
      this._jobPoll = 0;
    }
  }

  async loadAll() {
    this.busy = true;
    this.render();
    const [models, datasets, jobs] = await Promise.all([
      api('/admin/training/models'),
      api('/admin/training/datasets'),
      api('/admin/training/jobs'),
    ]);
    this.models = models.models || [];
    this.datasets = datasets.datasets || [];
    this.jobs = jobs.jobs || [];
    const firstId = this.activeDataset?.dataset_id || this.datasets[0]?.dataset_id || '';
    if (firstId) await this.loadDataset(firstId, { preserveBusy: true });
    this.busy = false;
    this.error = '';
    this.ensureJobPolling();
    this.render();
  }

  async loadJobs({ silent = false } = {}) {
    if (!silent) {
      this.busy = true;
      this.render();
    }
    const data = await api('/admin/training/jobs');
    this.jobs = data.jobs || [];
    if (!silent) this.busy = false;
    this.ensureJobPolling();
    this.render();
  }

  async loadDataset(datasetId, { preserveBusy = false } = {}) {
    if (!datasetId) {
      this.activeDataset = null;
      this.render();
      return;
    }
    if (!preserveBusy) {
      this.busy = true;
      this.render();
    }
    const data = await api(`/admin/training/datasets/${encodeURIComponent(datasetId)}`);
    this.activeDataset = data.dataset || null;
    if (!preserveBusy) {
      this.busy = false;
      this.error = '';
      this.render();
    }
  }

  fail(error) {
    this.busy = false;
    this.error = error?.message || String(error);
    adminLog(this.error);
    this.render();
  }

  selectedModelOptions() {
    return this.models.map(model => ({
      value: model.model_id,
      label: `${model.label || model.model_id}${model.trainable ? '' : ' · не для прямого SFT'}`,
    }));
  }

  datasetOptions() {
    return this.datasets.map(dataset => ({
      value: dataset.dataset_id,
      label: `${dataset.name || dataset.dataset_id} · ${dataset.stats?.total || 0}`,
    }));
  }

  currentSourceId() {
    return state.currentSource?.source_id || state.currentSheetSourceId || '';
  }

  computeStats(items = []) {
    const stats = { total: items.length, draft: 0, approved: 0, rejected: 0 };
    items.forEach(item => { stats[item.status || 'draft'] = (stats[item.status || 'draft'] || 0) + 1; });
    return stats;
  }

  render() {
    const dataset = this.activeDataset || {};
    const stats = dataset.items ? this.computeStats(dataset.items) : (dataset.stats || {});
    const currentDatasetId = dataset.dataset_id || this.datasets[0]?.dataset_id || '';
    this.innerHTML = `
      <div class="training-arena training-arena--compact">
        <div class="training-arena__head">
          <div class="training-arena__title">
            <h3>Арена датасетов и обучения</h3>
            <div class="muted">SFT-наборы, candidates из RAG/агента, review, LoRA/QLoRA-задачи.</div>
          </div>
          <div class="toolbar toolbar--compact training-head-actions">
            <button class="btn" data-training-action="reload" type="button">Обновить</button>
            <button class="btn btn--brand" data-training-tab="jobs" data-training-action="create-job" type="button" ${dataset.dataset_id ? '' : 'disabled'}>Создать задачу</button>
          </div>
        </div>

        ${this.error ? `<div class="training-alert training-alert--error">${escapeHtml(this.error)}</div>` : ''}
        ${this.busy ? `<div class="training-alert">Загрузка...</div>` : ''}

        <div class="training-summary card card--flat">
          <label class="field-label training-summary__select">Датасет<select id="trainingDatasetSelect">${optionHtml(this.datasetOptions(), currentDatasetId, 'датасет не выбран')}</select></label>
          <div class="training-stats training-stats--inline">
            <div><strong>${stats.total || 0}</strong><span>всего</span></div>
            <div><strong>${stats.approved || 0}</strong><span>принято</span></div>
            <div><strong>${stats.draft || 0}</strong><span>черновик</span></div>
            <div><strong>${stats.rejected || 0}</strong><span>отклонено</span></div>
          </div>
          <div class="toolbar toolbar--compact training-toolbar-wrap">
            <button class="btn" data-training-action="export-approved" type="button" ${dataset.dataset_id ? '' : 'disabled'}>Export approved</button>
            <button class="btn" data-training-action="export-all" type="button" ${dataset.dataset_id ? '' : 'disabled'}>Export all</button>
          </div>
        </div>

        <div class="training-tabs" role="tablist">
          ${this.renderTabButton('dataset', 'Датасет')}
          ${this.renderTabButton('generate', 'Генерация')}
          ${this.renderTabButton('review', `Review ${stats.total || 0}`)}
          ${this.renderTabButton('jobs', `Обучение ${this.jobs.length || 0}`)}
        </div>

        <div class="training-tab-panel">
          ${this.renderActiveTab(dataset)}
        </div>
      </div>`;
  }

  renderTabButton(tab, label) {
    return `<button class="training-tab ${this.activeTab === tab ? 'is-active' : ''}" data-training-tab="${escapeAttr(tab)}" type="button">${escapeHtml(label)}</button>`;
  }

  renderActiveTab(dataset) {
    if (this.activeTab === 'generate') return this.renderGenerateTab(dataset);
    if (this.activeTab === 'review') return this.renderReviewTab(dataset);
    if (this.activeTab === 'jobs') return this.renderJobsTab(dataset);
    return this.renderDatasetTab(dataset);
  }

  renderDatasetTab(dataset) {
    const modelValue = dataset.target_model || this.models.find(item => item.trainable)?.model_id || this.models[0]?.model_id || '';
    return `
      <div class="training-grid training-grid--equal">
        <section class="card card--flat training-card">
          <div class="workspace-card__head"><h3>Новый датасет</h3><span class="pill">metadata</span></div>
          <div class="builder-form builder-form--editor training-form-compact">
            <label class="field-label">Название<input id="trainingDatasetName" placeholder="ai-talapker-sft-ru" value=""></label>
            <label class="field-label">Описание<input id="trainingDatasetDescription" placeholder="Для дообучения FAQ/admission поведения" value=""></label>
            <div class="form-grid form-grid--two">
              <label class="field-label">Модель<select id="trainingTargetModel">${optionHtml(this.selectedModelOptions(), modelValue, 'выбери модель')}</select></label>
              <label class="field-label">Формат<select id="trainingDatasetFormat">
                <option value="chatml_jsonl">Chat messages JSONL</option>
                <option value="alpaca_jsonl">Alpaca JSONL</option>
                <option value="plain_pairs_jsonl">Plain QA JSONL</option>
              </select></label>
              <label class="field-label">Тип задачи<select id="trainingTaskType">
                <option value="chat_qa">chat_qa</option>
                <option value="rag_grounded_qa">rag_grounded_qa</option>
                <option value="guardrail">guardrail</option>
              </select></label>
              <label class="field-label">Язык<select id="trainingLang">
                <option value="ru">русский</option>
                <option value="kk">қазақша</option>
                <option value="en">english</option>
              </select></label>
            </div>
            <button class="btn btn--brand" data-training-action="create-dataset" type="button">Создать датасет</button>
          </div>
        </section>
        <section class="card card--flat training-card">
          <div class="workspace-card__head"><h3>Параметры текущего</h3><span class="pill">${escapeHtml(dataset.dataset_id || 'не выбран')}</span></div>
          ${dataset.dataset_id ? `
            <div class="training-kv"><span>Название</span><strong>${escapeHtml(dataset.name || '')}</strong></div>
            <div class="training-kv"><span>Модель</span><strong>${escapeHtml(dataset.target_model || '')}</strong></div>
            <div class="training-kv"><span>Формат</span><strong>${escapeHtml(dataset.dataset_format || '')}</strong></div>
            <div class="training-kv"><span>Язык</span><strong>${escapeHtml(dataset.language || '')}</strong></div>
            <div class="training-kv"><span>Обновлен</span><strong>${escapeHtml(formatDate(dataset.updated_at))}</strong></div>
            <div class="inspector-note">Создание и выбор датасета вынесены сюда. Генерация, review и запуск обучения разнесены по вкладкам, чтобы арена помещалась в рабочую область.</div>
          ` : '<div class="inspector-note">Создай или выбери датасет.</div>'}
        </section>
      </div>`;
  }

  renderGenerateTab(dataset) {
    return `
      <div class="training-grid training-grid--equal">
        <section class="card card--flat training-card training-card--suggest">
          <div class="workspace-card__head"><h3>Suggestion из RAG / текущего агента</h3><span class="pill">генерация кандидатов</span></div>
          <div class="form-grid form-grid--four form-grid--compact">
            <label class="field-label">source_id<input id="trainingSourceId" placeholder="текущий источник или пусто" value="${escapeAttr(this.currentSourceId())}"></label>
            <label class="field-label">domain<input id="trainingDomain" placeholder="например benefits" value=""></label>
            <label class="field-label">schema<input id="trainingSchema" placeholder="например document_list" value=""></label>
            <label class="field-label">count<input id="trainingSuggestCount" type="number" min="1" max="24" value="6"></label>
          </div>
          <div class="toolbar toolbar--compact training-toolbar-wrap">
            <label class="check-row"><input id="trainingUseLlm" type="checkbox" checked> использовать текущую LLM</label>
            <button class="btn" data-training-action="use-current-source" type="button">Взять текущий источник</button>
            <button class="btn btn--brand" data-training-action="suggest" type="button">Сгенерировать</button>
            <button class="btn" data-training-action="save-candidates" type="button" ${dataset.dataset_id && this.candidates.length ? '' : 'disabled'}>Добавить выбранные</button>
          </div>
          <div class="training-candidates">${this.renderCandidates()}</div>
        </section>
        <section class="card card--flat training-card training-card--manual">
          <div class="workspace-card__head"><h3>Ручная QA-запись</h3><span class="pill">one item</span></div>
          <label class="field-label">Вопрос<textarea id="trainingManualQuestion" placeholder="Вопрос абитуриента"></textarea></label>
          <label class="field-label">Ответ<textarea id="trainingManualAnswer" placeholder="Короткий эталонный ответ"></textarea></label>
          <label class="field-label">Контекст<textarea id="trainingManualContext" placeholder="Опционально: chunk/context"></textarea></label>
          <button class="btn btn--brand" data-training-action="add-manual" type="button" ${dataset.dataset_id ? '' : 'disabled'}>Добавить в датасет</button>
        </section>
      </div>`;
  }

  renderReviewTab(dataset) {
    return `
      <section class="card card--flat training-card training-card--items">
        <div class="workspace-card__head"><h3>Записи датасета</h3><span class="pill">review arena</span></div>
        ${this.renderItems(dataset.items || [])}
      </section>`;
  }

  renderJobsTab(dataset) {
    return `
      <section class="card card--flat training-card training-card--jobs">
        <div class="workspace-card__head">
          <h3>Обучение</h3>
          <span class="pill">LoRA / QLoRA</span>
        </div>
        <div class="toolbar toolbar--compact training-toolbar-wrap training-jobs-toolbar">
          <button class="btn btn--brand" data-training-action="create-job" type="button" ${dataset.dataset_id ? '' : 'disabled'}>Создать задачу</button>
          <button class="btn" data-training-action="reload-jobs" type="button">Обновить jobs</button>
        </div>
        ${this.renderJobs()}
      </section>`;
  }

  renderCandidates() {
    if (!this.candidates.length) return '<div class="inspector-note">Кандидаты появятся после генерации. Можно брать готовые Q/A из документов или просить текущую LLM сформировать новые вопросы по чанкам.</div>';
    return this.candidates.map((item, index) => `
      <article class="training-item training-item--candidate">
        <label class="check-row"><input type="checkbox" data-candidate-index="${index}" checked> выбрать</label>
        <div class="training-item__qa"><strong>Q:</strong> ${escapeHtml(item.question)}</div>
        <div class="training-item__qa"><strong>A:</strong> ${escapeHtml(item.answer)}</div>
        <div class="training-item__meta">${escapeHtml(item.source_id || '')} ${escapeHtml(item.chunk_id || '')}</div>
      </article>
    `).join('');
  }

  renderItems(items = []) {
    if (!items.length) return '<div class="inspector-note">В датасете пока нет записей.</div>';
    return `<div class="training-items-list">${items.map(item => `
      <article class="training-item" data-item-id="${escapeAttr(item.item_id)}">
        <div class="training-item__top">
          <span class="pill">${escapeHtml(itemStatusLabel(item.status))}</span>
          <span class="muted">${escapeHtml(item.split || 'train')} · ${escapeHtml(item.language || '')} · ${escapeHtml(item.source_id || '')}</span>
          <div class="training-item__actions">
            <button class="btn" data-training-action="approve-item" type="button">Принять</button>
            <button class="btn" data-training-action="draft-item" type="button">Черновик</button>
            <button class="danger danger--soft" data-training-action="reject-item" type="button">Отклонить</button>
            <button class="danger" data-training-action="delete-item" type="button">Удалить</button>
          </div>
        </div>
        <div class="training-item__qa"><strong>Q:</strong> ${escapeHtml(item.question)}</div>
        <div class="training-item__qa"><strong>A:</strong> ${escapeHtml(item.answer)}</div>
        ${item.context ? `<details class="training-item__context"><summary>context</summary><pre>${escapeHtml(item.context)}</pre></details>` : ''}
      </article>
    `).join('')}</div>`;
  }

  renderJobs() {
    if (!this.jobs.length) return '<div class="inspector-note">Пока нет созданных задач обучения.</div>';
    return `<div class="training-jobs-list">${this.jobs.map(job => {
      const progress = pct(job.progress);
      const running = job.status === 'running';
      const log = Array.isArray(job.log_tail) ? job.log_tail.slice(-40).join('\n') : '';
      return `
        <article class="training-job" data-job-id="${escapeAttr(job.job_id)}">
          <div class="training-job__top">
            <div><strong>${escapeHtml(job.job_id)}</strong> <span class="pill">${escapeHtml(job.status || 'planned')}</span></div>
            <div class="training-item__actions">
              <button class="btn btn--brand" data-training-action="start-job" type="button" ${running ? 'disabled' : ''}>Запустить</button>
              <button class="danger danger--soft" data-training-action="stop-job" type="button" ${running ? '' : 'disabled'}>Стоп</button>
            </div>
          </div>
          <div class="muted">dataset=${escapeHtml(job.dataset_id)} · model=${escapeHtml(job.model_id)} · pid=${escapeHtml(job.pid || '')} · ${escapeHtml(formatDate(job.created_at))}</div>
          <div class="training-progress"><span style="width:${progress}%"></span></div>
          <div class="training-progress__label">${progress}% ${job.returncode !== undefined && job.returncode !== null ? `· returncode=${escapeHtml(job.returncode)}` : ''}</div>
          ${job.note ? `<div class="inspector-note">${escapeHtml(job.note)}</div>` : ''}
          <details class="training-job__details"><summary>command / log</summary><pre class="json-wrap">${escapeHtml(job.command || job.command_hint || '')}${log ? `\n\n--- log ---\n${escapeHtml(log)}` : ''}</pre></details>
        </article>`;
    }).join('')}</div>`;
  }

  async onClick(event) {
    const tabButton = event.target.closest('[data-training-tab]');
    if (tabButton && this.contains(tabButton)) {
      this.activeTab = tabButton.dataset.trainingTab || 'dataset';
      this.render();
    }

    const button = event.target.closest('[data-training-action]');
    if (!button || !this.contains(button)) return;
    const action = button.dataset.trainingAction;
    try {
      if (action === 'reload') return await this.loadAll();
      if (action === 'reload-jobs') return await this.loadJobs();
      if (action === 'create-dataset') return await this.createDataset();
      if (action === 'use-current-source') {
        const input = this.querySelector('#trainingSourceId');
        if (input) input.value = this.currentSourceId();
        return;
      }
      if (action === 'suggest') return await this.suggest();
      if (action === 'save-candidates') return await this.saveCandidates();
      if (action === 'add-manual') return await this.addManual();
      if (action === 'export-approved') return await this.downloadExport(true);
      if (action === 'export-all') return await this.downloadExport(false);
      if (action === 'create-job') return await this.createJob();
      const job = event.target.closest('[data-job-id]');
      if (job && action === 'start-job') return await this.startJob(job.dataset.jobId);
      if (job && action === 'stop-job') return await this.stopJob(job.dataset.jobId);
      const item = event.target.closest('[data-item-id]');
      if (item && ['approve-item', 'draft-item', 'reject-item'].includes(action)) {
        const status = { 'approve-item': 'approved', 'draft-item': 'draft', 'reject-item': 'rejected' }[action];
        return await this.patchItem(item.dataset.itemId, { status });
      }
      if (item && action === 'delete-item') return await this.deleteItem(item.dataset.itemId);
    } catch (error) {
      this.fail(error);
    }
  }

  async onChange(event) {
    if (event.target?.id === 'trainingDatasetSelect') {
      await this.loadDataset(event.target.value).catch(error => this.fail(error));
    }
  }

  async createDataset() {
    const payload = {
      name: this.querySelector('#trainingDatasetName')?.value || 'AI Talapker SFT',
      description: this.querySelector('#trainingDatasetDescription')?.value || '',
      target_model: this.querySelector('#trainingTargetModel')?.value || '',
      dataset_format: this.querySelector('#trainingDatasetFormat')?.value || 'chatml_jsonl',
      task_type: this.querySelector('#trainingTaskType')?.value || 'chat_qa',
      language: this.querySelector('#trainingLang')?.value || 'ru',
    };
    const data = await api('/admin/training/datasets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    this.datasets.unshift(data.dataset);
    this.activeTab = 'generate';
    await this.loadDataset(data.dataset.dataset_id);
    adminLog(data);
  }

  async suggest() {
    const payload = {
      source_id: this.querySelector('#trainingSourceId')?.value || '',
      domain: this.querySelector('#trainingDomain')?.value || '',
      schema: this.querySelector('#trainingSchema')?.value || '',
      language: this.activeDataset?.language || 'ru',
      count: Number(this.querySelector('#trainingSuggestCount')?.value || 6),
      use_llm: Boolean(this.querySelector('#trainingUseLlm')?.checked),
    };
    this.busy = true;
    this.render();
    const data = await api('/admin/training/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    this.candidates = data.candidates || [];
    this.busy = false;
    this.error = '';
    this.render();
    adminLog(data);
  }

  selectedCandidates() {
    return Array.from(this.querySelectorAll('[data-candidate-index]:checked')).map(input => this.candidates[Number(input.dataset.candidateIndex)]).filter(Boolean);
  }

  async saveCandidates() {
    if (!this.activeDataset?.dataset_id) throw new Error('Датасет не выбран');
    const items = this.selectedCandidates();
    const data = await api(`/admin/training/datasets/${encodeURIComponent(this.activeDataset.dataset_id)}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    this.activeDataset.items = data.items || [];
    this.candidates = [];
    this.datasets = this.datasets.map(item => item.dataset_id === data.dataset.dataset_id ? data.dataset : item);
    this.activeTab = 'review';
    this.render();
    adminLog(data);
  }

  async addManual() {
    if (!this.activeDataset?.dataset_id) throw new Error('Датасет не выбран');
    const item = {
      question: this.querySelector('#trainingManualQuestion')?.value || '',
      answer: this.querySelector('#trainingManualAnswer')?.value || '',
      context: this.querySelector('#trainingManualContext')?.value || '',
      source_id: this.currentSourceId(),
      language: this.activeDataset.language || 'ru',
    };
    const data = await api(`/admin/training/datasets/${encodeURIComponent(this.activeDataset.dataset_id)}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [item] }),
    });
    this.activeDataset.items = data.items || [];
    this.datasets = this.datasets.map(row => row.dataset_id === data.dataset.dataset_id ? data.dataset : row);
    this.activeTab = 'review';
    this.render();
    adminLog(data);
  }

  async patchItem(itemId, patch) {
    if (!this.activeDataset?.dataset_id) return;
    const data = await api(`/admin/training/datasets/${encodeURIComponent(this.activeDataset.dataset_id)}/items/${encodeURIComponent(itemId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    this.activeDataset.items = data.items || this.activeDataset.items || [];
    this.datasets = this.datasets.map(row => row.dataset_id === data.dataset.dataset_id ? data.dataset : row);
    this.render();
  }

  async deleteItem(itemId) {
    if (!this.activeDataset?.dataset_id) return;
    const data = await api(`/admin/training/datasets/${encodeURIComponent(this.activeDataset.dataset_id)}/items/${encodeURIComponent(itemId)}/delete`, { method: 'POST' });
    this.activeDataset.items = data.items || [];
    this.datasets = this.datasets.map(row => row.dataset_id === data.dataset.dataset_id ? data.dataset : row);
    this.render();
  }

  async downloadExport(approvedOnly) {
    if (!this.activeDataset?.dataset_id) throw new Error('Датасет не выбран');
    const url = apiUrl(`/admin/training/datasets/${encodeURIComponent(this.activeDataset.dataset_id)}/export?approved_only=${approvedOnly ? 'true' : 'false'}`);
    const headers = new Headers();
    if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
    const response = await fetch(url, { headers });
    if (!response.ok) throw new Error(`Export failed: HTTP ${response.status}`);
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = `${this.activeDataset.dataset_id}.jsonl`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
  }

  async createJob() {
    if (!this.activeDataset?.dataset_id) throw new Error('Датасет не выбран');
    const data = await api('/admin/training/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dataset_id: this.activeDataset.dataset_id, target_model: this.activeDataset.target_model, method: 'qlora_sft' }),
    });
    this.jobs.unshift(data.job);
    this.activeTab = 'jobs';
    this.render();
    adminLog(data);
  }

  async startJob(jobId) {
    const data = await api(`/admin/training/jobs/${encodeURIComponent(jobId)}/start`, { method: 'POST' });
    this.jobs = this.jobs.map(job => job.job_id === data.job.job_id ? data.job : job);
    this.ensureJobPolling();
    this.render();
    adminLog(data);
  }

  async stopJob(jobId) {
    const data = await api(`/admin/training/jobs/${encodeURIComponent(jobId)}/stop`, { method: 'POST' });
    this.jobs = this.jobs.map(job => job.job_id === data.job.job_id ? data.job : job);
    this.ensureJobPolling();
    this.render();
    adminLog(data);
  }
}

defineComponent('admin-training-arena', AdminTrainingArena);

import { defineComponent, LightComponent } from './component_base.js';

class AdminUploadModal extends LightComponent {
  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this._onKeyDown = event => {
      if (event.key === 'Escape' && this.isOpen() && !this.isBusy()) this.close();
    };
    this.addEventListener('click', event => {
      if (event.target.closest('[data-upload-close], #closeUploadModalBtn, #cancelUploadModalBtn')) {
        if (!this.isBusy()) this.close();
        return;
      }
      // Upload submission is wired in main.js with direct document-level handlers.
      // Keeping it there avoids silent failures if the custom element is not upgraded yet.
    });
    this.$('#uploadFile')?.addEventListener('change', () => {
      this.refreshFileName();
      this.setProgress(0);
      this.setStatus('', 'idle');
    });
    document.addEventListener('keydown', this._onKeyDown);
    this.setProgress(0);
  }

  disconnectedCallback() {
    document.removeEventListener('keydown', this._onKeyDown);
  }

  isOpen() {
    return !this.classList.contains('hidden');
  }

  isBusy() {
    return this.dataset.busy === '1';
  }

  open() {
    this.classList.remove('hidden');
    document.body.classList.add('modal-open');
    this.querySelector('.modal__panel')?.focus();
  }

  close() {
    this.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  setBusy(isBusy) {
    this.dataset.busy = isBusy ? '1' : '0';
    this.$('#uploadBtn')?.toggleAttribute('disabled', isBusy);
    this.$('#cancelUploadModalBtn')?.toggleAttribute('disabled', isBusy);
    this.$('#closeUploadModalBtn')?.toggleAttribute('disabled', isBusy);
    this.$('#uploadFile')?.toggleAttribute('disabled', isBusy);
    this.$('#uploadClass')?.toggleAttribute('disabled', isBusy);
    this.$('#uploadSchema')?.toggleAttribute('disabled', isBusy);
    this.$('#uploadLevel')?.toggleAttribute('disabled', isBusy);
    this.$('#uploadLang')?.toggleAttribute('disabled', isBusy);
  }

  setProgress(percent) {
    const safe = Math.max(0, Math.min(100, Number(percent) || 0));
    const bar = this.$('#uploadProgressBar');
    const wrap = this.$('#uploadProgressWrap');
    if (bar) bar.style.width = `${safe}%`;
    if (wrap) wrap.setAttribute('aria-valuenow', String(Math.round(safe)));
  }

  setStatus(message, kind = 'idle') {
    const target = this.$('#uploadStatus');
    if (!target) return;
    target.textContent = message || '';
    target.dataset.kind = kind;
    target.classList.toggle('hidden', !message);
  }

  refreshFileName() {
    const file = this.$('#uploadFile')?.files?.[0];
    const name = this.$('#uploadFileName');
    if (!name) return;
    if (!file) {
      name.textContent = 'Файл не выбран';
      return;
    }
    const sizeMb = file.size ? ` · ${(file.size / 1024 / 1024).toFixed(2)} MB` : '';
    name.textContent = `${file.name}${sizeMb}`;
  }

  reset() {
    const fileInput = this.$('#uploadFile');
    if (fileInput) fileInput.value = '';
    this.setProgress(0);
    this.setStatus('', 'idle');
    this.refreshFileName();
  }

  formData() {
    const file = this.$('#uploadFile')?.files?.[0];
    if (!file) return null;
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('class_name', this.$('#uploadClass')?.value || 'general');
    form.append('schema_name', this.$('#uploadSchema')?.value || 'generic_text');
    form.append('education_level', this.$('#uploadLevel')?.value || '');
    form.append('language', this.$('#uploadLang')?.value || '');
    return form;
  }
}

defineComponent('admin-upload-modal', AdminUploadModal);

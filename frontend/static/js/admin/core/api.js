import { apiUrl, state } from './state.js';

async function readResponseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function makeHttpError(response, data, fallbackMessage) {
  const detail = data?.detail;
  const message = Array.isArray(detail)
    ? detail.map(item => {
        const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
        return `${loc ? `${loc}: ` : ''}${item.msg || JSON.stringify(item)}`;
      }).join('\n')
    : detail || data?.message || fallbackMessage || response.statusText || `HTTP ${response.status}`;
  const error = new Error(message);
  error.status = response.status;
  error.payload = data;
  return error;
}

function makeXhrError(xhr, data, fallbackMessage) {
  const detail = data?.detail;
  const message = Array.isArray(detail)
    ? detail.map(item => {
        const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
        return `${loc ? `${loc}: ` : ''}${item.msg || JSON.stringify(item)}`;
      }).join('\n')
    : detail || data?.message || fallbackMessage || xhr.statusText || `HTTP ${xhr.status}`;
  const error = new Error(message);
  error.status = xhr.status;
  error.payload = data;
  return error;
}

function parseJsonText(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  let response;
  try {
    response = await fetch(apiUrl(path), { ...options, headers });
  } catch (error) {
    throw new Error(`API недоступен: ${apiUrl(path)}. ${error.message || error}`);
  }
  const data = await readResponseJson(response);
  if (response.status === 401) {
    sessionStorage.removeItem('admin_jwt');
    state.token = '';
    throw makeHttpError(response, data, 'Нужна повторная авторизация');
  }
  if (!response.ok) throw makeHttpError(response, data, response.statusText);
  return data;
}

export function uploadForm(path, formData, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', apiUrl(path));
    if (state.token) xhr.setRequestHeader('Authorization', `Bearer ${state.token}`);
    xhr.upload.onprogress = event => {
      if (!onProgress || !event.lengthComputable) return;
      onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new Error(`API недоступен: ${apiUrl(path)}`));
    xhr.ontimeout = () => reject(new Error(`Истекло время ожидания API: ${apiUrl(path)}`));
    xhr.onload = () => {
      const data = parseJsonText(xhr.responseText);
      if (xhr.status === 401) {
        sessionStorage.removeItem('admin_jwt');
        state.token = '';
        reject(makeXhrError(xhr, data, 'Нужна повторная авторизация'));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(makeXhrError(xhr, data, xhr.statusText));
        return;
      }
      resolve(data);
    };
    xhr.send(formData);
  });
}

export async function loginAdmin(username, password) {
  const url = apiUrl('/admin/auth/login');
  const jsonBody = JSON.stringify({ username, password });

  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: jsonBody,
    });
  } catch (error) {
    throw new Error(`API недоступен: ${url}. ${error.message || error}`);
  }

  let data = await readResponseJson(response);
  if (response.ok) return data;

  // Some FastAPI auth endpoints use OAuth2PasswordRequestForm and require form-urlencoded.
  // Fall back automatically so the admin page is not locked behind a transport-format mismatch.
  if ([400, 415, 422].includes(response.status)) {
    const form = new URLSearchParams();
    form.set('username', username);
    form.set('password', password);
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form,
      });
    } catch (error) {
      throw new Error(`API недоступен: ${url}. ${error.message || error}`);
    }
    data = await readResponseJson(response);
    if (response.ok) return data;
  }

  throw makeHttpError(response, data, `Ошибка входа через ${url}`);
}

function contentDispositionFilename(headerValue = '') {
  const value = String(headerValue || '');
  const utfMatch = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch) {
    try { return decodeURIComponent(utfMatch[1]); } catch { return utfMatch[1]; }
  }
  const plainMatch = value.match(/filename="?([^";]+)"?/i);
  if (plainMatch) return plainMatch[1];
  return '';
}

async function readErrorPayload(response) {
  const text = await response.text().catch(() => '');
  const data = parseJsonText(text);
  return data || { detail: text || response.statusText || `HTTP ${response.status}` };
}

export async function downloadSourceFile(sourceId) {
  const encoded = encodeURIComponent(String(sourceId || ''));
  if (!encoded) throw new Error('Источник не выбран');
  const headers = new Headers();
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  let response;
  try {
    response = await fetch(apiUrl(`/admin/source-download/${encoded}`), { headers });
  } catch (error) {
    throw new Error(`API недоступен: ${apiUrl(`/admin/source-download/${encoded}`)}. ${error.message || error}`);
  }
  if (response.status === 401) {
    const payload = await readErrorPayload(response);
    sessionStorage.removeItem('admin_jwt');
    state.token = '';
    throw makeHttpError(response, payload, 'Нужна повторная авторизация');
  }
  if (!response.ok) {
    const payload = await readErrorPayload(response);
    throw makeHttpError(response, payload, response.statusText);
  }
  const blob = await response.blob();
  const headerFilename = contentDispositionFilename(response.headers.get('content-disposition'));
  const filename = headerFilename || `${sourceId}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  return { ok: true, filename, bytes: blob.size };
}

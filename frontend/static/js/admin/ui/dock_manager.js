const STORAGE_KEY = 'ai_talapker_admin_layout';
const MIN_LEFT = 220;
const MAX_LEFT = 560;
const MIN_RIGHT = 220;
const MAX_RIGHT = 560;
const MIN_BOTTOM = 160;
const MAX_BOTTOM = 620;
const MIN_CENTER = 420;

const dockState = {
  floating: new Map(),
  z: 40,
  drag: null,
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function loadLayout() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function saveLayout(patch) {
  const next = { ...loadLayout(), ...patch };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

function applyStoredLayout(root = document) {
  const layout = loadLayout();
  const style = root.documentElement?.style || document.documentElement.style;
  if (layout.leftWidth) style.setProperty('--dock-left', `${layout.leftWidth}px`);
  if (layout.rightWidth) style.setProperty('--dock-right', `${layout.rightWidth}px`);
  if (layout.bottomHeight) style.setProperty('--dock-bottom', `${layout.bottomHeight}px`);
}

function ensureFloatingLayer() {
  let layer = document.getElementById('floatingDockLayer');
  if (!layer) {
    layer = document.createElement('div');
    layer.id = 'floatingDockLayer';
    layer.className = 'floating-dock-layer';
    document.body.appendChild(layer);
  }
  return layer;
}

function panelTitle(panel) {
  return panel.dataset.dockTitle || panel.querySelector('.pane-title')?.textContent?.trim() || 'Panel';
}

function injectPanelButtons() {
  document.querySelectorAll('[data-dock-panel]').forEach(panel => {
    const head = panel.querySelector(':scope > .pane-head, :scope > .dock-panel__head');
    if (!head || head.dataset.dockDetachHandle) return;
    head.dataset.dockDetachHandle = panel.dataset.dockPanel;
    head.classList.add('dock-detach-handle');
    head.title = `Двойной клик — отделить панель: ${panelTitle(panel)}`;
  });
}

function makePlaceholder(panel) {
  const placeholder = document.createElement('div');
  placeholder.className = 'dock-placeholder';
  placeholder.dataset.dockPlaceholder = panel.dataset.dockPanel;
  placeholder.innerHTML = `
    <div>
      <strong>${panelTitle(panel)}</strong>
      <span>отделена</span>
    </div>
    <button class="dock-btn" data-dock-return="${panel.dataset.dockPanel}" type="button" title="Dock panel back">↙</button>
  `;
  return placeholder;
}

function updatePlaceholder(placeholder, panelId, title) {
  placeholder.dataset.dockPlaceholder = panelId;
  const strong = placeholder.querySelector('strong');
  if (strong) strong.textContent = title;
  const button = placeholder.querySelector('[data-dock-return]');
  if (button) button.dataset.dockReturn = panelId;
}

function makeFloatingWindow(panel) {
  const panelId = panel.dataset.dockPanel;
  const layer = ensureFloatingLayer();
  const rect = panel.getBoundingClientRect();
  const win = document.createElement('section');
  win.className = 'floating-panel';
  win.dataset.floatingPanel = panelId;
  const left = clamp(rect.left + 24, 16, Math.max(16, window.innerWidth - 360));
  const top = clamp(rect.top + 24, 16, Math.max(16, window.innerHeight - 260));
  const maxWidth = Math.max(260, window.innerWidth - left - 16);
  const maxHeight = Math.max(220, window.innerHeight - top - 16);
  const width = clamp(rect.width, Math.min(420, maxWidth), maxWidth);
  const height = clamp(rect.height, Math.min(280, maxHeight), maxHeight);
  win.style.left = `${left}px`;
  win.style.top = `${top}px`;
  win.style.width = `${width}px`;
  win.style.height = `${height}px`;
  win.style.zIndex = String(++dockState.z);
  win.innerHTML = `
    <div class="floating-panel__bar" data-floating-drag="${panelId}">
      <div class="floating-panel__title">${panelTitle(panel)}</div>
      <div class="dock-actions">
        <button class="dock-btn" data-dock-return="${panelId}" type="button" title="Dock panel back">↙</button>
      </div>
    </div>
    <div class="floating-panel__body"></div>
    <div class="floating-panel__resize" data-floating-resize="${panelId}"></div>
  `;
  layer.appendChild(win);
  win.querySelector('.floating-panel__body').appendChild(panel);
  return win;
}

export function detachPanel(panelId) {
  if (dockState.floating.has(panelId)) {
    const record = dockState.floating.get(panelId);
    record.window.style.zIndex = String(++dockState.z);
    return;
  }
  const panel = document.querySelector(`[data-dock-panel="${panelId}"]`);
  if (!panel) return;
  const placeholder = makePlaceholder(panel);
  panel.parentNode.insertBefore(placeholder, panel);
  const floatingWindow = makeFloatingWindow(panel);
  panel.classList.add('is-floating-content');
  dockState.floating.set(panelId, { panel, placeholder, window: floatingWindow });
}

export function dockPanel(panelId, targetSlot = null, options = {}) {
  const record = dockState.floating.get(panelId);
  if (!record) return;
  const removeOriginalPlaceholder = options.removeOriginalPlaceholder !== false;
  const slot = targetSlot || record.placeholder;
  record.panel.classList.remove('is-floating-content');
  slot.parentNode.insertBefore(record.panel, slot);
  slot.remove();
  if (removeOriginalPlaceholder && record.placeholder.isConnected) record.placeholder.remove();
  record.window.remove();
  dockState.floating.delete(panelId);
}

function replaceDockedPanel(floatingPanelId, targetPanelId) {
  if (floatingPanelId === targetPanelId) {
    dockPanel(floatingPanelId);
    return;
  }
  const floatingRecord = dockState.floating.get(floatingPanelId);
  const targetPanel = document.querySelector(`[data-dock-panel="${targetPanelId}"]`);
  if (!floatingRecord || !targetPanel || targetPanel.closest('.floating-panel')) return;

  const oldFloatingPlaceholder = floatingRecord.placeholder;
  detachPanel(targetPanelId);
  const targetRecord = dockState.floating.get(targetPanelId);
  const targetPlaceholder = document.querySelector(`[data-dock-placeholder="${targetPanelId}"]`);
  if (!targetPlaceholder) return;
  dockPanel(floatingPanelId, targetPlaceholder, { removeOriginalPlaceholder: false });
  if (targetRecord && oldFloatingPlaceholder?.isConnected) {
    updatePlaceholder(oldFloatingPlaceholder, targetPanelId, panelTitle(targetPanel));
    targetRecord.placeholder = oldFloatingPlaceholder;
  }
}

function findDockTarget(clientX, clientY, movingWindow) {
  const previousPointerEvents = movingWindow.style.pointerEvents;
  movingWindow.style.pointerEvents = 'none';
  const element = document.elementFromPoint(clientX, clientY);
  movingWindow.style.pointerEvents = previousPointerEvents;
  const placeholder = element?.closest?.('[data-dock-placeholder]');
  if (placeholder) {
    return { type: 'placeholder', id: placeholder.dataset.dockPlaceholder, element: placeholder };
  }
  const panel = element?.closest?.('[data-dock-panel]');
  if (panel && !panel.closest('.floating-panel')) {
    return { type: 'panel', id: panel.dataset.dockPanel, element: panel };
  }
  return null;
}

function startDrag(event, floatingWindow) {
  const panelId = floatingWindow.dataset.floatingPanel;
  const startX = event.clientX;
  const startY = event.clientY;
  const startLeft = floatingWindow.offsetLeft;
  const startTop = floatingWindow.offsetTop;
  floatingWindow.style.zIndex = String(++dockState.z);
  event.preventDefault();
  floatingWindow.classList.add('is-dragging');

  const onMove = moveEvent => {
    const maxLeft = Math.max(8, window.innerWidth - floatingWindow.offsetWidth - 8);
    const maxTop = Math.max(8, window.innerHeight - floatingWindow.offsetHeight - 8);
    const nextLeft = clamp(startLeft + moveEvent.clientX - startX, 8, maxLeft);
    const nextTop = clamp(startTop + moveEvent.clientY - startY, 8, maxTop);
    floatingWindow.style.left = `${nextLeft}px`;
    floatingWindow.style.top = `${nextTop}px`;
  };
  const onUp = upEvent => {
    floatingWindow.classList.remove('is-dragging');
    const target = findDockTarget(upEvent.clientX, upEvent.clientY, floatingWindow);
    if (target?.type === 'placeholder' && target.id === panelId) {
      dockPanel(panelId, target.element);
    } else if (target?.type === 'panel') {
      replaceDockedPanel(panelId, target.id);
    }
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function startFloatingResize(event, floatingWindow) {
  const startX = event.clientX;
  const startY = event.clientY;
  const startWidth = floatingWindow.offsetWidth;
  const startHeight = floatingWindow.offsetHeight;
  floatingWindow.style.zIndex = String(++dockState.z);
  event.preventDefault();

  const onMove = moveEvent => {
    const maxWidth = Math.max(160, window.innerWidth - floatingWindow.offsetLeft - 8);
    const maxHeight = Math.max(140, window.innerHeight - floatingWindow.offsetTop - 8);
    floatingWindow.style.width = `${clamp(startWidth + moveEvent.clientX - startX, Math.min(320, maxWidth), maxWidth)}px`;
    floatingWindow.style.height = `${clamp(startHeight + moveEvent.clientY - startY, Math.min(220, maxHeight), maxHeight)}px`;
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

function startDockResize(event, paneName) {
  const layout = document.querySelector('.admin-layout');
  const bottom = document.querySelector('.admin-bottom');
  const rootStyle = document.documentElement.style;
  const startX = event.clientX;
  const startY = event.clientY;
  const current = loadLayout();
  const startLeft = current.leftWidth || document.querySelector('.admin-pane--left')?.getBoundingClientRect().width || 320;
  const startRight = current.rightWidth || document.querySelector('.admin-pane--right')?.getBoundingClientRect().width || 320;
  const startBottom = current.bottomHeight || bottom?.getBoundingClientRect().height || 260;
  event.preventDefault();

  const onMove = moveEvent => {
    const layoutWidth = layout?.getBoundingClientRect().width || window.innerWidth;
    const gutter = 12;
    if (paneName === 'left') {
      const rightWidth = document.querySelector('.admin-pane--right')?.getBoundingClientRect().width || startRight;
      const maxLeft = Math.min(MAX_LEFT, Math.max(MIN_LEFT, layoutWidth - rightWidth - MIN_CENTER - gutter));
      const value = clamp(startLeft + moveEvent.clientX - startX, MIN_LEFT, maxLeft);
      rootStyle.setProperty('--dock-left', `${value}px`);
      saveLayout({ leftWidth: value });
    }
    if (paneName === 'right') {
      const leftWidth = document.querySelector('.admin-pane--left')?.getBoundingClientRect().width || startLeft;
      const maxRight = Math.min(MAX_RIGHT, Math.max(MIN_RIGHT, layoutWidth - leftWidth - MIN_CENTER - gutter));
      const value = clamp(startRight - (moveEvent.clientX - startX), MIN_RIGHT, maxRight);
      rootStyle.setProperty('--dock-right', `${value}px`);
      saveLayout({ rightWidth: value });
    }
    if (paneName === 'bottom') {
      const value = clamp(startBottom - (moveEvent.clientY - startY), MIN_BOTTOM, MAX_BOTTOM);
      rootStyle.setProperty('--dock-bottom', `${value}px`);
      saveLayout({ bottomHeight: value });
    }
    if (layout) layout.classList.add('is-resizing');
  };
  const onUp = () => {
    layout?.classList.remove('is-resizing');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

export function initializeDocking() {
  applyStoredLayout();
  injectPanelButtons();

  document.addEventListener('click', event => {
    const detachButton = event.target.closest('[data-dock-detach]');
    if (detachButton) {
      detachPanel(detachButton.dataset.dockDetach);
      return;
    }
    const returnButton = event.target.closest('[data-dock-return]');
    if (returnButton) dockPanel(returnButton.dataset.dockReturn);
  });


  document.addEventListener('dblclick', event => {
    const handle = event.target.closest('[data-dock-detach-handle]');
    if (handle && !handle.closest('.floating-panel')) {
      detachPanel(handle.dataset.dockDetachHandle);
    }
  });

  document.addEventListener('pointerdown', event => {
    const drag = event.target.closest('[data-floating-drag]');
    if (drag) {
      const floatingWindow = drag.closest('.floating-panel');
      if (floatingWindow) startDrag(event, floatingWindow);
      return;
    }
    const floatingResize = event.target.closest('[data-floating-resize]');
    if (floatingResize) {
      const floatingWindow = floatingResize.closest('.floating-panel');
      if (floatingWindow) startFloatingResize(event, floatingWindow);
      return;
    }
    const dockResize = event.target.closest('[data-resize-pane]');
    if (dockResize) startDockResize(event, dockResize.dataset.resizePane);
  });
}

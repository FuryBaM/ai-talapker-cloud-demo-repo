export function defineComponent(name, klass) {
  if (!customElements.get(name)) customElements.define(name, klass);
}

export class LightComponent extends HTMLElement {
  $(selector) {
    return this.querySelector(selector);
  }

  $all(selector) {
    return Array.from(this.querySelectorAll(selector));
  }

  emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, {
      detail,
      bubbles: true,
      composed: true,
    }));
  }
}

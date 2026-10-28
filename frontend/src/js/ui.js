export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

class SafeMarkup {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

// Only the literal structure is parsed. Values become text nodes or inert attributes.
export function html(strings, ...values) {
  const template = document.createElement('template');
  template.innerHTML = strings.reduce((result, part, index) => result + part + (index < values.length ? `__value_${index}__` : ''), '');
  const pattern = /__value_(\d+)__/g;
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      for (const attribute of [...node.attributes]) {
        if (/^on/i.test(attribute.name) || ['href', 'src', 'srcdoc'].includes(attribute.name)) throw new Error('Unsafe template attribute');
        node.setAttribute(attribute.name, attribute.value.replace(pattern, (_, index) => String(values[index] ?? '')));
      }
    } else {
      const parts = node.textContent.split(/(__value_\d+__)/);
      const replacement = document.createDocumentFragment();
      for (const part of parts) {
        const match = /^__value_(\d+)__$/.exec(part);
        const value = match ? values[match[1]] : part;
        if (value instanceof SafeMarkup) {
          const nested = document.createElement('template');
          nested.innerHTML = value.value;
          replacement.append(nested.content);
        } else replacement.append(document.createTextNode(String(value ?? '')));
      }
      node.replaceWith(replacement);
    }
  }
  return new SafeMarkup(template.innerHTML);
}

export function joinHtml(values) {
  if (!values.every(value => value instanceof SafeMarkup)) throw new Error('Untrusted template join');
  return new SafeMarkup(values.join(''));
}

export function safeColor(value) {
  return /^#[0-9a-f]{6}$/i.test(value) ? value : '#38bdf8';
}

export function canvasPoint(event, canvas, camera) {
  const rect = canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left) * camera.screenW / rect.width,
    y: (event.clientY - rect.top) * camera.screenH / rect.height };
}

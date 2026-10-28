// Images stay in the HTTP asset cache, never in simulation snapshots.
export function safeImagePath(path) {
  return typeof path === 'string' && /^[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_.-]+)+\.(svg|png|jpg|webp)$/.test(path) &&
    !path.split('/').some(part => part === '.' || part === '..');
}

export class AssetCache {
  constructor(registry = {}, createImage = () => new Image()) {
    this.registry = registry;
    this.createImage = createImage;
    this.entries = new Map();
    this.disposed = false;
  }

  get(id) {
    if (this.disposed) return { state: 'disposed', image: null };
    const path = this.registry[id]?.path;
    if (this.entries.has(path || id)) return this.entries.get(path || id);
    const entry = { state: 'loading', image: null, error: null, promise: null };
    this.entries.set(path || id, entry);
    if (!safeImagePath(path)) {
      entry.state = 'error'; entry.error = 'Unknown or unsafe image path';
      entry.promise = Promise.resolve(entry);
      return entry;
    }
    let image;
    try { image = this.createImage(); }
    catch (error) {
      entry.state = 'error'; entry.error = String(error);
      entry.promise = Promise.resolve(entry); return entry;
    }
    entry.image = image;
    entry.promise = new Promise(resolve => {
      entry.finish = () => resolve(entry);
      image.onload = () => {
        if (this.disposed) { resolve(entry); return; }
        if (image.naturalWidth > 0) entry.state = 'ready';
        else { entry.state = 'error'; entry.error = 'Empty image'; }
        resolve(entry);
      };
      image.onerror = () => {
        if (!this.disposed) { entry.state = 'error'; entry.error = `Image load failed: ${path}`; }
        resolve(entry);
      };
      try { image.src = `/assets/${path}`; }
      catch (error) { entry.state = 'error'; entry.error = String(error); resolve(entry); }
    });
    return entry;
  }

  dispose() {
    this.disposed = true;
    for (const entry of this.entries.values()) {
      if (entry.image) { entry.image.onload = null; entry.image.onerror = null; entry.image.src = ''; }
      entry.state = 'disposed'; entry.image = null; entry.finish?.();
    }
    this.entries.clear();
  }
}

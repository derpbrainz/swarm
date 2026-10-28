import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml } from '../../frontend/src/js/ui.js';

test('untrusted names are escaped as text, including attributes', () => {
  assert.equal(escapeHtml('<svg onload=alert()>'), '&lt;svg onload=alert()&gt;');
  assert.equal(escapeHtml('&"\''), '&amp;&quot;&#39;');
});

import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  workers: 1,
  webServer: {
    command: 'PYTHONPATH=. python3 tests/browser_server.py',
    url: 'http://127.0.0.1:8765/api/config',
    reuseExistingServer: false,
  },
  use: { baseURL: 'http://127.0.0.1:8765', browserName: 'chromium', channel: 'chrome' },
});

import { defineConfig } from '@playwright/test'

const port = Number(process.env.PLAYWRIGHT_PORT || 4173)
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './tests',
  timeout: 60000,
  workers: 1,
  use: { baseURL, viewport: { width: 1440, height: 1100 }, trace: 'retain-on-failure' },
  webServer: { command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`, url: baseURL, reuseExistingServer: false,
    env: { VITE_DESKTOP_WINDOWS_URL: 'https://downloads.example.test/8t-windows.exe', VITE_DESKTOP_MAC_ARM_URL: '', VITE_DESKTOP_MAC_INTEL_URL: '', VITE_DESKTOP_LINUX_URL: '' } },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] } } },
    { name: 'firefox', use: { browserName: 'firefox', launchOptions: { firefoxUserPrefs: { 'media.navigator.streams.fake': true, 'media.navigator.permission.disabled': true } } } },
  ],
})
import { access, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const desktopTargets = [
  ['VITE_DESKTOP_WINDOWS_URL', 'Windows x64'],
  ['VITE_DESKTOP_MAC_ARM_URL', 'macOS Apple Silicon'],
  ['VITE_DESKTOP_MAC_INTEL_URL', 'macOS Intel'],
  ['VITE_DESKTOP_LINUX_URL', 'Linux x64'],
]

const errors = []

for (const [name, label] of desktopTargets) {
  const value = process.env[name]?.trim()
  if (!value) {
    errors.push(`${name} is required for the ${label} download.`)
    continue
  }

  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') errors.push(`${name} must use HTTPS.`)
    if (url.hostname.endsWith('.example.test')) errors.push(`${name} still uses a test URL.`)
  } catch {
    errors.push(`${name} must be a valid HTTPS URL.`)
  }
}

const dist = resolve('dist')
for (const file of ['index.html']) {
  try {
    await access(resolve(dist, file))
  } catch {
    errors.push(`dist/${file} is missing; run npm run build first.`)
  }
}

try {
  const html = await readFile(resolve(dist, 'index.html'), 'utf8')
  if (!html.includes('<script')) errors.push('dist/index.html does not reference a JavaScript bundle.')
} catch {
  // The missing-file error above is more useful to the caller.
}

if (errors.length) {
  console.error('Release check failed:')
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log('Release check passed: production web build and desktop download URLs are configured.')

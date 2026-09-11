import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { strToU8, unzipSync, zipSync } from 'fflate'
import { newSong } from '../src/model'
import { decodeProject, encodeProject, encodeWav } from '../src/storage/project'

test('trial demo playback, project exchange and audio undo', async ({ page }, testInfo) => {
  const failures: string[] = []
  page.on('pageerror', error => failures.push(error.message))
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '8T.' })).toBeVisible()
  await expect(page.locator('.track-row')).toHaveCount(4)
  await expect(page.getByText('BROWSER TRIAL / 4 TRACKS / 5 MIN')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Arm track 5', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Load demo' }).click()
  await expect(page.getByLabel('Session name')).toHaveValue('Sunday sketches')
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(3)
  const ink = await page.locator('canvas').first().evaluate((canvas: HTMLCanvasElement) => {
    const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data
    let colored = 0
    for (let index = 0; index < data.length; index += 4) if (data[index + 3] && data[index + 1] > data[index] * 1.5) colored++
    return colored
  })
  expect(ink).toBeGreaterThan(100)
  await page.screenshot({ path: testInfo.outputPath('desktop-mixer.png'), fullPage: true })
  await page.getByRole('button', { name: 'Play', exact: true }).click()
  await expect.poll(() => page.getByLabel('Output level').evaluate((element: HTMLMeterElement) => element.value)).toBeGreaterThan(0.01)
  await expect(page.getByLabel('Playhead time')).not.toHaveText('00:00.00')
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Rewind' })).toBeEnabled()
  await page.getByRole('button', { name: 'Rewind' }).click()
  await page.getByLabel('Track 1 name', { exact: true }).fill('Keys')
  await page.getByLabel('Track 1 name', { exact: true }).press('Space')
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeDisabled()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download project', exact: true }).click()
  const download = await downloadEvent
  const path = testInfo.outputPath('browser.8t')
  await download.saveAs(path)
  const decoded = decodeProject(new Uint8Array(readFileSync(path)))
  expect(decoded.lyrics).toContain('SUNDAY SKETCHES')
  expect(decoded.tracks).toHaveLength(8)
  expect(decoded.tracks.slice(4).every(track => !track.audio.length)).toBe(true)
  expect(decoded.tracks[0].audio.length).toBe(441000)
  await page.getByRole('button', { name: 'New session' }).click()
  await page.getByTestId('project-input').setInputFiles(path)
  await expect(page.getByLabel('Track 1 name', { exact: true })).toHaveValue('Keys ', { timeout: 15000 })
  await expect(page.getByRole('button', { name: 'Download project', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Erase track 1', exact: true }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(2)
  await page.getByRole('button', { name: 'Undo audio edit' }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(3)
  await page.getByRole('button', { name: 'Redo audio edit' }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(2)
  const exportEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export stereo WAV' }).click()
  expect((await exportEvent).suggestedFilename()).toMatch(/\.wav$/)
  expect(failures).toEqual([])
})

test('records a fake microphone while backing tracks play and retains the take', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext()
      await context.resume()
      const oscillator = context.createOscillator()
      const gain = context.createGain()
      const destination = context.createMediaStreamDestination()
      gain.gain.value = 0.2
      oscillator.frequency.value = 440
      oscillator.connect(gain).connect(destination)
      oscillator.start()
      return destination.stream
    }
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Load demo' }).click()
  await page.getByRole('button', { name: 'Arm track 4', exact: true }).click()
  await page.getByRole('button', { name: 'Record', exact: true }).click()
  await expect(page.locator('.transport-state')).toHaveText('REC')
  await expect.poll(() => page.getByLabel('Input level').evaluate((element: HTMLMeterElement) => element.value)).toBeGreaterThan(0.001)
  await expect.poll(() => page.getByLabel('Output level').evaluate((element: HTMLMeterElement) => element.value)).toBeGreaterThan(0.001)
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.locator('.transport-state')).toHaveText('STOP')
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(4)
  await expect(page.getByRole('button', { name: 'Undo audio edit' })).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('mobile trial mixer, downloads and settings stay within the viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Load demo' }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(3)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-mixer.png'), fullPage: true })
  await page.getByRole('button', { name: 'Download desktop', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '8T Desktop' })).toBeVisible()
  await expect(page.getByText('Unsigned preview')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Download Windows x64' })).toHaveAttribute('href', 'https://downloads.example.test/8t-windows.exe')
  await expect(page.getByText('Not yet available')).toHaveCount(3)
  await page.screenshot({ path: testInfo.outputPath('mobile-downloads.png'), fullPage: true })
  await page.getByRole('button', { name: 'Close desktop downloads' }).click()
  await page.getByRole('button', { name: 'Audio settings', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-settings.png'), fullPage: true })
})

test('incompatible desktop project leaves trial work intact', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/')
  await page.getByLabel('Session name').fill('Keep this session')
  const entries = unzipSync(encodeProject(newSong()))
  const metadata = JSON.parse(new TextDecoder().decode(entries['song.json']))
  metadata.version = 5
  const bytes = zipSync({ ...entries, 'song.json': strToU8(JSON.stringify(metadata)), 'track-4.wav': encodeWav([new Float32Array([0.1])]) })
  await page.getByTestId('project-input').setInputFiles({ name: 'desktop.8t', mimeType: 'application/zip', buffer: Buffer.from(bytes) })
  await expect(page.getByRole('alert')).toContainText('tracks 5-8')
  await expect(page.getByLabel('Session name')).toHaveValue('Keep this session')
  await expect(page.locator('.track-row')).toHaveCount(4)
})

test('trial recovery restores stopped work', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/')
  await page.getByRole('button', { name: 'Load demo' }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(3)
  await expect.poll(() => page.evaluate(async () => {
    const databases = await indexedDB.databases()
    if (!databases.some(database => database.name === 'keyval-store')) return false
    return new Promise<boolean>(resolve => {
      const request = indexedDB.open('keyval-store')
      request.onsuccess = () => {
        const read = request.result.transaction('keyval').objectStore('keyval').get('8t-web-session-v1')
        read.onsuccess = () => { resolve(Boolean(read.result)); request.result.close() }
        read.onerror = () => { resolve(false); request.result.close() }
      }
      request.onerror = () => resolve(false)
    })
  })).toBe(true)
  await page.reload()
  await page.getByRole('button', { name: 'Restore session', exact: true }).click()
  await expect(page.getByLabel('Recorded audio waveform')).toHaveCount(3)
  await expect(page.getByLabel('Session name')).toHaveValue('Sunday sketches')
})
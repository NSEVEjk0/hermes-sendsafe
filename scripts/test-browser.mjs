import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const root = path.resolve(import.meta.dirname, '..')
const artifact = path.join(root, 'reports', 'browser')
const screenshots = path.join(root, 'docs', 'screenshots')
await fs.mkdir(artifact, { recursive: true })
await fs.mkdir(screenshots, { recursive: true })
const port = 8788
const server = spawn('python3', ['scripts/serve_demo.py', '--port', String(port), '--storage', '.local/e2e-review'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
let stderr = ''
server.stderr.on('data', b => { stderr += b.toString() })
let browser
try {
  const link = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Preview did not start: ' + stderr)), 10000)
    server.stdout.on('data', b => {
      const match = b.toString().match(/http:\/\/127\.0\.0\.1:[0-9]+\/#[^\s]+/)
      if (match) { clearTimeout(timer); resolve(match[0]) }
    })
    server.on('exit', code => { clearTimeout(timer); reject(new Error('Preview exited ' + code + ': ' + stderr)) })
  })
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(link)
  await page.locator('#empty').waitFor({ state: 'visible' })
  await page.screenshot({ path: path.join(screenshots, 'review.png'), fullPage: true })
  await page.click('#demo')
  await page.locator('#report').waitFor({ state: 'visible' })
  assert.match(await page.locator('#filename').textContent(), /xlsx$/)
  assert.match(await page.locator('#findings').textContent(), /Hidden Excel worksheets/)
  assert.equal(await page.locator('#prepare').isDisabled(), true)
  assert.equal((await page.textContent('body')).includes('CANARY_7391'), false)
  await page.screenshot({ path: path.join(screenshots, 'excel-inspection.png'), fullPage: true })
  await page.check('#consent')
  await page.click('#prepare')
  await page.locator('#output').waitFor({ state: 'visible' })
  assert.match(await page.locator('#preview').textContent(), /16800/)
  assert.equal((await page.locator('#preview').textContent()).includes('Payroll'), false)
  await page.screenshot({ path: path.join(screenshots, 'sharing-copy.png'), fullPage: true })
  const download = await Promise.all([page.waitForEvent('download'), page.click('#download')])
  await download[0].saveAs(path.join(artifact, 'client-summary.xlsx'))
  await page.click('#verify')
  await page.getByText('✓ Copy still matches its receipt').waitFor()
  await page.locator('.file-button').filter({ hasText: '.docx' }).click()
  await page.locator('#report').waitFor({ state: 'visible' })
  assert.match(await page.locator('#findings').textContent(), /deleted/i)
  await page.check('#consent')
  await page.click('#prepare')
  await page.locator('#output').waitFor({ state: 'visible' })
  assert.match(await page.locator('#preview').textContent(), /12,000/)
  assert.equal((await page.textContent('body')).includes('CANARY_7391'), false)
  const word = await Promise.all([page.waitForEvent('download'), page.click('#download')])
  await word[0].saveAs(path.join(artifact, 'proposal.docx'))
  await page.screenshot({ path: path.join(screenshots, 'word-copy.png'), fullPage: true })
  await page.locator('#upload').setInputFiles({ name: 'broken.xlsx', mimeType: 'application/octet-stream', buffer: Buffer.from('not an office document') })
  await page.locator('#error').waitFor({ state: 'visible' })
  assert.equal(await page.locator('#error').textContent() === '', false)
  const unauthorized = await context.request.post(`http://127.0.0.1:${port}/api/demo`, { data: {} })
  assert.equal(unauthorized.status(), 401)
  assert.deepEqual(errors, [])
  await context.close()
  console.log('PASS: Excel and Word inspect → consent → rebuild → preview → download; reverify, invalid upload, API authentication; no hidden canaries or browser errors.')
} finally {
  if (browser) await browser.close()
  server.kill('SIGTERM')
}

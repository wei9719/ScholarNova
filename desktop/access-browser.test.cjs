const { test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { safeAccessUrl, safeNetworkAccess, candidateLinks, createAccessBrowser } = require('./access-browser.cjs')

for (const url of ['file:///C:/private', 'javascript:alert(1)', 'https://key:secret@example.org/',
  'http://127.0.0.1/api', 'https://localhost/', 'http://0x7f000001', 'http://10.0.0.1',
  'http://169.254.169.254', 'http://[::1]', 'https://foo.localhost', 'not a url']) {
  test('rejects privileged or invalid navigation: ' + url, () => assert.equal(safeAccessUrl(url), false))
}
test('allows normal public library pages', () => assert.equal(safeAccessUrl('https://lib.lut.edu.cn/'), true))
test('PDF candidates are deduplicated, bounded and checked', () => {
  const links = candidateLinks([{ url: 'https://example.org/a.pdf', label: 'a' },
    { url: 'https://example.org/a.pdf' }, { url: 'file:///secret' }, null,
    ...Array.from({ length: 20 }, (_, i) => ({ url: `https://example.org/${i}.pdf` }))])
  assert.equal(links.length, 12)
  assert.equal(links[0].label, 'a')
})

function harness() {
  const sessions = [], windows = [], views = [], handlers = new Map()
  let nextId = 1
  const makeContents = () => Object.assign(new EventEmitter(), {
    id: nextId++, mainFrame: {}, url: '', messages: [],
    getURL() { return this.url }, loadURL: async function (url) { this.url = url },
    setWindowOpenHandler(handler) { this.openHandler = handler },
    send(channel, value) { this.messages.push([channel, value]) },
    isDestroyed: () => false, close() {},
  })
  class BrowserWindow extends EventEmitter {
    constructor(options) { super(); this.options = options; this.webContents = makeContents(); this.contentView = { addChildView() {} }; windows.push(this) }
    isDestroyed() { return false } getContentSize() { return [1200, 900] } async loadFile() {}
  }
  class WebContentsView {
    constructor(options) { this.options = options; this.webContents = makeContents(); views.push(this) } setBounds() {}
  }
  const electron = { BrowserWindow, WebContentsView, ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: {}, shell: {}, session: { fromPartition(partition) {
      const ses = Object.assign(new EventEmitter(), { partition, proxy: null,
        async setProxy(value) { this.proxy = value }, setPermissionRequestHandler(value) { this.permission = value },
        setPermissionCheckHandler(value) { this.check = value }, webRequest: { onBeforeRequest() {} }, async clearStorageData() {},
      }); sessions.push(ses); return ses
    } } }
  const manager = createAccessBrowser({ electron, userData: 'unused-test-profile',
    networkCheck: async () => true,
    getPaper: async id => ({ id, title: 'Test paper' }), importPdf: async () => { throw new Error('must not import') }, onImported() {},
  })
  return { manager, sessions, windows, views, handlers }
}
test('campus window isolates the unprivileged page and uses direct session proxy', async () => {
  const run = harness()
  await run.manager.open({ url: 'https://lib.lut.edu.cn/', channel: 'campus', paper_id: 'p1' })
  assert.deepEqual(run.sessions[0].proxy, { mode: 'direct' })
  assert.ok(!run.sessions[0].partition.startsWith('persist:'))
  assert.equal(run.views[0].options.webPreferences.preload, undefined)
  assert.equal(run.views[0].options.webPreferences.nodeIntegration, false)
  assert.equal(run.views[0].options.webPreferences.sandbox, true)
  assert.equal(run.sessions[0].check(), false)
})
test('DNS-based private and loopback aliases are rejected', async () => {
  for (const address of ['127.0.0.1', '10.0.0.1', '192.168.1.1', '::ffff:127.0.0.1', 'fe80::1']) {
    assert.equal(await safeNetworkAccess('https://alias.example.org', async () => [{ address }]), false)
  }
  assert.equal(await safeNetworkAccess('https://alias.example.org', async () => [{ address: '93.184.216.34' }]), true)
})
test('simultaneous opens cannot bypass the three-window reservation', async () => {
  const run = harness()
  const result = await Promise.allSettled(Array.from({ length: 7 }, () => run.manager.open({ url: 'https://example.org' })))
  assert.equal(result.filter(item => item.status === 'fulfilled').length, 3)
})
test('public browsing does not modify proxy settings and each window gets its own session', async () => {
  const run = harness()
  await run.manager.open({ url: 'https://example.org/a', channel: 'public' })
  await run.manager.open({ url: 'https://example.org/b', channel: 'public' })
  assert.equal(run.sessions[0].proxy, null)
  assert.notEqual(run.sessions[0].partition, run.sessions[1].partition)
})
test('remote pages and toolbar subframes cannot execute bridge commands', async () => {
  const run = harness()
  await run.manager.open({ url: 'https://example.org' })
  const command = run.handlers.get('scholarnova:access:command')
  assert.equal(await command({ sender: run.views[0].webContents }, 'detect'), false)
  assert.equal(await command({ sender: run.windows[0].webContents, senderFrame: {} }, 'detect'), false)
})
test('unbound browser download is cancelled before touching local files', async () => {
  const run = harness()
  await run.manager.open({ url: 'https://example.org' })
  let cancelled = false
  run.sessions[0].emit('will-download', {}, { cancel() { cancelled = true } }, run.views[0].webContents)
  assert.equal(cancelled, true)
})
test('concurrent access windows are bounded', async () => {
  const run = harness()
  for (let i = 0; i < 3; i++) await run.manager.open({ url: 'https://example.org' })
  await assert.rejects(run.manager.open({ url: 'https://example.org' }), /最多/)
})

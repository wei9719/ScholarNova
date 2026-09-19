const { test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const mainSource = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')

test('portable packaging uses direct zip extraction instead of a nested LZMA archive', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'))
  assert.equal(manifest.build.portable.useZip, true)
})

function mockWindow({ minimized = false, destroyed = false } = {}) {
  const calls = []
  return {
    calls,
    isDestroyed: () => destroyed,
    isMinimized: () => minimized,
    restore: () => calls.push('restore'),
    show: () => calls.push('show'),
    focus: () => calls.push('focus'),
    loadURL: async url => calls.push(['loadURL', url]),
    webContents: {
      setWindowOpenHandler() {},
      on() {},
      session: { setPermissionRequestHandler() {} },
    },
  }
}

function loadMain({ smoke = false, window = mockWindow(), localModel, gotLock = true } = {}) {
  const app = new EventEmitter()
  Object.assign(app, {
    isPackaged: false,
    setAppUserModelId() {},
    requestSingleInstanceLock: () => gotLock,
    quit() {},
    getPath: () => 'unused-test-profile',
    // Do not bootstrap servers, spawn a backend, or access user data in tests.
    whenReady: () => new Promise(() => {}),
  })
  const constructed = []
  const handlers = new Map()
  const electron = {
    app,
    BrowserWindow: function (options) { constructed.push(options); return window },
    dialog: {},
    shell: {},
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
  }
  const context = vm.createContext({
    require: name => name === 'electron' ? electron
      : name === './local-model.cjs' && localModel ? localModel : require(name),
    process: { argv: smoke ? ['--smoke-test'] : [], env: {}, platform: 'win32' },
    __dirname,
    console,
  })
  vm.runInContext(mainSource, context, { filename: 'main.cjs' })
  return {
    app, context, constructed, window, handlers,
    setWindow(value) {
      context.testWindow = value
      vm.runInContext('mainWindow = testWindow', context)
    },
  }
}

test('normal startup explicitly shows and focuses the window after loading', async () => {
  const { context, constructed, window } = loadMain()
  await context.createWindow(18766)
  assert.equal(constructed[0].show, true)
  assert.equal(constructed[0].webPreferences.preload, path.join(__dirname, 'preload.cjs'))
  assert.equal(constructed[0].webPreferences.contextIsolation, true)
  assert.equal(constructed[0].webPreferences.nodeIntegration, false)
  assert.equal(constructed[0].webPreferences.sandbox, true)
  assert.deepEqual(window.calls, [['loadURL', 'http://127.0.0.1:18766'], 'show', 'focus'])
})

test('second launch explicitly shows a hidden, non-minimized window', () => {
  const { app, setWindow, window } = loadMain()
  setWindow(window)
  app.emit('second-instance')
  assert.deepEqual(window.calls, ['show', 'focus'])
})

test('second launch restores a minimized window before showing and focusing', () => {
  const { app, setWindow, window } = loadMain({ window: mockWindow({ minimized: true }) })
  setWindow(window)
  app.emit('second-instance')
  assert.deepEqual(window.calls, ['restore', 'show', 'focus'])
})

test('missing or destroyed windows are ignored without native window calls', () => {
  const { app, setWindow, window } = loadMain({ window: mockWindow({ destroyed: true }) })
  assert.doesNotThrow(() => app.emit('second-instance'))
  setWindow(window)
  assert.doesNotThrow(() => app.emit('second-instance'))
  assert.deepEqual(window.calls, [])
})

test('smoke mode never restores, shows or focuses its hidden window', () => {
  const { app, context, setWindow, window } = loadMain({ smoke: true, window: mockWindow({ minimized: true }) })
  setWindow(window)
  context.showMainWindow()
  app.emit('second-instance')
  assert.deepEqual(window.calls, [])
})

test('second launch during bootstrap is safe and startup still reveals the window', async () => {
  const { app, context, window } = loadMain()
  app.emit('second-instance')
  await context.createWindow(18766)
  assert.deepEqual(window.calls, [['loadURL', 'http://127.0.0.1:18766'], 'show', 'focus'])
})

test('a second desktop instance does not construct or start a local-model manager', () => {
  let constructed = 0
  loadMain({ gotLock: false, localModel: { createLocalModelManager() { constructed += 1 } } })
  assert.equal(constructed, 0)
})

test('quitting during local-model startup waits for owned cleanup and never starts the backend', async () => {
  let finishStart
  let stopCalls = 0
  const manager = {
    status: () => ({ status: 'starting' }),
    start: () => new Promise(resolve => { finishStart = resolve }),
    stop: async () => { stopCalls += 1 },
  }
  const { app, context } = loadMain({ localModel: { createLocalModelManager: () => manager } })
  let prevented = 0
  let quitCalls = 0
  app.quit = () => { quitCalls += 1 }
  const bootstrapping = context.bootstrap()
  await Promise.resolve()
  app.emit('before-quit', { preventDefault: () => { prevented += 1 } })
  app.emit('before-quit', { preventDefault: () => { prevented += 1 } })
  finishStart({ status: 'started' })
  await bootstrapping
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(prevented, 2)
  assert.equal(stopCalls, 1)
  assert.equal(quitCalls, 1)
  assert.equal(vm.runInContext('backendProcess', context), null)
  app.emit('before-quit', { preventDefault: () => { prevented += 1 } })
  assert.equal(stopCalls, 1)
  assert.equal(prevented, 2)
})

function fakeManager() {
  let state = 'stopped'
  return {
    startCalls: 0, stopCalls: 0,
    status: () => ({ status: state }),
    async start() { this.startCalls += 1; state = 'running' },
    async stop() { this.stopCalls += 1; state = 'stopped' },
  }
}

test('IPC controls reject other windows before constructing or touching a local service', async () => {
  let made = 0
  const run = loadMain({ localModel: { createLocalModelManager: () => { made += 1; return fakeManager() } } })
  run.setWindow(run.window)
  assert.equal(run.handlers.size, 3)
  for (const action of ['status', 'start', 'stop']) {
    const result = await run.handlers.get(`scholarnova:local-model:${action}`)({ sender: {} })
    assert.equal(result.status, 'error')
  }
  assert.equal(made, 0)
})

test('IPC serializes repeated starts, stops only the current manager and creates a fresh restart', async () => {
  const made = []
  const run = loadMain({ localModel: { createLocalModelManager: () => {
    const manager = fakeManager(); made.push(manager); return manager
  } } })
  run.setWindow(run.window)
  const invoke = action => run.handlers.get(`scholarnova:local-model:${action}`)({ sender: run.window.webContents })
  const first = await Promise.all([invoke('start'), invoke('start')])
  assert.ok(first.every(item => item.status === 'running'))
  assert.equal(made.length, 1)
  assert.equal(made[0].startCalls, 1)
  assert.equal((await invoke('stop')).status, 'stopped')
  assert.equal((await invoke('start')).status, 'running')
  assert.equal(made.length, 2)
  assert.equal(made[1].startCalls, 1)
  assert.equal((await invoke('status')).status, 'running')
})

test('a failed owned-process stop cannot lead to a second local-model process', async () => {
  let made = 0
  const manager = fakeManager()
  manager.stop = async () => { manager.status = () => ({ status: 'error', message: 'stop failed' }) }
  const run = loadMain({ localModel: { createLocalModelManager: () => { made += 1; return manager } } })
  await run.context.controlLocalModel('start')
  assert.equal((await run.context.controlLocalModel('stop')).status, 'error')
  assert.equal((await run.context.controlLocalModel('start')).status, 'error')
  assert.equal(made, 1)
})

test('preload exposes only argument-free status, start and stop functions', async () => {
  const calls = []
  let exposed
  const context = vm.createContext({ require: name => {
    assert.equal(name, 'electron')
    return {
      contextBridge: { exposeInMainWorld: (name, api) => { exposed = { name, api } } },
      ipcRenderer: { invoke: async (...args) => { calls.push(args); return { status: 'stopped' } } },
    }
  } })
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'preload.cjs'), 'utf8'), context)
  assert.equal(exposed.name, 'scholarLocalModel')
  assert.deepEqual(Object.keys(exposed.api).sort(), ['start', 'status', 'stop'])
  for (const action of ['status', 'start', 'stop']) await exposed.api[action]({ path: 'untrusted', token: 'untrusted' })
  assert.deepEqual(calls, ['status', 'start', 'stop'].map(action => [`scholarnova:local-model:${action}`]))
})

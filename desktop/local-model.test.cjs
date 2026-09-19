const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const { EventEmitter, once } = require('node:events')
const { PassThrough } = require('node:stream')
const { createLocalModelManager, readConfig, localEnvironment, portAvailable, terminateOwnedProcess } = require('./local-model.cjs')

function fixture(t, overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'scholarnova-local-model-test-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const userData = path.join(root, 'profile')
  const serviceRoot = path.join(root, 'service')
  const python = path.join(root, 'isolated-python.exe')
  const model = path.join(root, 'isolated-model')
  fs.mkdirSync(userData)
  fs.mkdirSync(model)
  fs.mkdirSync(path.join(serviceRoot, 'local_inference'), { recursive: true })
  fs.writeFileSync(path.join(serviceRoot, 'local_inference', '__main__.py'), '# test fixture')
  fs.writeFileSync(python, '')
  const config = { enabled: true, python_path: python, model_dir: model,
    token: 'only-this-test-local-model-secret-1234567890', port: 8766,
    runtime_dir: path.join(root, 'scholarnova-cache'), ...overrides }
  fs.writeFileSync(path.join(userData, 'local_model.json'), JSON.stringify(config))
  return { root, userData, serviceRoot, config }
}

function fakeProcess() {
  const child = new EventEmitter()
  Object.assign(child, { pid: 424242, exitCode: null, signalCode: null, killed: false,
    stdout: new PassThrough(), stderr: new PassThrough() })
  return child
}

function managerFor(f, extra = {}) {
  const calls = []
  const stopped = []
  const messages = []
  const child = fakeProcess()
  const manager = createLocalModelManager({ ...f,
    logger: { info: message => messages.push(message) }, environment: {},
    checkPort: async () => true,
    spawnProcess: (...args) => { calls.push(args); queueMicrotask(() => child.emit('spawn')); return child },
    terminate: async owned => { stopped.push(owned); owned.exitCode = 0; owned.emit('exit', 0, null) },
    ...extra,
  })
  return { manager, child, calls, stopped, messages }
}

test('missing or disabled configuration never starts Python or touches caches', async t => {
  const f = fixture(t, { enabled: false })
  for (const removeConfig of [false, true]) {
    if (removeConfig) fs.unlinkSync(path.join(f.userData, 'local_model.json'))
    const run = managerFor(f)
    assert.equal((await run.manager.start()).status, 'disabled')
    await run.manager.stop()
    assert.equal(run.calls.length, 0)
    assert.equal(run.stopped.length, 0)
    assert.equal(fs.existsSync(f.config.runtime_dir), false)
  }
})

for (const overrides of [
  { python_path: 'relative-python' }, { model_dir: 'relative-model' },
  { port: 0 }, { port: 1023 }, { port: 65536 }, { port: '8766' }, { port: 8766.5 },
  { token: 'short' }, { token: 'has whitespace '.repeat(4) }, { token: '中文'.repeat(32) },
  { runtime_dir: 'relative-cache' },
]) {
  test(`rejects invalid configuration ${Object.keys(overrides)[0]}=${JSON.stringify(Object.values(overrides)[0])}`, t => {
    const f = fixture(t, overrides)
    assert.throws(() => readConfig(f.userData))
  })
}

test('requires the configured Python file and model directory to exist with the right types', t => {
  const f = fixture(t)
  fs.unlinkSync(f.config.python_path)
  assert.throws(() => readConfig(f.userData), /python_path/)
  fs.mkdirSync(f.config.python_path)
  assert.throws(() => readConfig(f.userData), /wrong file type/)
})

test('invalid JSON logs a safe message without leaking private configuration contents', async t => {
  const f = fixture(t)
  fs.writeFileSync(path.join(f.userData, 'local_model.json'), `{"token":"${f.config.token}", broken`)
  const run = managerFor(f)
  assert.equal((await run.manager.start()).status, 'error')
  assert.equal(run.calls.length, 0)
  assert.ok(run.messages.join('\n').includes('not valid JSON'))
  assert.ok(!run.messages.join('\n').includes(f.config.token))
})

test('occupied port never starts, reuses, or stops another process', async t => {
  const f = fixture(t)
  const run = managerFor(f, { checkPort: async () => false })
  assert.equal((await run.manager.start()).status, 'error')
  await run.manager.stop()
  assert.equal(run.calls.length, 0)
  assert.equal(run.stopped.length, 0)
  assert.match(run.messages.join('\n'), /occupied.*Close the existing local-model service yourself/)
})

test('port probe binds only loopback and leaves an existing listener intact', async t => {
  const server = net.createServer()
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => server.close())
  assert.equal(await portAvailable(server.address().port), false)
  assert.equal(server.listening, true)
})

test('environment isolates all caches and removes inherited imports and cloud credentials', t => {
  const f = fixture(t)
  const inherited = { PATH: 'tool-path', PYTHONPATH: 'another-project', PYTHONHOME: 'other-python',
    VIRTUAL_ENV: 'other-venv', CONDA_PREFIX: 'other-conda', OPENAI_API_KEY: 'cloud-secret',
    HF_TOKEN: 'hub-secret', HF_HOME: 'other-cache', TORCH_HOME: 'other-torch', TEMP: 'other-temp' }
  const env = localEnvironment(f.config, inherited)
  for (const key of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'OPENAI_API_KEY', 'HF_TOKEN']) assert.equal(env[key], undefined)
  for (const key of ['HF_HOME', 'HF_HUB_CACHE', 'TORCH_HOME', 'TEMP', 'TMP', 'TMPDIR', 'CUDA_CACHE_PATH']) {
    assert.ok(env[key].startsWith(f.config.runtime_dir + path.sep))
    assert.ok(fs.statSync(env[key]).isDirectory())
  }
  assert.equal(env.HF_HUB_OFFLINE, '1')
  assert.equal(env.TRANSFORMERS_OFFLINE, '1')
  assert.equal(env.PYTHONDONTWRITEBYTECODE, '1')
  assert.equal(env.SCHOLARNOVA_LOCAL_MODEL_DIR, f.config.model_dir)
  assert.equal(env.SCHOLARNOVA_LOCAL_MODEL_TOKEN, f.config.token)
  assert.equal(env.SCHOLARNOVA_LOCAL_MODEL_PORT, '8766')
  assert.equal(env.PATH, 'tool-path')
  assert.equal(inherited.HF_HOME, 'other-cache')
})

test('starts exactly one owned isolated Python, preserves service root and stops only that child', async t => {
  const f = fixture(t)
  const run = managerFor(f)
  const [first, second] = await Promise.all([run.manager.start(), run.manager.start()])
  assert.equal(first.status, 'started')
  assert.equal(second.pid, first.pid)
  assert.equal(run.calls.length, 1)
  const [executable, args, options] = run.calls[0]
  assert.equal(executable, f.config.python_path)
  assert.deepEqual(args, ['-B', '-m', 'local_inference'])
  assert.equal(options.cwd, f.serviceRoot)
  assert.equal(options.windowsHide, true)
  assert.deepEqual(options.stdio, ['ignore', 'pipe', 'pipe'])
  assert.ok(!args.join(' ').includes(f.config.token))
  await run.manager.stop()
  await run.manager.stop()
  assert.deepEqual(run.stopped, [run.child])
  assert.equal((await run.manager.start()).status, 'stopped')
})

test('shutdown during port check prevents a late Python launch', async t => {
  const f = fixture(t)
  let release
  const run = managerFor(f, { checkPort: () => new Promise(resolve => { release = resolve }) })
  const starting = run.manager.start()
  assert.equal(run.manager.status().status, 'starting')
  const stopping = run.manager.stop()
  release(true)
  assert.equal((await starting).status, 'stopped')
  await stopping
  assert.equal(run.manager.status().status, 'stopped')
  assert.equal(run.calls.length, 0)
})

test('public status contains no configuration and tracks process exit without claiming readiness', async t => {
  const f = fixture(t)
  const run = managerFor(f)
  await run.manager.start()
  const status = run.manager.status()
  assert.equal(status.status, 'running')
  assert.match(status.message, /可能仍在加载/)
  assert.deepEqual(Object.keys(status).sort(), ['message', 'status'])
  assert.ok(!JSON.stringify(status).includes(f.config.token))
  assert.ok(!JSON.stringify(status).includes(f.config.model_dir))
  status.status = 'tampered'
  assert.equal(run.manager.status().status, 'running')
  run.child.exitCode = 1
  run.child.emit('exit', 1, null)
  assert.equal(run.manager.status().status, 'error')
  await run.manager.stop()
  assert.equal(run.stopped.length, 0)
})

test('failed stop stays error and is not reported as released memory', async t => {
  const f = fixture(t)
  const run = managerFor(f, { terminate: async () => { throw new Error('stop failed') } })
  await run.manager.start()
  assert.equal((await run.manager.stop()).status, 'error')
  assert.equal(run.manager.status().status, 'error')
  assert.equal(run.calls.length, 1)
})

test('Python startup failure is logged and does not target another process on shutdown', async t => {
  const f = fixture(t)
  const child = fakeProcess()
  const run = managerFor(f, { spawnProcess: () => {
    queueMicrotask(() => child.emit('error', Object.assign(new Error('private error text'), { code: 'ENOENT' })))
    return child
  } })
  assert.equal((await run.manager.start()).status, 'error')
  await run.manager.stop()
  assert.equal(run.stopped.length, 0)
  assert.match(run.messages.join('\n'), /ENOENT/)
})

test('stdout and stderr redact the token even when it arrives in separate chunks', async t => {
  const f = fixture(t)
  const run = managerFor(f)
  await run.manager.start()
  for (const stream of [run.child.stdout, run.child.stderr]) {
    stream.write(`prefix ${f.config.token.slice(0, 12)}`)
    stream.write(`${f.config.token.slice(12)} suffix\n`)
    stream.end('last line')
    await once(stream, 'end')
  }
  for (const name of ['local-model.stdout.log', 'local-model.stderr.log']) {
    const content = fs.readFileSync(path.join(f.userData, 'logs', name), 'utf8')
    assert.ok(!content.includes(f.config.token))
    assert.match(content, /prefix \[REDACTED\] suffix/)
    assert.match(content, /last line/)
  }
  await run.manager.stop()
})

test('an already exited child is not targeted by the Windows tree terminator', async () => {
  await terminateOwnedProcess({ exitCode: 0, signalCode: null, killed: false }, 'win32')
})

test('non-Windows stop waits for the owned process exit before reporting completion', async () => {
  const child = fakeProcess()
  const signals = []
  child.kill = signal => { signals.push(signal); return true }
  let finished = false
  const stopping = terminateOwnedProcess(child, 'darwin').then(() => { finished = true })
  await Promise.resolve()
  assert.equal(finished, false)
  assert.deepEqual(signals, ['SIGTERM'])
  child.exitCode = 0
  child.emit('exit', 0, null)
  await stopping
  assert.equal(finished, true)
})

test('desktop packaging includes the helper and only Python local-inference resources', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'))
  assert.ok(manifest.build.files.includes('desktop/local-model.cjs'))
  assert.ok(manifest.build.files.includes('desktop/preload.cjs'))
  assert.deepEqual(manifest.build.extraResources.find(item => item.to === 'local_inference'), {
    from: 'backend/local_inference', to: 'local_inference', filter: ['**/*.py'],
  })
})

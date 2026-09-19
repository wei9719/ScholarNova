const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const { spawn, execFile } = require('node:child_process')

function readConfig(userData) {
  const filename = path.join(userData, 'local_model.json')
  if (!fs.existsSync(filename)) return null
  let config
  try { config = JSON.parse(fs.readFileSync(filename, 'utf8')) }
  catch { throw new Error('local_model.json is not valid JSON; check the private local-model configuration') }
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error('local_model.json must contain an object')
  }
  if (config.enabled !== true) return null
  for (const [field, directory] of [['python_path', false], ['model_dir', true]]) {
    const value = config[field]
    if (typeof value !== 'string' || !path.isAbsolute(value) || !fs.existsSync(value)) {
      throw new Error(`${field} must be an existing absolute path`)
    }
    const stat = fs.statSync(value)
    if (directory ? !stat.isDirectory() : !stat.isFile()) {
      throw new Error(`${field} has the wrong file type`)
    }
  }
  const port = config.port === undefined ? 8766 : config.port
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error('local-model port must be an integer from 1024 to 65535')
  }
  if (typeof config.token !== 'string' || config.token.length < 32 || /[^\x21-\x7e]/.test(config.token)) {
    throw new Error('local-model token must contain at least 32 printable non-whitespace ASCII characters')
  }
  const runtimeDir = config.runtime_dir || path.join(userData, 'local_model_runtime')
  if (typeof runtimeDir !== 'string' || !path.isAbsolute(runtimeDir)) {
    throw new Error('runtime_dir must be an absolute directory path')
  }
  if (fs.existsSync(runtimeDir) && !fs.statSync(runtimeDir).isDirectory()) {
    throw new Error('runtime_dir must be a directory')
  }
  return { ...config, port, runtime_dir: runtimeDir }
}

function portAvailable(port) {
  return new Promise(resolve => {
    const probe = net.createServer()
    probe.once('error', () => resolve(false))
    probe.listen({ host: '127.0.0.1', port, exclusive: true }, () => probe.close(() => resolve(true)))
  })
}

function localEnvironment(config, inherited = process.env) {
  const env = { ...inherited }
  // An explicitly selected Python must not inherit another project's imports or credentials.
  for (const key of Object.keys(env)) {
    if (/API_KEY|TOKEN/i.test(key) || /^(PYTHONPATH|PYTHONHOME|VIRTUAL_ENV|CONDA_PREFIX)$/i.test(key)) delete env[key]
  }
  const directories = {
    HF_HOME: 'huggingface', HF_HUB_CACHE: 'huggingface/hub',
    HUGGINGFACE_HUB_CACHE: 'huggingface/hub', TRANSFORMERS_CACHE: 'huggingface/transformers',
    HF_DATASETS_CACHE: 'huggingface/datasets', TORCH_HOME: 'torch',
    XDG_CACHE_HOME: 'cache', CUDA_CACHE_PATH: 'cuda', TEMP: 'tmp', TMP: 'tmp', TMPDIR: 'tmp',
  }
  for (const [key, directory] of Object.entries(directories)) {
    env[key] = path.join(config.runtime_dir, directory)
    fs.mkdirSync(env[key], { recursive: true })
  }
  return {
    ...env,
    SCHOLARNOVA_LOCAL_MODEL_DIR: config.model_dir,
    SCHOLARNOVA_LOCAL_MODEL_TOKEN: config.token,
    SCHOLARNOVA_LOCAL_MODEL_PORT: String(config.port),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1', PYTHONUNBUFFERED: '1',
    HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', HF_HUB_DISABLE_TELEMETRY: '1',
  }
}

function terminateOwnedProcess(child, platform = process.platform) {
  if (!child || child.exitCode !== null || child.signalCode !== null || child.killed) return Promise.resolve()
  if (platform === 'win32') {
    // The Windows venv launcher can own the real Python worker: target only our tracked tree.
    return new Promise((resolve, reject) => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'],
      { windowsHide: true }, error => {
        if (error && child.exitCode === null && child.signalCode === null) reject(new Error('Could not stop the owned local-model process tree'))
        else resolve()
      }))
  }
  return new Promise((resolve, reject) => {
    let timer
    const finish = error => {
      clearTimeout(timer)
      child.removeListener('exit', onExit)
      child.removeListener('error', onError)
      if (error) reject(new Error('Could not stop the owned local-model process'))
      else resolve()
    }
    const onExit = () => finish()
    const onError = () => finish(true)
    child.once('exit', onExit)
    child.once('error', onError)
    timer = setTimeout(() => {
      timer = setTimeout(() => finish(true), 5000)
      child.kill('SIGKILL')
    }, 5000)
    child.kill('SIGTERM')
  })
}

function createLocalModelManager({ userData, serviceRoot, spawnProcess = spawn,
  checkPort = portAvailable, terminate = terminateOwnedProcess, environment = process.env, logger = console }) {
  let child = null
  let starting = null
  let stopPromise = null
  let stopping = false
  let secret = ''
  let state = { status: 'stopped', message: '本机服务尚未启动' }
  const logsDir = path.join(userData, 'logs')
  const lifecycleLog = path.join(logsDir, 'local-model.lifecycle.log')
  const redact = value => secret ? String(value).split(secret).join('[REDACTED]') : String(value)
  function report(message) {
    const safe = redact(message)
    try {
      fs.mkdirSync(logsDir, { recursive: true })
      fs.appendFileSync(lifecycleLog, `${new Date().toISOString()} ${safe}\n`, { mode: 0o600 })
    } catch { /* Console remains available when the log directory is unwritable. */ }
    logger.info(`[ScholarNova local model] ${safe}`)
  }
  function capture(stream, filename) {
    if (!stream) return
    stream.setEncoding('utf8')
    let pending = ''
    function write(value) {
      try { fs.appendFileSync(filename, redact(value), { mode: 0o600 }) }
      catch { report('Could not write the local-model output log') }
    }
    stream.on('data', data => {
      pending += data
      const end = pending.lastIndexOf('\n')
      if (end >= 0) { write(pending.slice(0, end + 1)); pending = pending.slice(end + 1) }
    })
    stream.on('end', () => { if (pending) write(pending); pending = '' })
  }
  async function launch() {
    try {
      const config = readConfig(userData)
      if (!config) {
        state = { status: 'disabled', message: '尚未启用独立本机服务，请先完成本机配置' }
        return { ...state }
      }
      secret = config.token
      if (!path.isAbsolute(serviceRoot) || !fs.existsSync(path.join(serviceRoot, 'local_inference', '__main__.py'))) {
        throw new Error('Local inference service is missing; rebuild or reinstall the ScholarNova service resources')
      }
      if (!await checkPort(config.port)) {
        throw new Error(`Port ${config.port} is occupied. Close the existing local-model service yourself before retrying; no process was reused or stopped`)
      }
      if (stopping) return { status: 'stopped' }
      fs.mkdirSync(logsDir, { recursive: true })
      const env = localEnvironment(config, environment)
      child = spawnProcess(config.python_path, ['-B', '-m', 'local_inference'], {
        cwd: serviceRoot, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      })
      const owned = child
      capture(owned.stdout, path.join(logsDir, 'local-model.stdout.log'))
      capture(owned.stderr, path.join(logsDir, 'local-model.stderr.log'))
      owned.on('exit', (code, signal) => {
        if (child === owned) child = null
        state = stopping
          ? { status: 'stopped', message: '本次本机服务已停止，模型资源已释放' }
          : { status: 'error', message: '本机服务已退出，请检查本机服务日志' }
        report(`Owned local-model process exited (code=${code}, signal=${signal || 'none'}). Logs: ${logsDir}`)
      })
      return await new Promise(resolve => {
        owned.once('spawn', () => {
          state = { status: 'running', message: '本机服务进程已启动；模型可能仍在加载，请测试连接确认' }
          report(`Started owned local-model process on 127.0.0.1:${config.port}; weights may still be loading. Logs: ${logsDir}`)
          resolve({ status: 'started', port: config.port, pid: owned.pid })
        })
        owned.once('error', error => {
          if (child === owned) child = null
          state = { status: 'error', message: '本机服务启动失败，请检查本机配置和服务日志' }
          report(`Could not start local-model Python (${error.code || error.name}); check python_path. Logs: ${logsDir}`)
          resolve({ status: 'error', logsDir })
        })
      })
    } catch (error) {
      state = { status: 'error', message: '本机服务未启动，请检查配置、端口占用及服务日志；不会停止其他程序' }
      report(`${error.message}. Logs: ${logsDir}`)
      return { status: 'error', logsDir }
    }
  }
  return {
    status() { return { ...state } },
    start() {
      if (stopping) return Promise.resolve({ status: 'stopped' })
      if (!starting) {
        state = { status: 'starting', message: '正在启动本机服务进程' }
        starting = launch()
      }
      return starting
    },
    stop() {
      stopping = true
      if (!stopPromise) stopPromise = (async () => {
        if (starting) await starting
        const owned = child
        if (owned) {
          try { await terminate(owned) }
          catch (error) {
            state = { status: 'error', message: '未能停止本应用启动的本机服务，请查看服务日志' }
            report(`${error.message}. Logs: ${logsDir}`)
            return { ...state }
          }
        }
        state = { status: 'stopped', message: '本次本机服务已停止；下次启动应用仍按本机配置决定是否启用' }
        return { ...state }
      })()
      return stopPromise
    },
  }
}

module.exports = { createLocalModelManager, readConfig, localEnvironment, portAvailable, terminateOwnedProcess }

if (require.main === module) {
  const [userData, serviceRoot] = process.argv.slice(2)
  if (!userData || !serviceRoot || !path.isAbsolute(userData) || !path.isAbsolute(serviceRoot)) {
    console.error('Usage: node desktop/local-model.cjs <absolute-userData> <absolute-serviceRoot>')
    process.exitCode = 1
  } else {
    const manager = createLocalModelManager({ userData, serviceRoot })
    let exiting = false
    const stop = () => {
      if (exiting) return
      exiting = true
      void manager.stop().finally(() => process.exit())
    }
    process.on('SIGINT', stop)
    process.on('SIGTERM', stop)
    void manager.start().then(result => { if (result.status === 'error') process.exitCode = 1 })
  }
}

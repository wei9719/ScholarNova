// Remote institutional pages never receive the application's preload or token.
const fs = require('node:fs/promises')
const fsSync = require('node:fs')
const path = require('node:path')
const { randomUUID } = require('node:crypto')
const { isIP } = require('node:net')
const dns = require('node:dns/promises')
const LIMIT = 50 * 1024 * 1024

function safeAccessUrl(value) {
  try {
    if (typeof value !== 'string' || value.length > 8192) return false
    const url = new URL(value)
    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '')
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return false
    if (['localhost', 'localhost.localdomain'].includes(host) || host.endsWith('.localhost')) return false
    if (isIP(host) === 6) return false
    if (isIP(host) === 4) {
      const [a, b] = host.split('.').map(Number)
      if (a === 0 || a === 10 || a === 127 || a >= 224 ||
          (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
          (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127)) return false
    }
    return Boolean(host)
  } catch { return false }
}

function candidateLinks(items) {
  const seen = new Set()
  return (Array.isArray(items) ? items : []).filter(item => {
    if (!item || !safeAccessUrl(item.url) || seen.has(item.url)) return false
    seen.add(item.url)
    return true
  }).slice(0, 12).map(item => ({ url: item.url, label: String(item.label || 'PDF').slice(0, 100) }))
}

function publicAddress(ip) {
  if (isIP(ip) === 4) return safeAccessUrl('https://' + ip)
  // Global IPv6 unicast only; reject mapped, loopback, link-local and unique-local.
  return isIP(ip) === 6 && /^[23][0-9a-f]{0,3}:/i.test(ip) && !/^2001:db8:/i.test(ip)
}

async function safeNetworkAccess(value, lookup = dns.lookup) {
  if (!safeAccessUrl(value)) return false
  const hostname = new URL(value).hostname
  if (isIP(hostname)) return publicAddress(hostname)
  let timer
  try {
    const addresses = await Promise.race([
      lookup(hostname, { all: true, verbatim: true }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('DNS timeout')), 3500) }),
    ])
    return addresses.length > 0 && addresses.every(item => publicAddress(item.address))
  } catch { return false }
  finally { clearTimeout(timer) }
}

function createAccessBrowser({ electron, userData, getPaper, importPdf, inspectPdf, onImported, networkCheck = safeNetworkAccess }) {
  const { BrowserWindow, WebContentsView, session, ipcMain, dialog, shell } = electron
  const windows = new Map()
  let opening = 0
  const emit = (entry, message) => {
    if (!entry.window.isDestroyed()) entry.window.webContents.send('scholarnova:access:state', {
      message, url: entry.view.webContents.getURL(), paper: entry.paper?.title || '', channel: entry.channel,
    })
  }

  async function importDownload(entry, item) {
    if (!entry.paper || entry.downloading) {
      item.cancel()
      emit(entry, entry.paper ? '正在处理一个下载，请稍候' : '请回到论文详情，再点击“机构授权获取”绑定论文后导入')
      return
    }
    if (item.getTotalBytes() > LIMIT) { item.cancel(); emit(entry, 'PDF 不能超过 50 MB'); return }
    entry.downloading = true
    const folder = path.join(userData, 'access-downloads')
    fsSync.mkdirSync(folder, { recursive: true })
    const file = path.join(folder, randomUUID() + '.pdf')
    item.setSavePath(file)
    entry.item = item
    const timer = setTimeout(() => item.cancel(), 120000)
    item.on('updated', () => {
      if (item.getReceivedBytes() > LIMIT) item.cancel()
      else emit(entry, `正在下载 ${Math.round(item.getReceivedBytes() / 1024)} KB，完成后请核对论文再确认导入`)
    })
    item.once('done', async (_event, state) => {
      clearTimeout(timer)
      try {
        if (state !== 'completed') { emit(entry, '下载未完成，请检查权限，或使用外部浏览器下载后手动导入'); return }
        const stat = await fs.stat(file)
        if (stat.size > LIMIT) throw new Error('PDF 不能超过 50 MB')
        const bytes = await fs.readFile(file)
        if (bytes.subarray(0, 5).toString() !== '%PDF-') throw new Error('下载内容不是 PDF，可能是登录页；请完成授权后重试')
        if (entry.window.isDestroyed()) return
        const inspection = inspectPdf ? await inspectPdf(entry.paper.id, bytes) : null
        const source = new URL(item.getURL())
        const answer = await dialog.showMessageBox(entry.window, {
          type: 'question', title: '确认论文与全文一致',
          message: `将此 PDF 导入《${entry.paper.title}》？`,
          detail: `文件：${path.basename(item.getFilename()).slice(0, 120)}\n来源：${source.origin}${source.pathname}\n${inspection ? inspection.summary + '\nPDF 首页：' + inspection.excerpt : '尚未自动核验论文身份，请核对当前网页与文件。'}\n这里只保存并解析，不自动发送云模型。替换已有全文后，旧页面定位会失效。`,
          buttons: ['确认导入', '取消'], defaultId: 1, cancelId: 1,
        })
        if (answer.response !== 0) { emit(entry, '已取消导入'); return }
        emit(entry, '正在导入并建立全文索引…')
        const result = await importPdf(entry.paper.id, bytes)
        onImported(entry.paper.id)
        emit(entry, result.feature_error || 'PDF 已导入。回到 ScholarNova 点击“原文伴读”开始阅读')
      } catch (error) {
        emit(entry, error.message || '导入失败，请手动下载 PDF 后重试')
      } finally {
        entry.downloading = false
        entry.item = null
        await fs.unlink(file).catch(() => {})
      }
    })
  }

  ipcMain.handle('scholarnova:access:command', async (event, action) => {
    const entry = windows.get(event.sender.id)
    if (!entry || event.sender !== entry.window.webContents || event.senderFrame !== entry.window.webContents.mainFrame) return false
    const wc = entry.view.webContents
    if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack()
    else if (action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward()
    else if (action === 'reload') wc.reload()
    else if (action === 'external' && safeAccessUrl(wc.getURL())) await shell.openExternal(wc.getURL())
    else if (action === 'detect') {
      const links = candidateLinks(await wc.executeJavaScript(`(() => {
        const items = [];
        for (const el of document.querySelectorAll('meta[name="citation_pdf_url"], a[href], embed[src], object[data]')) {
          const raw = el.getAttribute('content') || el.getAttribute('href') || el.getAttribute('src') || el.getAttribute('data');
          try { const url = new URL(raw, document.baseURI).href;
            if (el.tagName === 'META' || /(?:\\.pdf(?:[?#]|$)|\\/pdf(?:[/?#]|$)|download)/i.test(url))
              items.push({url, label: (el.textContent || '页面 PDF').trim().slice(0,100)});
          } catch {}
          if (items.length >= 30) break;
        }
        return items;
      })()`))
      if (!links.length) { emit(entry, '未识别到直接 PDF 链接。可点击网页下载按钮，或在外部浏览器下载后手动导入'); return false }
      if (!entry.paper) { emit(entry, '已识别 PDF；请从论文详情的“机构授权获取”进入，先绑定论文再导入'); return false }
      const choices = links.slice(0, 5)
      const answer = await dialog.showMessageBox(entry.window, {
        type: 'question', title: '选择当前页面的 PDF', message: '选择要下载并导入的 PDF',
        detail: '下载使用当前浏览会话的权限；如果无权限，请完成机构登录。',
        buttons: [...choices.map((link, index) => `${index + 1}. ${link.label}`), '取消'],
        defaultId: choices.length, cancelId: choices.length,
      })
      if (answer.response < choices.length) wc.downloadURL(choices[answer.response].url)
    }
    return true
  })

  async function open(request) {
    if (!request || !safeAccessUrl(request.url)) throw new Error('请选择有效的外部 HTTP(S) 学术页面')
    if (windows.size + opening >= 3) throw new Error('请先关闭一个授权浏览窗口，最多同时打开三个')
    opening++
    let created = null
    try {
    if (!await networkCheck(request.url)) throw new Error('页面地址无法核验或指向本机/内网，请使用外部浏览器登录后手动导入 PDF')
    const paper = request.paper_id ? await getPaper(request.paper_id) : null
    const channel = request.channel === 'campus' ? 'campus' : 'public'
    // In-memory, per-window sessions; logging out/closing destroys session credentials.
    const ses = session.fromPartition('scholarnova-access-' + randomUUID())
    if (channel === 'campus') await ses.setProxy({ mode: 'direct' })
    ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
    ses.setPermissionCheckHandler(() => false)
    const dnsCache = new Map()
    ses.webRequest.onBeforeRequest((details, callback) => {
      if (/^(blob:|data:|chrome-extension:)/.test(details.url)) { callback({ cancel: false }); return }
      const networkUrl = details.url.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:')
      let hostname
      try { hostname = new URL(networkUrl).hostname } catch { callback({ cancel: true }); return }
      if (!safeAccessUrl(networkUrl)) { callback({ cancel: true }); return }
      let check = dnsCache.get(hostname)
      if (!check || Date.now() - check.time > 30000) {
        if (dnsCache.size > 500) dnsCache.delete(dnsCache.keys().next().value)
        check = { time: Date.now(), result: networkCheck(networkUrl) }
        dnsCache.set(hostname, check)
      }
      void check.result.then(allowed => callback({ cancel: !allowed }), () => callback({ cancel: true }))
    })
    const window = new BrowserWindow({
      width: 1200, height: 900, minWidth: 800, minHeight: 600, title: 'ScholarNova 授权获取全文',
      autoHideMenuBar: true,
      webPreferences: { preload: path.join(__dirname, 'access-preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
    })
    created = window
    const view = new WebContentsView({ webPreferences: { session: ses, contextIsolation: true, nodeIntegration: false, sandbox: true } })
    window.contentView.addChildView(view)
    const entry = { window, view, paper, channel, downloading: false }
    const controlId = window.webContents.id
    windows.set(controlId, entry)
    const resize = () => { const [width, height] = window.getContentSize(); view.setBounds({ x: 0, y: 142, width, height: Math.max(1, height - 142) }) }
    window.on('resize', resize)
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    window.webContents.on('will-navigate', event => event.preventDefault())
    view.webContents.setWindowOpenHandler(({ url }) => {
      if (safeAccessUrl(url)) void view.webContents.loadURL(url).catch(() => emit(entry, '页面打开失败，请使用外部浏览器'))
      return { action: 'deny' }
    })
    view.webContents.on('will-navigate', (event, url) => { if (!safeAccessUrl(url)) event.preventDefault() })
    view.webContents.on('did-navigate', () => emit(entry, '登录和授权由你完成。找到全文后点击“识别 PDF 并导入”，或点击网页下载按钮'))
    view.webContents.on('did-fail-load', (_event, code) => { if (code !== -3) emit(entry, '页面无法加载。请检查网络或使用外部浏览器；应用不会更改系统 VPN') })
    ses.on('will-download', (_event, item, wc) => {
      if (wc !== view.webContents) { item.cancel(); return }
      void importDownload(entry, item).catch(() => { item.cancel(); entry.downloading = false; emit(entry, '下载处理失败，请使用手动导入') })
    })
    window.on('closed', () => {
      windows.delete(controlId)
      if (entry.downloading && entry.item) entry.item.cancel()
      if (!view.webContents.isDestroyed()) view.webContents.close()
      void ses.clearStorageData().catch(() => {})
    })
    await window.loadFile(path.join(__dirname, 'access.html'))
    resize()
    emit(entry, channel === 'campus' ? '校园通道使用应用内直连，不修改系统代理。若学校要求 WebVPN，请在此完成学校授权' : '公共通道沿用系统网络。Google Scholar 由你主动浏览，不进行后台批量抓取')
    void view.webContents.loadURL(request.url).catch(() => emit(entry, '无法打开网页，请使用外部浏览器'))
    return { opened: true, channel }
    } catch (error) {
      if (created && !created.isDestroyed()) created.close()
      throw error
    } finally { opening-- }
  }
  return { open }
}

module.exports = { safeAccessUrl, safeNetworkAccess, candidateLinks, createAccessBrowser }

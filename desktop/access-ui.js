document.querySelectorAll('button[data-action]').forEach(button => {
  button.addEventListener('click', () => window.accessControls.command(button.dataset.action)
    .catch(() => { document.querySelector('#status').textContent = '操作未完成，请重试或使用外部浏览器' }))
})
window.accessControls.onState(state => {
  document.querySelector('#channel').textContent = state.channel === 'campus' ? '校园与机构通道' : '公共学术通道'
  document.querySelector('#paper').textContent = state.paper ? '绑定论文：' + state.paper : '浏览模式：从论文详情进入后可导入全文'
  document.querySelector('#status').textContent = state.message
  // Do not expose signed query strings or credentials in the toolbar.
  try { const url = new URL(state.url); document.querySelector('#address').textContent = url.origin + url.pathname } catch {}
})

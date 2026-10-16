const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('scholarLocalModel', {
  status: () => ipcRenderer.invoke('scholarnova:local-model:status'),
  start: () => ipcRenderer.invoke('scholarnova:local-model:start'),
  stop: () => ipcRenderer.invoke('scholarnova:local-model:stop'),
})
contextBridge.exposeInMainWorld('scholarAccess', {
  open: request => ipcRenderer.invoke('scholarnova:access:open', request),
  onImported: callback => {
    const listener = (_event, value) => callback(value)
    ipcRenderer.on('scholarnova:access:imported', listener)
    return () => ipcRenderer.removeListener('scholarnova:access:imported', listener)
  },
})

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('scholarLocalModel', {
  status: () => ipcRenderer.invoke('scholarnova:local-model:status'),
  start: () => ipcRenderer.invoke('scholarnova:local-model:start'),
  stop: () => ipcRenderer.invoke('scholarnova:local-model:stop'),
})

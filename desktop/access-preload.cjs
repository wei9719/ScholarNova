const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('accessControls', {
  command: action => ['back', 'forward', 'reload', 'external', 'detect'].includes(action)
    ? ipcRenderer.invoke('scholarnova:access:command', action) : Promise.resolve(false),
  onState: callback => {
    const listener = (_event, value) => callback(value)
    ipcRenderer.on('scholarnova:access:state', listener)
    return () => ipcRenderer.removeListener('scholarnova:access:state', listener)
  },
})

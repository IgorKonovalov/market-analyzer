import { IPC_CHANNELS } from './ipc-channels'

describe('IPC_CHANNELS', () => {
  it('contains every documented channel', () => {
    expect(IPC_CHANNELS.APP_GET_INFO).toBe('app:get-info')
    expect(IPC_CHANNELS.SIDECAR_GET_PORT).toBe('sidecar:get-port')
    expect(IPC_CHANNELS.SIDECAR_STATUS).toBe('sidecar:status')
    expect(IPC_CHANNELS.DIALOG_OPEN_DIRECTORY).toBe('dialog:open-directory')
    expect(IPC_CHANNELS.SHELL_OPEN_EXTERNAL).toBe('shell:open-external')
  })

  it('has unique channel names', () => {
    const values = Object.values(IPC_CHANNELS)
    expect(new Set(values).size).toBe(values.length)
  })
})

/** Commands owned by the currently mounted workspace, including its selection snapshot. */
type WorkspaceCommands = { captureSelection: () => void; openExtensions: () => void }
let active: WorkspaceCommands | undefined

export function registerWorkspaceCommands(commands: WorkspaceCommands) {
  active = commands
  return () => { if (active === commands) active = undefined }
}

export function captureWorkspaceSelection() { active?.captureSelection() }
export function openWorkspaceExtensions() { active?.openExtensions() }

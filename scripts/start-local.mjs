import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'

const root = process.cwd()
const python = process.platform === 'win32'
  ? join(root, '.venv', 'Scripts', 'python.exe')
  : join(root, '.venv', 'bin', 'python')
const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js')
let backend
let frontend

function portInUse(port) {
  return new Promise((resolve) => {
    const server = createServer()
    server.once('error', () => resolve(true))
    server.once('listening', () => server.close(() => resolve(false)))
    server.listen(port, '127.0.0.1')
  })
}

async function freePort(preferred) {
  for (let port = preferred; port < preferred + 100; port += 1) {
    if (!(await portInUse(port))) return port
  }
  throw new Error(`No available local port was found near ${preferred}.`)
}

async function waitForBackend(apiUrl) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`${apiUrl}/api/health`)
      if (response.ok) return true
    } catch { /* The local API is still starting. */ }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

function stop() {
  backend?.kill()
  frontend?.kill()
}

if (!existsSync(python)) {
  console.error('Mirdain Print Hub needs its local Python environment first.')
  console.error('Run: py -3 -m venv .venv')
  console.error('Then: .\\.venv\\Scripts\\python.exe -m pip install -e .')
  process.exitCode = 1
} else {
  const apiPort = await freePort(8765)
  const webPort = await freePort(5174)
  const apiUrl = `http://127.0.0.1:${apiPort}`
  const webUrl = `http://127.0.0.1:${webPort}`
  backend = spawn(python, ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], { stdio: 'inherit' })
  if (!(await waitForBackend(apiUrl))) {
    console.error('The local API did not start. Check the message above.')
    stop()
    process.exitCode = 1
  } else {
    frontend = spawn(process.execPath, [vite, '--host', '127.0.0.1', '--port', String(webPort)], { stdio: 'inherit', env: { ...process.env, MIRDAIN_API_URL: apiUrl } })
    setTimeout(() => { void import('node:child_process').then(({ exec }) => exec(process.platform === 'win32' ? `start "" ${webUrl}` : `xdg-open ${webUrl}`)) }, 900)
    console.log(`\nMirdain Print Hub is opening at ${webUrl}. Press Ctrl+C here to stop both local services.\n`)
  }
}

process.on('SIGINT', () => { stop(); process.exit() })
process.on('SIGTERM', () => { stop(); process.exit() })
backend?.on('exit', (code) => { if (code && code !== 0) { stop(); process.exitCode = code } })
frontend?.on('exit', (code) => { if (code && code !== 0) { stop(); process.exitCode = code } })

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createServer } from 'node:net'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const python = process.platform === 'win32'
  ? join(root, '.venv', 'Scripts', 'python.exe')
  : join(root, '.venv', 'bin', 'python')
const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js')
const apiHost = '127.0.0.1'
const webHost = process.env.MIRDAIN_BIND_ADDRESS?.trim() || '127.0.0.1'
const preferredApiPort = configuredPort('MIRDAIN_API_PORT', 8765)
const preferredWebPort = configuredPort('MIRDAIN_LOCAL_WEB_PORT', 5174)
let backend
let frontend
let stopping = false

function configuredPort(name, fallback) {
  const raw = process.env[name]?.trim()
  if (!raw) return fallback
  const port = Number(raw)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    console.error(`${name} must be a whole number from 1 to 65535.`)
    process.exit(1)
  }
  return port
}

function portInUse(port, host) {
  return new Promise((resolve) => {
    const server = createServer()
    server.once('error', () => resolve(true))
    server.once('listening', () => server.close(() => resolve(false)))
    server.listen(port, host)
  })
}

async function freePort(preferred, host) {
  for (let port = preferred; port < Math.min(preferred + 100, 65536); port += 1) {
    if (!(await portInUse(port, host))) return port
  }
  throw new Error(`No available port was found near ${preferred} on ${host}.`)
}

async function waitForBackend(apiUrl) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`${apiUrl}/api/health`)
      if (response.ok) return true
    } catch {
      // The local API is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

function openBrowser(url) {
  if (process.env.MIRDAIN_OPEN_BROWSER?.toLowerCase() === 'false') return
  let command
  let args
  if (process.platform === 'win32') {
    command = 'explorer.exe'
    args = [url]
  } else if (process.platform === 'darwin') {
    command = 'open'
    args = [url]
  } else {
    command = 'xdg-open'
    args = [url]
  }
  const opener = spawn(command, args, { detached: true, stdio: 'ignore' })
  opener.on('error', () => {
    console.log(`Open ${url} in a browser.`)
  })
  opener.unref()
}

function stop() {
  if (stopping) return
  stopping = true
  frontend?.kill()
  backend?.kill()
}

function setupHint() {
  if (process.platform === 'win32') {
    return 'Run .\\scripts\\setup.ps1 from PowerShell, then try again.'
  }
  return 'Run bash scripts/setup.sh, then try again.'
}

if (!existsSync(python)) {
  console.error('Mirdain Print Hub needs its local Python environment first.')
  console.error(setupHint())
  process.exitCode = 1
} else if (!existsSync(vite)) {
  console.error('Mirdain Print Hub needs its frontend dependencies first.')
  console.error(setupHint())
  process.exitCode = 1
} else {
  try {
    const apiPort = await freePort(preferredApiPort, apiHost)
    const webPort = await freePort(preferredWebPort, webHost)
    const apiUrl = `http://${apiHost}:${apiPort}`
    const browserHost = webHost === '0.0.0.0' || webHost === '::' ? '127.0.0.1' : webHost
    const webUrl = `http://${browserHost}:${webPort}`

    backend = spawn(
      python,
      ['-m', 'uvicorn', 'backend.app.main:app', '--host', apiHost, '--port', String(apiPort)],
      { cwd: root, stdio: 'inherit' },
    )
    backend.on('error', (error) => {
      console.error(`The local API could not start: ${error.message}`)
      stop()
      process.exitCode = 1
    })

    if (!(await waitForBackend(apiUrl))) {
      console.error('The local API did not start. Check the message above.')
      stop()
      process.exitCode = 1
    } else {
      frontend = spawn(
        process.execPath,
        [vite, '--host', webHost, '--port', String(webPort), '--strictPort'],
        {
          cwd: root,
          stdio: 'inherit',
          env: { ...process.env, MIRDAIN_API_URL: apiUrl },
        },
      )
      frontend.on('error', (error) => {
        console.error(`The dashboard could not start: ${error.message}`)
        stop()
        process.exitCode = 1
      })
      setTimeout(() => openBrowser(webUrl), 900)
      console.log(`\nMirdain Print Hub is starting at ${webUrl}. Press Ctrl+C here to stop both local services.\n`)
      if (webHost !== '127.0.0.1' && webHost !== 'localhost') {
        console.warn('Network binding is enabled. Remote browser access also requires exact MIRDAIN_ALLOWED_HOSTS entries; use only on a trusted private network or authenticated VPN.')
      }
    }
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    stop()
    process.exitCode = 1
  }
}

process.on('SIGINT', () => {
  stop()
  process.exit()
})
process.on('SIGTERM', () => {
  stop()
  process.exit()
})
backend?.on('exit', (code) => {
  if (!stopping) {
    console.error('The local API stopped unexpectedly.')
    stop()
    process.exitCode = code || 1
  }
})
frontend?.on('exit', (code) => {
  if (!stopping) {
    console.error('The dashboard stopped unexpectedly.')
    stop()
    process.exitCode = code || 1
  }
})

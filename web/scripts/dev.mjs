// Dev: Node API server + Vite dev server side by side, one Ctrl-C kills both.
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const npx = process.platform === 'win32' ? 'npm.cmd' : 'npm'

const kids = [
  spawn(process.execPath, ['server/server.mjs'], { cwd: WEB, stdio: 'inherit' }),
  spawn(npx, ['run', 'dev'], { cwd: path.join(WEB, 'frontend'), stdio: 'inherit' }),
]

const stop = () => { for (const k of kids) k.kill('SIGINT') }
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
for (const k of kids) k.on('exit', code => { stop(); process.exit(code ?? 0) })

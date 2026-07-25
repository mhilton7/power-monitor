import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const dist = join(root, 'dist')
if (!existsSync(dist)) {
  throw new Error('Production bundle is missing. Run npm run build first.')
}

const forbidden = [
  'DashboardPage',
  'DevicesPage',
  'TopologyPage',
  'EnrollmentPage',
  'UsagePage',
  'CostsPage',
  'RatesPage',
  'UsersAccessPage',
  'WorkspaceShell',
]
const files = readdirSync(join(dist, 'assets')).filter((name) => name.endsWith('.js'))
let total = 0
for (const file of files) {
  const path = join(dist, 'assets', file)
  total += statSync(path).size
  const source = readFileSync(path, 'utf8')
  for (const token of forbidden) {
    if (source.includes(token)) throw new Error(`Legacy module token ${token} found in ${file}`)
  }
}
const budget = 1_250_000
if (total > budget) throw new Error(`JavaScript bundle ${total} bytes exceeds ${budget}-byte budget`)
console.log(`Single Home bundle verified: ${files.length} chunks, ${total} bytes, no legacy modules`)

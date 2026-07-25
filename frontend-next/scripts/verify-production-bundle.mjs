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

const index = readFileSync(join(dist, 'index.html'), 'utf8')
const stylesheets = readdirSync(join(dist, 'assets')).filter((name) => name.endsWith('.css'))
if (stylesheets.length !== 1) throw new Error(`Expected one compiled CSS asset, found ${stylesheets.length}`)
const stylesheet = stylesheets[0]
if (!stylesheet || !index.includes(`/assets/${stylesheet}`)) {
  throw new Error('Production index does not reference the compiled CSS asset')
}
const css = readFileSync(join(dist, 'assets', stylesheet), 'utf8')
const compactCss = css.replaceAll(/\s+/g, '')
const requiredCssContracts = [
  '--content-max:1680px',
  '.page-stack{display:grid',
  '.billing-top-metrics',
  '.billing-main-grid',
  '.metadata-list',
  '.history-summary',
  '.advanced-disclosure',
  '.subnav',
  '.structured-list',
]
for (const contract of requiredCssContracts) {
  if (!compactCss.includes(contract)) throw new Error(`Compiled CSS is missing required layout contract ${contract}`)
}
if (css.trimStart().startsWith('<!doctype') || css.includes('<div id="root">')) {
  throw new Error('The production CSS path resolved to HTML instead of CSS')
}
console.log(`Single Home bundle verified: ${files.length} chunks, ${total} bytes, CSS ${stylesheet}, no legacy modules`)

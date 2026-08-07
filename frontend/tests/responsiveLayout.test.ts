import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const components = readFileSync(path.resolve('src/theme/components.css'), 'utf8')
const responsive = readFileSync(path.resolve('src/theme/responsive.css'), 'utf8')
const home = readFileSync(path.resolve('src/pages/home/HomePage.tsx'), 'utf8')

describe('responsive Home layout contract', () => {
  it('uses content-driven Home cards without child-order selectors or equal side rows', () => {
    expect(components).toContain('.home-main-grid { align-items: start; }')
    expect(components).toContain('grid-auto-rows: max-content')
    expect(components).not.toMatch(/\.home-side-stack\s*\{[^}]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/u)
    expect(components).not.toContain('.home-side-stack > .surface:nth-child')
    expect(components).not.toContain('min-height: 28rem')
    expect(components).not.toContain('min-height: 15rem')
    expect(components).not.toMatch(/\.home-power-facts\s*\{[^}]*margin-top:\s*auto/u)
  })

  it('gives Current Pricing a stable class and responsive charts definite heights', () => {
    expect(home).toContain('className="current-pricing-card"')
    expect(components).toContain('.current-pricing-card')
    expect(components).toContain('block-size: clamp(17rem, 28vw, 23rem)')
    expect(components).toContain('block-size: clamp(20rem, 38vw, 32rem)')
    expect(responsive).toContain('.chart-canvas.home-chart { block-size: 17rem; }')
    expect(responsive).toContain('.chart-canvas.history-chart { block-size: 18rem; }')
  })
})

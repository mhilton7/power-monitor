import { describe, expect, it } from 'vitest'
import { energy, fileSize, money, rate, statusLabel } from '../src/utils/format'

describe('centralized display formatting', () => {
  it('formats exact decimal strings only at the display boundary', () => {
    expect(money('4.005', 'USD')).toBe('$4.01')
    expect(rate('0.34421', 'USD')).toBe('$0.34421/kWh')
    expect(energy('12.3456')).toBe('12.3 kWh')
  })

  it('uses readable status and file-size labels', () => {
    expect(statusLabel('online_with_backlog')).toBe('Online With Backlog')
    expect(fileSize(1_048_576)).toBe('1 MB')
  })
})

import { describe, expect, it } from 'vitest'
import {
  current,
  energy,
  fileSize,
  frequency,
  money,
  power,
  powerFactor,
  rate,
  statusLabel,
  voltage,
} from '../src/utils/format'

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

  it('formats sensor electrical metrics without converting missing data to zero', () => {
    expect(power(undefined)).toBe('—')
    expect(power('0')).toBe('0 W')
    expect(voltage('120.4')).toBe('120.4 V')
    expect(current('0.01')).toBe('0.01 A')
    expect(frequency('60.0')).toBe('60.0 Hz')
    expect(powerFactor('0.83')).toBe('0.83')
    expect(powerFactor(undefined)).toBe('—')
    expect(current('-0')).toBe('0.00 A')
  })
})

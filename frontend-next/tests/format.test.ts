import { describe, expect, it } from 'vitest'
import {
  current,
  energy,
  elapsedSince,
  fileSize,
  frequency,
  money,
  power,
  powerFactor,
  percentage,
  rate,
  sensorMeasurementTime,
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

  it.each([
    [0, '0 W'],
    [0.04, '0.04 W'],
    [0.8, '0.8 W'],
    [1, '1 W'],
    [1.25, '1.25 W'],
    [9.99, '9.99 W'],
    [10, '10 W'],
    [12.4, '12.4 W'],
    [999.5, '999.5 W'],
    [1000, '1 kW'],
    [1250, '1.25 kW'],
  ])('preserves useful power precision for %s', (value, expected) => {
    expect(power(value)).toBe(expected)
  })

  it('rejects missing or non-finite power without fabricating zero', () => {
    expect(power(null)).toBe('—')
    expect(power(undefined)).toBe('—')
    expect(power(Number.NaN)).toBe('—')
    expect(power(Number.POSITIVE_INFINITY)).toBe('—')
    expect(power(-0)).toBe('0 W')
  })

  it.each([
    ['60.3444444444444', '60.34%'],
    ['60.3', '60.3%'],
    ['60', '60%'],
    ['0', '0%'],
    ['99.999', '100%'],
    ['100', '100%'],
    ['100.00000000001', '100%'],
  ])('formats coverage %s as %s', (value, expected) => {
    expect(percentage(value)).toBe(expected)
  })

  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY, -1, 101])(
    'rejects invalid coverage %s',
    (value) => {
      expect(percentage(value)).toBe('—')
    },
  )

  it('formats elapsed receipt age from an authoritative server baseline', () => {
    const observed = Date.parse('2026-07-30T12:00:00Z')
    expect(elapsedSince('2026-07-30T12:00:00Z', observed, '2026-07-30T12:00:00Z', observed))
      .toBe('Just now')
    expect(elapsedSince('2026-07-30T11:59:56Z', observed + 1_000, '2026-07-30T12:00:00Z', observed))
      .toBe('5s ago')
    expect(elapsedSince('2026-07-30T11:58:56Z', observed, '2026-07-30T12:00:00Z', observed))
      .toBe('1m 4s ago')
    expect(elapsedSince(undefined, observed)).toBe('Waiting for data')
    expect(elapsedSince('not-a-date', observed)).toBe('Invalid timestamp')
    expect(elapsedSince('2026-07-30T12:01:00Z', observed)).toBe('Invalid timestamp')
  })

  it('keeps sensor timestamps readable without five-digit hour counts', () => {
    const now = Date.parse('2026-07-30T12:00:00Z')
    expect(sensorMeasurementTime('2026-07-30T11:59:45Z', 'live', now))
      .toBe('Updated just now')
    expect(sensorMeasurementTime('2026-07-30T11:55:00Z', 'live', now))
      .toBe('Updated 5 minutes ago')
    expect(sensorMeasurementTime('2026-07-29T12:00:00Z', 'stale', now))
      .toBe('Last reading yesterday')
    expect(sensorMeasurementTime('2023-07-29T12:00:00Z', 'offline', now))
      .toBe('Last reading Jul 29, 2023')
    expect(sensorMeasurementTime(undefined, 'waiting', now))
      .toBe('Waiting for first reading')
    expect(sensorMeasurementTime('not-a-date', 'invalid', now))
      .toBe('Measurement time unavailable')
  })
})

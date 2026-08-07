import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

const storageValues = new Map<string, string>()
const browserStorage: Storage = {
  get length() { return storageValues.size },
  clear: () => { storageValues.clear() },
  getItem: (key) => storageValues.get(key) ?? null,
  key: (index) => [...storageValues.keys()][index] ?? null,
  removeItem: (key) => { storageValues.delete(key) },
  setItem: (key, value) => { storageValues.set(key, value) },
}
Object.defineProperty(window, 'localStorage', { configurable: true, value: browserStorage })
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: browserStorage })

afterEach(() => {
  cleanup()
})

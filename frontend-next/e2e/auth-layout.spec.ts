import { expect, test, type Page } from '@playwright/test'

async function mockSignedOutSession(page: Page) {
  await page.route('**/api/v1/auth/session', async (route) => {
    await route.fulfill({
      json: {
        authenticated: false,
        bootstrap_required: false,
        user: null,
      },
    })
  })
}

test.beforeEach(async ({ page }) => {
  await mockSignedOutSession(page)
})

test('sign-in uses the complete authentication layout and semantic form', async ({ page }) => {
  await page.goto('/sign-in')

  await expect(page.getByRole('heading', { name: 'Understand your home’s energy, privately.' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sign in to your home' })).toBeVisible()
  await expect(page.getByLabel('Email')).toHaveAttribute('autocomplete', 'username')
  await expect(page.locator('#current-password')).toHaveAttribute('autocomplete', 'current-password')
  await expect(page.getByRole('button', { name: 'Show password' })).toBeVisible()

  const css = await page.evaluate(async () => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="stylesheet"]')
    return link
      ? fetch(link.href).then((response) => response.text())
      : [...document.querySelectorAll('style')].map((style) => style.textContent ?? '').join('\n')
  })
  expect(css).toContain('.auth-page')
  expect(css).toContain('.auth-brand')
  expect(css).toContain('.auth-card')
  expect(css).toContain('.password-field')
  expect(css).toContain('.skip-link')

  const layout = await page.evaluate(() => {
    const authPage = document.querySelector<HTMLElement>('.auth-page')
    const pageRect = authPage?.getBoundingClientRect()
    const brandRect = document.querySelector<HTMLElement>('.auth-brand')?.getBoundingClientRect()
    const cardRect = document.querySelector<HTMLElement>('.auth-card')?.getBoundingClientRect()
    const controls = [...document.querySelectorAll<HTMLElement>('.auth-card input, .auth-card button')]
      .map((element) => element.getBoundingClientRect())
    const card = cardRect
      ? {
          left: cardRect.left,
          right: cardRect.right,
          top: cardRect.top,
          bottom: cardRect.bottom,
          width: cardRect.width,
        }
      : null
    return {
      display: authPage ? getComputedStyle(authPage).display : '',
      page: pageRect ? { width: pageRect.width, height: pageRect.height } : null,
      brand: brandRect ? { width: brandRect.width, height: brandRect.height } : null,
      card,
      controlsContained: card !== null && controls.every(
        (control) => control.left >= card.left - 1 && control.right <= card.right + 1,
      ),
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }
  })
  expect(layout.display).toBe('grid')
  expect(layout.page?.height).toBeGreaterThanOrEqual(700)
  expect(layout.brand?.width).toBeGreaterThan(0)
  expect(layout.card?.width).toBeGreaterThan(300)
  expect(layout.controlsContained).toBe(true)
  expect(layout.scrollWidth - layout.clientWidth).toBeLessThanOrEqual(1)

  const skip = page.getByRole('link', { name: 'Skip to main content' })
  await expect(skip).not.toBeInViewport()
  await page.keyboard.press('Tab')
  await expect(skip).toBeFocused()
  await expect(skip).toBeInViewport()
})

test('sign-in remains contained across the reported viewport and responsive breakpoints', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop')
  const viewports = [
    { width: 2048, height: 740 },
    { width: 1440, height: 900 },
    { width: 834, height: 1194 },
    { width: 390, height: 844 },
  ]
  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await page.goto('/sign-in')
    await expect(page.getByRole('heading', { name: 'Sign in to your home' })).toBeVisible()
    const geometry = await page.evaluate(() => {
      const card = document.querySelector<HTMLElement>('.auth-card')?.getBoundingClientRect()
      const inputs = [...document.querySelectorAll<HTMLElement>('.auth-card input')]
        .map((input) => input.getBoundingClientRect())
      const bounds = card ? { left: card.left, right: card.right, width: card.width } : null
      return {
        card: bounds,
        inputsContained: bounds !== null && inputs.every(
          (input) => input.left >= bounds.left - 1 && input.right <= bounds.right + 1,
        ),
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }
    })
    expect(geometry.card?.width).toBeGreaterThan(300)
    expect(geometry.inputsContained).toBe(true)
    expect(geometry.scrollWidth - geometry.clientWidth).toBeLessThanOrEqual(1)
  }
})

test('sign-in visual regression', async ({ page }, testInfo) => {
  test.skip(!['desktop', 'mobile'].includes(testInfo.project.name))
  await page.goto('/sign-in')
  await expect(page.getByRole('heading', { name: 'Sign in to your home' })).toBeVisible()
  await expect(page).toHaveScreenshot('sign-in-repaired.png', {
    fullPage: true,
    animations: 'disabled',
  })
})

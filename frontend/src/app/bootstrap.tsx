import { createRoot } from 'react-dom/client'
import { App } from './App'
import { AppProviders } from './AppProviders'
import '../theme/tokens.css'
import '../theme/base.css'
import '../theme/components.css'
import '../theme/responsive.css'

export function bootstrapApplication(): void {
  const root = document.getElementById('root')
  if (!root) throw new Error('Application root element is missing')
  createRoot(root).render(
    <AppProviders>
      <App />
    </AppProviders>,
  )
}

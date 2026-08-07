import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
  name?: string
  resetKey?: string
}

interface State {
  error?: Error
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = {}

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Single Home interface boundary', { error: error.name, componentStack: info.componentStack })
  }

  componentDidUpdate(previous: Props): void {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: undefined })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="boundary-page" id="main-content">
        <section className="error-state" role="alert" aria-live="assertive">
          <AlertTriangle aria-hidden="true" />
          <div>
            <h1>{this.props.name ?? 'This page needs attention'}</h1>
            <p>Your data is safe. Reload this page to try the request again.</p>
            <button type="button" className="button primary" onClick={() => { window.location.reload(); }}>
              <RefreshCw size={17} /> Reload page
            </button>
          </div>
        </section>
      </main>
    )
  }
}

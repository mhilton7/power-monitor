import { AlertTriangle, RotateCcw } from 'lucide-react'
import {
  Component,
  type ErrorInfo,
  type ReactNode,
} from 'react'
import { Link } from 'react-router-dom'
import { createCorrelationId, toAppError, type AppError } from '../billImportContext'

interface BoundaryProps {
  children: ReactNode
  scope: string
  resetKey: string
  onRetry?: () => void
  returnTo?: string
  returnLabel?: string
  allowContinue?: boolean
  onContinue?: () => void
  administrator?: boolean
}

interface BoundaryState {
  error: AppError | null
  componentStack: string
}

export class AppErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, componentStack: '' }

  static getDerivedStateFromError(error: unknown): Partial<BoundaryState> {
    const appError = toAppError(error)
    return {
      error: {
        ...appError,
        correlation_id: appError.correlation_id ?? createCorrelationId(),
      },
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const correlationId = this.state.error?.correlation_id ?? createCorrelationId()
    // Deliberately omit props, request data, PDF text, account numbers, and credentials.
    console.error(`[${this.props.scope}] render failure`, {
      correlation_id: correlationId,
      error_name: error.name,
      stack: error.stack,
      component_stack: info.componentStack,
    })
    this.setState({ componentStack: info.componentStack ?? '' })
  }

  componentDidUpdate(previous: BoundaryProps) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null, componentStack: '' })
    }
  }

  private retry = () => {
    this.setState({ error: null, componentStack: '' })
    this.props.onRetry?.()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <section className="app-error-boundary" role="alert" data-error-code={error.code}>
        <AlertTriangle aria-hidden="true" />
        <div>
          <h2>{error.title}</h2>
          <p>{error.message}</p>
          <p className="diagnostic-note">
            Reference: <code>{error.correlation_id}</code>
          </p>
          <div className="inline-actions">
            {error.retryable && (
              <button type="button" className="button secondary" onClick={this.retry}>
                <RotateCcw size={16} /> Retry
              </button>
            )}
            {this.props.allowContinue && this.props.onContinue && (
              <button
                type="button"
                className="button secondary"
                onClick={() => {
                  this.setState({ error: null, componentStack: '' })
                  this.props.onContinue?.()
                }}
              >
                Continue without account
              </button>
            )}
            {this.props.returnTo && (
              <Link className="button secondary" to={this.props.returnTo}>
                {this.props.returnLabel ?? 'Return'}
              </Link>
            )}
          </div>
          {this.props.administrator && (
            <details className="technical-details">
              <summary>Technical details</summary>
              <p>Code: {error.code}</p>
              <p>Scope: {this.props.scope}</p>
              {error.technical_details && <pre>{error.technical_details}</pre>}
              {this.state.componentStack && <pre>{this.state.componentStack}</pre>}
            </details>
          )}
        </div>
      </section>
    )
  }
}

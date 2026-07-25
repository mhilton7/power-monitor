import type { ElementType, ReactNode } from 'react'

export function Surface({
  title,
  subtitle,
  action,
  className = '',
  as: Tag = 'section',
  children,
}: {
  title?: string
  subtitle?: string
  action?: ReactNode
  className?: string
  as?: ElementType
  children: ReactNode
}) {
  return (
    <Tag className={`surface ${className}`.trim()}>
      {(title || subtitle || action) && (
        <header className="surface-header">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </Tag>
  )
}

export function Metric({
  label,
  value,
  detail,
  identity,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  identity: string
}) {
  return (
    <article className="metric" data-metric-identity={identity}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  )
}

export function StatusDot({ state, label }: { state: 'live' | 'waiting' | 'attention'; label: string }) {
  return <span className={`status-dot ${state}`}><i aria-hidden="true" />{label}</span>
}

import type { KeyboardEvent, ReactNode } from 'react'

export function Page({
  className = '',
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return <div className={`workspace-page page-stack ${className}`.trim()}>{children}</div>
}

export function PageHeader({
  title,
  description,
  eyebrow,
  action,
}: {
  title: string
  description: string
  eyebrow?: string
  action?: ReactNode
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <small>{eyebrow}</small>}
        <h1 className="page-title">{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  )
}

export function StatGrid({
  className = '',
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return <div className={`stat-grid ${className}`.trim()}>{children}</div>
}

export function MetadataList({ children }: { children: ReactNode }) {
  return <div className="metadata-list">{children}</div>
}

export function MetadataItem({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: ReactNode
}) {
  return (
    <div className="metadata-item">
      <span className="metadata-icon" aria-hidden="true">{icon}</span>
      <span>
        <small>{label}</small>
        <strong>{value}</strong>
      </span>
    </div>
  )
}

export function SegmentedControl<T extends string>({
  label,
  value,
  items,
  onChange,
}: {
  label: string
  value: T
  items: ReadonlyArray<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <div className="segmented-control" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={value === item.value ? 'active' : undefined}
          aria-pressed={value === item.value}
          onClick={() => { onChange(item.value); }}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

export function TabList<T extends string>({
  idBase,
  label,
  value,
  items,
  onChange,
}: {
  idBase: string
  label: string
  value: T
  items: ReadonlyArray<readonly [T, string]>
  onChange: (value: T) => void
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
    const current = tabs.indexOf(document.activeElement as HTMLButtonElement)
    if (current < 0) return
    event.preventDefault()
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    const item = items[next]
    if (!item) return
    onChange(item[0])
    tabs[next]?.focus()
  }
  return (
    <div className="subnav" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {items.map(([id, itemLabel]) => (
        <button
          id={`${idBase}-tab-${id}`}
          key={id}
          type="button"
          role="tab"
          aria-selected={value === id}
          aria-controls={`${idBase}-panel-${id}`}
          tabIndex={value === id ? 0 : -1}
          className={value === id ? 'active' : undefined}
          onClick={() => { onChange(id); }}
        >
          {itemLabel}
        </button>
      ))}
    </div>
  )
}

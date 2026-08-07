import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { CalendarDays } from 'lucide-react'
import {
  MetadataItem,
  MetadataList,
  Page,
  PageHeader,
  SegmentedControl,
  StatGrid,
  TabList,
} from '../src/components/layout/Layout'
import { Metric } from '../src/components/data-display/Surface'
import { EmptyState } from '../src/components/feedback/States'

describe('layout primitives', () => {
  it('renders a bounded page, structured metadata, and compact empty states', () => {
    render(
      <Page className="billing-page">
        <PageHeader title="Billing" description="Billing description" />
        <StatGrid><Metric label="Current plan" value="Home" identity="billing.current_plan" /></StatGrid>
        <MetadataList><MetadataItem icon={<CalendarDays />} label="Billing day" value="1st" /></MetadataList>
        <EmptyState compact title="Nothing here" message="Add a record." />
      </Page>,
    )
    expect(screen.getByRole('heading', { name: 'Billing' }).closest('.workspace-page')).toHaveClass('page-stack')
    expect(screen.getByText('Billing day').closest('.metadata-item')).toHaveTextContent('1st')
    expect(screen.getByText('Nothing here').closest('.state-block')).toHaveClass('compact')
  })

  it('keeps segmented controls and tabs native, labeled, and keyboard operable', () => {
    function Fixture() {
      const [range, setRange] = useState<'today' | 'week'>('today')
      const [tab, setTab] = useState<'plans' | 'sources'>('plans')
      return (
        <>
          <SegmentedControl label="History range" value={range} items={[{ value: 'today', label: 'Today' }, { value: 'week', label: '7 days' }]} onChange={setRange} />
          <TabList idBase="rates" label="Advanced rate settings" value={tab} items={[['plans', 'Plans'], ['sources', 'Sources']]} onChange={setTab} />
          <section id={`rates-panel-${tab}`} role="tabpanel" aria-labelledby={`rates-tab-${tab}`}>{tab}</section>
        </>
      )
    }
    render(<Fixture />)
    fireEvent.click(screen.getByRole('button', { name: '7 days' }))
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')
    const plans = screen.getByRole('tab', { name: 'Plans' })
    plans.focus()
    fireEvent.keyDown(plans.parentElement as HTMLElement, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Sources')
  })
})

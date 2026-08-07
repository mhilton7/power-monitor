import { Chart as ChartJS, Filler, Legend, LinearScale, LineElement, PointElement, Tooltip, type ChartData, type ChartDataset, type ChartOptions, type ScatterDataPoint } from 'chart.js'
import { Line } from 'react-chartjs-2'
import { AlertTriangle } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { useAppearance } from '../../state/AppearanceContext'
import type { HistoryBucket, HistoryPoint } from '../../types/models'
import { energy, money, percentage, power, rate } from '../../utils/format'
import {
  chartAvailabilityMessageFromEpoch,
  chartAxisValue,
  chartIntervalLabel,
  chartTickLabel,
  chartTickTimestampsFromEpochs,
  colorWithAlpha,
} from './chartUtils'
import { ResponsiveChartFrame } from './ResponsiveChartFrame'
import { HISTORY_PERFORMANCE_MARKS, measureSync } from '../../utils/performance'

ChartJS.register(LinearScale, PointElement, LineElement, Filler, Tooltip, Legend)
const ACCESSIBLE_TABLE_PAGE_SIZE = 100

export function energyChartTooltipLines(point: HistoryPoint, currency: string): string[] {
  return [point.period ? `Period: ${point.period}` : '', point.tier ? `Tier: ${point.tier}` : '', point.rate ? `Rate: ${rate(point.rate, currency)}` : '', `Stored readings: ${percentage(point.coveragePercent)}`].filter(Boolean)
}

type ChartDatum = ScatterDataPoint
interface ParsedHistoryPoint {
  point: HistoryPoint
  startMs: number
  endMs: number
}

export function energyChartSeries(
  points: HistoryPoint[],
  field: 'powerW' | 'energyKwh' | 'cost',
): ChartDatum[] {
  return points.flatMap((point) => {
    const x = Date.parse(point.start)
    if (!Number.isFinite(x)) return []
    const rawValue = point[field]
    const y = rawValue === undefined || rawValue.trim() === '' ? Number.NaN : Number(rawValue)
    // With parsing disabled, Chart.js still requires every datum to be an
    // object so its scale resolver can read x. NaN is the supported y-axis
    // gap marker; a literal null datum makes getMinMax dereference null.x.
    return [{ x, y: point.missing || !Number.isFinite(y) ? Number.NaN : y }]
  })
}

export function energyChartPointRadii(points: HistoryPoint[], now = Date.now()): number[] {
  return points.map((point) => {
    const start = Date.parse(point.start)
    const end = Date.parse(point.end)
    return !point.missing && Number.isFinite(start) && Number.isFinite(end) && start <= now && now < end ? 4 : 0
  })
}

export function energyChartPointIndex(points: HistoryPoint[]): Map<number, HistoryPoint> {
  return new Map(points.map((point) => [Date.parse(point.start), point]))
}

export function chartTickLimitForWidth(width: number, bucket: HistoryBucket = '15m'): number {
  if (!Number.isFinite(width) || width <= 0) return 6
  const estimatedLabelWidth = bucket === '1h' ? 84 : bucket === '1d' ? 68 : 64
  const usableWidth = Math.max(0, width - 32)
  return Math.max(3, Math.min(20, Math.floor(usableWidth / estimatedLabelWidth)))
}

export function EnergyChart({ points, mode, currency, title, timezone = 'UTC', bucket = '15m', rangeStart, rangeEnd, variant = 'history' }: {
  points: HistoryPoint[]
  mode: 'power' | 'energy' | 'cost' | 'energy_cost'
  currency: string
  title: string
  timezone?: string
  bucket?: HistoryBucket
  rangeStart?: string
  rangeEnd?: string
  variant?: 'home' | 'history'
}) {
  const { chartColors } = useAppearance()
  const chartRef = useRef<ChartJS<'line', ChartDatum[]> | null>(null)
  const [tableOpen, setTableOpen] = useState(false)
  const [tableRowLimit, setTableRowLimit] = useState(ACCESSIBLE_TABLE_PAGE_SIZE)
  const parsedPoints = useMemo<ParsedHistoryPoint[]>(() => measureSync(
    HISTORY_PERFORMANCE_MARKS.timestampParse,
    () => points.map((point) => ({
      point,
      startMs: Date.parse(point.start),
      endMs: Date.parse(point.end),
    })),
  ), [points])
  const invalidCount = useMemo(
    () => parsedPoints.filter(({ startMs, endMs }) => !Number.isFinite(startMs) || !Number.isFinite(endMs)).length,
    [parsedPoints],
  )
  const orderedParsed = useMemo(
    () => parsedPoints
      .filter(({ startMs, endMs }) => Number.isFinite(startMs) && Number.isFinite(endMs))
      .sort((left, right) => left.startMs - right.startMs),
    [parsedPoints],
  )
  const ordered = useMemo(() => orderedParsed.map(({ point }) => point), [orderedParsed])
  const pointIndex = useMemo(
    () => new Map(orderedParsed.map(({ point, startMs }) => [startMs, point])),
    [orderedParsed],
  )
  const rangeStartMs = useMemo(() => Date.parse(rangeStart ?? ''), [rangeStart])
  const rangeEndMs = useMemo(() => Date.parse(rangeEnd ?? ''), [rangeEnd])
  const visibleRangeEnd = Number.isFinite(rangeEndMs) ? rangeEndMs - 1 : Number.NaN
  const series = useMemo(() => measureSync(HISTORY_PERFORMANCE_MARKS.chartPreparation, () => {
    const build = (field: 'powerW' | 'energyKwh' | 'cost'): ChartDatum[] => orderedParsed.map(
      ({ point, startMs }) => {
        const rawValue = point[field]
        const y = rawValue === undefined || rawValue.trim() === ''
          ? Number.NaN
          : Number(rawValue)
        return { x: startMs, y: point.missing || !Number.isFinite(y) ? Number.NaN : y }
      },
    )
    return {
      powerW: build('powerW'),
      energyKwh: build('energyKwh'),
      cost: build('cost'),
    }
  }), [orderedParsed])
  const pointRadii = useMemo(
    () => orderedParsed.map(({ point, startMs, endMs }) => (
      !point.missing && startMs <= visibleRangeEnd && visibleRangeEnd < endMs ? 4 : 0
    )),
    [orderedParsed, visibleRangeEnd],
  )
  const pointHoverRadii = useMemo(
    () => pointRadii.map((radius) => radius > 0 ? 6 : 4),
    [pointRadii],
  )
  const pointBorderWidths = useMemo(
    () => pointRadii.map((radius) => radius > 0 ? 2 : 1),
    [pointRadii],
  )
  const datasets = useMemo<ChartDataset<'line', ChartDatum[]>[]>(() => mode === 'power'
    ? [{ label: 'Power (W)', data: series.powerW, borderColor: chartColors.power, backgroundColor: colorWithAlpha(chartColors.power, .12), fill: true, tension: .2, spanGaps: false, pointRadius: pointRadii, pointHoverRadius: pointHoverRadii, pointBorderWidth: pointBorderWidths, pointBackgroundColor: chartColors.power, pointBorderColor: '#071A14', yAxisID: 'power' }]
    : [
        ...(mode !== 'cost' ? [{ label: 'Energy (kWh)', data: series.energyKwh, borderColor: chartColors.energy, backgroundColor: colorWithAlpha(chartColors.energy, .12), fill: true, tension: .2, spanGaps: false, pointRadius: pointRadii, pointHoverRadius: pointHoverRadii, pointBorderWidth: pointBorderWidths, pointBackgroundColor: chartColors.energy, pointBorderColor: '#071A14', yAxisID: 'energy' } satisfies ChartDataset<'line', ChartDatum[]>] : []),
        ...(mode !== 'energy' ? [{ label: `Cost (${currency})`, data: series.cost, borderColor: chartColors.cost, backgroundColor: colorWithAlpha(chartColors.cost, .08), borderDash: [7, 5], fill: false, tension: .2, spanGaps: false, pointRadius: pointRadii, pointHoverRadius: pointHoverRadii, pointBorderWidth: pointBorderWidths, pointBackgroundColor: chartColors.cost, pointBorderColor: '#071A14', yAxisID: 'cost' } satisfies ChartDataset<'line', ChartDatum[]>] : []),
      ], [chartColors.cost, chartColors.energy, chartColors.power, currency, mode, pointBorderWidths, pointHoverRadii, pointRadii, series])
  const data = useMemo<ChartData<'line', ChartDatum[]>>(() => ({ datasets }), [datasets])
  const min = Number.isFinite(rangeStartMs) ? rangeStartMs : orderedParsed[0]?.startMs
  const max = Number.isFinite(rangeEndMs) ? rangeEndMs : orderedParsed.at(-1)?.endMs
  const options = useMemo<ChartOptions<'line'>>(() => ({
    responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
    animation: false, interaction: { intersect: false, mode: 'nearest', axis: 'x' },
    plugins: {
      legend: { display: datasets.length > 1, labels: { color: '#a9b5b1', usePointStyle: true } },
      tooltip: { callbacks: {
        title(items) { const timestamp = Number(items[0]?.parsed.x); const point = pointIndex.get(timestamp); return point ? chartIntervalLabel(point.start, point.end, timezone) : '' },
        afterBody(items) { const timestamp = Number(items[0]?.parsed.x); const point = pointIndex.get(timestamp); return point ? energyChartTooltipLines(point, currency) : [] },
      } },
    },
    scales: {
      x: { type: 'linear', min: Number.isFinite(min) ? min : undefined, max: Number.isFinite(max) ? max : undefined, afterBuildTicks(axis) { const tickLimit = chartTickLimitForWidth(axis.width || axis.chart.width, bucket); axis.ticks = chartTickTimestampsFromEpochs(orderedParsed.map(({ startMs }) => startMs), rangeStartMs, rangeEndMs, tickLimit).map((value) => ({ value })) }, grid: { display: false }, title: { display: true, text: `Time (${timezone})`, color: '#97b1a7' }, ticks: { color: '#82908b', autoSkip: true, maxTicksLimit: chartTickLimitForWidth(1920, bucket), sampleSize: 20, maxRotation: 0, minRotation: 0, callback: (value) => chartTickLabel(Number(value), bucket, timezone) } },
      power: { display: mode === 'power', position: 'left', title: { display: true, text: 'Power (W)', color: '#97b1a7' }, grid: { color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: '#82908b', callback: (value) => chartAxisValue(value, 'power', currency) } },
      energy: { display: mode !== 'power' && mode !== 'cost', position: 'left', title: { display: true, text: 'Energy (kWh)', color: '#97b1a7' }, grid: { color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: '#82908b', callback: (value) => chartAxisValue(value, 'energy', currency) } },
      cost: { display: mode === 'cost' || mode === 'energy_cost', position: mode === 'cost' ? 'left' : 'right', title: { display: true, text: `Estimated cost (${currency})`, color: '#97b1a7' }, grid: { display: mode === 'cost', color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: chartColors.cost, callback: (value) => chartAxisValue(value, 'cost', currency) } },
    },
  }), [bucket, chartColors.cost, currency, datasets.length, max, min, mode, orderedParsed, pointIndex, rangeEndMs, rangeStartMs, timezone])
  const missingCount = useMemo(() => ordered.filter((point) => point.missing).length, [ordered])
  const firstAvailable = orderedParsed.find(({ point }) => !point.missing)
  const availabilityMessage = chartAvailabilityMessageFromEpoch(
    firstAvailable?.point.start,
    firstAvailable?.startMs,
    rangeStartMs,
    timezone,
  )
  const gapMessage = [
    availabilityMessage,
    missingCount > 0 ? `${missingCount} missing interval${missingCount === 1 ? '' : 's'} shown as gaps` : undefined,
  ].filter(Boolean).join(' · ')
  const guideItems = mode === 'energy_cost'
    ? [{ color: chartColors.energy, label: 'Solid line · Left scale: Energy (kWh)' }, { color: chartColors.cost, label: `Dashed line · Right scale: Estimated cost (${currency})`, dashed: true }]
    : mode === 'power' ? [{ color: chartColors.power, label: 'Solid line · Left scale: Power (W)' }]
      : mode === 'cost' ? [{ color: chartColors.cost, label: `Dashed line · Left scale: Estimated cost (${currency})`, dashed: true }]
        : [{ color: chartColors.energy, label: 'Solid line · Left scale: Energy (kWh)' }]
  return <div className="chart-block">
    <p className="sr-only" role="img">{title}. {ordered.length} time intervals. Missing readings are rendered as gaps.</p>
    <p className="chart-scale-guide">Each point represents one {bucket === '1d' ? 'day' : bucket.replace('m', '-minute').replace('h', '-hour')} interval · shown in {timezone}.</p>
    <div className="chart-scale-guide" aria-label={guideItems.map((item) => item.label).join('. ')}>{guideItems.map((item) => <span className="chart-guide-item" key={item.label}><i className={item.dashed ? 'dashed' : ''} style={{ backgroundColor: item.color, color: item.color }} />{item.label}</span>)}</div>
    {gapMessage && <p className="chart-gap-guide"><AlertTriangle aria-hidden="true" /> <span>{gapMessage}.</span></p>}
    {invalidCount > 0 && <p className="chart-gap-guide">{invalidCount} interval{invalidCount === 1 ? '' : 's'} could not be plotted because its timestamp was invalid.</p>}
    <ResponsiveChartFrame
      chartRef={chartRef}
      variant={variant}
      resizeKey={`${points.length}:${rangeStart ?? ''}:${rangeEnd ?? ''}:${mode}`}
    >
      <Line ref={chartRef} data={data} options={options} />
    </ResponsiveChartFrame>
    <details className="chart-table" open={tableOpen} onToggle={(event) => {
      setTableOpen(event.currentTarget.open)
      if (!event.currentTarget.open) setTableRowLimit(ACCESSIBLE_TABLE_PAGE_SIZE)
    }}><summary>View accessible data table</summary>{tableOpen && <div className="table-scroll"><table>
      <thead><tr><th>Exact interval ({timezone})</th><th>Power</th><th>Energy</th><th>Cost</th><th>Rate period</th><th>Stored readings</th></tr></thead>
      <tbody>{ordered.slice(0, tableRowLimit).map((point) => <tr key={`${point.start}-${point.end}`}><th scope="row">{chartIntervalLabel(point.start, point.end, timezone)}</th><td>{point.missing ? 'Missing' : power(point.powerW)}</td><td>{point.missing ? 'Missing' : energy(point.energyKwh)}</td><td>{point.missing ? 'Missing' : money(point.cost, currency)}</td><td>{[point.tier, point.period].filter(Boolean).join(' · ') || 'Not available'}</td><td>{percentage(point.coveragePercent)}</td></tr>)}</tbody>
    </table>{tableRowLimit < ordered.length && <button className="button secondary" type="button" onClick={() => { setTableRowLimit((current) => Math.min(current + ACCESSIBLE_TABLE_PAGE_SIZE, ordered.length)); }}>Show next {Math.min(ACCESSIBLE_TABLE_PAGE_SIZE, ordered.length - tableRowLimit)} intervals</button>}</div>}</details>
  </div>
}

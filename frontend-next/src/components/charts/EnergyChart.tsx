import { Chart as ChartJS, Filler, Legend, LinearScale, LineElement, PointElement, Tooltip, type ChartData, type ChartDataset, type ChartOptions, type ScatterDataPoint } from 'chart.js'
import { Line } from 'react-chartjs-2'
import { useAppearance } from '../../state/AppearanceContext'
import type { HistoryBucket, HistoryPoint } from '../../types/models'
import { energy, money, percentage, power, rate } from '../../utils/format'
import { chartAvailabilityMessage, chartAxisValue, chartIntervalLabel, chartTickLabel, chartTickTimestamps, colorWithAlpha } from './chartUtils'

ChartJS.register(LinearScale, PointElement, LineElement, Filler, Tooltip, Legend)

export function energyChartTooltipLines(point: HistoryPoint, currency: string): string[] {
  return [point.period ? `Period: ${point.period}` : '', point.tier ? `Tier: ${point.tier}` : '', point.rate ? `Rate: ${rate(point.rate, currency)}` : '', `Coverage: ${percentage(point.coveragePercent)}`].filter(Boolean)
}

type ChartDatum = ScatterDataPoint | null

export function EnergyChart({ points, mode, currency, title, timezone = 'UTC', bucket = '15m', rangeStart, rangeEnd }: {
  points: HistoryPoint[]
  mode: 'power' | 'energy' | 'cost' | 'energy_cost'
  currency: string
  title: string
  timezone?: string
  bucket?: HistoryBucket
  rangeStart?: string
  rangeEnd?: string
}) {
  const { chartColors } = useAppearance()
  const invalidCount = points.filter((point) => !Number.isFinite(Date.parse(point.start)) || !Number.isFinite(Date.parse(point.end))).length
  const ordered = points.filter((point) => Number.isFinite(Date.parse(point.start)) && Number.isFinite(Date.parse(point.end))).sort((left, right) => Date.parse(left.start) - Date.parse(right.start))
  const series = (field: 'powerW' | 'energyKwh' | 'cost'): ChartDatum[] => ordered.map((point) => {
    const x = Date.parse(point.start)
    const y = Number(point[field])
    return point.missing || !Number.isFinite(x) || !Number.isFinite(y) ? null : { x, y }
  })
  const datasets: ChartDataset<'line', ChartDatum[]>[] = mode === 'power'
    ? [{ label: 'Power (W)', data: series('powerW'), borderColor: chartColors.power, backgroundColor: colorWithAlpha(chartColors.power, .12), fill: true, tension: .2, spanGaps: false, pointRadius: 0, yAxisID: 'power' }]
    : [
        ...(mode !== 'cost' ? [{ label: 'Energy (kWh)', data: series('energyKwh'), borderColor: chartColors.energy, backgroundColor: colorWithAlpha(chartColors.energy, .12), fill: true, tension: .2, spanGaps: false, pointRadius: 0, yAxisID: 'energy' } satisfies ChartDataset<'line', ChartDatum[]>] : []),
        ...(mode !== 'energy' ? [{ label: `Cost (${currency})`, data: series('cost'), borderColor: chartColors.cost, backgroundColor: colorWithAlpha(chartColors.cost, .08), borderDash: [7, 5], fill: false, tension: .2, spanGaps: false, pointRadius: 0, yAxisID: 'cost' } satisfies ChartDataset<'line', ChartDatum[]>] : []),
      ]
  const data: ChartData<'line', ChartDatum[]> = { datasets }
  const min = rangeStart ? Date.parse(rangeStart) : Date.parse(ordered[0]?.start ?? '')
  const max = rangeEnd ? Date.parse(rangeEnd) : Date.parse(ordered.at(-1)?.end ?? '')
  const tickTimestamps = chartTickTimestamps(ordered, rangeStart, rangeEnd)
  const options: ChartOptions<'line'> = {
    responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
    animation: { duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250 }, interaction: { intersect: false, mode: 'nearest', axis: 'x' },
    plugins: {
      legend: { display: datasets.length > 1, labels: { color: '#a9b5b1', usePointStyle: true } },
      tooltip: { callbacks: {
        title(items) { const timestamp = Number(items[0]?.parsed.x); const point = ordered.find((candidate) => Date.parse(candidate.start) === timestamp); return point ? chartIntervalLabel(point.start, point.end, timezone) : '' },
        afterBody(items) { const timestamp = Number(items[0]?.parsed.x); const point = ordered.find((candidate) => Date.parse(candidate.start) === timestamp); return point ? energyChartTooltipLines(point, currency) : [] },
      } },
    },
    scales: {
      x: { type: 'linear', min: Number.isFinite(min) ? min : undefined, max: Number.isFinite(max) ? max : undefined, afterBuildTicks(axis) { axis.ticks = tickTimestamps.map((value) => ({ value })) }, grid: { display: false }, title: { display: true, text: `Time (${timezone})`, color: '#97b1a7' }, ticks: { color: '#82908b', maxTicksLimit: 12, autoSkip: true, autoSkipPadding: 32, maxRotation: 0, minRotation: 0, callback: (value) => tickTimestamps.includes(Number(value)) ? chartTickLabel(Number(value), bucket, timezone) : '' } },
      power: { display: mode === 'power', position: 'left', title: { display: true, text: 'Power (W)', color: '#97b1a7' }, grid: { color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: '#82908b', callback: (value) => chartAxisValue(value, 'power', currency) } },
      energy: { display: mode !== 'power' && mode !== 'cost', position: 'left', title: { display: true, text: 'Energy (kWh)', color: '#97b1a7' }, grid: { color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: '#82908b', callback: (value) => chartAxisValue(value, 'energy', currency) } },
      cost: { display: mode === 'cost' || mode === 'energy_cost', position: mode === 'cost' ? 'left' : 'right', title: { display: true, text: `Estimated cost (${currency})`, color: '#97b1a7' }, grid: { display: mode === 'cost', color: colorWithAlpha('#FFFFFF', .06) }, ticks: { color: chartColors.cost, callback: (value) => chartAxisValue(value, 'cost', currency) } },
    },
  }
  const missingCount = ordered.filter((point) => point.missing).length
  const availabilityMessage = chartAvailabilityMessage(ordered, rangeStart, timezone)
  const guideItems = mode === 'energy_cost'
    ? [{ color: chartColors.energy, label: 'Solid line · Left scale: Energy (kWh)' }, { color: chartColors.cost, label: `Dashed line · Right scale: Estimated cost (${currency})`, dashed: true }]
    : mode === 'power' ? [{ color: chartColors.power, label: 'Solid line · Left scale: Power (W)' }]
      : mode === 'cost' ? [{ color: chartColors.cost, label: `Dashed line · Left scale: Estimated cost (${currency})`, dashed: true }]
        : [{ color: chartColors.energy, label: 'Solid line · Left scale: Energy (kWh)' }]
  return <div className="chart-block">
    <p className="sr-only" role="img">{title}. {ordered.length} time intervals. Missing readings are rendered as gaps.</p>
    <p className="chart-scale-guide">Each point represents one {bucket === '1d' ? 'day' : bucket.replace('m', '-minute').replace('h', '-hour')} interval · shown in {timezone}.</p>
    <div className="chart-scale-guide" aria-label={guideItems.map((item) => item.label).join('. ')}>{guideItems.map((item) => <span className="chart-guide-item" key={item.label}><i className={item.dashed ? 'dashed' : ''} style={{ backgroundColor: item.color, color: item.color }} />{item.label}</span>)}</div>
    {availabilityMessage && <p className="chart-gap-guide">{availabilityMessage}</p>}
    {missingCount > 0 && <p className="chart-gap-guide">{missingCount} interval{missingCount === 1 ? '' : 's'} missing; the line remains intentionally broken.</p>}
    {invalidCount > 0 && <p className="chart-gap-guide">{invalidCount} interval{invalidCount === 1 ? '' : 's'} could not be plotted because its timestamp was invalid.</p>}
    <div className="chart-canvas" aria-hidden="true"><Line data={data} options={options} /></div>
    <details className="chart-table"><summary>View accessible data table</summary><div className="table-scroll"><table>
      <thead><tr><th>Exact interval ({timezone})</th><th>Power</th><th>Energy</th><th>Cost</th><th>Rate period</th><th>Coverage</th></tr></thead>
      <tbody>{ordered.map((point) => <tr key={`${point.start}-${point.end}`}><th scope="row">{chartIntervalLabel(point.start, point.end, timezone)}</th><td>{point.missing ? 'Missing' : power(point.powerW)}</td><td>{point.missing ? 'Missing' : energy(point.energyKwh)}</td><td>{point.missing ? 'Missing' : money(point.cost, currency)}</td><td>{[point.tier, point.period].filter(Boolean).join(' · ') || 'Not available'}</td><td>{percentage(point.coveragePercent)}</td></tr>)}</tbody>
    </table></div></details>
  </div>
}

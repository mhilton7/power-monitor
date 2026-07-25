import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  TimeScale,
  Tooltip,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import type { HistoryPoint } from '../../types/models'
import { energy, money, power } from '../../utils/format'

ChartJS.register(CategoryScale, LinearScale, TimeScale, PointElement, LineElement, Filler, Tooltip, Legend)

export function EnergyChart({
  points,
  mode,
  currency,
  title,
}: {
  points: HistoryPoint[]
  mode: 'power' | 'energy' | 'cost' | 'energy_cost'
  currency: string
  title: string
}) {
  const energyValues = points.map((point) => point.missing ? null : Number(point.energyKwh))
  const powerValues = points.map((point) => point.missing ? null : Number(point.powerW))
  const costValues = points.map((point) => point.missing ? null : Number(point.cost))
  const datasets = mode === 'power'
    ? [{
        label: 'Power (W)',
        data: powerValues,
        borderColor: '#78dfbf',
        backgroundColor: 'rgba(120, 223, 191, .12)',
        fill: true,
        tension: 0.34,
        spanGaps: false,
        pointRadius: 0,
      }]
    : [
        ...(mode !== 'cost' ? [{
          label: 'Energy (kWh)',
          data: energyValues,
          borderColor: '#78dfbf',
          backgroundColor: 'rgba(120, 223, 191, .12)',
          fill: true,
          tension: 0.34,
          spanGaps: false,
          pointRadius: 0,
          yAxisID: 'energy',
        }] : []),
        ...(mode !== 'energy' ? [{
          label: `Cost (${currency})`,
          data: costValues,
          borderColor: '#c9a7ff',
          backgroundColor: 'rgba(201, 167, 255, .08)',
          fill: false,
          tension: 0.34,
          spanGaps: false,
          pointRadius: 0,
          yAxisID: 'cost',
        }] : []),
      ]
  return (
    <div className="chart-block">
      <p className="sr-only" role="img">
        {title}. {points.length} time intervals. Missing readings are rendered as gaps.
      </p>
      <div className="chart-canvas" aria-hidden="true">
        <Line
          data={{
            labels: points.map((point) => new Intl.DateTimeFormat(undefined, {
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
            }).format(new Date(point.start))),
            datasets,
          }}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 250 },
            interaction: { intersect: false, mode: 'index' },
            plugins: {
              legend: { display: datasets.length > 1, labels: { color: '#a9b5b1', usePointStyle: true } },
              tooltip: {
                callbacks: {
                  afterBody(items) {
                    const point = points[items[0]?.dataIndex ?? 0]
                    if (!point) return []
                    return [
                      point.period ? `Period: ${point.period}` : '',
                      point.tier ? `Tier: ${point.tier}` : '',
                      point.rate ? `Rate: ${point.rate}/kWh` : '',
                      `Coverage: ${point.coveragePercent}%`,
                    ].filter(Boolean)
                  },
                },
              },
            },
            scales: {
              x: { grid: { display: false }, ticks: { color: '#82908b', maxTicksLimit: 7 } },
              energy: { display: mode !== 'power' && mode !== 'cost', position: 'left', grid: { color: 'rgba(255,255,255,.06)' }, ticks: { color: '#82908b' } },
              cost: { display: mode === 'cost' || mode === 'energy_cost', position: 'right', grid: { display: false }, ticks: { color: '#a991c8' } },
              y: { display: mode === 'power', grid: { color: 'rgba(255,255,255,.06)' }, ticks: { color: '#82908b' } },
            },
          }}
        />
      </div>
      <details className="chart-table">
        <summary>View accessible data table</summary>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Interval</th><th>Power</th><th>Energy</th><th>Cost</th><th>Rate period</th><th>Coverage</th></tr></thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.start}>
                  <th scope="row">{new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(point.start))}</th>
                  <td>{point.missing ? 'Missing' : power(point.powerW)}</td>
                  <td>{point.missing ? 'Missing' : energy(point.energyKwh)}</td>
                  <td>{point.missing ? 'Missing' : money(point.cost, currency)}</td>
                  <td>{[point.tier, point.period].filter(Boolean).join(' · ') || 'Not available'}</td>
                  <td>{point.coveragePercent}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

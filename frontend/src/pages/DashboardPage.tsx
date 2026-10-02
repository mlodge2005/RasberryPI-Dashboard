import type { BatteryEstimate, BatteryInfo, NetworkStats, SystemSnapshot } from "../api/types"
import { formatBytes, formatDuration, formatPercent, formatUptime, formatWhen, unavailable } from "../utils/format"

export function DashboardPage({
  snapshot,
  online,
}: {
  snapshot: SystemSnapshot | null
  online: boolean
}) {
  if (!snapshot) {
    return (
      <section className="page">
        <header className="page-header">
          <h1>Dashboard</h1>
          <p>{online ? "Collecting system stats" : "Waiting for the system API"}</p>
        </header>
      </section>
    )
  }

  const { cpu, memory, storage, network, system, battery } = snapshot
  const load = cpu.load_average ? cpu.load_average.map((value) => value.toFixed(2)).join("  ") : "Unavailable"

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Dashboard</h1>
          <p>Updated {formatWhen(snapshot.collected_at)}</p>
        </div>
      </header>
      <div className="cards">
        <article className="card card-wide">
          <div className="card-label">CPU</div>
          <div className="metric-row">
            <div className="metric">{formatPercent(cpu.usage_percent)}</div>
            <div className="card-sub">
              <span>{cpu.frequency_mhz ? `${Math.round(cpu.frequency_mhz)} MHz` : "Frequency unavailable"}</span>
              <span>{cpu.temperature_c === null ? "Temperature unavailable" : `${cpu.temperature_c.toFixed(1)} °C`}</span>
              <span>Load {load}</span>
            </div>
          </div>
          <Progress value={cpu.usage_percent} />
          <div className="cores">
            {cpu.per_core_percent.map((value, index) => (
              <div className="core-row" key={index}>
                <span>Core {index}</span>
                <Progress value={value} />
                <span>{formatPercent(value)}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="card">
          <div className="card-label">Memory</div>
          <div className="metric">{formatPercent(memory.percent)}</div>
          <Progress value={memory.percent} />
          <dl className="kv">
            <Row label="Used" value={formatBytes(memory.used_bytes)} />
            <Row label="Total" value={formatBytes(memory.total_bytes)} />
          </dl>
        </article>

        <article className="card">
          <div className="card-label">Storage</div>
          <div className="metric">{formatPercent(storage.percent)}</div>
          <Progress value={storage.percent} />
          <dl className="kv">
            <Row label="Used" value={formatBytes(storage.used_bytes)} />
            <Row label="Free" value={formatBytes(storage.free_bytes)} />
            <Row label="Total" value={formatBytes(storage.total_bytes)} />
            <Row label="Mount" value={storage.mount} />
          </dl>
        </article>

        <article className="card">
          <div className="card-label">Network</div>
          <div className="metric metric-small">{network.hostname}</div>
          <dl className="kv">
            <Row label="Local IP" value={unavailable(network.local_ip)} />
            <Row label="Tailscale IP" value={tailscaleValue(network)} />
            <Row label="Tailscale name" value={unavailable(network.tailscale_hostname)} />
            <Row label="Sent" value={formatBytes(network.bytes_sent)} />
            <Row label="Received" value={formatBytes(network.bytes_received)} />
            <Row label="Uptime" value={formatUptime(system.uptime_seconds)} />
          </dl>
        </article>

        <article className="card">
          <div className="card-label">System</div>
          <div className="metric metric-small">{system.pi_model ?? "Raspberry Pi model not detected"}</div>
          <dl className="kv">
            <Row label="OS" value={system.os_name} />
            <Row label="Kernel" value={system.kernel} />
            <Row label="Architecture" value={system.architecture} />
            <Row label="Uptime" value={formatUptime(system.uptime_seconds)} />
            <Row label="Boot time" value={formatWhen(system.boot_time)} />
          </dl>
        </article>

        <BatteryCard battery={battery} />
      </div>
    </section>
  )
}

function tailscaleValue(network: NetworkStats): string {
  if (network.tailscale_status === "unavailable") {
    return "Unavailable"
  }
  if (network.tailscale_ip) {
    return network.tailscale_ip
  }
  if (network.tailscale_status === "offline") {
    return "Offline"
  }
  return "Unavailable"
}

function BatteryCard({ battery }: { battery: BatteryInfo }) {
  const estimate = battery.estimate
  if (estimate?.tracking) {
    return (
      <article className="card card-wide battery-card" data-level={estimate.level}>
        <div className="card-label">Battery / Power</div>
        <EstimateDetails estimate={estimate} />
      </article>
    )
  }
  return (
    <article className="card">
      <div className="card-label">Battery / Power</div>
      <SensorDetails battery={battery} />
    </article>
  )
}

function SensorDetails({ battery }: { battery: BatteryInfo }) {
  if (!battery.available) {
    return <p className="unavailable-copy">{battery.estimate?.detail ?? battery.message ?? "Battery information unavailable"}</p>
  }
  const status = battery.status ?? (battery.charging ? "charging" : "unknown")
  return (
    <>
      <div className="metric">{battery.percent === null ? "Unknown" : formatPercent(battery.percent)}</div>
      {battery.percent !== null ? <Progress value={battery.percent} /> : null}
      <dl className="kv">
        <Row label="Status" value={status} />
        <Row label="Voltage" value={battery.voltage === null ? "Unavailable" : `${battery.voltage.toFixed(2)} V`} />
      </dl>
    </>
  )
}

function EstimateDetails({ estimate }: { estimate: BatteryEstimate }) {
  const stats = estimate.stats
  const charge = estimate.estimated_percent === null ? (estimate.estimate_uncertain ? "Uncertain" : "Not calibrated") : formatPercent(estimate.estimated_percent)
  const recommendation =
    estimate.level === "normal" && estimate.charge_recommended_in_seconds != null
      ? `Charge recommended in ${formatDuration(estimate.charge_recommended_in_seconds, true)}`
      : estimate.advice
  const samples = stats.calibration_samples_minutes.map((minutes) => formatDuration(minutes * 60))
  return (
    <div className="battery-estimate">
      <div className="battery-kicker">{estimate.model}</div>
      <div className="battery-grid">
        <BatteryBlock label="Power" value={estimate.power_label} />
        <BatteryBlock label="Estimated charge" value={charge} emphasis />
        {estimate.time_on_battery_seconds != null ? (
          <BatteryBlock label="Time on battery" value={formatDuration(estimate.time_on_battery_seconds)} />
        ) : null}
        {estimate.estimated_remaining_seconds != null ? (
          <BatteryBlock label="Est. remaining" value={formatDuration(estimate.estimated_remaining_seconds, true)} />
        ) : null}
      </div>
      {estimate.estimated_percent != null ? <Progress value={estimate.estimated_percent} /> : null}
      {recommendation ? <p className="battery-advice">{recommendation}</p> : null}
      {estimate.detail ? <p className="battery-note">{estimate.detail}</p> : null}
      <div className="battery-stats">
        <BatteryBlock
          label="Current session"
          value={stats.current_session_seconds == null ? "On external power" : formatDuration(stats.current_session_seconds)}
          hint="Time on battery"
        />
        <BatteryBlock label="Today" value={formatDuration(stats.today_seconds)} hint="Battery runtime today" />
        <BatteryBlock label="7 days" value={formatDuration(stats.week_seconds)} hint="Total battery runtime" />
        <BatteryBlock
          label="7 days"
          value={stats.week_average_session_seconds == null ? "No completed sessions" : formatDuration(stats.week_average_session_seconds)}
          hint="Average session length"
        />
        <BatteryBlock
          label="Calibration"
          value={stats.estimated_full_runtime_minutes == null ? "Not set" : formatDuration(stats.estimated_full_runtime_minutes * 60)}
          hint="Estimated full-charge runtime"
        />
        <BatteryBlock label="Calibration" value={String(stats.calibration_sample_count)} hint="Calibrated discharge sessions" />
      </div>
      {samples.length > 0 ? <p className="battery-note">Calibration history: {samples.join(" · ")}</p> : null}
      <p className="battery-note">{estimate.disclaimer}</p>
    </div>
  )
}

function BatteryBlock({
  label,
  value,
  hint,
  emphasis = false,
}: {
  label: string
  value: string
  hint?: string
  emphasis?: boolean
}) {
  return (
    <div className="battery-block">
      <div className="battery-block-label">{label}</div>
      {hint ? <div className="battery-block-hint">{hint}</div> : null}
      <div className={emphasis ? "battery-block-value emphasis" : "battery-block-value"}>{value}</div>
    </div>
  )
}

function Progress({ value }: { value: number }) {
  const width = Math.max(0, Math.min(100, value))
  return (
    <div className="bar" aria-hidden="true">
      <span style={{ width: `${width}%` }} />
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="kv-row">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

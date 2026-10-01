import type { BatteryInfo, NetworkStats, SystemSnapshot } from "../api/types"
import { formatBytes, formatPercent, formatUptime, formatWhen, unavailable } from "../utils/format"

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

        <article className="card">
          <div className="card-label">Battery / Power</div>
          <BatteryDetails battery={battery} />
        </article>
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

function BatteryDetails({ battery }: { battery: BatteryInfo }) {
  if (!battery.available) {
    return <p className="unavailable-copy">{battery.message ?? "Battery information unavailable"}</p>
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

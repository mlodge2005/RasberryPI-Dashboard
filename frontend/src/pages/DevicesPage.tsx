import { useCallback, useEffect, useState } from "react"
import { ApiError, getDevices, refreshDevices, saveDeviceAlias } from "../api/client"
import type { DeviceInventory, DeviceRecord } from "../api/types"

const STATUS_LABEL: Record<DeviceRecord["status"], string> = {
  online: "Connected",
  offline: "Offline",
  unknown: "Unknown",
}

function formatWhen(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) {
    return "—"
  }
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remain = seconds % 60
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  if (minutes > 0) {
    return `${minutes}m ${remain}s`
  }
  return `${remain}s`
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return "—"
  }
  if (value < 1024) {
    return `${value} B`
  }
  const units = ["KB", "MB", "GB"]
  let amount = value / 1024
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[unit]}`
}

function sourceLabel(kind: "ok" | "unavailable"): string {
  return kind === "ok" ? "available" : "unavailable"
}

export function DevicesPage() {
  const [inventory, setInventory] = useState<DeviceInventory | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busyMac, setBusyMac] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const apply = useCallback((next: DeviceInventory) => {
    setInventory(next)
    setError(null)
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getDevices()
        if (!cancelled) {
          apply(next)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load devices.")
        }
      }
    }
    void load()
    const timer = window.setInterval(() => {
      if (!refreshing && busyMac === null) {
        void load()
      }
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [apply, busyMac, refreshing])

  const refresh = async () => {
    setRefreshing(true)
    setError(null)
    try {
      apply(await refreshDevices())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not refresh devices.")
    } finally {
      setRefreshing(false)
    }
  }

  const save = async (device: DeviceRecord) => {
    setBusyMac(device.mac)
    setError(null)
    try {
      const next = await saveDeviceAlias(device.mac, drafts[device.mac] ?? device.alias)
      apply(next)
      setDrafts((current) => {
        const copy = { ...current }
        delete copy[device.mac]
        return copy
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save that alias.")
    } finally {
      setBusyMac(null)
    }
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Devices</h1>
          <p>
            Connected means the device is associated to the hotspot. A DHCP lease or neighbor entry does not mean it is
            online. Each MAC address stays its own device.
          </p>
        </div>
        <button type="button" className="button" onClick={() => void refresh()} disabled={refreshing}>
          {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </header>
      <div className="card">
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
        <div className="device-stats" aria-live="polite">
          <div>
            <span>Connected</span>
            <strong>{inventory?.connected ?? "—"}</strong>
          </div>
          <div>
            <span>Known</span>
            <strong>{inventory?.known ?? "—"}</strong>
          </div>
          <div>
            <span>Unidentified</span>
            <strong>{inventory?.unidentified ?? "—"}</strong>
          </div>
        </div>
        {inventory ? (
          <p className="device-sources">
            Association {sourceLabel(inventory.hostapd)}. DHCP {sourceLabel(inventory.dhcp)}. Neighbors{" "}
            {sourceLabel(inventory.neighbors)}.
          </p>
        ) : null}
      </div>
      <div className="card">
        {inventory && inventory.devices.length === 0 ? <p className="lede">No devices have been seen yet.</p> : null}
        <div className="device-list">
          {inventory?.devices.map((device) => {
            const title = device.alias || device.hostname || device.mac
            return (
              <article key={device.mac} className="device-row">
                <div className="device-identity">
                  <strong>{title}</strong>
                  <div className="wifi-meta">
                    <span>{STATUS_LABEL[device.status]}</span>
                    <span>{device.mac}</span>
                    <span>{device.ip_address || "No IP"}</span>
                    <span>{device.hostname || "No hostname"}</span>
                  </div>
                  <div className="wifi-meta">
                    <span>First seen {formatWhen(device.first_seen)}</span>
                    <span>Last seen {formatWhen(device.last_seen)}</span>
                    {device.status === "online" ? (
                      <>
                        <span>Connected for {formatDuration(device.connected_seconds)}</span>
                        <span>{device.signal_dbm === null ? "No signal" : `${device.signal_dbm} dBm`}</span>
                        <span>Received {formatBytes(device.rx_bytes)}</span>
                        <span>Sent {formatBytes(device.tx_bytes)}</span>
                        {device.authorized === null ? null : (
                          <span>{device.authorized ? "Authorized" : "Not authorized"}</span>
                        )}
                      </>
                    ) : null}
                  </div>
                </div>
                <form
                  className="device-alias"
                  onSubmit={(event) => {
                    event.preventDefault()
                    void save(device)
                  }}
                >
                  <label>
                    Alias
                    <input
                      value={drafts[device.mac] ?? device.alias}
                      maxLength={40}
                      onChange={(event) =>
                        setDrafts((current) => ({ ...current, [device.mac]: event.target.value }))
                      }
                    />
                  </label>
                  <button type="submit" className="button button-small" disabled={busyMac === device.mac}>
                    {busyMac === device.mac ? "Saving" : "Save"}
                  </button>
                </form>
              </article>
            )
          })}
        </div>
      </div>
    </section>
  )
}

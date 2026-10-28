import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react"
import { ApiError, disconnectDevice, getDevices, refreshDevices, saveDeviceAlias } from "../api/client"
import type { DeviceInventory, DeviceKind, DeviceRecord } from "../api/types"
import { formatWhen } from "../utils/format"

const STATUS_LABEL: Record<DeviceRecord["status"], string> = {
  online: "Connected",
  offline: "Offline",
  unknown: "Unknown",
}

function formatDuration(seconds: number | null): string | null {
  if (seconds === null) {
    return null
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

function connectedCount(inventory: DeviceInventory | null): string {
  if (!inventory || inventory.hostapd !== "ok" || inventory.connected === null) {
    return "Unavailable"
  }
  return String(inventory.connected)
}

function titleFor(device: DeviceRecord): string {
  return device.alias || device.hostname || device.mac
}

function secondaryFor(device: DeviceRecord): string {
  if (!device.alias || !device.hostname || device.alias === device.hostname) {
    return ""
  }
  return device.hostname
}

function DeviceGlyph({ kind }: { kind: DeviceKind }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  }
  if (kind === "phone") {
    return (
      <svg {...common}>
        <rect x="8" y="3" width="8" height="18" rx="2" />
        <path d="M11 18h2" />
      </svg>
    )
  }
  if (kind === "laptop") {
    return (
      <svg {...common}>
        <rect x="5" y="5" width="14" height="9" rx="1.5" />
        <path d="M3 18h18" />
      </svg>
    )
  }
  if (kind === "tablet") {
    return (
      <svg {...common}>
        <rect x="5" y="3" width="14" height="18" rx="2" />
        <path d="M11 18h2" />
      </svg>
    )
  }
  if (kind === "desktop") {
    return (
      <svg {...common}>
        <rect x="3" y="4" width="18" height="12" rx="1.5" />
        <path d="M8 20h8M12 16v4" />
      </svg>
    )
  }
  if (kind === "tv") {
    return (
      <svg {...common}>
        <rect x="3" y="7" width="18" height="12" rx="1.5" />
        <path d="M8 3l4 4 4-4" />
      </svg>
    )
  }
  if (kind === "watch") {
    return (
      <svg {...common}>
        <rect x="8" y="6" width="8" height="12" rx="2" />
        <path d="M9 6V4h6v2M9 18v2h6v-2" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  )
}

function EllipsisIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <circle cx="6" cy="12" r="1.4" fill="currentColor" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" />
      <circle cx="18" cy="12" r="1.4" fill="currentColor" />
    </svg>
  )
}

function DeviceCard({
  device,
  menuOpen,
  expanded,
  onToggleMenu,
  onRename,
  onToggleDetails,
  onDisconnect,
}: {
  device: DeviceRecord
  menuOpen: boolean
  expanded: boolean
  onToggleMenu: () => void
  onRename: () => void
  onToggleDetails: () => void
  onDisconnect: () => void
}) {
  const title = titleFor(device)
  const secondary = secondaryFor(device)
  const duration = formatDuration(device.connected_seconds)
  const menuId = useId()
  return (
    <article className={`device-card status-${device.status}`} data-kind={device.kind} data-mac={device.mac}>
      <div className="device-card-top">
        <div className="device-heading">
          <span className={`device-icon kind-${device.kind}`}>
            <DeviceGlyph kind={device.kind} />
          </span>
          <div className="device-names">
            <h3>{title}</h3>
            {secondary ? <p className="device-secondary">{secondary}</p> : null}
          </div>
        </div>
        <div className="device-menu-wrap">
          <button
            type="button"
            className="icon-button"
            aria-label={`Actions for ${title}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-controls={menuId}
            onClick={onToggleMenu}
          >
            <EllipsisIcon />
          </button>
          {menuOpen ? (
            <div className="device-menu" id={menuId} role="menu">
              <button type="button" role="menuitem" onClick={onRename}>
                Rename
              </button>
              <button type="button" role="menuitem" onClick={onToggleDetails}>
                {expanded ? "Hide details" : "View details"}
              </button>
              {device.status === "online" ? (
                <button type="button" role="menuitem" onClick={onDisconnect}>
                  Disconnect
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      <p className={`status-pill status-${device.status}`}>
        <span className="status-dot" aria-hidden="true" />
        {STATUS_LABEL[device.status]}
      </p>
      <dl className="device-facts">
        <dt>IP address</dt>
        <dd>{device.ip_address || "No address"}</dd>
        <dt>MAC address</dt>
        <dd>{device.mac}</dd>
        <dt>Last seen</dt>
        <dd>{formatWhen(device.last_seen)}</dd>
        {device.signal_dbm !== null ? (
          <>
            <dt>Signal</dt>
            <dd>{device.signal_dbm} dBm</dd>
          </>
        ) : null}
        {duration ? (
          <>
            <dt>Session</dt>
            <dd>{duration}</dd>
          </>
        ) : null}
      </dl>
      {expanded ? (
        <dl className="device-facts device-details">
          <dt>First seen</dt>
          <dd>{formatWhen(device.first_seen)}</dd>
          <dt>Received</dt>
          <dd>{formatBytes(device.rx_bytes)}</dd>
          <dt>Sent</dt>
          <dd>{formatBytes(device.tx_bytes)}</dd>
          <dt>Packets in</dt>
          <dd>{device.rx_packets === null ? "—" : String(device.rx_packets)}</dd>
          <dt>Packets out</dt>
          <dd>{device.tx_packets === null ? "—" : String(device.tx_packets)}</dd>
          {device.authorized === null ? null : (
            <>
              <dt>Association</dt>
              <dd>{device.authorized ? "Authorized" : "Not authorized"}</dd>
            </>
          )}
        </dl>
      ) : null}
    </article>
  )
}

function DeviceSection({
  id,
  title,
  count,
  note,
  children,
}: {
  id: string
  title: string
  count: number
  note?: string
  children: ReactNode
}) {
  return (
    <section className="device-section" aria-labelledby={id}>
      <div className="device-section-heading">
        <h2 id={id}>{title}</h2>
        <span>{count}</span>
      </div>
      {note ? <p className="device-section-note">{note}</p> : null}
      {children}
    </section>
  )
}

export function DevicesPage() {
  const [inventory, setInventory] = useState<DeviceInventory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [menuMac, setMenuMac] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [aliasTarget, setAliasTarget] = useState<DeviceRecord | null>(null)
  const [aliasDraft, setAliasDraft] = useState("")
  const [confirmTarget, setConfirmTarget] = useState<DeviceRecord | null>(null)
  const aliasInput = useRef<HTMLInputElement>(null)
  const pageRef = useRef<HTMLElement>(null)

  const apply = useCallback((next: DeviceInventory) => {
    setInventory(next)
  }, [])

  const paused = busy || refreshing || aliasTarget !== null || confirmTarget !== null || menuMac !== null

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
      if (!paused) {
        void load()
      }
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [apply, paused])

  useEffect(() => {
    if (!menuMac) {
      return
    }
    const close = (event: MouseEvent) => {
      const target = event.target
      if (!(target instanceof Node)) {
        return
      }
      const card = pageRef.current?.querySelector(`[data-mac="${menuMac}"]`)
      if (card && !card.contains(target)) {
        setMenuMac(null)
      }
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuMac(null)
      }
    }
    document.addEventListener("mousedown", close)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", close)
      document.removeEventListener("keydown", onKey)
    }
  }, [menuMac])

  useEffect(() => {
    if (aliasTarget) {
      aliasInput.current?.focus()
    }
  }, [aliasTarget])

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

  const saveAlias = async () => {
    if (!aliasTarget) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      apply(await saveDeviceAlias(aliasTarget.mac, aliasDraft))
      setAliasTarget(null)
      setNotice("Alias saved.")
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save that alias.")
    } finally {
      setBusy(false)
    }
  }

  const confirmDisconnect = async () => {
    if (!confirmTarget) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await disconnectDevice(confirmTarget.mac)
      apply(result.inventory)
      setNotice(result.detail)
      setConfirmTarget(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not disconnect that device.")
    } finally {
      setBusy(false)
    }
  }

  const devices = inventory?.devices ?? []
  const connected = devices.filter((device) => device.status === "online")
  const previous = devices.filter((device) => device.status === "offline")
  const unknown = devices.filter((device) => device.status === "unknown")
  const associationKnown = inventory?.hostapd === "ok"

  const renderCards = (items: DeviceRecord[]) => (
    <div className="device-grid">
      {items.map((device) => (
        <DeviceCard
          key={device.mac}
          device={device}
          menuOpen={menuMac === device.mac}
          expanded={expanded[device.mac] === true}
          onToggleMenu={() => setMenuMac((current) => (current === device.mac ? null : device.mac))}
          onRename={() => {
            setMenuMac(null)
            setAliasDraft(device.alias)
            setAliasTarget(device)
          }}
          onToggleDetails={() => {
            setMenuMac(null)
            setExpanded((current) => ({ ...current, [device.mac]: !current[device.mac] }))
          }}
          onDisconnect={() => {
            setMenuMac(null)
            setConfirmTarget(device)
          }}
        />
      ))}
    </div>
  )

  return (
    <section className="page devices-page" ref={pageRef}>
      <header className="page-header">
        <div>
          <h1>Devices</h1>
          <p>
            Connected means the device is associated to the hotspot. A DHCP lease or neighbor entry does not mean it is
            online. Disconnect asks the hotspot to drop one client. The device can reconnect on its own.
          </p>
        </div>
        <button type="button" className="button" onClick={() => void refresh()} disabled={refreshing || busy}>
          {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </header>
      <div className="card">
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
        {notice ? (
          <p className="success-text" role="status">
            {notice}
          </p>
        ) : null}
        <div className="device-stats" aria-live="polite">
          <div>
            <span>Connected</span>
            <strong>{inventory ? connectedCount(inventory) : "—"}</strong>
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
        {inventory && inventory.hostapd !== "ok" ? (
          <p className="error-text" role="status">
            {inventory.hostapd_error || "Association data is unavailable."} Devices stay Unknown until association data
            returns.
          </p>
        ) : null}
      </div>
      {!inventory ? <p className="lede">Loading devices.</p> : null}
      {inventory && devices.length === 0 ? <p className="lede">No devices have been seen yet.</p> : null}
      {inventory && devices.length > 0 ? (
        <div className="device-board">
          {associationKnown ? (
            <DeviceSection id="connected-devices" title="Connected" count={connected.length}>
              {connected.length === 0 ? (
                <p className="lede">No devices are associated right now.</p>
              ) : (
                renderCards(connected)
              )}
            </DeviceSection>
          ) : null}
          {previous.length > 0 ? (
            <DeviceSection id="previous-devices" title="Previously seen" count={previous.length}>
              {renderCards(previous)}
            </DeviceSection>
          ) : null}
          {unknown.length > 0 ? (
            <DeviceSection
              id="unknown-devices"
              title="Status unknown"
              count={unknown.length}
              note="Association data is unavailable, so these devices are not marked offline."
            >
              {renderCards(unknown)}
            </DeviceSection>
          ) : null}
        </div>
      ) : null}
      {aliasTarget ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !busy && setAliasTarget(null)}>
          <form
            className="modal device-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-device-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={(event) => {
              event.preventDefault()
              void saveAlias()
            }}
          >
            <h2 id="rename-device-title">Rename</h2>
            <p className="lede">{titleFor(aliasTarget)}</p>
            <label className="field">
              Alias
              <input
                ref={aliasInput}
                value={aliasDraft}
                maxLength={40}
                onChange={(event) => setAliasDraft(event.target.value)}
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="button button-ghost" disabled={busy} onClick={() => setAliasTarget(null)}>
                Cancel
              </button>
              <button type="submit" className="button" disabled={busy}>
                {busy ? "Saving" : "Save"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {confirmTarget ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !busy && setConfirmTarget(null)}>
          <div
            className="modal device-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="disconnect-device-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <h2 id="disconnect-device-title">Disconnect</h2>
            <p>
              Disconnect {titleFor(confirmTarget)} from the hotspot? It can reconnect on its own. This does not block
              the device.
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="button button-ghost"
                disabled={busy}
                onClick={() => setConfirmTarget(null)}
              >
                Cancel
              </button>
              <button type="button" className="button button-danger" disabled={busy} onClick={() => void confirmDisconnect()}>
                {busy ? "Disconnecting" : "Disconnect"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}

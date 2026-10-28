import { useCallback, useEffect, useState } from "react"
import { ApiError, connectWifi, disconnectWifi, getWifiStatus, scanWifi } from "../api/client"
import type { WifiNetwork, WifiStatus } from "../api/types"

const STATE_LABEL: Record<WifiStatus["state"], string> = {
  connected: "Connected",
  connecting: "Connecting",
  disconnected: "Disconnected",
  disconnecting: "Disconnecting",
  unavailable: "Unavailable",
  unmanaged: "Unmanaged",
  failed: "Failed",
  deactivating: "Disconnecting",
  prepare: "Preparing",
  config: "Configuring",
  "ip-config": "Requesting an address",
  "ip-check": "Checking the address",
  secondaries: "Connecting",
  "need-auth": "Needs a password",
  unknown: "Unknown",
}

const LINK_LABEL: Record<WifiStatus["connectivity"], string> = {
  full: "Internet",
  limited: "Limited connectivity",
  portal: "Sign-in required",
  none: "No internet",
  unknown: "Checking",
}

function display(value: string | null | undefined): string {
  return value ? value : "—"
}

export function WifiPage() {
  const [status, setStatus] = useState<WifiStatus | null>(null)
  const [networks, setNetworks] = useState<WifiNetwork[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [password, setPassword] = useState("")
  const [useSaved, setUseSaved] = useState(true)
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const fail = (err: unknown, fallback: string) => {
    setError(err instanceof ApiError ? err.message : fallback)
  }

  const refresh = useCallback(async () => {
    const next = await getWifiStatus()
    setStatus(next)
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getWifiStatus()
        if (!cancelled) {
          setStatus(next)
          setError((current) => (current === "Upstream Wi-Fi control is unavailable." ? null : current))
        }
      } catch (err) {
        if (!cancelled) {
          setStatus(null)
          if (!busy) {
            fail(err, "Could not read the upstream connection.")
          }
        }
      }
    }
    void load()
    const timer = window.setInterval(() => {
      if (!busy) {
        void load()
      }
    }, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [busy])

  const scan = async () => {
    setBusy(true)
    setError(null)
    setProgress("Scanning the upstream adapter")
    try {
      const result = await scanWifi()
      setStatus(result.status)
      setNetworks(result.networks)
      setProgress(result.networks.length === 0 ? "No networks were found." : null)
    } catch (err) {
      setProgress(null)
      fail(err, "The scan failed.")
    } finally {
      setBusy(false)
    }
  }

  const choose = (network: WifiNetwork) => {
    setSelected(network.ssid)
    setPassword("")
    setUseSaved(network.saved)
    setError(null)
  }

  const connect = async (network: WifiNetwork, savedOverride?: boolean) => {
    const saved = savedOverride ?? (network.saved && useSaved)
    if (!saved && network.security !== "open" && password.length === 0) {
      setError("That network needs a password.")
      return
    }
    setBusy(true)
    setError(null)
    setProgress(saved ? `Reconnecting to ${network.ssid}` : `Connecting to ${network.ssid}`)
    try {
      const next = await connectWifi(network.ssid, saved ? "" : password, saved)
      setStatus(next)
      setPassword("")
      setNetworks((current) =>
        current.map((item) => ({ ...item, in_use: item.ssid === next.ssid && next.state === "connected" })),
      )
      setProgress(next.state === "connected" ? `Connected to ${next.ssid || network.ssid}` : "The connection did not finish.")
      try {
        await refresh()
      } catch {
        setStatus(next)
      }
    } catch (err) {
      setProgress(null)
      fail(err, "Could not connect.")
    } finally {
      setBusy(false)
    }
  }

  const disconnect = async () => {
    setBusy(true)
    setError(null)
    setProgress("Disconnecting the upstream adapter")
    try {
      const next = await disconnectWifi()
      setStatus(next)
      setNetworks((current) => current.map((item) => ({ ...item, in_use: false })))
      setProgress("Upstream Wi-Fi is disconnected.")
    } catch (err) {
      setProgress(null)
      fail(err, "Could not disconnect.")
    } finally {
      setBusy(false)
    }
  }

  const active = networks.find((network) => network.ssid === selected) ?? null
  const connected = status?.state === "connected"
  const showPassword = active !== null && active.supported && active.security !== "open" && !(active.saved && useSaved)

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Wi-Fi</h1>
          <p>Upstream connection on wlan1. The CYBERDECK hotspot stays on wlan0.</p>
        </div>
      </header>

      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
      {progress ? (
        <p className="wifi-progress" role="status">
          {progress}
        </p>
      ) : null}

      <div className="card">
        <div className="card-label">Current upstream</div>
        {status ? (
          <>
            <div className="metric-row">
              <div>
                <div className="metric metric-small">{display(status.ssid)}</div>
                <div className="card-sub wifi-sub">
                  <span>{STATE_LABEL[status.state]}</span>
                  <span>{status.signal === null ? "Signal —" : `Signal ${status.signal}%`}</span>
                </div>
              </div>
              {connected ? (
                <button type="button" className="button button-ghost" onClick={() => void disconnect()} disabled={busy}>
                  Disconnect
                </button>
              ) : null}
            </div>
            {connected && (status.connectivity === "portal" || status.connectivity === "limited") ? (
              <p className="wifi-notice" role="status">
                {status.connectivity === "portal"
                  ? "This upstream network is asking for a sign-in page before the internet is available."
                  : "This upstream network has limited connectivity."}
              </p>
            ) : null}
            <dl className="kv">
              <div className="kv-row">
                <dt>Interface</dt>
                <dd>{status.interface}</dd>
              </div>
              <div className="kv-row">
                <dt>State</dt>
                <dd>{STATE_LABEL[status.state]}</dd>
              </div>
              <div className="kv-row">
                <dt>Internet</dt>
                <dd>{connected ? LINK_LABEL[status.connectivity] : "—"}</dd>
              </div>
              <div className="kv-row">
                <dt>IP address</dt>
                <dd>{display(status.ip_address)}</dd>
              </div>
              <div className="kv-row">
                <dt>Gateway</dt>
                <dd>{display(status.gateway)}</dd>
              </div>
              <div className="kv-row">
                <dt>DNS</dt>
                <dd>{status.dns.length > 0 ? status.dns.join(", ") : "—"}</dd>
              </div>
            </dl>
          </>
        ) : (
          <p className="unavailable-copy">Upstream status is not available yet.</p>
        )}
      </div>

      <div className="card">
        <div className="wifi-toolbar">
          <div className="card-label">Nearby networks</div>
          <button type="button" className="button button-small" onClick={() => void scan()} disabled={busy}>
            {busy && progress?.startsWith("Scanning") ? "Scanning" : "Scan"}
          </button>
        </div>
        {networks.length === 0 ? (
          <p className="unavailable-copy">Scan to list networks visible to the upstream adapter.</p>
        ) : (
          <div className="wifi-list">
            {networks.map((network) => {
              const open = selected === network.ssid
              return (
                <div key={network.ssid} className={open ? "wifi-row open" : "wifi-row"}>
                  <div className="wifi-identity">
                    <strong>{network.ssid}</strong>
                    <div className="wifi-meta">
                      <span>{network.signal}%</span>
                      <span>{network.security_label}</span>
                      {network.saved ? <span>Saved</span> : null}
                      {network.in_use ? <span>In use</span> : null}
                    </div>
                  </div>
                  <div className="wifi-actions">
                    <button
                      type="button"
                      className="button button-ghost button-small"
                      onClick={() => choose(network)}
                      disabled={busy || !network.supported}
                    >
                      {network.supported ? (open ? "Selected" : "Select") : "Unsupported"}
                    </button>
                    {network.saved ? (
                      <button
                        type="button"
                        className="button button-small"
                        onClick={() => {
                          setSelected(network.ssid)
                          setUseSaved(true)
                          setPassword("")
                          void connect(network, true)
                        }}
                        disabled={busy}
                      >
                        Reconnect
                      </button>
                    ) : null}
                  </div>
                  {open && network.supported ? (
                    <form
                      className="wifi-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void connect(network)
                      }}
                    >
                      {network.saved ? (
                        <label className="wifi-check">
                          <input
                            type="checkbox"
                            checked={useSaved}
                            onChange={(event) => setUseSaved(event.target.checked)}
                          />
                          Use the saved profile
                        </label>
                      ) : null}
                      {showPassword ? (
                        <label className="field">
                          Password
                          <input
                            type="password"
                            value={password}
                            autoComplete="off"
                            autoCapitalize="off"
                            spellCheck={false}
                            onChange={(event) => setPassword(event.target.value)}
                          />
                        </label>
                      ) : null}
                      <button type="submit" className="button" disabled={busy}>
                        {network.saved && useSaved ? "Reconnect" : "Connect"}
                      </button>
                    </form>
                  ) : null}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

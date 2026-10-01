import { FitAddon } from "@xterm/addon-fit"
import { Terminal } from "@xterm/xterm"
import { useEffect, useRef, useState } from "react"
import "@xterm/xterm/css/xterm.css"
import { terminalUrl } from "../api/client"
import type { ThemeSettings } from "../api/types"
import { useTheme } from "../theme/ThemeContext"

type Status = "connecting" | "connected" | "closed"

export function TerminalPage() {
  const { theme } = useTheme()
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState<Status>("connecting")
  const [detail, setDetail] = useState("Connecting")
  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)

  useEffect(() => {
    const term = termRef.current
    if (!term) {
      return
    }
    term.options.theme = xtermTheme(theme)
  }, [theme])

  useEffect(() => {
    const host = hostRef.current
    if (!host) {
      return
    }
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"Cascadia Mono", "Segoe UI Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 14,
      lineHeight: 1.2,
      scrollback: 5000,
      theme: xtermTheme(theme),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    fit.fit()
    termRef.current = term

    const socket = new WebSocket(terminalUrl())
    socket.binaryType = "arraybuffer"
    setStatus("connecting")
    setDetail("Connecting")

    const sendResize = () => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }))
      }
    }

    socket.addEventListener("open", () => {
      setStatus("connected")
      setDetail("Connected")
      fit.fit()
      sendResize()
      term.focus()
    })
    socket.addEventListener("close", (event) => {
      setStatus("closed")
      if (event.code === 4401) {
        setDetail("Authentication required")
      } else if (event.code === 4403) {
        setDetail("Connection blocked")
      } else if (event.code === 4429) {
        setDetail("Too many terminal sessions")
      } else {
        setDetail("Disconnected")
      }
    })
    socket.addEventListener("error", () => {
      setStatus("closed")
      setDetail("Connection error")
    })
    socket.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        const control = readControl(event.data)
        if (control === "output") {
          term.write(event.data)
        } else if (control) {
          setDetail(control)
        }
        return
      }
      term.write(new Uint8Array(event.data as ArrayBuffer))
    })

    const input = term.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }))
      }
    })

    const observer = new ResizeObserver(() => {
      fit.fit()
      sendResize()
    })
    observer.observe(host)

    return () => {
      observer.disconnect()
      input.dispose()
      socket.close()
      term.dispose()
      termRef.current = null
    }
  }, [attempt])

  return (
    <section className="terminal-page">
      <div className="terminal-toolbar">
        <span className={status === "connected" ? "status-pill online" : "status-pill offline"}>
          <span className="status-dot" aria-hidden="true" />
          {detail}
        </span>
        {status === "closed" ? (
          <button type="button" className="button button-small" onClick={() => setAttempt((value) => value + 1)}>
            Reconnect
          </button>
        ) : null}
      </div>
      <div className="terminal-host" ref={hostRef} />
    </section>
  )
}

function xtermTheme(theme: ThemeSettings) {
  return {
    background: theme.background,
    foreground: theme.text,
    cursor: theme.accent,
    cursorAccent: theme.background,
    selectionBackground: theme.secondary,
  }
}

function readControl(data: string): string | null {
  try {
    const payload = JSON.parse(data) as { type?: string; message?: string }
    if (payload.type === "error" && payload.message) {
      return payload.message
    }
    if (payload.type === "exit") {
      return "Shell exited"
    }
    if (payload.type === "error" || payload.type === "exit") {
      return "Terminal closed"
    }
    return "output"
  } catch {
    return "output"
  }
}

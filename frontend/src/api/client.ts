import type { BatterySettings, SystemSnapshot, ThemeSettings, UserProfile } from "./types"

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    throw new ApiError(await readDetail(response), response.status)
  }
  return (await response.json()) as T
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === "string") {
        return detail
      }
    }
  } catch {
    return response.statusText || "Request failed"
  }
  return response.statusText || "Request failed"
}

export function getMe(): Promise<UserProfile> {
  return request<UserProfile>("/api/auth/me")
}

export function login(username: string, password: string): Promise<UserProfile> {
  return request<UserProfile>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  })
}

export function logout(): Promise<{ detail: string }> {
  return request<{ detail: string }>("/api/auth/logout", { method: "POST" })
}

export function getSnapshot(): Promise<SystemSnapshot> {
  return request<SystemSnapshot>("/api/system/snapshot")
}

export function saveTheme(theme: ThemeSettings): Promise<{ theme: ThemeSettings }> {
  return request<{ theme: ThemeSettings }>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(theme),
  })
}

export function getBatterySettings(): Promise<BatterySettings> {
  return request<BatterySettings>("/api/settings/battery")
}

export function saveBatterySettings(settings: {
  estimated_full_runtime_minutes: number | null
  full_charge_time_minutes: number | null
}): Promise<BatterySettings> {
  return request<BatterySettings>("/api/settings/battery", {
    method: "PUT",
    body: JSON.stringify(settings),
  })
}

export function terminalUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${protocol}//${window.location.host}/ws/terminal`
}

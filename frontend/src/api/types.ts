export type ThemePreset = "blue" | "hacker" | "purple" | "red" | "custom"

export type ThemeSettings = {
  preset: ThemePreset
  primary: string
  secondary: string
  accent: string
  background: string
  panel: string
  text: string
}

export type UserProfile = {
  id: number
  username: string
  theme: ThemeSettings
}

export type CpuStats = {
  usage_percent: number
  per_core_percent: number[]
  temperature_c: number | null
  frequency_mhz: number | null
  load_average: number[] | null
}

export type MemoryStats = {
  used_bytes: number
  total_bytes: number
  percent: number
}

export type StorageStats = {
  used_bytes: number
  free_bytes: number
  total_bytes: number
  percent: number
  mount: string
}

export type NetworkStats = {
  hostname: string
  local_ip: string | null
  tailscale_ip: string | null
  tailscale_hostname: string | null
  tailscale_status: "online" | "offline" | "unavailable"
  bytes_sent: number
  bytes_received: number
}

export type SystemInfo = {
  os_name: string
  kernel: string
  architecture: string
  uptime_seconds: number
  boot_time: string
  pi_model: string | null
}

export type BatteryInfo = {
  available: boolean
  percent: number | null
  charging: boolean | null
  status: string | null
  voltage: number | null
  message: string | null
}

export type SystemSnapshot = {
  cpu: CpuStats
  memory: MemoryStats
  storage: StorageStats
  network: NetworkStats
  system: SystemInfo
  battery: BatteryInfo
  collected_at: string
}

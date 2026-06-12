const TOKEN_KEY = 'shinehe_api_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function isAuthenticated(): boolean {
  return Boolean(getToken())
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body && typeof init.body === 'string') {
    headers.set('Content-Type', 'application/json')
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    window.location.href = '/login'
    throw new Error('认证已过期，请重新登录')
  }
  if (!res.ok) {
    const message = await res.text().catch(() => `Request failed: ${res.status}`)
    throw new Error(message || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path)
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function apiDelete<T>(path: string): Promise<T> {
  return apiRequest<T>(path, { method: 'DELETE' })
}

export async function apiUpload<T>(path: string, file: File, fields?: Record<string, string>): Promise<T> {
  const token = getToken()
  const form = new FormData()
  form.append('file', file)
  if (fields) {
    for (const [k, v] of Object.entries(fields)) {
      form.append(k, v)
    }
  }
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(path, { method: 'POST', headers, body: form })
  if (!res.ok) {
    const message = await res.text().catch(() => `Upload failed: ${res.status}`)
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

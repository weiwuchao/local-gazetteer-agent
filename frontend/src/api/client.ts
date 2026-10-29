/**
 * fetch 封装：Bearer 注入、401 自动 refresh、错误码 → toast。
 * 统一约定见详细设计 2.1。
 */
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public detail: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body.code ?? 'EX-99', body.message ?? res.statusText, body.detail)
  }
  return res.json() as Promise<T>
}

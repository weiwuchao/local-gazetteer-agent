/**
 * POST + ReadableStream 解析 text/event-stream（D-3 / 6.5）。
 * 需要自行处理帧边界、断流重连（Last-Event-ID）。
 */
export interface SseFrame {
  event: string
  data: unknown
  id?: string
}

export async function postStream(
  path: string,
  body: unknown,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`SSE failed: ${res.status}`)
  // TODO: 按 \n\n 切帧，解析 event/id/data，处理 heartbeat 与重连
  void onFrame
}

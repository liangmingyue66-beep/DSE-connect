// 接口层：凭证管理 + 401 自动重签 + SSE 流式解析
const TOKEN_KEY = 'dse_token'
const AUTH_HEADER = 'X-Session-Token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

function setToken(t) {
  localStorage.setItem(TOKEN_KEY, t)
}

async function requestToken() {
  const r = await fetch('/auth/token', { method: 'POST' })
  if (!r.ok) throw new Error('无法建立会话凭证，请检查服务是否启动')
  const data = await r.json()
  setToken(data.token)
  return data.token
}

export async function ensureToken(force = false) {
  const t = getToken()
  if (t && !force) return t
  return requestToken()
}

async function api(path, { method = 'GET', body, retry = true } = {}) {
  const token = await ensureToken()
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', [AUTH_HEADER]: token },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  // 凭证失效（服务端换了密钥）：重新签发一次再重试
  if (r.status === 401 && retry) {
    await ensureToken(true)
    return api(path, { method, body, retry: false })
  }
  if (!r.ok) {
    let detail = ''
    try {
      detail = (await r.json()).detail || ''
    } catch { /* 非 JSON 响应 */ }
    throw new Error(detail || `请求失败（${r.status}）`)
  }
  return r.json()
}

export const listSessions = () => api('/sessions')
export const createSession = () => api('/sessions', { method: 'POST' })
export const deleteSession = (id) => api(`/sessions/${id}`, { method: 'DELETE' })
export const fetchMessages = (id) => api(`/sessions/${id}/messages`)

/**
 * 流式对话：把 SSE 事件逐个交给 onEvent。
 * 事件类型：stream(文本片段) / node(工具状态) / error / done
 */
export async function streamChat({ sessionId, message, onEvent, signal }) {
  const token = await ensureToken()
  const r = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', [AUTH_HEADER]: token },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal,
  })
  if (r.status === 401) {
    await ensureToken(true)
    throw new Error('凭证已刷新，请重新发送')
  }
  if (!r.ok) throw new Error(`请求失败（${r.status}）`)

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = raw.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch { /* 跳过不完整帧 */ }
    }
  }
}

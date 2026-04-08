const BASE = import.meta.env.VITE_API_URL || ''

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  chats: () => get('/api/chats'),

  messages: (chatId, { limit = 50, offset = 0, after = null, before = null } = {}) => {
    const params = new URLSearchParams({ limit, offset })
    if (after  !== null) params.set('after',  after)
    if (before !== null) params.set('before', before)
    return get(`/api/chats/${chatId}/messages?${params}`)
  },

  media: (chatId, { kind = null, limit = 60, offset = 0 } = {}) => {
    const params = new URLSearchParams({ limit, offset })
    if (kind) params.set('kind', kind)
    return get(`/api/chats/${chatId}/media?${params}`)
  },
}

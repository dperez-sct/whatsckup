import { useEffect, useState } from 'react'
import { api } from '../api.js'
import ChatItem from './ChatItem.jsx'

export default function ChatList({ onSelect, selectedId }) {
  const [chats, setChats]       = useState([])
  const [query, setQuery]       = useState('')
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  useEffect(() => {
    api.chats()
      .then(setChats)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = chats.filter(c =>
    c.name.toLowerCase().includes(query.toLowerCase())
  )

  if (loading) return <div className="list-status">Cargando chats…</div>
  if (error)   return <div className="list-status error">Error: {error}</div>

  return (
    <div className="chat-list">
      <div className="search-box">
        <input
          type="search"
          placeholder="Buscar chat…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>
      <div className="chat-items">
        {filtered.length === 0
          ? <div className="list-status">Sin resultados</div>
          : filtered.map(chat =>
              <ChatItem
                key={chat.id}
                chat={chat}
                selected={chat.id === selectedId}
                onClick={() => onSelect(chat)}
              />
            )
        }
      </div>
    </div>
  )
}

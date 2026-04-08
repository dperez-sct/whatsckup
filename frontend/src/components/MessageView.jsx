import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import MediaGallery from './MediaGallery.jsx'

function formatDateTime(ms) {
  if (!ms) return ''
  return new Date(ms).toLocaleString('es-ES', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// "YYYY-MM-DD" → timestamp ms inicio/fin del día
function dateToAfter(str)  { return str ? new Date(str + 'T00:00:00').getTime() : null }
function dateToBefore(str) { return str ? new Date(str + 'T23:59:59').getTime() : null }

function MediaContent({ media }) {
  if (!media) return null
  const src = `/media/${media.path}`

  if (media.mime?.startsWith('image/'))
    return <img src={src} alt={media.caption || ''} className="msg-media" loading="lazy" />

  if (media.mime?.startsWith('video/'))
    return (
      <video controls className="msg-media">
        <source src={src} type={media.mime} />
      </video>
    )

  if (media.mime?.startsWith('audio/'))
    return <audio controls src={src} className="msg-audio" />

  return (
    <a href={src} target="_blank" rel="noreferrer" className="msg-file">
      📄 {media.path.split('/').pop()}
    </a>
  )
}

const GROUP_JID_SUFFIX = '@g.us'

export default function MessageView({ chat }) {
  const isGroup = chat.jid?.endsWith(GROUP_JID_SUFFIX)
  const [messages, setMessages] = useState([])
  const [total, setTotal]       = useState(0)
  const [offset, setOffset]     = useState(0)
  const [loading, setLoading]   = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo,   setDateTo]   = useState('')
  const [showGallery, setShowGallery] = useState(false)
  const LIMIT = 50

  const containerRef        = useRef(null)
  const scrollToBottomRef   = useRef(false)
  const prevScrollHeightRef = useRef(null)
  const jumpTargetRef       = useRef(null)   // { messageId, timestamp } pendiente de scroll

  // Resetear y recargar cuando cambia chat o filtros de fecha
  useEffect(() => {
    setMessages([])
    setOffset(0)
    setTotal(0)
    load(chat.id, 0, dateFrom, dateTo, true)
  }, [chat.id, dateFrom, dateTo])

  // Ajustar scroll tras render
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    // Salto a mensaje concreto desde galería
    if (jumpTargetRef.current !== null) {
      const targetId = jumpTargetRef.current
      jumpTargetRef.current = null
      const elem = el.querySelector(`[data-msg-id="${targetId}"]`)
      if (elem) {
        elem.scrollIntoView({ behavior: 'smooth', block: 'center' })
        elem.classList.add('msg-highlight')
        setTimeout(() => elem.classList.remove('msg-highlight'), 2000)
      }
      return
    }

    if (scrollToBottomRef.current) {
      el.scrollTop = el.scrollHeight
      scrollToBottomRef.current = false
    } else if (prevScrollHeightRef.current !== null) {
      el.scrollTop = el.scrollHeight - prevScrollHeightRef.current
      prevScrollHeightRef.current = null
    }
  }, [messages])

  function load(chatId, off, from, to, initial = false) {
    if (initial) {
      scrollToBottomRef.current = true
    } else {
      prevScrollHeightRef.current = containerRef.current?.scrollHeight ?? null
    }

    setLoading(true)
    api.messages(chatId, {
      limit:  LIMIT,
      offset: off,
      after:  dateToAfter(from),
      before: dateToBefore(to),
    })
      .then(data => {
        const batch = [...data.messages].reverse()
        setTotal(data.total)
        setMessages(prev => initial ? batch : [...batch, ...prev])
        setOffset(off + data.messages.length)
      })
      .finally(() => setLoading(false))
  }

  // Saltar a un mensaje concreto desde la galería
  function handleJumpToMessage(messageId, timestamp) {
    setShowGallery(false)
    jumpTargetRef.current = messageId
    scrollToBottomRef.current = false
    prevScrollHeightRef.current = null

    setLoading(true)
    // Cargar los mensajes en torno al timestamp (antes = incluye el mensaje)
    api.messages(chat.id, { limit: LIMIT, offset: 0, before: timestamp + 1 })
      .then(data => {
        const batch = [...data.messages].reverse()
        setTotal(data.total)
        setMessages(batch)
        setOffset(data.messages.length)
      })
      .finally(() => setLoading(false))
  }

  function clearDates() {
    setDateFrom('')
    setDateTo('')
  }

  const hasFilter = dateFrom || dateTo

  if (showGallery) {
    return (
      <MediaGallery
        chat={chat}
        onClose={() => setShowGallery(false)}
        onJumpToMessage={handleJumpToMessage}
      />
    )
  }

  return (
    <div className="message-view">
      <header className="msg-header">
        <div className="msg-header-left">
          <span className="msg-header-name">{chat.name}</span>
          <span className="msg-header-count">
            {hasFilter ? `${total} resultados` : `${total} mensajes`}
          </span>
        </div>
        <button className="gallery-btn" onClick={() => setShowGallery(true)} title="Ver multimedia">
          🖼
        </button>
        <div className="date-filter">
          <label>
            Desde
            <input
              type="date"
              value={dateFrom}
              onChange={e => setDateFrom(e.target.value)}
              max={dateTo || undefined}
            />
          </label>
          <label>
            Hasta
            <input
              type="date"
              value={dateTo}
              onChange={e => setDateTo(e.target.value)}
              min={dateFrom || undefined}
            />
          </label>
          {hasFilter && (
            <button className="clear-dates" onClick={clearDates} title="Quitar filtro">
              ✕
            </button>
          )}
        </div>
      </header>

      <div className="messages" ref={containerRef}>
        {total > offset && (
          <button
            className="load-more"
            onClick={() => load(chat.id, offset, dateFrom, dateTo)}
            disabled={loading}
          >
            {loading ? 'Cargando…' : `Cargar mensajes anteriores (${total - offset} restantes)`}
          </button>
        )}

        {!loading && messages.length === 0 && (
          <div className="no-results">
            {hasFilter ? 'Sin mensajes en ese rango de fechas' : 'Sin mensajes'}
          </div>
        )}

        {messages.map(msg => (
          <div
            key={msg.id}
            data-msg-id={msg.id}
            className={`message ${msg.from_me ? 'outgoing' : 'incoming'}`}
          >
            <div className="bubble">
              {isGroup && !msg.from_me && msg.sender && (
                <span className="msg-sender">{msg.sender}</span>
              )}
              <MediaContent media={msg.media} />
              {msg.text && <p className="msg-text">{msg.text}</p>}
              {msg.media?.caption && !msg.text &&
                <p className="msg-caption">{msg.media.caption}</p>
              }
              <span className="msg-time">{formatDateTime(msg.timestamp)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

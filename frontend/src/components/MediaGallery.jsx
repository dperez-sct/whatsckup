import { useEffect, useState } from 'react'
import { api } from '../api.js'

const TABS = [
  { key: 'image',    label: 'Imágenes' },
  { key: 'video',    label: 'Vídeos' },
  { key: 'audio',    label: 'Audios' },
  { key: 'document', label: 'Docs' },
]

function formatDate(ms) {
  if (!ms) return ''
  return new Date(ms).toLocaleDateString('es-ES', { day: '2-digit', month: 'short', year: 'numeric' })
}

function Lightbox({ item, onClose, onPrev, onNext, hasPrev, hasNext, onJump }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft'  && hasPrev) onPrev()
      if (e.key === 'ArrowRight' && hasNext) onNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [hasPrev, hasNext])

  const src = `/media/${item.file_path}`

  return (
    <div className="lightbox" onClick={onClose}>
      <button className="lb-close" onClick={onClose}>✕</button>
      {hasPrev && <button className="lb-prev" onClick={e => { e.stopPropagation(); onPrev() }}>‹</button>}
      {hasNext && <button className="lb-next" onClick={e => { e.stopPropagation(); onNext() }}>›</button>}

      <div className="lb-content" onClick={e => e.stopPropagation()}>
        {item.mime_type?.startsWith('image/') && (
          <img src={src} alt={item.media_caption || ''} className="lb-img" />
        )}
        {item.mime_type?.startsWith('video/') && (
          <video src={src} controls autoPlay className="lb-video" />
        )}
        {item.media_caption && <p className="lb-caption">{item.media_caption}</p>}
        <div className="lb-footer">
          <span className="lb-date">{formatDate(item.timestamp)}</span>
          <button className="lb-jump" onClick={() => onJump(item.message_id, item.timestamp)}>
            Ir al mensaje →
          </button>
        </div>
      </div>
    </div>
  )
}

export default function MediaGallery({ chat, onClose, onJumpToMessage }) {
  const [tab, setTab]           = useState('image')
  const [items, setItems]       = useState([])
  const [total, setTotal]       = useState(0)
  const [offset, setOffset]     = useState(0)
  const [loading, setLoading]   = useState(false)
  const [lightbox, setLightbox] = useState(null)
  const LIMIT = 60

  useEffect(() => {
    setItems([])
    setOffset(0)
    setTotal(0)
    load(0)
  }, [tab, chat.id])

  function load(off) {
    setLoading(true)
    api.media(chat.id, { kind: tab, limit: LIMIT, offset: off })
      .then(data => {
        setTotal(data.total)
        setItems(prev => off === 0 ? data.items : [...prev, ...data.items])
        setOffset(off + data.items.length)
      })
      .finally(() => setLoading(false))
  }

  function handleJump(messageId, timestamp) {
    setLightbox(null)
    onJumpToMessage(messageId, timestamp)
  }

  return (
    <div className="media-gallery">
      <header className="gallery-header">
        <button className="gallery-back" onClick={onClose}>←</button>
        <span className="gallery-title">{chat.name}</span>
        <span className="gallery-subtitle">Multimedia</span>
      </header>

      <div className="gallery-tabs">
        {TABS.map(t => (
          <button
            key={t.key}
            className={`gallery-tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="gallery-body">
        {tab === 'audio' || tab === 'document' ? (
          <div className="gallery-list">
            {items.map((item, i) => (
              <div key={i} className="gallery-list-item">
                {tab === 'audio' ? (
                  <>
                    <audio controls src={`/media/${item.file_path}`} className="gallery-audio" />
                    <div className="gallery-list-footer">
                      <span className="gallery-list-date">{formatDate(item.timestamp)}</span>
                      <button className="gallery-jump-btn" onClick={() => handleJump(item.message_id, item.timestamp)}>
                        Ir al mensaje →
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <a href={`/media/${item.file_path}`} target="_blank" rel="noreferrer" className="gallery-doc">
                      <span className="gallery-doc-icon">📄</span>
                      <span className="gallery-doc-name">{item.file_path.split('/').pop()}</span>
                    </a>
                    <div className="gallery-list-footer">
                      <span className="gallery-list-date">{formatDate(item.timestamp)}</span>
                      <button className="gallery-jump-btn" onClick={() => handleJump(item.message_id, item.timestamp)}>
                        Ir al mensaje →
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="gallery-grid">
            {items.map((item, i) => (
              <div key={i} className="gallery-thumb" onClick={() => setLightbox(i)}>
                {item.mime_type?.startsWith('image/') ? (
                  <img src={`/media/${item.file_path}`} alt="" loading="lazy" />
                ) : (
                  <div className="gallery-thumb-video">
                    <video src={`/media/${item.file_path}`} preload="metadata" />
                    <span className="gallery-play">▶</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {!loading && items.length === 0 && (
          <div className="gallery-empty">Sin {TABS.find(t => t.key === tab)?.label.toLowerCase()}</div>
        )}

        {total > offset && (
          <button className="load-more" onClick={() => load(offset)} disabled={loading}>
            {loading ? 'Cargando…' : `Ver más (${total - offset} restantes)`}
          </button>
        )}
      </div>

      {lightbox !== null && (
        <Lightbox
          item={items[lightbox]}
          onClose={() => setLightbox(null)}
          onPrev={() => setLightbox(i => i - 1)}
          onNext={() => setLightbox(i => i + 1)}
          hasPrev={lightbox > 0}
          hasNext={lightbox < items.length - 1}
          onJump={handleJump}
        />
      )}
    </div>
  )
}

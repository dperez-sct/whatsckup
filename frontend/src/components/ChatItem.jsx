function avatar(name) {
  const words = name.trim().split(/\s+/)
  return words.length >= 2
    ? (words[0][0] + words[1][0]).toUpperCase()
    : name.slice(0, 2).toUpperCase()
}

function formatTime(ms) {
  if (!ms) return ''
  const d   = new Date(ms)
  const now = new Date()
  const isToday =
    d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear()
  if (isToday) return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
  const isThisYear = d.getFullYear() === now.getFullYear()
  return d.toLocaleDateString('es-ES', {
    day: '2-digit', month: '2-digit',
    ...(isThisYear ? {} : { year: '2-digit' }),
  })
}

// Deterministic hue from string
function nameHue(name) {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff
  return h % 360
}

export default function ChatItem({ chat, selected, onClick }) {
  const hue    = nameHue(chat.name)
  const abbr   = avatar(chat.name)
  const time   = formatTime(chat.last?.timestamp)
  const text   = chat.last?.text || ''
  const prefix = chat.last?.from_me ? 'Tú: ' : ''

  return (
    <div
      className={`chat-item${selected ? ' selected' : ''}`}
      onClick={onClick}
    >
      <div
        className="avatar"
        style={{ '--hue': hue }}
      >
        {abbr}
      </div>
      <div className="chat-info">
        <div className="chat-row">
          <span className="chat-name">{chat.name}</span>
          {time && <span className="chat-time">{time}</span>}
        </div>
        <div className="chat-preview">
          {prefix && <span className="from-me">{prefix}</span>}
          <span className="preview-text">{text}</span>
        </div>
      </div>
    </div>
  )
}

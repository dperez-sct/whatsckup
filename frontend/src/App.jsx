import { useState } from 'react'
import ChatList from './components/ChatList.jsx'
import MessageView from './components/MessageView.jsx'
import './App.css'

export default function App() {
  const [selectedChat, setSelectedChat] = useState(null)

  return (
    <div className="app">
      <aside className="sidebar">
        <header className="sidebar-header">
          <h1>whatsckup</h1>
        </header>
        <ChatList onSelect={setSelectedChat} selectedId={selectedChat?.id} />
      </aside>

      <main className="main">
        {selectedChat
          ? <MessageView chat={selectedChat} />
          : <div className="empty-state">
              <div className="empty-icon">💬</div>
              <p>Selecciona un chat para ver los mensajes</p>
            </div>
        }
      </main>
    </div>
  )
}

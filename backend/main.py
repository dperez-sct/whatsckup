"""
whatsckup backend — FastAPI
"""

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

log = logging.getLogger("whatsckup")

APP_PATH           = Path(os.getenv("APP_PATH", "/mnt/app"))
CONTACTS_VCF       = Path(os.getenv("CONTACTS_VCF", str(APP_PATH / "contacts.vcf")))
MSG_DB             = APP_PATH / "msgstore.db"
WA_DB              = APP_PATH / "wa.db"
MEDIA_DIR          = Path(os.getenv("MEDIA_PATH", str(APP_PATH)))  # raíz desde donde se sirve la media
DB_CHECK_INTERVAL  = int(os.getenv("DB_CHECK_INTERVAL", "60"))  # segundos

# ---------------------------------------------------------------------------
# Estado del watcher — actualizado por el background task
# ---------------------------------------------------------------------------
_db_state = {
    "hash":         None,
    "last_checked": None,   # epoch seconds
    "last_changed": None,   # epoch seconds (última vez que cambió)
    "version":      0,      # se incrementa cada vez que cambia el DB
}

_contacts_cache = {
    "version": -1,          # _db_state["version"] en el momento del último cálculo
    "names":   {},           # {jid_raw_string: display_name}
}


def _file_hash(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def _db_watcher():
    """Comprueba periódicamente si msgstore.db ha cambiado."""
    while True:
        try:
            current = _file_hash(MSG_DB)
            now = time.time()
            _db_state["last_checked"] = now
            if current and current != _db_state["hash"]:
                if _db_state["hash"] is not None:
                    log.info("msgstore.db changed — new data available (v%d)", _db_state["version"] + 1)
                _db_state["hash"]         = current
                _db_state["last_changed"] = now
                _db_state["version"]     += 1
        except Exception as e:
            log.warning("DB watcher error: %s", e)
        await asyncio.sleep(DB_CHECK_INTERVAL)


app = FastAPI(title="whatsckup")


@app.on_event("startup")
async def startup():
    # Primera comprobación síncrona para inicializar el hash
    _db_state["hash"]         = _file_hash(MSG_DB)
    _db_state["last_checked"] = time.time()
    _db_state["last_changed"] = time.time()
    asyncio.create_task(_db_watcher())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# VCF contact parser
# ---------------------------------------------------------------------------

def _normalize_phone(raw: str) -> str:
    """Normaliza un número de teléfono a formato numérico sin prefijo +."""
    phone = re.sub(r"[^\d+]", "", raw)
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("00"):
        phone = phone[2:]
    # Número español de 9 dígitos sin prefijo → añadir 34
    if len(phone) == 9 and phone[0] in "679":
        phone = "34" + phone
    return phone


def parse_vcf(path: Path) -> dict[str, str]:
    """
    Parsea un fichero VCF y devuelve {numero_normalizado: nombre_completo}.
    Usa el campo FN (formatted name) como nombre. Si hay varios TEL en un
    contacto, todos apuntan al mismo nombre.
    """
    if not path.exists():
        return {}

    contacts: dict[str, str] = {}
    fn = ""
    phones: list[str] = []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log.warning("No se pudo leer %s: %s", path, e)
        return {}

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.upper() == "BEGIN:VCARD":
            fn = ""
            phones = []

        elif line.upper().startswith("FN:"):
            fn = line[3:].strip()

        elif re.match(r"TEL[;:]", line, re.IGNORECASE):
            # TEL;TYPE=CELL:+34612345678  o  TEL:612345678
            val = line.split(":", 1)[-1].strip()
            norm = _normalize_phone(val)
            if norm:
                phones.append(norm)

        elif line.upper() == "END:VCARD":
            if fn and phones:
                for p in phones:
                    contacts[p] = fn

    log.info("VCF: %d contactos cargados desde %s", len(contacts), path)
    return contacts


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def msg_conn() -> sqlite3.Connection:
    if not MSG_DB.exists():
        raise HTTPException(503, "Database not ready")
    conn = sqlite3.connect(f"file:{MSG_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def contact_names() -> dict[str, str]:
    """
    Devuelve {jid_raw_string: display_name}.
    Fuentes (en orden de prioridad): VCF > wa.db
    El resultado se cachea y se invalida cuando cambia _db_state["version"].
    """
    if _contacts_cache["version"] == _db_state["version"]:
        return _contacts_cache["names"]

    names: dict[str, str] = {}

    # 1. wa.db (baja prioridad — suele estar vacío en versiones modernas)
    if WA_DB.exists():
        try:
            conn = sqlite3.connect(WA_DB)
            conn.row_factory = sqlite3.Row
            for r in conn.execute(
                "SELECT jid, display_name FROM wa_contacts WHERE display_name IS NOT NULL"
            ):
                names[r["jid"]] = r["display_name"]
            conn.close()
        except Exception:
            pass

    # 2. VCF + LID mapping — una sola conexión a msgstore.db
    vcf_path = Path(os.getenv("CONTACTS_VCF", str(APP_PATH / "contacts.vcf")))
    vcf = parse_vcf(vcf_path)
    try:
        conn = sqlite3.connect(f"file:{MSG_DB}?mode=ro", uri=True)

        # VCF: cruza {numero: nombre} con jid.user → jid.raw_string
        if vcf:
            for row in conn.execute("SELECT raw_string, user FROM jid"):
                raw_string, user = row[0], row[1]
                if user in vcf:
                    names[raw_string] = vcf[user]

        # LID → phone mapping: JIDs tipo @lid a su teléfono
        for row in conn.execute("""
            SELECT jlid.raw_string AS lid_jid, jphone.raw_string AS phone_jid, jphone.user AS phone_user
            FROM jid_map jm
            JOIN jid jlid   ON jm.lid_row_id = jlid._id
            JOIN jid jphone ON jm.jid_row_id  = jphone._id
        """):
            lid_jid, phone_jid, phone_user = row[0], row[1], row[2]
            names[lid_jid] = names.get(phone_jid) or _pretty_phone(phone_user)

        conn.close()
    except Exception as e:
        log.warning("Error resolviendo contactos desde msgstore: %s", e)

    _contacts_cache["version"] = _db_state["version"]
    _contacts_cache["names"]   = names
    log.info("Contactos cacheados (v%d, %d entradas)", _db_state["version"], len(names))
    return names


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/chats")
def list_chats():
    conn  = msg_conn()
    names = contact_names()

    rows = conn.execute("""
        SELECT
            c._id,
            j.raw_string          AS jid,
            c.subject,
            m.text_data           AS last_text,
            m.timestamp           AS last_ts,
            m.from_me             AS last_from_me,
            m.message_type        AS last_type,
            mm.mime_type          AS last_mime,
            mm.media_caption      AS last_caption
        FROM chat c
        JOIN jid j          ON c.jid_row_id        = j._id
        LEFT JOIN message m ON c.last_message_row_id = m._id
        LEFT JOIN message_media mm ON m._id         = mm.message_row_id
        WHERE c.hidden = 0
        ORDER BY c.sort_timestamp DESC
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        jid      = r["jid"]
        is_group = jid.endswith("@g.us")
        name     = r["subject"] or names.get(jid) or jid.split("@")[0]

        last = None
        if r["last_ts"]:
            text = r["last_text"] or r["last_caption"] or _type_label(r["last_type"], r["last_mime"])
            last = {
                "text":      text,
                "timestamp": r["last_ts"],
                "from_me":   bool(r["last_from_me"]),
            }

        result.append({
            "id":       r["_id"],
            "jid":      jid,
            "name":     name,
            "is_group": is_group,
            "last":     last,
        })

    return result


@app.get("/api/chats/{chat_id}/messages")
def get_messages(
    chat_id: int,
    limit:   int = Query(default=50, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
    after:   Optional[int] = Query(default=None, description="Timestamp ms — solo mensajes posteriores a esta fecha"),
    before:  Optional[int] = Query(default=None, description="Timestamp ms — solo mensajes anteriores a esta fecha"),
):
    conn = msg_conn()

    if not conn.execute("SELECT 1 FROM chat WHERE _id = ?", (chat_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Chat not found")

    # Construir filtros de fecha dinámicamente
    filters = ["m.chat_row_id = ?"]
    params:  list = [chat_id]

    if after is not None:
        filters.append("m.timestamp >= ?")
        params.append(after)
    if before is not None:
        filters.append("m.timestamp <= ?")
        params.append(before)

    where = " AND ".join(filters)

    total = conn.execute(
        f"SELECT COUNT(*) FROM message m WHERE {where}", params
    ).fetchone()[0]

    rows = conn.execute(f"""
        SELECT
            m._id,
            m.key_id,
            m.from_me,
            m.timestamp,
            m.text_data,
            m.message_type,
            m.status,
            mm.file_path,
            mm.mime_type,
            mm.media_caption,
            mm.media_duration,
            mm.width,
            mm.height,
            j.raw_string AS sender_jid
        FROM message m
        LEFT JOIN message_media mm ON m._id = mm.message_row_id
        LEFT JOIN jid j            ON m.sender_jid_row_id = j._id
        WHERE {where}
        ORDER BY m.timestamp DESC, m._id DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    names = contact_names()
    conn.close()

    return {
        "total":    total,
        "limit":    limit,
        "offset":   offset,
        "after":    after,
        "before":   before,
        "messages": [_format_message(r, names) for r in rows],
    }


@app.get("/api/chats/{chat_id}/media")
def get_chat_media(
    chat_id: int,
    kind:    Optional[str] = Query(default=None, description="image|video|audio|document"),
    limit:   int = Query(default=60, ge=1, le=200),
    offset:  int = Query(default=0, ge=0),
):
    conn = msg_conn()
    if not conn.execute("SELECT 1 FROM chat WHERE _id = ?", (chat_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "Chat not found")

    filters = ["m.chat_row_id = ?", "mm.file_path IS NOT NULL"]
    params: list = [chat_id]

    if kind == "image":
        filters.append("mm.mime_type LIKE 'image/%'")
    elif kind == "video":
        filters.append("mm.mime_type LIKE 'video/%'")
    elif kind == "audio":
        filters.append("mm.mime_type LIKE 'audio/%'")
    elif kind == "document":
        filters.append("mm.mime_type NOT LIKE 'image/%'")
        filters.append("mm.mime_type NOT LIKE 'video/%'")
        filters.append("mm.mime_type NOT LIKE 'audio/%'")

    where = " AND ".join(filters)

    total = conn.execute(
        f"SELECT COUNT(*) FROM message m JOIN message_media mm ON m._id = mm.message_row_id WHERE {where}",
        params
    ).fetchone()[0]

    rows = conn.execute(f"""
        SELECT
            mm.file_path,
            mm.mime_type,
            mm.media_caption,
            mm.width,
            mm.height,
            mm.media_duration,
            m.timestamp,
            m.from_me,
            m._id AS message_id
        FROM message m
        JOIN message_media mm ON m._id = mm.message_row_id
        WHERE {where}
        ORDER BY m.timestamp DESC, m._id DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()
    conn.close()

    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "items":  [dict(r) for r in rows],
    }


@app.get("/media/{file_path:path}")
def serve_media(file_path: str):
    full = (MEDIA_DIR / file_path).resolve()
    if not full.is_relative_to(MEDIA_DIR.resolve()):
        raise HTTPException(403, "Forbidden")
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "Media not found")
    return FileResponse(full)


@app.get("/health")
def health():
    return {
        "status":       "ok",
        "db":           MSG_DB.exists(),
        "db_version":   _db_state["version"],
        "last_changed": _db_state["last_changed"],
        "last_checked": _db_state["last_checked"],
        "check_interval": DB_CHECK_INTERVAL,
    }


@app.get("/api/status")
def status():
    """El frontend puede pollear esto para saber si hay datos nuevos."""
    return {
        "version":      _db_state["version"],
        "last_changed": _db_state["last_changed"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pretty_phone(user: str) -> str:
    """34622561531 → 622561531 (quita prefijo español para display)."""
    if len(user) == 11 and user.startswith("34"):
        return user[2:]
    return user


_TYPE_LABELS = {
    1: "📷 Photo", 2: "🎵 Audio", 3: "🎥 Video",
    4: "👤 Contact", 5: "📍 Location", 9: "📄 Document",
    10: "🎭 Sticker", 13: "GIF", 14: "🚫 Deleted",
    15: "📍 Live location",
}


def _type_label(msg_type: Optional[int], mime: Optional[str]) -> str:
    if msg_type in _TYPE_LABELS:
        return _TYPE_LABELS[msg_type]
    if mime:
        if mime.startswith("image/"): return "📷 Photo"
        if mime.startswith("video/"): return "🎥 Video"
        if mime.startswith("audio/"): return "🎵 Audio"
    return ""


def _format_message(r: sqlite3.Row, names: dict = {}) -> dict:
    media = None
    if r["file_path"]:
        media = {
            "path":     r["file_path"],
            "mime":     r["mime_type"],
            "caption":  r["media_caption"],
            "duration": r["media_duration"],
            "width":    r["width"],
            "height":   r["height"],
        }
    sender_jid = r["sender_jid"]
    if sender_jid:
        sender = names.get(sender_jid) or sender_jid.split("@")[0]
    else:
        sender = None
    return {
        "id":        r["_id"],
        "key_id":    r["key_id"],
        "from_me":   bool(r["from_me"]),
        "timestamp": r["timestamp"],
        "text":      r["text_data"],
        "type":      r["message_type"],
        "status":    r["status"],
        "media":     media,
        "sender":    sender,
    }

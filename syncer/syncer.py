#!/usr/bin/env python3
"""
whatsckup syncer — Decrypts and merges WhatsApp crypt15 backups into a master database.

Designed to run as a k8s CronJob. Reads from a read-only NFS input mount and writes
to a read-write NFS app mount.

Environment variables:
  CRYPT_KEY    — hex string (64 chars) for crypt15 decryption
  INPUT_PATH   — path to input NFS mount (default: /mnt/input)
  APP_PATH     — path to app NFS mount   (default: /mnt/app)
"""

import io
import os
import json
import shutil
import hashlib
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from Whatsapp_Chat_Exporter.android_crypt import decrypt_backup
from Whatsapp_Chat_Exporter.utility import Crypt, DbType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

INPUT_PATH = Path(os.getenv("INPUT_PATH", "/mnt/input"))
APP_PATH   = Path(os.getenv("APP_PATH",   "/mnt/app"))
CRYPT_KEY  = os.getenv("CRYPT_KEY", "")

STATE_FILE      = APP_PATH / ".sync_state.json"
MASTER_MSG_DB   = APP_PATH / "msgstore.db"
MASTER_WA_DB    = APP_PATH / "wa.db"

# Satellite tables that hang off message._id.
# pk:  column that must be unique in master (or None for no-pk tables).
# fks: {column: mapping_name} where mapping_name in {'message','chat','jid'}
SATELLITE_TABLES = {
    "message_media": {
        "pk":  "message_row_id",
        "fks": {"message_row_id": "message", "chat_row_id": "chat"},
    },
    "message_text": {
        "pk":  "message_row_id",
        "fks": {"message_row_id": "message"},
    },
    "message_quoted": {
        "pk":  "message_row_id",
        "fks": {"message_row_id": "message", "chat_row_id": "chat"},
    },
    "message_mentions": {
        "pk":  None,
        "fks": {"message_row_id": "message", "jid_row_id": "jid"},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_hash(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


# ---------------------------------------------------------------------------
# Decryption
# ---------------------------------------------------------------------------

def decrypt_crypt15(src: Path, key_hex: str, dst: Path, db_type: DbType = DbType.MESSAGE):
    data = src.read_bytes()
    key_bytes = bytes.fromhex(key_hex)
    rc = decrypt_backup(data, key_bytes, str(dst), crypt=Crypt.CRYPT15, db_type=db_type)
    if rc != 0:
        raise RuntimeError(f"Decryption of {src.name} failed (code {rc})")
    log.info("Decrypted %s → %s", src.name, dst)


# ---------------------------------------------------------------------------
# Merge: JIDs
# ---------------------------------------------------------------------------

def merge_jids(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict:
    """Returns {src_jid_id: dst_jid_id}"""
    cols   = table_columns(src, "jid")
    non_pk = [c for c in cols if c != "_id"]
    ph     = ", ".join("?" * len(non_pk))

    # Cargar existentes en memoria: raw_string → _id
    existing: dict[str, int] = {
        row[0]: row[1]
        for row in dst.execute("SELECT raw_string, _id FROM jid")
    }

    mapping: dict[int, int] = {}
    to_insert = []

    for row in src.execute(f"SELECT {', '.join(cols)} FROM jid"):
        d = dict(zip(cols, row))
        raw = d["raw_string"]
        if raw in existing:
            mapping[d["_id"]] = existing[raw]
        else:
            to_insert.append((d["_id"], [d[c] for c in non_pk]))

    # Insertar nuevos uno a uno para capturar lastrowid
    for src_id, values in to_insert:
        cur = dst.execute(f"INSERT INTO jid ({', '.join(non_pk)}) VALUES ({ph})", values)
        mapping[src_id] = cur.lastrowid

    dst.commit()
    log.info("JIDs: %d existing, %d new", len(existing), len(to_insert))
    return mapping


# ---------------------------------------------------------------------------
# Merge: Chats
# ---------------------------------------------------------------------------

# Message-ref columns in chat que aún no existen al momento del merge — poner NULL
_CHAT_MSG_REFS = {
    "display_message_row_id", "last_message_row_id", "last_read_message_row_id",
    "last_read_receipt_sent_message_row_id", "last_important_message_row_id",
    "change_number_notified_message_row_id", "last_read_ephemeral_message_row_id",
    "last_message_reaction_row_id", "last_seen_message_reaction_row_id",
    "last_read_message_sort_id", "display_message_sort_id", "last_message_sort_id",
    "last_read_receipt_sent_message_sort_id",
}


def merge_chats(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    jid_map: dict,
) -> dict:
    """Returns {src_chat_id: dst_chat_id}"""
    cols   = table_columns(src, "chat")
    non_pk = [c for c in cols if c != "_id"]

    # Cargar existentes en memoria: jid_row_id → chat_id
    existing: dict[int, int] = {
        row[0]: row[1]
        for row in dst.execute("SELECT jid_row_id, _id FROM chat")
    }

    mapping: dict[int, int] = {}
    to_insert = []

    for row in src.execute(f"SELECT {', '.join(cols)} FROM chat"):
        d = dict(zip(cols, row))
        master_jid_id = jid_map.get(d["jid_row_id"])
        if master_jid_id is None:
            log.warning("Chat %d: jid %d sin mapeo, omitido", d["_id"], d["jid_row_id"])
            continue
        if master_jid_id in existing:
            mapping[d["_id"]] = existing[master_jid_id]
        else:
            values = []
            for c in non_pk:
                if c == "jid_row_id":
                    values.append(master_jid_id)
                elif c in _CHAT_MSG_REFS:
                    values.append(None)
                else:
                    values.append(d[c])
            to_insert.append((d["_id"], values))

    ph = ", ".join("?" * len(non_pk))
    for src_id, values in to_insert:
        cur = dst.execute(f"INSERT INTO chat ({', '.join(non_pk)}) VALUES ({ph})", values)
        mapping[src_id] = cur.lastrowid

    dst.commit()
    log.info("Chats: %d existing, %d new", len(existing), len(to_insert))
    return mapping


# ---------------------------------------------------------------------------
# Merge: Messages
# ---------------------------------------------------------------------------

def merge_messages(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    chat_map: dict,
    jid_map: dict,
) -> dict:
    """Returns {src_msg_id: dst_msg_id} solo para mensajes nuevos."""
    cols   = table_columns(src, "message")
    non_pk = [c for c in cols if c != "_id"]
    ph     = ", ".join("?" * len(non_pk))

    # Cargar claves existentes en memoria — evita N queries individuales
    existing_keys: set[str] = {
        row[0] for row in dst.execute(
            "SELECT key_id FROM message WHERE key_id IS NOT NULL AND key_id != '-1'"
        )
    }
    # Para mensajes sistema (key_id='-1'): dedup por (chat_row_id, timestamp, from_me)
    existing_system: set[tuple] = {
        (row[0], row[1], row[2]) for row in dst.execute(
            "SELECT chat_row_id, timestamp, from_me FROM message "
            "WHERE key_id = '-1' OR key_id IS NULL"
        )
    }

    # Configurar WAL y cache para escrituras rápidas
    dst.execute("PRAGMA journal_mode = WAL")
    dst.execute("PRAGMA synchronous = NORMAL")
    dst.execute("PRAGMA cache_size = -64000")  # 64 MB

    new_map: dict[int, int] = {}
    to_insert: list[tuple] = []   # (src_id, [values])
    skip_count = 0

    for row in src.execute(f"SELECT {', '.join(cols)} FROM message ORDER BY _id"):
        d = dict(zip(cols, row))
        master_chat_id = chat_map.get(d["chat_row_id"])
        if master_chat_id is None:
            skip_count += 1
            continue

        key_id = d.get("key_id")
        if key_id and key_id != "-1":
            if key_id in existing_keys:
                skip_count += 1
                continue
        else:
            sig = (master_chat_id, d.get("timestamp"), d.get("from_me"))
            if sig in existing_system:
                skip_count += 1
                continue

        values = []
        for c in non_pk:
            if c == "chat_row_id":
                values.append(master_chat_id)
            elif c == "sender_jid_row_id":
                v = d.get("sender_jid_row_id")
                values.append(jid_map.get(v) if v else None)
            else:
                values.append(d[c])

        to_insert.append((d["_id"], values))

    # Insertar en lote dentro de una transacción
    log.info("Messages: %d new to insert, %d skipped", len(to_insert), skip_count)
    dst.execute("BEGIN")
    for src_id, values in to_insert:
        cur = dst.execute(
            f"INSERT INTO message ({', '.join(non_pk)}) VALUES ({ph})", values
        )
        new_map[src_id] = cur.lastrowid
    dst.execute("COMMIT")

    log.info("Messages: insert complete")
    return new_map


# ---------------------------------------------------------------------------
# Merge: Satellite tables
# ---------------------------------------------------------------------------

def merge_satellite(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    config: dict,
    maps: dict,
):
    if not table_exists(src, table) or not table_exists(dst, table):
        return

    message_map = maps.get("message", {})
    if not message_map:
        return

    cols = table_columns(src, table)
    pk   = config["pk"]
    fks  = config["fks"]
    ph   = ", ".join("?" * len(cols))
    inserted = 0

    for row in src.execute(f"SELECT {', '.join(cols)} FROM {table}"):
        d = dict(zip(cols, row))

        # Only process rows whose message was newly inserted
        msg_row_id = d.get("message_row_id")
        if msg_row_id not in message_map:
            continue

        # Remap FK columns
        new_d: dict = {}
        skip = False
        for c in cols:
            if c in fks:
                m = maps.get(fks[c], {})
                new_val = m.get(d[c])
                if new_val is None and d[c] is not None:
                    skip = True
                    break
                new_d[c] = new_val
            else:
                new_d[c] = d[c]

        if skip:
            continue

        try:
            dst.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({ph})",
                [new_d[c] for c in cols],
            )
            inserted += 1
        except sqlite3.Error as e:
            log.debug("Insert error in %s: %s", table, e)

    dst.commit()
    if inserted:
        log.info("%s: %d rows inserted", table, inserted)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def merge_msgstore(new_db: Path, master_db: Path):
    if not master_db.exists():
        log.info("No master DB yet — copying as initial database")
        shutil.copy2(new_db, master_db)
        return

    src = sqlite3.connect(new_db)
    dst = sqlite3.connect(master_db)
    try:
        jid_map  = merge_jids(src, dst)
        chat_map = merge_chats(src, dst, jid_map)
        msg_map  = merge_messages(src, dst, chat_map, jid_map)
        maps     = {"jid": jid_map, "chat": chat_map, "message": msg_map}
        for table, cfg in SATELLITE_TABLES.items():
            merge_satellite(src, dst, table, cfg, maps)
    finally:
        src.close()
        dst.close()


def run():
    if not CRYPT_KEY:
        raise ValueError("CRYPT_KEY environment variable is required")

    APP_PATH.mkdir(parents=True, exist_ok=True)

    state   = load_state()
    changed = False

    # --- msgstore ---
    msg_crypt = INPUT_PATH / "msgstore.db.crypt15"
    if msg_crypt.exists():
        h = file_hash(msg_crypt)
        if h != state.get("msgstore_hash"):
            log.info("msgstore.db.crypt15 changed — syncing")
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                decrypt_crypt15(msg_crypt, CRYPT_KEY, tmp_path, DbType.MESSAGE)
                merge_msgstore(tmp_path, MASTER_MSG_DB)
                state["msgstore_hash"] = h
                changed = True
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            log.info("msgstore.db.crypt15 unchanged")
    else:
        log.warning("msgstore.db.crypt15 not found at %s", msg_crypt)

    # --- wa.db (contacts) — overwrite, no merge needed ---
    wa_crypt = INPUT_PATH / "wa.db.crypt15"
    if wa_crypt.exists():
        h = file_hash(wa_crypt)
        if h != state.get("wa_hash"):
            log.info("wa.db.crypt15 changed — decrypting")
            decrypt_crypt15(wa_crypt, CRYPT_KEY, MASTER_WA_DB, DbType.CONTACT)
            state["wa_hash"] = h
            changed = True
        else:
            log.info("wa.db.crypt15 unchanged")

    if changed:
        save_state(state)

    log.info("Sync complete")


if __name__ == "__main__":
    run()

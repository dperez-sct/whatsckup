# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**whatsckup** is a self-hosted WhatsApp backup viewer. It decrypts Android crypt15 backups, merges them into a master SQLite database (append-only, device-change-resilient), and exposes a React SPA for browsing chats, messages, and media.

## Development commands

### Full stack with Docker Compose
```bash
# Place decrypted DBs in app_data/ first (or run syncer manually below)
docker compose up -d
# Frontend: http://localhost  Backend: http://localhost:8000
```

### Manual (no Docker)

**Syncer** — decrypt + merge a crypt15 backup:
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r syncer/requirements.txt
INPUT_PATH=. APP_PATH=./app_data CRYPT_KEY=<64-char-hex> python syncer/syncer.py
```

**Backend** — FastAPI, read-only DB access:
```bash
pip install -r backend/requirements.txt
APP_PATH=./app_data MEDIA_PATH=./app_data CONTACTS_VCF=./contacts.vcf \
  uvicorn backend.main:app --reload --port 8000
```

**Frontend** — React/Vite dev server with proxy to backend:
```bash
cd frontend && npm install && npm run dev
# http://localhost:5173
```

### Build & publish Docker images
```bash
docker build -t ghcr.io/USUARIO/whatsckup-syncer:TAG   ./syncer
docker build -t ghcr.io/USUARIO/whatsckup-backend:TAG  ./backend
docker build -t ghcr.io/USUARIO/whatsckup-frontend:TAG ./frontend
```

## Architecture

Three independent components share data via a filesystem (NFS in k8s, `app_data/` locally):

```
[Android phone]  →  NFS upload (crypt15 files + Media/ + contacts.vcf)
                           │
                     syncer (CronJob)
                           │  decrypts + merges
                           ▼
                     NFS data: msgstore.db (master), wa.db, .sync_state.json
                           │
              ┌────────────┴────────────┐
         backend (FastAPI)         frontend (React + nginx)
         reads msgstore.db          proxies /api and /media
         serves /media/* from        to backend:8000
         NFS upload directly
```

**syncer** (`syncer/syncer.py`) — runs once per invocation (not a daemon). Checks `.sync_state.json` hashes to skip unchanged files. Merges are append-only: messages are deduplicated by `key_id` (or `chat+timestamp+from_me` for system messages with `key_id='-1'`). FK remapping handles device changes: source local IDs are remapped to master IDs for `jid`, `chat`, and `message` tables before satellite tables (`message_media`, `message_text`, `message_quoted`, `message_mentions`) are inserted.

**backend** (`backend/main.py`) — FastAPI app with a background `_db_watcher` task that polls `msgstore.db` MD5 every `DB_CHECK_INTERVAL` seconds and increments `_db_state["version"]`. Opens DB read-only on every request (`sqlite3.connect("file:...?mode=ro", uri=True)`). Contact names are resolved at query time: VCF > wa.db, with LID-to-phone mapping via `jid_map` table.

**frontend** (`frontend/src/`) — single `App.jsx` holds `selectedChat` state and renders `ChatList` (sidebar) + `MessageView` (main). `MessageView` hosts the date filter and opens `MediaGallery` as an overlay. All API calls go through `src/api.js`. In dev, Vite proxies `/api` and `/media` to `localhost:8000`; in production nginx handles the proxy.

## Key data flows

- **Contact name resolution**: phone numbers from VCF are normalized (Spanish 9-digit → prepend `34`), then matched against `jid.user` in msgstore. LIDs (`@lid` JIDs) are resolved via `jid_map` → `jid` join.
- **Media serving**: `message_media.file_path` stores relative paths like `Media/WhatsApp Images/xxx.jpg`. The backend serves them from `MEDIA_PATH/{file_path}`. In k8s `MEDIA_PATH=/mnt/input` points to the upload NFS where media already lives — no copying.
- **Pagination**: messages API returns newest-first (`ORDER BY timestamp DESC`). The frontend loads older messages by incrementing `offset` and prepends them without losing scroll position.
- **DB version polling**: frontend can poll `/api/status` to detect when syncer has written new data (`version` increments).

## Kubernetes deployment

Manifests are in `k8s/`. `k8s/secret.yaml` is not in git — store it separately. Apply order matters:
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml   # not in git
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/volumes.yaml
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/frontend.yaml
kubectl apply -f k8s/cronjob.yaml
kubectl apply -f k8s/ingress.yaml
```

Trigger a manual sync:
```bash
kubectl create job --from=cronjob/whatsckup-syncer first-sync -n whats
kubectl logs -f job/first-sync -n whats
```

All data is in NFS volumes; the cluster is stateless and can be fully recreated without data loss.

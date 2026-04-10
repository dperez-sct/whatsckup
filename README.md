# whatsckup

Self-hosted WhatsApp backup viewer. Descifra, mergea y visualiza tus conversaciones de WhatsApp en una aplicación web propia.

## Características

- Descifrado de backups Android (formato `crypt15`)
- Merge incremental y append-only: nunca se borran mensajes aunque se eliminen en el origen
- Robusto ante cambio de móvil (merge por claves naturales, no por IDs locales)
- Procesamiento eficiente: 273K mensajes mergeados en ~3 segundos
- Visualización web con dark theme: lista de chats, mensajes, fotos, vídeos y audios inline
- Nombres de contactos desde VCF exportado del móvil (con resolución de LIDs de WhatsApp)
- Nombres de sender en mensajes de grupo
- Galería multimedia por chat con tabs (Imágenes / Vídeos / Audios / Docs), lightbox y salto al mensaje
- Filtro de fechas en la vista de mensajes
- Watcher de base de datos: detecta cambios automáticamente cada X segundos
- Despliegue en Kubernetes con NFS

## Imágenes Docker

```
ghcr.io/dperez-sct/whatsckup-syncer:dev2
ghcr.io/dperez-sct/whatsckup-backend:dev8
ghcr.io/dperez-sct/whatsckup-frontend:dev8
```

## Arquitectura

```
[Móvil Android]
      │  Syncthing / SFTP / ADB WiFi
      ▼
[NFS upload]  <NFS_SERVER>:<UPLOAD_PATH>
  msgstore.db.crypt15
  wa.db.crypt15
  contacts.vcf
  Media/
    WhatsApp Images/
    WhatsApp Audio/
    WhatsApp Video/
    ...
      │
      │  syncer (CronJob en k8s)
      ▼
[NFS data]  <NFS_SERVER>:<DATA_PATH>
  msgstore.db        ← master mergeado (syncer escribe)
  wa.db              ← contactos
  .sync_state.json   ← hashes para detectar cambios
      │
      ├── backend (FastAPI)  ──→  /api/chats
      │   monta: NFS data (/mnt/app)       /api/chats/{id}/messages
      │           NFS upload (/mnt/input)  /api/chats/{id}/media
      │                                    /media/{file_path}  (desde /mnt/input)
      │                                    /api/status, /health
      └── frontend (React/nginx) ──→  <tu-dominio> (HTTPS)
```

## Estructura del repositorio

```
whatsckup/
├── syncer/          Proceso de descifrado y merge (Python)
├── backend/         API REST (FastAPI)
├── frontend/        SPA (React + Vite, servida por nginx)
├── k8s/             Manifiestos Kubernetes
├── docker-compose.yml
└── README.md
```

---

## Desarrollo local

### Requisitos

- Python 3.12+
- Node.js 20+
- Docker + Docker Compose
- Clave hex crypt15 del backup Android

### Arranque rápido con Docker Compose

```bash
# Estructura de directorios esperada:
#   app_data/      → DB maestra (msgstore.db, wa.db) — generada por el syncer
#   media_input/   → ficheros multimedia del móvil (Media/WhatsApp Images/…)
#   contacts.vcf   → exportado desde la app Contactos del móvil

docker compose up -d

# Frontend: http://localhost
# Backend:  http://localhost:8000
```

### Arranque manual (sin Docker)

```bash
# Syncer (primera vez o cuando llegue un backup nuevo)
python3 -m venv .venv && source .venv/bin/activate
pip install -r syncer/requirements.txt
INPUT_PATH=. APP_PATH=./app_data CRYPT_KEY=<tu_clave_hex> python syncer/syncer.py

# Backend
pip install -r backend/requirements.txt
APP_PATH=./app_data MEDIA_PATH=./media_input CONTACTS_VCF=./contacts.vcf \
  uvicorn backend.main:app --reload --port 8000

# Frontend
cd frontend && npm install && npm run dev
# Disponible en http://localhost:5173 (proxy automático a backend en :8000)
```

---

## Syncer

Descifra los ficheros `.crypt15` y mergea la base de datos de mensajes. **No copia media** — los ficheros multimedia se sirven directamente desde el NFS de upload.

### Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `CRYPT_KEY` | — | Clave hex de 64 chars (obligatoria) |
| `INPUT_PATH` | `/mnt/input` | Carpeta con los ficheros del móvil |
| `APP_PATH` | `/mnt/app` | Carpeta de datos de la app (master DB) |

### Estrategia de merge

El merge es **append-only**: nunca se eliminan datos del master aunque se hayan borrado en el origen.

| Tabla | Clave natural para deduplicar |
|---|---|
| `jid` | `raw_string` (ej. `34612345678@s.whatsapp.net`) |
| `chat` | `jid_row_id` mapeado al master |
| `message` | `key_id` (UUID asignado por WhatsApp) |
| `message` (sistema) | `(chat, timestamp, from_me)` cuando `key_id = '-1'` |
| Tablas satélite | Solo para mensajes nuevos, con remapeo de FKs |

Este diseño es **robusto ante cambios de móvil**: los IDs locales se remapean correctamente.

---

## Backend

API REST en FastAPI. Lee la base de datos en modo read-only.

### Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `APP_PATH` | `/mnt/app` | Carpeta raíz de datos (DB) |
| `MEDIA_PATH` | `APP_PATH` | Raíz desde donde se sirven los ficheros multimedia |
| `DB_CHECK_INTERVAL` | `60` | Segundos entre comprobaciones de cambio en el DB |
| `CONTACTS_VCF` | `APP_PATH/contacts.vcf` | Ruta al fichero VCF de contactos |

### Nombres de contactos (VCF)

El backend carga automáticamente un fichero VCF. Los números se normalizan (prefijo de país a números locales de 9 dígitos) y se cruzan con los JIDs de la base de datos. También resuelve JIDs de tipo `@lid` (identificadores internos de WhatsApp) a través de la tabla `jid_map`.

Para generarlo: en Android, exporta los contactos desde la app Contactos → Exportar → `.vcf`.

### Multimedia

Los ficheros de media se sirven directamente desde `MEDIA_PATH/{file_path}`, donde `file_path` (almacenado en `message_media`) tiene el formato `Media/WhatsApp Images/xxx.jpg`.

En k8s, `MEDIA_PATH=/mnt/input` apunta al NFS de upload donde está la carpeta `Media/`.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/chats` | Lista de chats ordenados por actividad |
| `GET` | `/api/chats/{id}/messages` | Mensajes paginados (`limit`, `offset`, `after`, `before` en ms) |
| `GET` | `/api/chats/{id}/media` | Media paginada (`limit`, `offset`, `kind`=image/video/audio/document) |
| `GET` | `/media/{path}` | Sirve archivos multimedia |
| `GET` | `/api/status` | Versión actual del DB |
| `GET` | `/health` | Estado del servicio y del watcher |

---

## Frontend

SPA en React 18 + Vite, servida por nginx en producción.

- Dark theme inspirado en WhatsApp
- Lista de chats con búsqueda por nombre
- Vista de mensajes: más recientes abajo, scroll automático al fondo al abrir
- Botón "Cargar mensajes anteriores" al principio — los mensajes antiguos aparecen arriba sin perder la posición de scroll
- Fecha alineada a la izquierda en mensajes recibidos, derecha en enviados
- Nombre del sender en mensajes de grupo (azul, encima del bubble)
- Filtro de fechas (Desde/Hasta) en la cabecera del chat
- Media inline: fotos, vídeos, audios, documentos
- **Galería multimedia** (botón 🖼 en la cabecera):
  - Tabs: Imágenes · Vídeos · Audios · Docs
  - Grid 3 columnas para imágenes y vídeos con lightbox
  - Lightbox: navegación ← →, teclas de teclado, Escape para cerrar
  - Botón "Ir al mensaje →": salta al mensaje concreto en el chat con flash verde

En desarrollo, el proxy de Vite redirige `/api` y `/media` al backend en `localhost:8000`.
En producción (k8s), nginx hace el proxy internamente al servicio `whatsckup-backend:8000`.

---

## Despliegue en Kubernetes

### 1. Preparación

Edita `k8s/volumes.yaml` con los datos de tu NFS:
- `<NFS_SERVER>` → IP de tu servidor NFS
- `<UPLOAD_PATH>` → export donde el móvil sube los ficheros (crypt15 + Media/ + contacts.vcf)
- `<DATA_PATH>` → export para la DB maestra

Edita `k8s/ingress.yaml` con tu dominio.

Crea el secret con tu clave crypt15 (**no subir a git**, está en `.gitignore`):

```bash
# Genera el valor base64
echo -n "<tu_clave_hex_64_chars>" | base64

# Edita k8s/secret.yaml con el valor anterior
```

Despliega la configuración base:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/volumes.yaml
```

### 2. Despliegue

```bash
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/frontend.yaml
kubectl apply -f k8s/cronjob.yaml
kubectl apply -f k8s/ingress.yaml
```

### 3. Primer sync manual

```bash
kubectl create job --from=cronjob/whatsckup-syncer first-sync -n whats
kubectl logs -f job/first-sync -n whats
```

### Schedule del CronJob

Edita `schedule` en `k8s/cronjob.yaml`:

| Schedule | Significado |
|---|---|
| `0 * * * *` | Cada hora |
| `0 */6 * * *` | Cada 6 horas |
| `0 2 * * *` | Una vez al día a las 2:00 |

### Recuperación ante pérdida del clúster

Los datos residen en los NFS, no en el clúster. Si pierdes k8s pero conservas los volúmenes NFS, basta con redesplegar en orden:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml      # ⚠️ guárdalo fuera del repo (no está en git)
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/volumes.yaml
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/frontend.yaml
kubectl apply -f k8s/cronjob.yaml
kubectl apply -f k8s/ingress.yaml
```

Los PVs apuntan a los mismos paths NFS, los PVCs se bindean igual y la app arranca donde lo dejó. El único fichero imprescindible que **no está en git** es `k8s/secret.yaml` — guárdalo en un gestor de contraseñas o bóveda segura.

---

## Construir y publicar imágenes

```bash
# Build
docker build -t ghcr.io/USUARIO/whatsckup-syncer:TAG   ./syncer
docker build -t ghcr.io/USUARIO/whatsckup-backend:TAG  ./backend
docker build -t ghcr.io/USUARIO/whatsckup-frontend:TAG ./frontend

# Push
docker push ghcr.io/USUARIO/whatsckup-syncer:TAG
docker push ghcr.io/USUARIO/whatsckup-backend:TAG
docker push ghcr.io/USUARIO/whatsckup-frontend:TAG
```

Requiere login: `echo TOKEN | docker login ghcr.io -u USUARIO --password-stdin`

---

## Componentes

| Componente | Imagen base | Puerto | Rol |
|---|---|---|---|
| syncer | python:3.12-slim | — | CronJob: descifra y mergea |
| backend | python:3.12-slim | 8000 | API REST + watcher DB |
| frontend | node:20-alpine → nginx:alpine | 80 | SPA + proxy nginx |

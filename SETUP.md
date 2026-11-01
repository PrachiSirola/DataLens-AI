# DataLensAI — Local Setup Guide

Everything runs on your machine. No paid APIs, no cloud subscriptions, no telemetry.

---

## Prerequisites

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 8 GB | 16 GB |
| Disk | 5 GB free | 15 GB free |
| OS | Ubuntu 20.04 / macOS 12 / Windows 10+ | Latest LTS |
| Python | 3.11+ | 3.12 |
| Node.js | 18+ | 20 LTS |
| MongoDB | 6.0+ Community | 7.0 Community |

No GPU required. CPU inference via Ollama works well with AVX2+ instruction sets (any modern Intel/AMD processor).

---

## 1. Install MongoDB Community Edition

### Ubuntu/Debian
```bash
# Import the public key
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
  sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor

# Add repo (Ubuntu 22.04 example — adjust for your release)
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] \
  https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

sudo apt update && sudo apt install -y mongodb-org
sudo systemctl start mongod
sudo systemctl enable mongod
```

### macOS
```bash
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0
```

### Windows
Download the MSI installer from https://www.mongodb.com/try/download/community and install as a service.

### Verify
```bash
mongosh --eval "db.runCommand({ ping: 1 })"
```

---

## 2. Install Ollama

### Linux
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### macOS
```bash
brew install ollama
```

### Windows
Download from https://ollama.com/download/windows

### Start the Ollama server
```bash
ollama serve
# (runs on http://localhost:11434 by default)
```

### Pull the recommended model

**For machines with 8 GB RAM** (1 GB download, ~2 GB at runtime):
```bash
ollama pull qwen2.5-coder:1.5b
```

**For machines with 16+ GB RAM** (better accuracy, 4.7 GB download):
```bash
ollama pull qwen2.5-coder:7b
```
Then update `OLLAMA_MODEL=qwen2.5-coder:7b` in your `.env`.

**For machines with 32+ GB RAM** (best accuracy, ~9 GB download):
```bash
ollama pull qwen2.5-coder:14b
```

### Verify
```bash
ollama run qwen2.5-coder:1.5b "SELECT 1+1"
# Should output: SELECT 1+1 (or a SQL query)
```

---

## 3. Set up the backend

```bash
cd backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Create your .env from the example
cp .env.example .env
```

### Configure `.env`

Edit `.env` with your values:

```env
MONGODB_URI=mongodb://localhost:27017
DATABASE_NAME=datalensai
SESSION_SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
SESSION_EXPIRE_HOURS=24
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=<strong password>
ADMIN_NAME=Admin
INITIAL_INVITE_CODE=<your secret invite code>
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=http://localhost:5173
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:1.5b
```

### Start the backend
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The first startup will:
- Connect to MongoDB and create indexes
- Create the admin user from your `.env` credentials
- Create the initial invitation code

---

## 4. Set up the frontend

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

Open http://localhost:5173

### For production build
```bash
npm run build
# Serve the dist/ folder with any static file server
```

---

## 5. First use

1. Open http://localhost:5173 in your browser
2. Click **Register** and enter the invitation code from your `.env` (`INITIAL_INVITE_CODE`)
3. Fill in your name, email, and password
4. Log in and start uploading CSV/PDF/DOCX files
5. Ask natural language questions about your data

### Admin panel

Log in with the admin credentials from `.env` to access `/admin/invitations` where you can create, list, and revoke invitation codes for other users.

---

## How it works (offline)

```
User question (natural language)
    │
    ▼
┌─────────────┐     ┌──────────────┐
│ Ollama LLM  │────▶│ DuckDB SQL   │
│ (local CPU) │     │ (validated)  │
└─────────────┘     └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   DuckDB     │  ← All computation happens here
                    │  (per-user)  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Ollama LLM   │  ← Summarises DuckDB results
                    │ (formatting) │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   Response   │  ← Answer + chart + data table
                    └──────────────┘
```

- The LLM **only** translates questions to SQL and summarises results
- **All calculations are done by DuckDB** — the LLM never computes numbers
- If Ollama is down, a pattern-matching fallback generates basic SQL for common queries (count, average, sum, min/max, show rows)
- MongoDB stores users, sessions, invitation codes, and dataset metadata
- Each user gets an isolated DuckDB database and upload directory

---

## Model recommendations by hardware

| RAM | Model | Download | Quality |
|-----|-------|----------|---------|
| 8 GB | `qwen2.5-coder:1.5b` | ~1 GB | Good for simple queries |
| 16 GB | `qwen2.5-coder:7b` | ~4.7 GB | Good for complex SQL |
| 32 GB | `qwen2.5-coder:14b` | ~9 GB | Excellent accuracy |
| 32 GB + GPU | `qwen2.5-coder:32b` | ~20 GB | Best quality, fast |

All models run via Ollama using GGUF quantization (Q4_K_M). Change the model in `.env`:
```
OLLAMA_MODEL=qwen2.5-coder:7b
```

No restart needed — the next query picks up the new model.

---

## Running tests

```bash
cd backend
pip install pytest pytest-asyncio mongomock-motor
python -m pytest tests/ -v
```

Tests use `mongomock_motor` (in-memory MongoDB mock) and don't require a running MongoDB or Ollama server.

---

## Troubleshooting

**"Connection refused" on queries**: Make sure Ollama is running (`ollama serve`). The app still works without it using the fallback SQL generator, but with limited query understanding.

**Slow first query**: Ollama loads the model into RAM on the first request. Subsequent queries are much faster. On 8 GB RAM with the 1.5B model, expect 2–5 seconds per query.

**"No datasets uploaded"**: Upload a CSV, PDF, or DOCX file first. The file is parsed and loaded into your personal DuckDB database.

**MongoDB connection error**: Verify MongoDB is running: `mongosh --eval "db.runCommand({ ping: 1 })"`.

**Out of memory**: Switch to a smaller model in `.env` or close other applications. The 1.5B model uses ~2 GB RAM at runtime.

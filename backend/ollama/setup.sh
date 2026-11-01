#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# DataLensAI — Build the custom datalens-sql Ollama model
#
# Prerequisites:
#   - Ollama installed and running (systemctl status ollama)
#   - qwen2.5:7b already pulled (ollama pull qwen2.5:7b)
#
# Usage:
#   cd backend/ollama
#   chmod +x setup.sh
#   ./setup.sh
# ──────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELFILE="$SCRIPT_DIR/Modelfile"
MODEL_NAME="datalens-sql"

echo "══════════════════════════════════════════════════════"
echo "  DataLensAI — Custom Ollama Model Setup"
echo "══════════════════════════════════════════════════════"

# 1. Check Ollama is running
if ! command -v ollama &> /dev/null; then
    echo "✗ Ollama not found. Install it first:"
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
    exit 1
fi

if ! ollama list &> /dev/null 2>&1; then
    echo "✗ Ollama service is not running. Start it:"
    echo "  sudo systemctl start ollama"
    exit 1
fi
echo "✓ Ollama is running"

# 2. Check base model exists
if ! ollama list | grep -q "qwen2.5:7b"; then
    echo "⏳ Base model qwen2.5:7b not found. Pulling now..."
    ollama pull qwen2.5:7b
fi
echo "✓ Base model qwen2.5:7b available"

# 3. Build custom model
echo ""
echo "⏳ Building '$MODEL_NAME' from Modelfile..."
ollama create "$MODEL_NAME" -f "$MODELFILE"
echo "✓ Model '$MODEL_NAME' created successfully"

# 4. Quick verification
echo ""
echo "⏳ Running quick test..."
RESULT=$(ollama run "$MODEL_NAME" "SELECT 1 AS test" 2>&1 || true)
echo "  Test output: $RESULT"

# 5. Show model info
echo ""
echo "══════════════════════════════════════════════════════"
echo "  ✓ Setup complete!"
echo ""
echo "  Model name:  $MODEL_NAME"
echo "  Base:        qwen2.5:7b (~4.5 GB)"
echo "  RAM usage:   ~5.5 GB (same as qwen2.5:7b)"
echo ""
echo "  Your .env already has:  OLLAMA_MODEL=datalens-sql"
echo "  Restart the FastAPI server to use the new model."
echo "══════════════════════════════════════════════════════"

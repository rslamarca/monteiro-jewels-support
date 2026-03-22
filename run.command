#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Monteiro Jewels — Support Agent Dashboard
# Clique duplo neste arquivo no Finder para iniciar o dashboard
# ─────────────────────────────────────────────────────────────────

# Move para o diretório deste script (independente de onde está)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo ""
echo "  ✦  Monteiro Jewels — Support Agent"
echo "  ─────────────────────────────────────"
echo ""

# Verifica Python 3
if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 não encontrado."
    echo "     Instale em: https://python.org/downloads"
    read -p "Pressione Enter para fechar..."
    exit 1
fi

echo "  ✓ Python $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── Para qualquer servidor antigo na porta 8000 ──────────────────
echo "  → Verificando servidor anterior..."
OLD_PID=$(lsof -ti tcp:8000 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "  → Encerrando servidor anterior (PID $OLD_PID)..."
    kill -9 $OLD_PID 2>/dev/null
    sleep 1
    echo "  ✓ Servidor anterior encerrado"
else
    echo "  ✓ Porta 8000 livre"
fi

# Instala dependências (inclui certifi para SSL no macOS)
echo "  → Instalando dependências..."
pip3 install -r requirements.txt -q 2>/dev/null \
    || python3 -m pip install -r requirements.txt -q 2>/dev/null \
    || echo "  ⚠  Não foi possível instalar todas as deps."

echo "  ✓ Dependências OK"

# ── Corrige certificados SSL do macOS (fix para CERTIFICATE_VERIFY_FAILED) ──
echo "  → Verificando certificados SSL..."
CERT_CMD=$(find /Applications/Python* -name "Install Certificates.command" 2>/dev/null | head -1)
if [ -n "$CERT_CMD" ]; then
    bash "$CERT_CMD" > /dev/null 2>&1
    echo "  ✓ Certificados SSL atualizados"
else
    # Fallback: configura certifi via variável de ambiente
    SSL_CERT=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null)
    if [ -n "$SSL_CERT" ]; then
        export SSL_CERT_FILE="$SSL_CERT"
        export REQUESTS_CA_BUNDLE="$SSL_CERT"
        echo "  ✓ SSL via certifi: $SSL_CERT"
    else
        echo "  ⚠  certifi não encontrado — SSL pode falhar"
    fi
fi
echo ""

# Abre o browser após 2 segundos
(sleep 2 && open "http://localhost:8000") &

echo "  → Iniciando servidor om http://localhost:8000"
echo "  → Pressione Ctrl+C para parar"
echo ""

# Inicia o servidor
python3 main.py

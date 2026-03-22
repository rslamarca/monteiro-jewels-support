#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Monteiro Jewels — Support Agent Dashboard
# Script de inicialização
# ─────────────────────────────────────────────────────────────────

set -e

echo ""
echo "  ✦  Monteiro Jewels — Support Agent"
echo "  ─────────────────────────────────────"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 não encontrado. Instale em https://python.org"
    exit 1
fi

echo "  ✓ Python $(python3 --version 2>&1 | cut -d' ' -f2)"

# Check .env
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo ""
        echo "  ⚠  Arquivo .env não encontrado."
        echo "     Copiando .env.example → .env"
        echo "     EDITE o arquivo .env com suas credenciais antes de usar!"
        echo ""
        cp .env.example .env
    else
        echo "  ✗ Arquivo .env não encontrado."
        exit 1
    fi
fi

# Check Gmail credentials
if [ ! -f "gmail_credentials.json" ]; then
    echo ""
    echo "  ⚠  gmail_credentials.json não encontrado."
    echo "     O Gmail não funcionará até você configurar o OAuth2."
    echo "     Veja o README.md para instruções."
    echo ""
fi

# Install dependencies
echo "  → Instalando dependências..."
pip3 install -r requirements.txt -q 2>/dev/null || pip install -r requirements.txt -q

echo "  ✓ Dependências instaladas"
echo ""
echo "  → Iniciando servidor..."
echo "  → Acesse: http://localhost:8000"
echo ""

# Start
python3 main.py

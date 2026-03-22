# Monteiro Jewels — Support Agent Dashboard

Agente semi-autônomo de suporte ao cliente que lê emails, consulta dados na Shopify e gera respostas para aprovação humana.

## Arquitetura

```
Frontend (React)  ←→  Backend (FastAPI)  ←→  Gmail API + Shopify API
                            ↕
                    SQLite (histórico)
```

## Requisitos

- Python 3.10+
- Conta Gmail com API habilitada
- Loja Shopify com Custom App

## Instalação Rápida

```bash
cd monteiro-support-app
cp .env.example .env        # edite com suas credenciais
chmod +x start.sh
./start.sh
```

Acesse: **http://localhost:8000**

## Configuração Detalhada

### 1. Shopify API

1. Acesse `rachap-8j.myshopify.com/admin/settings/apps`
2. Clique em **Develop apps** → **Create an app**
3. Nomeie: "Support Agent"
4. Em **API Scopes**, ative:
   - `read_orders`
   - `read_products`
   - `read_customers`
   - `read_content` (para políticas)
5. Instale o app e copie o **Admin API access token**
6. Cole no `.env` em `SHOPIFY_ACCESS_TOKEN`

### 2. Gmail API (OAuth2)

1. Acesse [Google Cloud Console](https://console.cloud.google.com)
2. Crie um projeto ou selecione existente
3. Ative a **Gmail API** em "APIs & Services"
4. Configure a **OAuth consent screen** (tipo: External)
5. Crie **OAuth 2.0 credentials** (tipo: Desktop App)
6. Baixe o JSON e salve como `gmail_credentials.json` na pasta do app
7. Na primeira execução, uma janela do navegador abrirá para autorizar

### 3. Variáveis de Ambiente (.env)

```
SHOPIFY_STORE=rachap-8j.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_xxxxx
GMAIL_CLIENT_ID=xxxxx.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=xxxxx
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_URL=sqlite:///./support.db
```

## Como Usar

### Fluxo Principal

1. Clique em **"Buscar Emails"** no dashboard (ou aguarde o refresh automático)
2. O agente lê emails novos, classifica, consulta a Shopify e gera rascunhos
3. Revise cada rascunho na interface
4. **Aprovar** → marca como aprovado
5. **Editar** → modifique o texto e salve
6. **Enviar** → envia a resposta via Gmail
7. **Descartar** → marca como rejeitado

### Categorias Automáticas

| Categoria | Descrição |
|-----------|-----------|
| STATUS_PEDIDO | Onde está meu pedido, rastreamento |
| TROCA_DEVOLUCAO | Trocas, devoluções, reembolsos |
| DUVIDA_PRODUTO | Perguntas sobre produtos |
| CANCELAMENTO | Cancelamento de pedidos |
| PROBLEMA_PEDIDO | Produto errado, defeituoso, faltando |

### API Endpoints

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | /api/tickets | Listar tickets |
| GET | /api/tickets/{id} | Detalhe do ticket |
| PUT | /api/tickets/{id} | Atualizar ticket |
| POST | /api/tickets/{id}/send | Enviar resposta |
| POST | /api/fetch-emails | Buscar novos emails |
| GET | /api/stats | Estatísticas |
| GET | /api/shopify/orders?search= | Buscar pedidos |
| GET | /api/shopify/products?search= | Buscar produtos |
| GET | /api/shopify/policies | Políticas da loja |

## Banco de Dados

SQLite local (`support.db`) criado automaticamente. Armazena:
- Tickets de suporte (emails, classificação, dados Shopify, respostas)
- Logs de ações (criação, classificação, aprovação, envio)

## Estrutura do Projeto

```
monteiro-support-app/
├── main.py              # FastAPI backend + rotas
├── models.py            # Modelos SQLAlchemy
├── database.py          # Configuração do banco
├── shopify_client.py    # Cliente da API Shopify
├── gmail_client.py      # Cliente da API Gmail
├── requirements.txt     # Dependências Python
├── .env.example         # Template de variáveis
├── start.sh             # Script de inicialização
├── static/
│   └── index.html       # Frontend React (SPA)
└── README.md
```

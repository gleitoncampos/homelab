#!/bin/bash
#set -euo pipefail

# ============================
# Configurações
# ============================
DB_PATH="${1:-./portainer.db}"
NEW_ID=$(docker info --format '{{.Swarm.Cluster.ID}}' 2>/dev/null || true)
IMAGE_NAME="portainer-db-patcher"

if [[ -z "$NEW_ID" ]]; then
  echo "❌ Swarm ID não encontrado. O swarm foi iniciado?"
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "❌ Arquivo $DB_PATH não encontrado!"
  exit 1
fi

WORKDIR=$(dirname "$(realpath "$DB_PATH")")

# ============================
# Dockerfile (builda binário Go existente)
# ============================
cat > "$WORKDIR/Dockerfile.patcher" <<'EOF'
FROM golang:1.23 AS builder

WORKDIR /app
RUN go mod init patchportainer && \
    go get go.etcd.io/bbolt@v1.3.7

COPY patch_swarmid.go .
RUN go build -o patch_swarmid patch_swarmid.go

FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates grep && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY --from=builder /app/patch_swarmid /usr/local/bin/patch_swarmid
ENTRYPOINT ["/usr/local/bin/patch_swarmid"]
EOF

# ============================
# Build da imagem
# ============================
echo ">>> Buildando a imagem docker"
docker build -t "$IMAGE_NAME" -f "$WORKDIR/Dockerfile.patcher" "$WORKDIR"

# ============================
# Executa patch dentro do container
# ============================
echo ">>> Executando o container"
if ! docker run --rm -v "$WORKDIR":/work "$IMAGE_NAME" "/work/$(basename "$DB_PATH")" "$NEW_ID"; then
  echo "❌ Erro durante execução do patcher"
  exit 1
fi

echo ">>> Compactando o DB"
if ! docker run --rm -v "$WORKDIR":/work "$IMAGE_NAME" "/work/$(basename "$DB_PATH")" --compact; then
  echo "❌ Erro durante a compactação"
  exit 1
fi

# ============================
# Verificação final
# ============================
echo ">>> Verificando SwarmID no portainer.db..."
sleep 5
FOUND_ID=$(grep -aPio '"SwarmId"\s*:\s*"\K[^"]+' "$DB_PATH" | sort -u)

if [[ "$FOUND_ID" == "$NEW_ID" ]]; then
  echo "✅ Sucesso: SwarmID atualizado para $FOUND_ID"
  exit 0
else
  echo "❌ Erro: SwarmID esperado '$NEW_ID', mas encontrado '$FOUND_ID'"
  echo "Por favor, execute novamente o script."
  exit 1
fi

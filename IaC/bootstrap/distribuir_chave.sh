#!/usr/bin/env bash
# Instala a chave pública gerada pelo `make bootstrap` em todo host do
# inventário — o único passo do fluxo que precisa da senha de root, e só uma
# vez por host.
#
# Por que existe: o bootstrap gerava a chave e ninguém a distribuía. O playbook
# do hipervisor instala a chave pública, mas para ele rodar você já precisa
# estar autenticado — a instalação do zero parava em
# "Permission denied (publickey,password)" sem que nada dissesse o que fazer.
#
#   make ssh-keys                    # inventário de produção
#   make ssh-keys ENV=dev            # o de desenvolvimento
set -euo pipefail

ambiente="${1:-prod}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
inventario="${repo}/IaC/ansible/inventories/${ambiente}/hosts.ini"
chave="${HOME}/.ssh/homelab_ed25519"   # o mesmo SSH_KEY do bootstrap.py

[[ -f "${inventario}" ]] || { echo "✗ inventário ausente: ${inventario}" >&2
  echo "  rode 'make bootstrap' antes" >&2; exit 1; }
[[ -f "${chave}.pub" ]] || { echo "✗ chave pública ausente: ${chave}.pub" >&2
  echo "  rode 'make bootstrap' antes" >&2; exit 1; }

# Extrai host e usuário das linhas de host do INI: linhas com `ansible_host=`,
# ignorando comentários e cabeçalhos de grupo. `ansible-inventory` seria mais
# correto, mas exigiria o Ansible instalado só para isto.
mapeamento="$(grep -E '^[^#[:space:]#].*ansible_host=' "${inventario}" || true)"
[[ -n "${mapeamento}" ]] || { echo "✗ nenhum host com ansible_host= em ${inventario}" >&2; exit 1; }

falhas=0
# O laço lê do fd 3, não do stdin: `ssh` e `ssh-copy-id` herdam o stdin e o
# consomem, e o laço parava no primeiro host sem erro nenhum.
while read -r linha <&3; do
  [[ -n "${linha}" ]] || continue
  ip="$(grep -oE 'ansible_host=[^[:space:]]+' <<<"${linha}" | cut -d= -f2)"
  usuario="$(grep -oE 'ansible_user=[^[:space:]]+' <<<"${linha}" | cut -d= -f2 || true)"
  usuario="${usuario:-root}"
  nome="${linha%% *}"

  # Já instalada? Não peça senha à toa. `BatchMode` impede o prompt, então o
  # teste falha rápido quando a chave ainda não está lá.
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
         -i "${chave}" "${usuario}@${ip}" true 2>/dev/null; then
    echo "✓ ${nome} (${usuario}@${ip}): chave já instalada"
    continue
  fi

  echo "→ ${nome} (${usuario}@${ip}): instalando — a senha pedida é a de ${usuario} NO HOST"
  if ssh-copy-id -i "${chave}.pub" -o StrictHostKeyChecking=accept-new \
       "${usuario}@${ip}" >/dev/null; then
    echo "✓ ${nome}: instalada"
  else
    echo "✗ ${nome}: falhou" >&2
    falhas=$((falhas + 1))
  fi
done 3<<<"${mapeamento}"

# O agente é exigência do provider do Terraform (`ssh { agent = true }` em
# providers.tf), não conveniência: sem a chave carregada, o upload de imagem
# para o nó falha.
if [[ -n "${SSH_AUTH_SOCK:-}" ]]; then
  ssh-add "${chave}" 2>/dev/null && echo "✓ chave carregada no ssh-agent"
else
  echo "⚠️  sem ssh-agent nesta sessão. O provider do Terraform exige um:"
  echo "    eval \"\$(ssh-agent -s)\" && ssh-add ${chave}"
fi

[[ "${falhas}" -eq 0 ]] || { echo "✗ ${falhas} host(s) falharam" >&2; exit 1; }
echo "✓ todos os hosts do inventário ${ambiente} aceitam a chave"

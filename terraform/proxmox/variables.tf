# ==============================================================================
# VARIÁVEIS DE ENTRADA
# ------------------------------------------------------------------------------
# Nenhuma variável abaixo que carregue um IP/gateway/URL real de infraestrutura
# tem `default`. Ausência de valor deve ABORTAR o plano (erro de variável
# obrigatória), nunca cair silenciosamente num IP público de exemplo. O valor
# real só existe no terraform.tfvars local (git-ignorado) - é o equivalente,
# no mundo Terraform, do Ansible Vault usado no restante do repositório.
# Use terraform.tfvars.example como modelo (valores nele são fictícios,
# faixas RFC 5737 reservadas para documentação, nunca IPs reais do homelab).
# ==============================================================================

# ------------------------------------------------------------------------------
# Autenticação / Endpoint da API do Proxmox VE
# ------------------------------------------------------------------------------

variable "proxmox_api_url" {
  type        = string
  description = "URL da API do Proxmox VE (endpoint HTTPS do nó, porta 8006). Sem default: preencha em terraform.tfvars."
}

variable "proxmox_api_token_id" {
  type        = string
  description = <<-EOT
    Token ID da API do Proxmox VE, no formato usuario@realm!nome-do-token.
    Deve apontar para um usuário DEDICADO com role customizada de menor
    privilégio (ver README) - NUNCA use root@pam!... aqui: root@pam ignora
    toda e qualquer ACL/role no Proxmox, então "role customizada" seria só
    decoração, o token continuaria com acesso total ao cluster.
  EOT
}

variable "proxmox_api_token_secret" {
  type        = string
  description = "Secret do Token de API do Proxmox VE."
  sensitive   = true
}

variable "proxmox_ssh_username" {
  type        = string
  description = "Usuário de SO usado via SSH pelo provider bpg/proxmox para operações de arquivo no host (upload de ISO, import de disco). Independente do usuário/token da API."
  default     = "root"
}

# ------------------------------------------------------------------------------
# Nós do cluster Proxmox VE
# ------------------------------------------------------------------------------

variable "proxmox_node_primary" {
  type        = string
  description = "Nome do Nó 1 Proxmox Principal (Gateway/Core)."
}

variable "proxmox_node_secondary" {
  type        = string
  description = "Nome do Nó 2 Proxmox Secundário (Storage NAS & Swarm Worker)."
}

# ------------------------------------------------------------------------------
# Acesso SSH inicial (cloud-init / LXC user_account)
# ------------------------------------------------------------------------------

variable "ssh_public_key" {
  type        = string
  description = "Chave SSH pública injetada via cloud-init (VMs) ou user_account (LXC) nas VMs/CTs. Sem default proposital: uma chave pública ausente deve falhar o plano, não silenciosamente deixar a máquina sem acesso."

  validation {
    condition     = can(regex("^ssh-(ed25519|rsa|ecdsa)[a-zA-Z0-9-]* ", var.ssh_public_key))
    error_message = "ssh_public_key precisa começar com \"ssh-ed25519 \", \"ssh-rsa \" ou \"ssh-ecdsa...\" (chave pública OpenSSH válida, não um caminho de arquivo nem a chave privada)."
  }
}

# ------------------------------------------------------------------------------
# Infraestrutura Proxmox comum (bridge, storage) - não sensível, mas
# centralizado aqui para eliminar os hardcodes repetidos em main.tf.
# ------------------------------------------------------------------------------

variable "network_bridge" {
  type        = string
  description = "Nome da bridge Proxmox (vSwitch) VLAN-aware usada por todas as VMs/CTs. Ver ansible/templates/proxmox/proxmox_interfaces.j2 para a config de trunk correspondente no host."
  default     = "vmbr0"
}

variable "vm_datastore_id" {
  type        = string
  description = "Datastore (storage Proxmox) usado para os discos de VMs/CTs (ex.: LVM-Thin local)."
  default     = "local-lvm"
}

variable "file_datastore_id" {
  type        = string
  description = "Datastore de conteúdo 'iso'/arquivo usado para ISOs e imagens cloud baixadas (storage tipo diretório, ex. 'local'). Precisa ser DIFERENTE de vm_datastore_id quando este for um storage de blocos (LVM-Thin não guarda arquivo solto)."
  default     = "local"
}

variable "container_template_file_id" {
  type        = string
  description = "file_id do template LXC usado pelos CTs Docker Swarm (produção e dev) - mesma imagem para garantir paridade de guest entre prod e dev."
  default     = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
}

variable "vlan_homelab" {
  type        = number
  description = "VLAN ID da rede HOMELAB onde vivem os CTs do Swarm e a VM do OMV."
  default     = 20
}

# ------------------------------------------------------------------------------
# VM 100 - OPNsense (WAN via PCI passthrough + ISO de instalação)
# ------------------------------------------------------------------------------

variable "opnsense_iso_file_id" {
  type        = string
  description = "file_id (datastore:iso/arquivo) da ISO de instalação do OPNsense. Precisa estar em storage local (local:iso/...), NUNCA no NAS_STORAGE servido pela própria VM OMV (dependência circular: a ISO não existiria enquanto a VM que a serve não tiver bootado)."
  default     = "local:iso/OPNsense-24.1-dvd-amd64.iso"
}

variable "opnsense_wan_pci_id" {
  type        = string
  description = <<-EOT
    Endereço PCI (formato "0000:05:00" ou "0000:05:00.0") da NIC física de WAN
    (enp5s0 no nó primário) usada em passthrough VT-d/IOMMU para a VM do
    OPNsense. Não tem default: é específico do hardware de cada host, obtido
    via `lspci` no nó primário - nunca é o mesmo endereço em duas máquinas
    diferentes, então não existe valor "genérico" seguro para default aqui.
  EOT
}

# ------------------------------------------------------------------------------
# CT 200 / CT 201 - Docker Swarm (produção)
# ------------------------------------------------------------------------------

variable "docker_swarm_node01_ip" {
  type        = string
  description = "IP estático (CIDR, ex.: 192.168.x.x/24) do CT 200 (docker-swarm-node01) na VLAN homelab."
}

variable "docker_swarm_node02_ip" {
  type        = string
  description = "IP estático (CIDR) do CT 201 (docker-swarm-node02) na VLAN homelab."
}

variable "swarm_gateway" {
  type        = string
  description = "Gateway IPv4 (sem CIDR) da VLAN homelab, usado pelos CTs de produção e dev e pela VM do OMV."
}

# ------------------------------------------------------------------------------
# VM 300 - OpenMediaVault (imagem cloud-init Debian 12 genericcloud)
# ------------------------------------------------------------------------------

variable "omv_nas_ip" {
  type        = string
  description = "IP estático (CIDR) da VM 300 (OpenMediaVault) na VLAN homelab. É a única rota pela qual ansible/playbooks/00-omv-setup.yml alcança a máquina - sem IP determinístico via cloud-init, o playbook não tem para onde conectar."
}

variable "omv_cloud_image_url" {
  type        = string
  description = "URL da imagem cloud (qcow2) do Debian 12 'genericcloud' usada para provisionar a VM do OMV via disk.file_id + cloud-init."
  default     = "https://cloud.debian.org/images/cloud/bookworm/20260806-2562/debian-12-genericcloud-amd64-20260806-2562.qcow2"
}

variable "omv_cloud_image_checksum" {
  type        = string
  description = "Checksum (SHA512) da imagem em omv_cloud_image_url, validado pelo proxmox_virtual_environment_download_file no momento do download. Atualize junto com a URL a cada bump de versão da imagem (SHA512SUMS publicado pelo projeto Debian)."
  default     = "3622c990108a044ed411652f8741e77c5822c365114d7b940206b243f8fb617b8586792df4cdb7afba1b71d1a09289d8ed632124688f2c8352cb08190a1e9868"
}

variable "omv_cloud_image_checksum_algorithm" {
  type        = string
  description = "Algoritmo do checksum em omv_cloud_image_checksum."
  default     = "sha512"
}

variable "omv_disk_size" {
  type        = number
  description = "Tamanho (GB) do disco principal da VM do OMV (a imagem cloud é importada e depois redimensionada para este valor)."
  default     = 64
}

# ------------------------------------------------------------------------------
# CT 210 - Nó de desenvolvimento (dev), paridade de guest com prod (200/201)
# ------------------------------------------------------------------------------

variable "dev_node_ip" {
  type        = string
  description = "IP estático (CIDR) do CT 210 (dev) na VLAN homelab. Convenção deste homelab: .10-.19 reservados para infra de produção, .20 em diante para dev/testes."
}

variable "dev_node_cores" {
  type        = number
  description = "CPU cores do CT 210 (dev) - deliberadamente menor que produção (200/201) para não competir por recursos com o Swarm real."
  default     = 2
}

variable "dev_node_memory" {
  type        = number
  description = "RAM dedicada (MB) do CT 210 (dev)."
  default     = 2048
}

variable "dev_node_swap" {
  type        = number
  description = "Swap (MB) do CT 210 (dev)."
  default     = 512
}

variable "dev_node_disk_size" {
  type        = number
  description = "Disco (GB) do CT 210 (dev) - deliberadamente menor que produção (200/201)."
  default     = 32
}

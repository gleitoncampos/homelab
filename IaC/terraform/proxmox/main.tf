# ==============================================================================
# PROXMOX VE INFRASTRUCTURE PROVISIONING VIA TERRAFORM / OPENTOFU
# Nodes: <no-primario> (Node 1 - Core/Gateway) | <no-secundario> (Node 2 - NAS Storage & Worker)
# ==============================================================================
#
# ⚠️⚠️⚠️ LEIA O README ANTES DE RODAR `apply` NESTE DIRETÓRIO ⚠️⚠️⚠️
# Hoje NADA aqui está sob gestão do Terraform (não existe .tfstate). Os CTs
# 200 e 201 (docker_swarm_node01/02) já existem em produção, criados por
# caminho manual. Um `apply` direto tenta CRIAR os VMIDs 100/200/201/300 do
# zero: na melhor hipótese falha com "already exists"; na pior, se o VMID
# real divergir do daqui, cria uma VM/CT duplicado na VLAN homelab com o MESMO
# IP ESTÁTICO de um nó de produção e derruba o Swarm por conflito de IP.
# `terraform import` dos CTs 200/201 é pré-requisito obrigatório - ver README.
# ==============================================================================

# ------------------------------------------------------------------------------
# NÓ 1: <no-primario> (Primary Gateway & Swarm Manager)
# ------------------------------------------------------------------------------

# 1. VM 100 - OPNsense Firewall & Router Primary Gateway (no nó primário)
resource "proxmox_virtual_environment_vm" "opnsense" {
  node_name   = var.proxmox_node_primary
  vm_id       = 100
  name        = "opnsense-firewall"
  description = "OPNsense Core Firewall & Gateway - Segmentação Multi-VLAN"

  cpu {
    cores    = 1 # 1 núcleo dedicado para teste de performance
    type     = "host"
    affinity = "0" # CPU Pinning no Core 0 da CPU Intel N100
  }

  memory {
    dedicated = 4096 # 4GB RAM
  }

  # FASE 1 (instalação via ISO): guest agent DESLIGADO. Ninguém instalou o
  # pacote os-qemu-guest-agent dentro do FreeBSD/OPNsense ainda - se
  # `agent.enabled = true` e `started = true` juntos, o `apply` fica preso
  # esperando um agente que não existe até bater o timeout. Ver README,
  # seção "OPNsense em duas fases", para o procedimento de habilitar depois.
  agent {
    enabled = false
  }

  # Trunk para a bridge VLAN-aware do host (ver
  # ansible/templates/proxmox/proxmox_interfaces.j2: bridge-vlan-aware yes).
  #
  # ⚠️ NUNCA adicione `vlan_id` aqui de volta. Este é o BUG MAIS GRAVE deste
  # arquivo quando presente: com `vlan_id` setado, o Proxmox transforma a
  # porta virtual em modo ACCESS e remove (untag) toda tag 802.1Q antes de
  # entregar o frame ao guest. A OPNsense cria internamente subinterfaces
  # tagged (vtnet0.10, vtnet0.20, vtnet0.30, ...) sobre esta MESMA NIC virtual
  # para cada VLAN - se a tag já foi removida pelo Proxmox, essas
  # subinterfaces nunca recebem nenhum frame. O `terraform plan`/`apply` não
  # falha com `vlan_id` setado: a VM cria, sobe, faz boot normalmente. O que
  # quebra é a rede, minutos depois, silenciosamente, sem nenhum erro do
  # Terraform - por isso é fácil "corrigir de volta" por engano pensando que
  # faltava a VLAN de management. Não falta: a interface deve permanecer
  # trunk (sem vlan_id nenhum) para a OPNsense decidir o tagging.
  network_device {
    bridge = var.network_bridge
    model  = "virtio"
  }

  # scsihw (ex.: "virtio-scsi-single") é o CONTROLADOR SCSI da VM, não uma
  # interface de disco. O regex de validação do provider bpg/proxmox só
  # aceita interface no formato ide|sata|scsi|virtio seguido de número
  # (ex.: "scsi0"); "scsihw0" não casa com nenhum padrão válido. O
  # controlador vai no atributo `scsi_hardware`, no nível do recurso.
  scsi_hardware = "virtio-scsi-single"

  disk {
    datastore_id = var.vm_datastore_id
    size         = 32
    interface    = "scsi0"
    file_format  = "raw"
    ssd          = true
    discard      = "on"
  }

  operating_system {
    type = "other"
  }

  # ISO local, NUNCA no storage do NAS (ver comentário na VM 300 do OMV
  # abaixo sobre a dependência circular).
  cdrom {
    file_id   = var.opnsense_iso_file_id
    interface = "ide3"
  }

  # Sem boot_order explícito, uma VM criada via API pode herdar um default
  # sem CD-ROM na ordem de boot e nunca bootar a ISO de instalação. Ordem:
  # tenta o disco (já instalado, em reboots futuros) antes da ISO.
  boot_order = ["scsi0", "ide3"]

  # Passthrough PCI (VT-d/IOMMU) da NIC física de WAN (enp5s0 no nó primário).
  # Pré-requisitos no host: IOMMU habilitado na BIOS + `intel_iommu=on` (ou
  # `amd_iommu=on`) na cmdline do kernel Proxmox, e a interface NÃO deve
  # aparecer no `bridge-ports` de nenhuma bridge (ver
  # ansible/templates/proxmox/proxmox_interfaces.j2: enp5s0 fica `inet
  # manual`, dedicada ao passthrough). O endereço PCI é específico de cada
  # host físico - ver variables.tf (var.opnsense_wan_pci_id, sem default).
  hostpci {
    device = "hostpci0"
    id     = var.opnsense_wan_pci_id
    pcie   = false
    rombar = true
  }

  started = true

  # Deliberadamente SEM lifecycle.prevent_destroy aqui: diferente dos CTs
  # 200/201, o README não afirma que esta VM já existe fora do Terraform.
  # Ainda assim, rode `terraform plan` e confira o VMID real no cluster antes
  # do primeiro apply - se ela também já existir manualmente, importe-a
  # primeiro (mesmo procedimento usado para os CTs 200/201).
}

# 2. CT 200 - Docker Swarm Manager Node 01 (no nó primário)
resource "proxmox_virtual_environment_container" "docker_swarm_node01" {
  node_name    = var.proxmox_node_primary
  vm_id        = 200
  unprivileged = true

  initialization {
    hostname = "docker-swarm-node01"

    ip_config {
      ipv4 {
        address = var.docker_swarm_node01_ip
        gateway = var.swarm_gateway
      }
    }

    user_account {
      keys = [var.ssh_public_key]
    }
  }

  cpu {
    cores = 4
  }

  memory {
    dedicated = 8192 # 8GB RAM
    swap      = 2048 # 2GB Swap ZRAM
  }

  features {
    nesting = true # Habilita Nesting para execução segura de Docker Engine dentro do LXC
    keyctl  = true # Habilita Keyctl para persistência de chaves do Docker Swarm
  }

  network_interface {
    name    = "eth0"
    bridge  = var.network_bridge
    vlan_id = var.vlan_homelab
  }

  disk {
    datastore_id = var.vm_datastore_id
    size         = 64 # 64GB SSD Storage LVM-Thin
  }

  operating_system {
    template_file_id = var.container_template_file_id
    type             = "debian"
  }

  started = true

  # ⚠️ Este CT já existe em produção, criado por caminho MANUAL (não pelo
  # Terraform). `prevent_destroy` e `ignore_changes` só têm efeito - e só
  # fazem sentido - DEPOIS de rodar:
  #   terraform import proxmox_virtual_environment_container.docker_swarm_node01 <node>/200
  # Antes do import não existe state, então este bloco não protege nada: um
  # `apply` sem import antes tenta CRIAR o CTID 200 e colide com o que já
  # está rodando (ver aviso no topo do arquivo). `operating_system` entra em
  # ignore_changes porque o provider frequentemente reporta um valor
  # derivado após import que não bate 1:1 com o que foi digitado aqui,
  # gerando diff perpétuo sem nenhuma mudança real no CT.
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [operating_system]
  }
}

# ------------------------------------------------------------------------------
# NÓ 2: <no-secundario> (NAS Storage & Swarm Worker Node)
# ------------------------------------------------------------------------------

# Imagem cloud-init do Debian 12 "genericcloud" para a VM do OMV. Baixada
# direto do storage de arquivo do nó secundário (var.file_datastore_id) - não do
# NAS_STORAGE (ver motivo no comentário do disco da VM 300 abaixo).
# file_name termina em ".img" (e não ".qcow2") de propósito: é o truque
# necessário para o Proxmox tratar o arquivo baixado como imagem de disco
# importável via disk.file_id, e não como uma ISO comum.
resource "proxmox_download_file" "debian_12_genericcloud" {
  node_name           = var.proxmox_node_secondary
  content_type        = "iso"
  datastore_id        = var.file_datastore_id
  url                 = var.omv_cloud_image_url
  file_name           = "debian-12-genericcloud-amd64.img"
  checksum            = var.omv_cloud_image_checksum
  checksum_algorithm  = var.omv_cloud_image_checksum_algorithm
  overwrite           = false
  overwrite_unmanaged = true
}

# 3. VM 300 - OpenMediaVault (OMV 7) NAS Storage Server (no nó secundário)
resource "proxmox_virtual_environment_vm" "omv_nas" {
  node_name   = var.proxmox_node_secondary
  vm_id       = 300
  name        = "openmediavault-nas"
  description = "OpenMediaVault NAS Storage Server (Samba, NFS, ZRAM, hdparm)"

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096 # 4GB RAM
  }

  # Diferente da OPNsense: a imagem cloud "genericcloud" do Debian já vem com
  # o pacote qemu-guest-agent pré-instalado de fábrica, então não há o
  # problema de "esperar um agente que ninguém instalou" - o agente sobe
  # junto com o primeiro boot do cloud-init.
  agent {
    enabled = true
  }

  network_device {
    bridge = var.network_bridge
    model  = "virtio"
    # VLAN homelab: aqui SIM é access/tagged de propósito (ao contrário do
    # trunk da OPNsense), a VM não cria subinterfaces internas por VLAN.
    vlan_id = var.vlan_homelab
  }

  scsi_hardware = "virtio-scsi-single"

  disk {
    datastore_id = var.vm_datastore_id
    # Importa a imagem cloud baixada acima como disco de boot e redimensiona
    # para omv_disk_size. Substitui o antigo boot via ISO (que não tinha
    # boot_order nem IP determinístico).
    file_id     = proxmox_download_file.debian_12_genericcloud.id
    interface   = "scsi0"
    size        = var.omv_disk_size
    file_format = "raw"
    ssd         = true
    discard     = "on"
  }

  # cloud-init: datastore que guarda o drive de configuração (ide2, padrão
  # do provider) e o IP estático - é a ÚNICA rota pela qual
  # ansible/playbooks/00-omv-setup.yml consegue alcançar esta máquina depois
  # do primeiro boot.
  initialization {
    datastore_id = var.vm_datastore_id

    ip_config {
      ipv4 {
        address = var.omv_nas_ip
        gateway = var.swarm_gateway
      }
    }

    user_account {
      keys = [var.ssh_public_key]
    }
  }

  operating_system {
    type = "l26"
  }

  # Sem cdrom/ide3 aqui: diferente da OPNsense, esta VM não instala a partir
  # de ISO, então não há CD-ROM na cadeia de boot. O disco importado
  # (scsi0) já vem particionado e pronto.
  boot_order = ["scsi0"]

  started = true

  # Mesmo aviso da nota no topo do arquivo: confira se o VMID 300 já existe
  # manualmente no cluster antes do primeiro apply. Se existir com dados
  # reais no NAS, importe antes de aplicar - não recrie do zero.
}

# 4. CT 201 - Docker Swarm Worker Node 02 (no nó secundário)
resource "proxmox_virtual_environment_container" "docker_swarm_node02" {
  node_name    = var.proxmox_node_secondary
  vm_id        = 201
  unprivileged = true

  initialization {
    hostname = "docker-swarm-node02"

    ip_config {
      ipv4 {
        address = var.docker_swarm_node02_ip
        gateway = var.swarm_gateway
      }
    }

    user_account {
      keys = [var.ssh_public_key]
    }
  }

  cpu {
    cores = 4
  }

  memory {
    dedicated = 8192 # 8GB RAM
    swap      = 2048 # 2GB Swap ZRAM
  }

  features {
    nesting = true # Habilita Nesting para execução segura de Docker Engine dentro do LXC
    keyctl  = true # Habilita Keyctl para persistência de chaves do Docker Swarm
  }

  network_interface {
    name    = "eth0"
    bridge  = var.network_bridge
    vlan_id = var.vlan_homelab
  }

  disk {
    datastore_id = var.vm_datastore_id
    size         = 64 # 64GB SSD Storage LVM-Thin
  }

  operating_system {
    template_file_id = var.container_template_file_id
    type             = "debian"
  }

  started = true

  # Mesmo caso do CT 200: só vale depois de
  #   terraform import proxmox_virtual_environment_container.docker_swarm_node02 <node>/201
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [operating_system]
  }
}

# ------------------------------------------------------------------------------
# CT 210 - Nó de DESENVOLVIMENTO (não faz parte do Swarm de produção)
# ------------------------------------------------------------------------------
#
# Fica no nó secundário (mesmo nó do CT 201) para não competir com a OPNsense por
# CPU no nó primário. É um CT NOVO: diferente de 200/201, não há colisão de VMID
# esperada, então este recurso pode ser criado normalmente por um `apply`
# (desde que o VMID 210 esteja de fato livre no cluster - confirme com
# `pvesh get /cluster/resources` antes do primeiro apply, mesma cautela).
resource "proxmox_virtual_environment_container" "docker_dev_node" {
  node_name = var.proxmox_node_secondary
  vm_id     = 210

  # Unprivileged, EXATAMENTE como os CTs de produção (200/201). Paridade de
  # guest é o ponto inteiro do split prod/dev: testar num CT privileged (ou
  # numa VM) e depois promover para produção num CT unprivileged invalidaria
  # o teste - o comportamento do Docker/overlay/AppArmor muda entre os dois.
  unprivileged = true

  initialization {
    hostname = "docker-swarm-dev-node"

    ip_config {
      ipv4 {
        # .20 em diante é a faixa dev: .10-.19 fica reservada para infra de
        # produção na VLAN homelab (ver variables.tf).
        address = var.dev_node_ip
        gateway = var.swarm_gateway
      }
    }

    # ⚠️ Em LXC, `initialization.user_account.keys` só escreve a chave em
    # /root/.ssh/authorized_keys - NÃO cria o usuário "nuvidio" que
    # ansible/inventories/prod/hosts.ini usa com `become` via sudo. Quem cria
    # esse usuário é ansible/playbooks/00-node-bootstrap.yml (responsabilidade
    # de outro agente/PR; NÃO recriado aqui), que roda como root via esta
    # mesma chave e provisiona o usuário de automação antes de qualquer outro
    # playbook assumir `ansible_user=nuvidio`.
    user_account {
      keys = [var.ssh_public_key]
    }
  }

  cpu {
    cores = var.dev_node_cores # 2 cores (prod: 4)
  }

  memory {
    dedicated = var.dev_node_memory # 2048MB (prod: 8192MB)
    swap      = var.dev_node_swap
  }

  # nesting + keyctl são obrigatórios para o Docker Engine (e o Swarm)
  # funcionarem dentro de um LXC unprivileged - mas "funciona" aqui quer
  # dizer "funcional, porém frágil": a rede overlay do Swarm depende dos
  # módulos overlay, ip_vs e vxlan carregados no KERNEL DO HOST (LXC
  # compartilha kernel, não pode carregar módulo próprio), e o perfil
  # AppArmor padrão do LXC unprivileged tem arestas conhecidas com
  # namespaces de rede/mount que o dockerd cria em runtime. Isso já é
  # verdade para os CTs de produção (200/201); o dev node herda a mesma
  # fragilidade de propósito, pela regra de paridade de guest acima.
  features {
    nesting = true
    keyctl  = true
  }

  network_interface {
    name   = "eth0"
    bridge = var.network_bridge
    # Mesma VLAN homelab dos CTs de produção - de propósito, NÃO há uma VLAN
    # dedicada para dev. Criar uma 7ª VLAN exigiria reconfigurar o
    # config.xml da OPNsense (interfaces, DHCP, regras), que é o playbook
    # potencialmente mais destrutivo deste repositório - o risco de um erro
    # de sintaxe ali derrubar TODAS as VLANs (incluindo a de produção) supera
    # o ganho de isolamento de rede de um nó dev. O isolamento real vem de
    # uma regra de FIREWALL na própria VLAN homelab (configurada na
    # OPNsense, fora do escopo deste diretório Terraform) bloqueando
    # var.dev_node_ip contra a faixa .10-.11 (nós de produção) nas portas do
    # Swarm: 2377/tcp (cluster management), 7946/tcp+udp (gossip) e
    # 4789/udp (VXLAN overlay) - assim o dev node nunca consegue conversar
    # com o Swarm real mesmo estando na mesma VLAN/L2.
    vlan_id = var.vlan_homelab
  }

  disk {
    datastore_id = var.vm_datastore_id
    size         = var.dev_node_disk_size # 32GB (prod: 64GB)
  }

  operating_system {
    # Mesmo template dos CTs de produção - paridade de guest, de novo.
    template_file_id = var.container_template_file_id
    type             = "debian"
  }

  started = true

  # ⚠️ Sem isto, TODO `terraform apply` religa o nó dev mesmo que alguém o
  # tenha desligado de propósito por não estar em uso (é um nó de dev, fica
  # desligado fora de janelas de teste). `started` fica fora do controle do
  # Terraform depois do primeiro apply.
  lifecycle {
    ignore_changes = [started]
  }
}

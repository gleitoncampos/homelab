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
  provider    = proxmox.primary
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

  # As duas fases da OPNsense, agora por VARIÁVEL e não por edição do arquivo.
  #
  # Fase 1 (instalação via ISO): agente DESLIGADO, que é o default. Ninguém
  # instalou o `os-qemu-guest-agent` dentro do FreeBSD ainda, e com
  # `agent.enabled = true` junto de `started = true` o apply fica preso
  # esperando um agente inexistente até o timeout.
  #
  # Fase 2, depois que o playbook de rede instalou o pacote:
  #   terraform apply -var opnsense_agent_enabled=true
  #
  # ⚠️ Antes disto o README mandava editar `agent.enabled` NESTE arquivo como
  # passo operacional. Editar fonte versionada para operar é o que o CLAUDE.md
  # existe para impedir: ou a edição era commitada, e a próxima instalação do
  # zero quebrava na fase 1, ou não era, e a divergência morria no apply
  # seguinte.
  agent {
    enabled = var.opnsense_agent_enabled
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
    file_id   = proxmox_download_file.opnsense_iso.id
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

  # hostpci1 = segunda WAN (CLARO), adicionada 29/08/2026, endereço via variável.
  hostpci {
    device = "hostpci1"
    id     = var.opnsense_wan2_pci_id
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

# ------------------------------------------------------------------------------
# Imagens de instalação — baixadas e verificadas pelo Terraform
# ------------------------------------------------------------------------------
# Antes destes dois recursos, o primeiro apply só funcionava se alguém tivesse
# rodado `pveam download` e enviado a ISO do OPNsense pela UI, em cada nó. Era
# pré-requisito manual não escrito em lugar nenhum: quem esquecia recebia um
# erro do provider sobre file_id inexistente, que não diz o que fazer.
#
# O storage `local` é POR NÓ no Proxmox: um template baixado só no primário não
# existe para o CT que nasce no secundário. São DOIS recursos, e não um
# `for_each` sobre os dois nós, porque `provider` não aceita valor derivado de
# `each` — e sem cluster cada nó fala por um provider diferente.
resource "proxmox_download_file" "lxc_template_primary" {
  provider = proxmox.primary

  node_name          = var.proxmox_node_primary
  content_type       = "vztmpl"
  datastore_id       = var.file_datastore_id
  url                = var.container_template_url
  checksum           = var.container_template_checksum
  checksum_algorithm = "sha512"

  # ⚠️ `overwrite_unmanaged = true` NÃO adota o arquivo que já existe: ele
  # APAGA e baixa de novo. É o que destrava o caso comum de já haver um
  # template no storage por um `pveam download` anterior (com `false`, o apply
  # falha por conflito de nome), mas o preço é real — um arquivo de mesmo nome
  # em uso por outro convidado é removido debaixo dele no primeiro apply.
  # Confira o conteúdo do datastore antes do primeiro apply em host povoado.
  overwrite           = false
  overwrite_unmanaged = true
}

resource "proxmox_download_file" "lxc_template_secondary" {
  provider = proxmox.secondary

  node_name           = var.proxmox_node_secondary
  content_type        = "vztmpl"
  datastore_id        = var.file_datastore_id
  url                 = var.container_template_url
  checksum            = var.container_template_checksum
  checksum_algorithm  = "sha512"
  overwrite           = false
  overwrite_unmanaged = true
}

# A ISO é publicada comprimida (.iso.bz2). O checksum declarado é o do arquivo
# COMPRIMIDO, que é o que o provider baixa e verifica antes de descomprimir.
resource "proxmox_download_file" "opnsense_iso" {
  node_name               = var.proxmox_node_primary
  provider                = proxmox.primary
  content_type            = "iso"
  datastore_id            = var.file_datastore_id
  url                     = var.opnsense_iso_url
  file_name               = var.opnsense_iso_file_name
  checksum                = var.opnsense_iso_checksum
  checksum_algorithm      = "sha256"
  decompression_algorithm = "bz2"
  overwrite               = false
  overwrite_unmanaged     = true
}

# 2. CT 200 - Docker Swarm Manager Node 01 (no nó primário)
resource "proxmox_virtual_environment_container" "docker_swarm_node01" {
  node_name    = var.proxmox_node_primary
  provider     = proxmox.primary
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

    dns {
      domain  = var.guest_search_domain
      servers = [var.swarm_gateway]
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
    template_file_id = proxmox_download_file.lxc_template_primary.id
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
resource "proxmox_download_file" "debian_13_genericcloud" {
  node_name           = var.proxmox_node_secondary
  provider            = proxmox.secondary
  content_type        = "iso"
  datastore_id        = var.file_datastore_id
  url                 = var.omv_cloud_image_url
  file_name           = "debian-13-genericcloud-amd64.img"
  checksum            = var.omv_cloud_image_checksum
  checksum_algorithm  = var.omv_cloud_image_checksum_algorithm
  overwrite           = false
  overwrite_unmanaged = true
}

# 3. VM 300 - OpenMediaVault (OMV 7) NAS Storage Server (no nó secundário)
resource "proxmox_virtual_environment_vm" "omv_nas" {
  node_name   = var.proxmox_node_secondary
  provider    = proxmox.secondary
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

  # ⚠️ q35 é REQUISITO do passthrough abaixo, não preferência. O chipset padrão
  # (i440fx) não expõe barramento PCIe ao convidado, e `pcie = true` num
  # i440fx é ignorado: a controladora entra como PCI legado, ou não entra.
  machine = "q35"

  # ----------------------------------------------------------------------------
  # Passthrough da controladora SATA - é por aqui que entram os discos do NAS
  # ----------------------------------------------------------------------------
  # ⚠️ Sem este bloco a VM sobe com o disco de sistema e MAIS NADA. Os discos de
  # dados do NAS não são virtuais: são físicos, atrás de uma controladora SATA
  # dedicada, e o convidado só os enxerga porque a controladora inteira é
  # entregue a ele. O `fstab` do OMV declara essas montagens com `nofail`, então
  # a ausência não trava o boot - ela some, e o NAS sobe vazio.
  #
  # Pré-requisitos no host, todos já cobertos por ansible/playbooks/00-proxmox-setup.yml:
  # IOMMU na cmdline do kernel (derivado do fabricante da CPU, `intel_iommu=on`
  # ou `amd_iommu=on`, mais `iommu=pt`) e o reboot que a flag exige. NÃO é
  # preciso carregar módulo do VFIO: o Proxmox faz o bind dinâmico no start da
  # VM a partir deste bloco - ver a nota longa no fim daquele playbook.
  #
  # `pcie = true` (e não `false` como no OPNsense): esta VM é q35 e a
  # controladora é PCIe. O OPNsense usa i440fx com uma NIC PCI, daí a diferença.
  hostpci {
    device = "hostpci0"
    id     = var.omv_hba_pci_id
    pcie   = true
  }

  disk {
    datastore_id = var.vm_datastore_id
    # Importa a imagem cloud baixada acima como disco de boot e redimensiona
    # para omv_disk_size. Substitui o antigo boot via ISO (que não tinha
    # boot_order nem IP determinístico).
    file_id     = proxmox_download_file.debian_13_genericcloud.id
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

    dns {
      domain  = var.guest_search_domain
      servers = [var.swarm_gateway]
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

  # ⚠️ Decisão de 25/08/2026: esta VM é para ser RECRIADA, não importada. O
  # VMID 300 e o nome daqui são portanto o ALVO, e divergem de propósito da VM
  # que roda hoje no nó secundário - que tem outro VMID e outro nome. Não
  # "corrija" estes dois campos para bater com a produção: fazê-lo transformaria
  # uma recriação limpa numa adoção pela metade.
  #
  # ⚠️ O que a recriação destrói e o que ela preserva, e a diferença importa:
  # os discos de DADOS são físicos, entram pelo hostpci acima e sobrevivem
  # intactos. O disco de SISTEMA é virtual e é perdido - e com ele vai o
  # /etc/openmediavault/config.xml, que é onde vivem usuários, pastas
  # compartilhadas, exports e a associação entre cada UUID de filesystem e seu
  # ponto de montagem. Numa OMV recém-instalada os discos de dados existem no
  # `lsblk` e NÃO estão montados sob /srv/dev-disk-by-uuid-*, que é justamente o
  # recorte de que ansible/tasks/mergerfs_pools.yml depende para eleger as
  # branches dos pools. Ver .local/docs/TODO-E-ROADMAP.md §1.1.12.
}

# 4. CT 201 - Docker Swarm Worker Node 02 (no nó secundário)
resource "proxmox_virtual_environment_container" "docker_swarm_node02" {
  node_name    = var.proxmox_node_secondary
  provider     = proxmox.secondary
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

    dns {
      domain  = var.guest_search_domain
      servers = [var.swarm_gateway]
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
    template_file_id = proxmox_download_file.lxc_template_secondary.id
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
  provider  = proxmox.secondary
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
    dns {
      domain  = var.guest_search_domain
      servers = [var.swarm_gateway]
    }

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
    template_file_id = proxmox_download_file.lxc_template_secondary.id
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

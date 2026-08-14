# 🏗️ Infraestrutura como Código (IaC) com Terraform / OpenTofu - Homelab

Este diretório centraliza o provisionamento declarativo da **Infraestrutura Virtual (Hardware Virtual)** na topologia multi-host do hypervisor **Proxmox VE**. Utilizando o provider oficial `bpg/proxmox`, definimos o ciclo de vida completo de **Virtual Machines (VMs)** e **Containers LXC** distribuídos entre os nós `<no-primario>` e `<no-secundario>` de forma automatizada, reproduzível e com estado controlado.

---

## ⚠️⚠️⚠️ AVISO CRÍTICO - LEIA ANTES DE RODAR QUALQUER `apply` ⚠️⚠️⚠️

**Hoje nada neste diretório está sob gestão do Terraform.** Não existe `.tfstate`, `.terraform/` nem `terraform.tfvars` neste repositório - o provisionamento até agora foi feito por caminho manual (via `pveum`/console/scripts ad-hoc direto no Proxmox), inclusive os **CTs 200 e 201** do Docker Swarm, que já rodam em produção.

Isso muda a análise de risco de um jeito específico: o risco imediato de um primeiro `apply` **não é "replacement" de recurso já gerenciado" - é COLISÃO DE VMID**:

- Um `apply` direto tenta **criar** os VMIDs 100 (OPNsense), 200/201 (Swarm) e 300 (OMV) do zero.
- Na melhor hipótese, a API do Proxmox responde `already exists` e o `apply` falha limpo.
- Na pior hipótese - se o VMID/CTID real no cluster divergir por qualquer motivo do que está hardcoded aqui - o Terraform **cria um CT/VM duplicado** na VLAN homelab **com o MESMO IP ESTÁTICO** de um nó de produção (`docker_swarm_node01_ip`/`node02_ip`), e o Swarm real perde quórum ou entra em split-brain de IP.

### Procedimento obrigatório antes do primeiro `apply` em produção

1. Confirme os VMIDs/CTIDs reais no cluster (`pvesh get /cluster/resources --type vm` ou a UI web) e confira que batem com 100/200/201/300 usados em `main.tf`. Se não baterem, ajuste o `.tf` para refletir a realidade **antes** de qualquer `import` - importar para o VMID errado não corrige nada.
2. Rode `terraform import` **antes** de qualquer `plan`/`apply` que toque produção:
   ```bash
   terraform import proxmox_virtual_environment_container.docker_swarm_node01 <NOME-DO-NO>/<CTID>
   terraform import proxmox_virtual_environment_container.docker_swarm_node02 <NOME-DO-NO>/<CTID>
   ```
   > ⚠️ Os VMIDs 200/201 escritos em `main.tf` **não** correspondem aos nós do
   > Swarm no parque atual — um levantamento da produção mostrou que estão em uso
   > por outros guests. Importar com esse mapeamento traria o recurso errado para
   > o state. Refaça o mapeamento pelo passo 1 antes de rodar qualquer coisa aqui.
3. Só depois do `import` os blocos `lifecycle { prevent_destroy = true; ignore_changes = [operating_system] }` presentes em `main.tf` para esses dois CTs passam a valer de fato. **Antes do import eles não protegem nada** - sem state prévio, o Terraform não tem "recurso existente" para proteger, ele simplesmente tenta criar um novo.
4. Rode `terraform plan` e revise linha por linha. Um plan limpo (sem `# forces replacement`, sem diffs inesperados em `disk`/`network_interface`/`features`) é o sinal de que o import capturou o estado real corretamente.
5. A mesma cautela vale, por analogia, para a VM 100 (OPNsense) e a VM 300 (OMV) caso elas também já existam manualmente no cluster - este runbook documenta o procedimento apenas para os CTs 200/201 porque são os casos confirmados de criação manual, mas **nunca rode `apply` contra um VMID de produção sem antes confirmar, via `plan`, que a ação não é "create".**

Nenhum destes quatro recursos (VM 100, CT 200, VM 300, CT 201) deve ser tratado como "seguro para recriar do zero" - apenas o **CT 210 (dev node)**, novo, não tem esse risco por não existir ainda em lugar nenhum.

---

## 📂 Estrutura de Arquivos

```text
terraform/
└── proxmox/
    ├── README.md                # Este arquivo (Guia de uso, comandos e runbook de import)
    ├── providers.tf             # Configuração do provider bpg/proxmox e autenticação API
    ├── main.tf                  # Definição dos recursos nos nós <no-primario> e <no-secundario>
    ├── variables.tf             # Definição de variáveis de entrada (URL, Tokens, Nodes, Chaves SSH, IPs)
    ├── outputs.tf               # Saídas estruturadas (VMIDs e IPs, lidos dos próprios recursos)
    ├── terraform.tfvars.example # Modelo com valores fictícios (RFC 5737) - nunca IPs reais
    └── .terraform.lock.hcl      # Lock do provider - DEVE ser commitado (não está no .gitignore)
```

---

## 🖥️ Topologia de Nós & Recursos Provisionados

### 🔹 Nó 1: `<no-primario>` (Host Principal - Core / Gateway / Manager)

1. **VM 100 (`opnsense-firewall`)**:
   - **SO**: OPNsense Core Firewall & Primary Router (FreeBSD), instalado via ISO.
   - **Hardware**: 1 CPU Core (Affinity `0` dedicado no Intel N100), 4GB RAM, 32GB SSD (`scsi0`, controlador `virtio-scsi-single`).
   - **Rede LAN**: `vmbr0` em modo **trunk** (sem `vlan_id` na interface) - a própria OPNsense cria as subinterfaces taggeadas (`vtnet0.10`, `.20`, `.30`, ...) internamente. Ver aviso abaixo.
   - **Rede WAN**: PCIe Pass-through (VT-d/IOMMU) da interface física WAN (`enp5s0` no nó primário), via bloco `hostpci` apontando para `var.opnsense_wan_pci_id`.
   - **Boot**: ISO `OPNsense-24.1-dvd-amd64.iso` em storage **local** (`local:iso/...`), com `boot_order = ["scsi0", "ide3"]`.

   > ⚠️ **NÃO adicione `vlan_id` na `network_device` da OPNsense.** Isso já foi feito antes e é o bug mais perigoso deste arquivo: com `vlan_id` setado, o Proxmox transforma a porta virtual em modo *access* e remove a tag 802.1Q do frame antes de entregá-lo ao guest. Como a OPNsense depende de receber os frames **taggeados** para distribuí-los entre `vtnet0.10`/`.20`/`.30`/etc., a rede das VLANs para de funcionar - mas **não há erro nenhum no `plan` ou no `apply`**: a VM sobe, faz boot, parece saudável. O problema só aparece depois, como perda de conectividade das VLANs internas, o que historicamente já levou alguém a "corrigir" isso reintroduzindo o `vlan_id` - não faça isso.

   > ⚠️ **OPNsense em duas fases (guest agent):** o primeiro `apply` sobe a VM com `agent.enabled = false`. Isso é proposital: o `apply` roda com `started = true` e, se `agent.enabled = true` estivesse junto, o provider ficaria esperando o QEMU Guest Agent responder - mas ninguém instalou o pacote `os-qemu-guest-agent` dentro do FreeBSD ainda (ele só existe depois da instalação manual do OPNsense via a ISO). Isso trava o `apply` até o timeout. Fluxo em duas fases:
   > 1. `apply` inicial: VM sobe com a ISO, `agent.enabled = false`. Instale o OPNsense manualmente pela console (VNC/noVNC) do Proxmox, incluindo o pacote `os-qemu-guest-agent`.
   > 2. Depois da instalação, edite `agent.enabled = true` em `main.tf` e rode `apply` de novo (só essa mudança).

2. **CT 200 (`docker-swarm-node01`)**:
   - **SO**: Debian 12 Bookworm (Container LXC Desprivilegiado).
   - **Hardware**: 4 CPU Cores, 8GB RAM, 2GB Swap ZRAM, 64GB SSD LVM-Thin.
   - **Rede**: IP Estático (`var.docker_swarm_node01_ip`) na VLAN homelab.
   - **Segurança**: `nesting = true` e `keyctl = true` ativados para suporte completo ao Docker Swarm.
   - **Já existe em produção** - ver runbook de import no topo deste README.

---

### 🔹 Nó 2: `<no-secundario>` (Host Secundário - Storage & Compute Worker)

3. **VM 300 (`openmediavault-nas`)**:
   - **SO**: OpenMediaVault (OMV 7) sobre **Debian 12 "genericcloud"**, provisionado via `proxmox_virtual_environment_download_file` + `disk.file_id` + cloud-init (não mais via ISO).
   - **Hardware**: 2 CPU Cores, 4GB RAM, disco importado e redimensionado para `var.omv_disk_size` (64GB por padrão).
   - **Rede**: IP Estático (`var.omv_nas_ip`) na VLAN homelab, definido via cloud-init - é a **única rota determinística** pela qual `ansible/playbooks/00-omv-setup.yml` consegue alcançar a máquina depois do boot.
   - **Guest agent**: habilitado desde o primeiro `apply` (a imagem `genericcloud` já traz `qemu-guest-agent` de fábrica, sem o problema de "esperar um agente que ninguém instalou" que existe na OPNsense).

   > A ISO/imagem de boot desta VM **nunca** pode vir do storage `NAS_STORAGE` (CIFS/NFS servido pela própria VM 300): seria uma dependência circular - o compartilhamento de rede não existe enquanto a VM que o serve ainda não bootou. A imagem cloud vem de `var.omv_cloud_image_url` (Debian oficial) para um datastore de arquivo local (`var.file_datastore_id`) e o download é validado por `checksum`/`checksum_algorithm`.

4. **CT 201 (`docker-swarm-node02`)**:
   - **SO**: Debian 12 Bookworm (Container LXC Desprivilegiado).
   - **Hardware**: 4 CPU Cores, 8GB RAM, 2GB Swap ZRAM, 64GB SSD LVM-Thin.
   - **Rede**: IP Estático (`var.docker_swarm_node02_ip`) na VLAN homelab.
   - **Segurança**: `nesting = true` e `keyctl = true` ativados para ingressar no cluster Docker Swarm.
   - **Já existe em produção** - ver runbook de import no topo deste README.

---

### 🔹 CT 210 (`docker-swarm-dev-node`) - nó de desenvolvimento

Fica no nó `<no-secundario>` (mesmo nó do CT 201), para não competir com a OPNsense por CPU no `<no-primario>`.

- **SO**: Debian 12 Bookworm - **mesmo template** dos CTs de produção (`var.container_template_file_id`) e **mesmo tipo de guest** (LXC unprivileged, `nesting`/`keyctl` habilitados). Paridade de guest é o ponto central do split prod/dev: testar em outro tipo de guest (VM, ou LXC privileged) invalidaria qualquer teste feito aqui antes de promover para produção.
- **Hardware**: 2 CPU Cores, 2048MB RAM, 32GB disco - deliberadamente menor que produção (4 cores / 8192MB / 64GB), reforçando que este nó é para teste, não para carga real.
- **Rede**: IP estático `var.dev_node_ip` (convenção: `.10`-`.19` reservado para infra de produção na VLAN homelab, `.20` em diante para dev/testes), mesma VLAN homelab dos CTs de produção, **sem** VLAN dedicada.
- **Novo** - não há risco de colisão de VMID (211/210 não existe em lugar nenhum hoje), então este recurso pode ser criado normalmente por um `apply`, desde que o VMID 210 esteja de fato livre no cluster.

> **Por que não há VLAN dedicada para dev?** Criar uma 7ª VLAN na OPNsense exigiria reescrever o `config.xml` (interfaces, DHCP, regras de firewall) - o playbook que faz isso é, por consenso da equipe, o mais destrutivo do repositório: um erro de sintaxe ali pode derrubar todas as VLANs simultaneamente, incluindo a de produção. O ganho de isolamento de rede de um único nó dev não justifica esse risco. O isolamento real é obtido por uma **regra de firewall** na própria VLAN homelab (configurada na OPNsense, fora do escopo deste diretório Terraform) bloqueando `var.dev_node_ip` contra a faixa `.10`-`.11` (nós de produção) nas portas usadas pelo Docker Swarm: **2377/tcp** (cluster management), **7946/tcp+udp** (gossip) e **4789/udp** (VXLAN overlay).

> ⚠️ **`lifecycle.ignore_changes = [started]`**: o nó dev fica desligado fora de janelas de teste (é assim que se economiza recurso do host `<no-secundario>`). Sem esse `ignore_changes`, **todo `terraform apply` religaria o CT automaticamente**, mesmo que alguém o tenha desligado de propósito.

> ⚠️ **`initialization.user_account.keys` em LXC só escreve em `/root/.ssh/authorized_keys`** - não cria o usuário `nuvidio` que `ansible/inventories/prod/hosts.ini` usa com `become` via sudo. Quem cria esse usuário é `ansible/playbooks/00-node-bootstrap.yml` (playbook de responsabilidade de outro fluxo de trabalho, não criado a partir deste diretório), que roda como root usando a mesma chave pública e provisiona o usuário de automação antes de qualquer outro playbook assumir `ansible_user=nuvidio`. Rode esse bootstrap antes do restante do Ansible contra o CT 210.

---

## 🔐 Autenticação da API - Usuário de Menor Privilégio (obrigatório)

**Nunca** use `root@pam` com role `Administrator` e `-privsep 0` para o token do Terraform - além de violar o princípio de menor privilégio, `root@pam` **ignora todas as ACLs do Proxmox** (é superusuário incondicional), então "role customizada" não teria efeito nenhum nesse usuário: o token continuaria com acesso total ao cluster independente da role atribuída. O usuário do Terraform precisa ser um usuário **novo e dedicado**, não o root.

No terminal SSH de um dos nós Proxmox (`<no-primario>` ou `<no-secundario>`):

```bash
# 1. Criar um usuário dedicado (realm PVE, não PAM/root)
pveum useradd terraform@pve -comment "Terraform IaC - menor privilégio"

# 2. Criar uma role customizada com apenas os privilégios necessários para
#    criar/gerenciar VMs e CTs - não a role "Administrator" embutida.
pveum role add TerraformProvisioner -privs "VM.Allocate VM.Config.Disk VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Options VM.Config.CDROM VM.Config.HWType VM.PowerMgmt VM.Console Datastore.AllocateSpace SDN.Use Sys.Audit"

# 3. Aplicar a role apenas no escopo necessário (não na raiz "/") e com
#    propagação para os objetos filhos (aclmod), e habilitar privsep=1 no
#    token (o token NÃO herda todos os privilégios do usuário por padrão).
pveum aclmod /vms -user terraform@pve -role TerraformProvisioner
pveum aclmod /storage -user terraform@pve -role TerraformProvisioner
pveum aclmod /sdn -user terraform@pve -role TerraformProvisioner

# 4. Criar o token com privsep=1 (privilege separation ATIVADO - o padrão
#    seguro; privsep=0 faz o token herdar 100% dos privilégios do usuário,
#    o que também derrota o propósito de uma role restrita).
pveum user token add terraform@pve terraform -privsep 1
```

Guarde o `token-id` (`terraform@pve!terraform`) e o `secret-value` gerado - o secret só é exibido uma vez. Se os privilégios acima se mostrarem insuficientes para alguma operação específica (ex.: `hostpci`/passthrough pode exigir `Sys.Modify` em instalações mais restritas), ajuste a role incrementalmente - nunca troque para `Administrator` para "resolver rápido".

---

## 🚀 Guia de Execução Passo a Passo

### 1. Configurar o Arquivo de Variáveis

```bash
cd /home/nuvidio/git/homelab/terraform/proxmox
cp terraform.tfvars.example terraform.tfvars
```

Edite `terraform.tfvars` e preencha **todos** os valores com dados reais do seu ambiente - `terraform.tfvars.example` só tem placeholders em faixas reservadas para documentação (RFC 5737), nunca IPs reais. Nenhuma variável de IP/URL/token tem `default` em `variables.tf` de propósito: se faltar algo, o `plan` deve abortar com erro de variável obrigatória, não silenciosamente assumir um valor público.

### 2. Inicializar

```bash
terraform init   # ou tofu init
```

Isso gera/atualiza `.terraform.lock.hcl` - **esse arquivo deve ser commitado** (não está no `.gitignore`), para fixar o hash exato do build do provider junto com o range de versão em `providers.tf` (`~> 0.66`).

### 3. Produção (VM 100, CTs 200/201, VM 300) - siga o runbook de import no topo deste README antes de continuar.

### 4. Simular o plano de execução

```bash
terraform plan   # ou tofu plan
```

Revise o plano com atenção redobrada a qualquer linha `# forces replacement` em recursos de produção - isso normalmente indica um `import` mal feito ou uma divergência real entre o `.tf` e o que está no cluster.

### 5. Aplicar

```bash
terraform apply   # ou tofu apply
```

---

## 🔗 Integração com Ansible

Depois do provisionamento (Terraform) e da instalação manual em duas fases da OPNsense (ver seção acima), utilize o Ansible para a configuração interna dos sistemas operacionais:

```bash
cd /home/nuvidio/git/homelab/ansible
ansible-playbook site.yml --ask-become-pass
```

Para o CT 210 (dev), rode primeiro `ansible/playbooks/00-node-bootstrap.yml` (cria o usuário `nuvidio` usado pelo restante do inventário) antes de qualquer outro playbook direcionado a esse host.

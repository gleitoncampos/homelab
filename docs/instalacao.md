# Instalação

Do hardware vazio às stacks no ar, em 15 passos, e como operar depois. As
premissas de arquitetura estão em [`CLAUDE.md`](../CLAUDE.md).

**Só contribuir com código?** `make hooks` uma vez, depois
`make check && make secrets` antes do PR. Nada abaixo é necessário — ver
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

Comandos do Ansible a partir de `IaC/ansible/`, os do Terraform de
`IaC/terraform/proxmox/`.

## Os dois nós Proxmox não são iguais

Sem cluster: cada um com API, token e banco de usuários próprios. E o hardware
difere — o primário faz o passthrough da NIC de WAN para o firewall, o
secundário hospeda o NAS.

O playbook roda contra o grupo `proxmox` e trata os dois numa passada só, mas
estas variáveis são **por host**, na linha do nó dentro do `hosts.ini`:

| variável | por quê |
|---|---|
| `ansible_host` | IPs diferentes |
| `backup_storage` | cada nó registra o mesmo share do NAS com um id próprio |
| `proxmox_wan_interface` | só o primário faz passthrough; no secundário a NIC de mesmo nome está em uso |

Comuns aos dois, em `[all:vars]`: `proxmox_gateway_ip`, `proxmox_backup_share`,
`backup_vmids`. O tamanho do ZRAM não é variável — sai de 75% da RAM que o
próprio host reporta.

`make bootstrap` pergunta os dois nomes de nó, os dois IPs e os dois ids de
storage, e escreve tudo na linha certa.

`00-proxmox-setup.yml` roda **duas vezes**: rede, DNS e storage dependem do
firewall e do NAS, então a primeira passada os adia e avisa; a segunda aplica.

| # | alvo | comando |
|---|---|---|
| 1 | máquina de controle | `dnf`/`apt` |
| 2 | dois nós Proxmox | ISO do PVE 9 |
| 3 | acesso e token | `make ssh-keys` + `00-proxmox-api-token.yml` |
| 4 | segredos e inventário | `make bootstrap` |
| 5 | hipervisor, 1ª passada | `00-proxmox-setup.yml` |
| 6 | VM do firewall | `terraform apply -target` |
| 7 | firewall | console + `network/01-opnsense-setup.yml` |
| 8 | demais convidados | `terraform apply` |
| 9 | NAS | `make omv-fs` → `00-omv-setup.yml` |
| 10 | hipervisor, 2ª passada | `00-proxmox-setup.yml` |
| 11 | access point | `network/02-openwrt-setup.yml` → `02-openwrt-exporter` → `make xinfc` + `02-openwrt-nfc` |
| 12 | nós Swarm | `00-node-bootstrap` → `01` → `02` |
| 13 | stacks | `03`, `04`, `05` |
| 14 | backup | `06-backup.yml` |
| 15 | nobreak | `07-nut-ups.yml` |

### Passo 9, duas armadilhas

⚠️ **`make omv-fs` roda ANTES de recriar a VM do NAS, não depois.** Ele coleta
do host vivo os UUIDs dos filesystems de dados e escreve uma proposta em
`.local/`, para você instalar no `group_vars/omv/99-local.yml` (gitignorado).
Depois que a VM for destruída não há de onde coletar. Sem essa lista o playbook
para numa asserção: a camada versionada traz UUIDs fictícios de propósito, para
que o esquecimento falhe alto em vez de montar nada em silêncio.

**Hipervisor reinstalado, discos com dados** (o NAS antigo não existe mais):
colete no nó que tem a controladora, ANTES do `terraform apply` do passo 8 — é o
único momento em que os discos aparecem nele; depois que a VM 300 sobe, ela
assume a controladora pelo passthrough.

```bash
make omv-fs ARGS=--hipervisor    # lê só os discos atrás de omv_hba_pci_id
```

Isso só se aplica a um NAS que já tem dados. Numa instalação do zero, com discos
novos, não há UUID a preservar — formate pela interface do OMV e gere a lista
depois.

⚠️ **O passo exige duas senhas no vault**, e sem elas o play aborta na largada:
a do usuário de compartilhamento Samba e a do segundo administrador
(`omv_admin_password`). As duas saem do `make bootstrap`.

---

## 1. Máquina de controle

```bash
sudo dnf install -y git make python3 ansible-core terraform yamllint ShellCheck libxml2 whois
sudo apt install -y git make python3 ansible-core yamllint shellcheck libxml2-utils whois
```

## 2. Proxmox nos dois nós

ISO do **Proxmox VE 9.x**, nos dois. Next-next-finish, com **hostname e IP
próprios de cada nó** e o LVM-Thin padrão (`local-lvm`). Ligue IOMMU/VT-d na
BIOS de ambos.

Anote os dois hostnames: viram `proxmox_node_primary` e
`proxmox_node_secondary` no Terraform, e precisam bater exatamente.

Sem `pvecm`: os nós ficam independentes.

### Internet dos nós antes do firewall existir

Os passos 5 e 6 baixam pacotes e a ISO do OPNsense **pelo hipervisor**, e o
firewall só existe no passo 7. Até lá, a máquina de controle é o gateway: no
instalador, gateway = o IP dela na LAN, DNS público. Na máquina de controle
(Fedora/firewalld), a cada boot dela — as regras são de runtime:

```bash
sudo firewall-cmd --zone=<zona-de-saida> --add-masquerade
# Com Docker instalado: ele põe a chain FORWARD em policy DROP, e o NAT não
# passa mesmo com o masquerade ligado.
sudo iptables -I DOCKER-USER -i <nic-da-lan> -o <nic-de-saida> -j ACCEPT
sudo iptables -I DOCKER-USER -i <nic-de-saida> -o <nic-da-lan> -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

Prova: `ping 1.1.1.1` a partir de um nó. O `apt update` dá `401` no
`enterprise.proxmox.com` até o passo 5 — esperado, o playbook troca o
repositório. Desfazer depois do passo 10: `--remove-masquerade` e as duas
regras com `-D`.

### Reinstalação: chave de host nova

Host reinstalado (nó ou firewall) apresenta chave de host nova, e o SSH recusa
com `REMOTE HOST IDENTIFICATION HAS CHANGED`. Confira o fingerprint **pela
console** antes de aceitar — é contra isso que o aviso protege:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub         # na console (OPNsense: /conf/sshd/)
ssh-keygen -R <ip>                                        # na máquina de controle, se bater
```

## 3. Acesso e token de API

```bash
git clone <url-do-repo> homelab && cd homelab
make hooks
make bootstrap                   # chave SSH, hosts.ini, vault e tfvars; o passo 4 confere e guarda
make ssh-keys                    # ssh-copy-id em cada host; pede a senha de root
cd IaC/ansible && ansible-playbook playbooks/00-proxmox-api-token.yml
```

`make ssh-keys` falha nos convidados, que ainda não existem — esperado; só os
dois hipervisores precisam passar aqui.

O playbook cria usuário, role e token nos dois nós e escreve
`IaC/terraform/proxmox/tokens.auto.tfvars`. Cria também os PCI mappings das
NICs de WAN e da controladora do NAS: o Terraform aplica com o token, e o PVE só
aceita `hostpci` por endereço do `root@pam`. Pode ser reexecutado: com o arquivo
presente, reconcilia role, ACLs e mappings sem tocar no segredo.

⚠️ O segredo do token é exibido uma única vez. Se o arquivo for perdido:
`pveum user token remove terraform@pve terraform` no nó, e rode o playbook de
novo.

No shell de cada nó, confira os endereços PCI (formato completo, `0000:05:00.0`)
das NICs de WAN e da controladora SATA do NAS. O bootstrap **não** os pergunta:
saem com os valores do template em `IaC/homelab.local.yml`. Diferindo, corrija
lá e rode `make bootstrap` de novo, antes deste playbook:

```bash
lspci -nn | grep -i ethernet
```

## 4. Segredos e inventário

```bash
make bootstrap                    # pergunta senhas de pessoa e nomes de Wi-Fi (Enter aceita a sugestão)
$EDITOR .githooks/secret-patterns.local.txt   # o bootstrap trunca este arquivo
make check && make secrets
```

⚠️ **Guarde no gerenciador de senhas, e confira que estão lá antes de seguir:**

| arquivo | sem ele |
|---|---|
| `IaC/ansible/.ansible_vault_pass` | os vaults são irrecuperáveis |
| `IaC/homelab.local.yml` | **a fonte única** — perdê-la obriga a repetir a entrevista inteira |
| `group_vars/network/99-local.yml` (prod e dev) | os playbooks de rede aplicam endereços de documentação, sem erro |
| `group_vars/omv/99-local.yml` | a asserção do NAS para na largada |
| `inventories/*/hosts.ini` | regenerável a partir da fonte única, mas guardar poupa a regeneração |
| `inventories/*/group_vars/all/vault.yml` | os segredos aleatórios gerados pelo bootstrap |
| `IaC/terraform/proxmox/tokens.auto.tfvars` | rotacionar o token nos dois nós |
| `~/.ssh/homelab_ed25519` | repetir o `make ssh-keys` com senha |

Guarde `.ansible_vault_pass` e `vault.yml` em entradas separadas: juntos, abrem
todos os segredos.

⚠️ **O arquivo de respostas é o que mais importa e o mais fácil de esquecer**,
porque é gerado uma vez e nunca mais olhado: ele regenera inventário, tfvars e
vault. Ver [`runbooks/restauracao.md`](./runbooks/restauracao.md), que o trata
como "o único que basta guardar".

## 5. Hipervisor, primeira passada

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook playbooks/00-proxmox-setup.yml --check --diff
ansible-playbook playbooks/00-proxmox-setup.yml
```

O `--check` para em "Garantir serviço do Fail2ban": em modo de simulação o
pacote não é instalado de verdade, e o serviço não existe. Limite do `--check`,
não defeito — siga para a execução real.

Termina avisando que rede, DNS e storage ficaram para o bloco 10. Esperado.

Reinicie os nós — o IOMMU só vale depois do boot. Antes, confira que o GRUB
gerado já tem o parâmetro (se uma execução anterior falhou no meio, rode
`update-grub` à mão):

```bash
ssh root@<IP-DO-NO> 'grep -c intel_iommu=on /boot/grub/grub.cfg'   # > 0
ssh root@<IP-DO-NO> reboot
ssh root@<IP-DO-NO> 'grep -o intel_iommu=on /proc/cmdline; dmesg | grep -e DMAR -e IOMMU | head'
```

## 6. VM do firewall

```bash
cd ../terraform/proxmox
terraform init
terraform plan
terraform apply -var opnsense_agent_enabled=false -target=proxmox_virtual_environment_vm.opnsense
```

O `-var ...=false` é só desta criação: o FreeBSD ainda não tem o guest agent,
e com ele ligado o apply espera até o timeout. O passo 7 o liga pelo default.

⚠️ **Não aplique um plano salvo sem `-target`** (`terraform plan -out=x` seguido
de `terraform apply x`): ele cria todos os convidados agora, na VLAN que ainda
não existe. O `apply` com `-target` deve mostrar 2 recursos: a ISO e a VM.

Se o `apply` criar a VM e falhar em seguida (ela fica `tainted` no state),
confira a config com `qm config 100` e, estando certa, `terraform untaint
proxmox_virtual_environment_vm.opnsense` antes do próximo `apply` — em vez de
deixá-lo destruir e recriar. Confira depois:

```bash
ssh root@<no-primario> 'qm status 100; qm config 100 | grep -E "^(ide3|hostpci|net0)"'
```

Esperado: `running`, `ide3` com a ISO, `hostpci0/1` com `mapping=`, `net0` sem `tag=`.

⚠️ **Este guia pressupõe parque VAZIO, e é o único caminho suportado:** os
hipervisores chegam recém-instalados e todo convidado nasce daqui. Não há
adoção de convidado pré-existente, e o runbook de `terraform import` que
versões anteriores descreviam **foi removido** — ver o aviso crítico em
[`IaC/terraform/proxmox/README.md`](../IaC/terraform/proxmox/README.md).

⚠️ Se por qualquer motivo o nó de destino **não** estiver vazio, pare: um
`apply` ali cria convidado duplicado com o mesmo IP estático de um que já roda,
e os dois passam a disputar o endereço. A garantia é o `plan` dizer `create`
para todos os recursos e não citar id nenhum já ocupado.

⚠️ Não acrescente `vlan_id` na `network_device` da OPNsense: a tag 802.1Q some
antes de chegar no guest e nada no `plan` acusa.

## 7. Firewall

Pela console noVNC: instale o OPNsense a partir da ISO e defina a senha de root.
O guest agent não é passo de console — está em `opnsense_plugins`. Depois do
reboot, ainda na console:

1. **Opção 1, atribuir interfaces — não aceite a atribuição automática.** Ela põe
   a LAN na primeira NIC física, que é uma das WANs em passthrough. LAN = `vtnet0`
   (o trunk do `vmbr0`); WAN = a NIC que recebe DHCP da provedora secundária, só
   para o firewall ter internet até o playbook; sem VLANs, que o playbook cria.
2. **Opção 2:** IP da LAN = `lan_ipaddr` do `group_vars/network/99-local.yml`,
   sem gateway e sem servidor DHCP.
3. **SSH.** O OPNsense instala com ele desligado, e o playbook entra como root
   por SSH. Pela GUI (`https://<lan_ipaddr>`): System → Settings →
   Administration → Secure Shell, ligado, com login de root e por senha. Depois:
   `ssh-copy-id -i ~/.ssh/homelab_ed25519.pub root@<lan_ipaddr>`.
4. **Internet antes do playbook.** Ele instala os plugins antes de gravar o
   `config.xml`, e aborta se algum faltar. WAN por PPPoE não se configura pela
   console: use a GUI (Interfaces → WAN), ou a WAN por DHCP. Prova:
   `ssh root@<lan_ipaddr> 'ping -c1 1.1.1.1'`.
5. **Nomes das NICs** (`ifconfig -l`). A do PPPoE é fixa em `igc0`
   (`config.xml.j2`); a da 2ª WAN vai em `passthrough_wan2_interface` no
   `99-local.yml`. O default é um nome Linux, que não existe no FreeBSD.

**Os segredos do OPNsense vêm do `make bootstrap`**: ele pergunta a senha de
root que você definiu acima e a senha PPPoE, e gera o resto (senha do admin, no
arquivo de credenciais; chave do admin; certificado da GUI). Vault de uma
execução anterior é completado, não sobrescrito — rode `make bootstrap` de novo
se ele ainda não tinha esses campos.

**O 1º apply roda como root** (`-e ansible_user=root`): o inventário entra pelo
admin nomeado, que ainda não existe — é este apply que o cria, com a chave e o
sudo sem senha. O mesmo apply desliga o login de root; dali em diante, sem o
`-e`.

```bash
cd ../../ansible
$EDITOR inventories/prod/group_vars/network/99-local.yml   # topologia real
ansible-playbook playbooks/network/01-opnsense-setup.yml -e ansible_user=root --check --diff
```

> **Aviso de segurança.** Este playbook escreve `/conf/config.xml` inteiro, sem
> merge. Um valor errado derruba todas as VLANs de uma vez, inclusive a que você
> está usando. Leia o diff antes e mantenha a console do Proxmox aberta. Ele faz
> backup remoto da configuração e recusa aplicar com endereço de documentação.

```bash
ansible-playbook playbooks/network/01-opnsense-setup.yml -e ansible_user=root
ssh <admin>@<lan_ipaddr> sudo -n true && echo admin-ok   # antes de qualquer outra execução
cd ../terraform/proxmox
terraform apply -target=proxmox_virtual_environment_vm.opnsense   # agente pelo default (true)
```

## 8. Demais convidados

⚠️ NAS com dados: colete os filesystems ANTES deste apply (ver "Passo 9, duas
armadilhas"): `make omv-fs ARGS=--hipervisor`.

```bash
terraform plan     # nenhuma linha "# forces replacement"; a VM 100 sem mudança
terraform apply
cd ../../ansible
ansible-playbook playbooks/00-proxmox-setup.yml --tags docker_ct
```

O último comando completa os CTs do Docker: grava `keyctl=1` (o Docker exige em
CT unprivileged, e o PVE só aceita essa feature do root — o Terraform, que
aplica com token, cria só com `nesting`) e reinicia cada CT que a ganhou.
Confira: `ssh <no> pct config 200 | grep features` → `keyctl=1,nesting=1`.

## 9. NAS

⚠️ **Antes, a rede dos hipervisores** — a 2ª passada (passo 10) já aqui, só que
ela adia o storage porque o NAS ainda não existe. Sem ela o NAS é inalcançável:
enquanto o `vmbr0` não é VLAN-aware, os convidados da VLAN 20 ficam numa bridge
(`vmbr0v20`) que o firewall, no `vmbr0`, não enxerga.

```bash
cd ../../ansible
ansible-playbook playbooks/00-proxmox-setup.yml --check --diff   # ler o diff do /etc/network/interfaces
ansible-playbook playbooks/00-proxmox-setup.yml                  # termina dizendo storage ADIADO — esperado
ssh root@<no> reboot                                              # nos dois: os convidados sobem já no vmbr0 VLAN-aware
```

Prova: do firewall, `ping` em cada IP da VLAN 20 (os CTs e o NAS).

O playbook instala o OMV, semeia a configuração, monta os discos e os pools,
cria os usuários, a pasta e o compartilhamento SMB de backup
(`proxmox_backup_share`, no pool sem SSD, só o usuário `smb`) e liga o Samba.
Também troca o hostname para `omv_hostname` (o Samba exige no máximo 15
caracteres) e desliga o cloud-init da VM, que senão devolveria o nome antigo a
cada boot.

```bash
cd ../../ansible
ansible-playbook playbooks/00-omv-setup.yml --check --diff
ansible-playbook playbooks/00-omv-setup.yml
```

Prova: `smbclient //<IP do NAS>/<proxmox_backup_share> -U smb -c ls` entra no
compartilhamento (ele não aparece no `smbclient -L`: `browseable` desligado).

## 10. Hipervisor, segunda passada

Com o NAS no ar, roda de novo — agora entra o storage de backup que a execução
de antes do passo 9 adiou.

> **Aviso de segurança.** Esta passada reescreve `/etc/network/interfaces` e
> `/etc/pve/storage.cfg` por inteiro e desliga o `systemd-resolved`. Valor errado
> tira o hipervisor da rede. Tenha acesso físico ou IPMI, e leia o diff.

```bash
ansible-playbook playbooks/00-proxmox-setup.yml --check --diff
ansible-playbook playbooks/00-proxmox-setup.yml
```

O resumo final não deve listar nada como adiado. Se listar, a dependência não
respondeu — confira o grupo no `hosts.ini` e a porta (445 no NAS, 443 no
firewall).

## 11. Access point

```bash
ansible-playbook playbooks/network/02-openwrt-setup.yml --check --diff
ansible-playbook playbooks/network/02-openwrt-setup.yml
ansible-playbook playbooks/network/02-openwrt-exporter.yml   # métricas, updates pendentes
```

NFC com a rede de visitantes (opcional; só Android — o iOS ignora a etiqueta
Wi-Fi). O binário é compilado do fonte, no SDK oficial do OpenWrt, e não é
versionado: precisa de Docker e baixa a imagem do SDK (~3 GB) na primeira vez.

```bash
make xinfc                                                    # da raiz do repositório
ansible-playbook playbooks/network/02-openwrt-nfc.yml
```

⚠️ A senha gravada no chip é legível por qualquer celular que encoste no AP —
por isso o playbook só grava a rede de visitantes.

## 12. Nós do Swarm

```bash
ansible-playbook playbooks/00-node-bootstrap.yml -e target=<host>   # um por CT novo
ansible-playbook playbooks/01-bootstrap-os.yml --ask-become-pass
ansible-playbook playbooks/02-bootstrap-cluster.yml --ask-become-pass
```

⚠️ Sem `-e target=`, o playbook cai num grupo inexistente em produção, imprime
`no hosts matched` e **sai com código 0**. A falha só aparece no passo seguinte.

## 13. Stacks

⚠️ Faça o push antes: o Portainer clona `refs/heads/main` do GitHub.

```bash
ansible-playbook playbooks/03-stack-monitoring.yml --ask-become-pass
ansible-playbook playbooks/04-stack-management.yml --ask-become-pass
ansible-playbook playbooks/05-stack-utilities.yml --ask-become-pass
```

## 14. Backup

```bash
ansible-playbook playbooks/06-backup.yml
```

Sem `backup_vmids` declarada, cada nó agenda todos os convidados que hospeda.

## 15. Nobreak

Depois de o cabo USB do nobreak estar ligado a um dos nós Proxmox:

```bash
ansible-playbook playbooks/07-nut-ups.yml
```

O nó que enumerou o nobreak vira `primary` (driver, `upsd`, `upsmon`); os
demais viram `secondary` (só `upsmon`). Não há nada a declarar no inventário: a
detecção é pelo par VID:PID no barramento USB. Sem nobreak em nó nenhum, o
playbook avisa e não altera nada — rode de novo depois de ligar o cabo.

⚠️ O nó primário **compila o NUT** a partir de um commit fixado: o driver deste
nobreak entrou no master depois da última versão lançada, e o pacote do Debian
não o tem. A primeira execução leva alguns minutos; as seguintes pulam o build.

Conferência:

```bash
ssh <no-primario> /usr/local/bin/upsc nobreak@localhost
```

## Conferência

```bash
ansible -m ping all
ssh <manager> docker node ls        # ambos Ready
ssh <manager> docker service ls     # nenhuma réplica 0/1
```

---

## Aplicar por alvo

`site.yml` é o caminho do zero ao fim. No dia a dia, chame o playbook direto —
isso restringe o **passo**. `--limit` restringe **hosts**, nunca playbooks:
`site.yml --limit proxmox` continua importando os sete, e seis rodam vazios.

| o que mexer | comando, a partir de `IaC/ansible/` | grupo |
|---|---|---|
| host Proxmox | `ansible-playbook playbooks/00-proxmox-setup.yml` | `proxmox` |
| NAS (OpenMediaVault) | `ansible-playbook playbooks/00-omv-setup.yml` | `omv` |
| token de API do Proxmox | `ansible-playbook playbooks/00-proxmox-api-token.yml` | `proxmox` |
| SO e Docker dos nós | `ansible-playbook playbooks/01-bootstrap-os.yml` | `homelab` |
| Swarm, Portainer e Traefik | `ansible-playbook playbooks/02-bootstrap-cluster.yml` | `homelab_managers` e `homelab_workers` |
| stacks de observabilidade | `ansible-playbook playbooks/03-stack-monitoring.yml` | `homelab_managers` |
| nobreak (NUT) | `ansible-playbook playbooks/07-nut-ups.yml` | `proxmox` |
| stacks de gestão | `ansible-playbook playbooks/04-stack-management.yml` | `homelab_managers` |
| stacks de utilitários | `ansible-playbook playbooks/05-stack-utilities.yml` | `homelab_managers` |
| backup dos convidados | `ansible-playbook playbooks/06-backup.yml` | `proxmox` |
| firewall | `ansible-playbook playbooks/network/01-opnsense-setup.yml` | `opnsense` |
| access point | `ansible-playbook playbooks/network/02-openwrt-setup.yml` | `openwrt` |
| exportador do AP | `ansible-playbook playbooks/network/02-openwrt-exporter.yml` | `openwrt` |
| NFC do AP | `make xinfc` + `ansible-playbook playbooks/network/02-openwrt-nfc.yml` | `openwrt` |

O inventário default é o de produção (`ansible.cfg`); para dev, passe
`-i inventories/dev/hosts.ini`.

⚠️ **`--limit` erra de dois jeitos, e só um avisa.** Nome inexistente em
inventário nenhum para com `ERROR! Specified inventory, host pattern and/or
--limit leaves us with no hosts to target.` e código 1. Já um nome que existe
mas **não intersecta** o grupo do playbook imprime `hosts (0)`, não executa nada
e **sai com código 0**. Confira antes com `--list-hosts`, que resolve o padrão
sem conectar em host nenhum.

Para ver o que mudaria antes de mudar, `--check --diff` — que é também como se
descobre que um equipamento divergiu do repositório.

## O que `site.yml` faz, e o que fica de fora

Importa sete playbooks nesta ordem: host Proxmox, host OpenMediaVault, sistema
operacional, cluster Swarm e as três de stacks (`03`, `04`, `05`).

⚠️ **Os dois primeiros reconfiguram o hipervisor e o NAS.** O
`00-proxmox-setup.yml` reescreve `/etc/apt/sources.list`, `/etc/hosts`,
`/etc/network/interfaces` e `/etc/pve/storage.cfg`, remove os repositórios
Enterprise, desativa o `systemd-resolved` e desliga os serviços de HA.

⚠️ Numa instalação do zero, uma execução única de `site.yml` deixa rede, DNS e
storage pendentes (as duas passadas, acima), e o único aviso é o resumo final.

Ficam **fora** de `site.yml`, disparados à mão: `00-node-bootstrap.yml`,
`00-proxmox-api-token.yml`, `06-backup.yml`, `07-nut-ups.yml` e os de
`playbooks/network/`.

Não rode `docker swarm init` nem `docker network create traefik_public` à mão: o
`02-bootstrap-cluster.yml` faz os dois, e configuração manual não parametrizada
é perdida na próxima execução.

---

## Ainda manual

Instalar o Proxmox e o OPNsense (instaladores interativos), IOMMU na BIOS, a
senha de root no `make ssh-keys`, escolher a NIC de WAN, o reboot do bloco 5, e
criar a pasta compartilhada no OMV.

⚠️ Nada aqui foi exercitado contra hardware real: o que está verificado é
sintaxe, schema do provider, render do inventário e os checksums das imagens.

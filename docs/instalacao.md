# Instalação

Do hardware vazio às stacks no ar, em 14 passos, e como operar depois. As
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
| 11 | access point | `network/02-openwrt-setup.yml` |
| 12 | nós Swarm | `00-node-bootstrap` → `01` → `02` |
| 13 | stacks | `03`, `04`, `05` |

### Passo 9, duas armadilhas

⚠️ **`make omv-fs` roda ANTES de recriar a VM do NAS, não depois.** Ele coleta
do host vivo os UUIDs dos filesystems de dados e escreve uma proposta em
`.local/`, para você instalar no `group_vars/omv/99-local.yml` (gitignorado).
Depois que a VM for destruída não há de onde coletar. Sem essa lista o playbook
para numa asserção: a camada versionada traz UUIDs fictícios de propósito, para
que o esquecimento falhe alto em vez de montar nada em silêncio.

Isso só se aplica a um NAS que já tem dados. Numa instalação do zero, com discos
novos, não há UUID a preservar — formate pela interface do OMV e gere a lista
depois.

⚠️ **O passo exige duas senhas no vault**, e sem elas o play aborta na largada:
a do usuário de compartilhamento Samba e a do segundo administrador
(`omv_admin_password`). As duas saem do `make bootstrap`.
| 14 | backup | `06-backup.yml` |
| 15 | nobreak | `07-nut-ups.yml` |

---

## 1. Máquina de controle

```bash
sudo dnf install -y git make python3 ansible-core terraform yamllint ShellCheck libxml2
sudo apt install -y git make python3 ansible-core yamllint shellcheck libxml2-utils
```

## 2. Proxmox nos dois nós

ISO do **Proxmox VE 9.x**, nos dois. Next-next-finish, com **hostname e IP
próprios de cada nó** e o LVM-Thin padrão (`local-lvm`). Ligue IOMMU/VT-d na
BIOS de ambos.

Anote os dois hostnames: viram `proxmox_node_primary` e
`proxmox_node_secondary` no Terraform, e precisam bater exatamente.

Sem `pvecm`: os nós ficam independentes.

## 3. Acesso e token de API

```bash
git clone <url-do-repo> homelab && cd homelab
make hooks
make bootstrap ARGS=--dry-run    # gera a chave SSH
make ssh-keys                    # ssh-copy-id em cada host; pede a senha de root
cd IaC/ansible && ansible-playbook playbooks/00-proxmox-api-token.yml
```

O playbook cria usuário, role e token nos dois nós e escreve
`IaC/terraform/proxmox/tokens.auto.tfvars`.

⚠️ O segredo do token é exibido uma única vez. Se o arquivo for perdido:
`pveum user token remove terraform@pve terraform` no nó, e rode o playbook de
novo.

No shell de um dos nós, anote o endereço PCI da NIC de WAN:

```bash
lspci -nn | grep -i ethernet
```

## 4. Segredos e inventário

```bash
make bootstrap
$EDITOR .githooks/secret-patterns.local.txt   # o bootstrap trunca este arquivo
make check && make secrets
```

⚠️ Guarde no gerenciador de senhas: `IaC/ansible/.ansible_vault_pass`, os dois
`hosts.ini` e os dois `group_vars/network/99-local.yml`. Sem a senha os vaults
são irrecuperáveis; sem o `99-local.yml` os playbooks de rede aplicam endereços
de documentação, sem erro.

## 5. Hipervisor, primeira passada

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook playbooks/00-proxmox-setup.yml --check --diff
ansible-playbook playbooks/00-proxmox-setup.yml
```

Termina avisando que rede, DNS e storage ficaram para o bloco 10. Esperado.

Reinicie os nós — o IOMMU só vale depois do boot:

```bash
ssh root@<IP-DO-NO> reboot
dmesg | grep -e DMAR -e IOMMU | head
```

## 6. VM do firewall

```bash
cd ../terraform/proxmox
terraform init
terraform plan
terraform apply -target=proxmox_virtual_environment_vm.opnsense
```

⚠️ `apply` direto só é seguro num parque vazio. Se algum convidado já existir,
siga o runbook de `terraform import` em
[`IaC/terraform/proxmox/README.md`](../IaC/terraform/proxmox/README.md) —
sem ele o Terraform cria um duplicado com o mesmo IP estático de um nó vivo.

⚠️ Não acrescente `vlan_id` na `network_device` da OPNsense: a tag 802.1Q some
antes de chegar no guest e nada no `plan` acusa.

## 7. Firewall

Pela console noVNC: instale o OPNsense a partir da ISO, defina IP da LAN e senha
de root. O guest agent não é passo de console — está em `opnsense_plugins`.

```bash
cd ../../ansible
$EDITOR inventories/prod/group_vars/network/99-local.yml   # topologia real
ansible-playbook playbooks/network/01-opnsense-setup.yml --check --diff
```

> **Aviso de segurança.** Este playbook escreve `/conf/config.xml` inteiro, sem
> merge. Um valor errado derruba todas as VLANs de uma vez, inclusive a que você
> está usando. Leia o diff antes e mantenha a console do Proxmox aberta. Ele faz
> backup remoto da configuração e recusa aplicar com endereço de documentação.

```bash
ansible-playbook playbooks/network/01-opnsense-setup.yml
cd ../terraform/proxmox
terraform apply -var opnsense_agent_enabled=true -target=proxmox_virtual_environment_vm.opnsense
```

## 8. Demais convidados

```bash
terraform plan     # nenhuma linha "# forces replacement"
terraform apply
```

## 9. NAS

O playbook instala o OMV, semeia a configuração e cria o usuário SMB.

```bash
cd ../../ansible
ansible-playbook playbooks/00-omv-setup.yml --check --diff
ansible-playbook playbooks/00-omv-setup.yml
```

Na UI do OMV, crie a pasta compartilhada com o nome que você deu em
`proxmox_backup_share`.

## 10. Hipervisor, segunda passada

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
```

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

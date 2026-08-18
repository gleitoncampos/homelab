# Restauração dos convidados do Swarm

Procedimento de verificação do backup, e de recuperação real. Quem cria o
backup que este runbook restaura é
[`IaC/ansible/playbooks/06-backup.yml`](../../IaC/ansible/playbooks/06-backup.yml),
agendado no Proxmox — não faz parte de `site.yml`, roda à parte.

⚠️ **Este runbook precisa ser executado pelo menos uma vez antes de qualquer
mudança em produção.** Backup nunca restaurado é uma suposição, não um
mecanismo.

## Antes de tudo: o convidado é LXC ou VM?

Os comandos são diferentes e **não** são intercambiáveis: `qmrestore` contra um
CT LXC falha, e `pct restore` contra uma VM QEMU também. Na topologia-alvo
([`IaC/terraform/proxmox/main.tf`](../../IaC/terraform/proxmox/main.tf)) os nós
do Swarm (`docker_swarm_node01`, `docker_swarm_node02`) e o nó de dev
(`docker_dev_node`) são **CT LXC**; só o firewall (`opnsense`) e o NAS
(`omv_nas`) são **VM QEMU**.

Na dúvida, pergunte ao cluster em vez de deduzir:

```bash
pvesh get /cluster/resources --type vm --output-format json | python3 -c \
  'import json,sys; [print(r["vmid"], r["type"], r.get("name","")) for r in json.load(sys.stdin)]'
```

A coluna do meio diz `lxc` ou `qemu`. Siga a seção correspondente.

## Verificação periódica (trimestral)

Restaura para um **VMID novo** (999), sem tocar no convidado de produção.

Vale para os dois tipos: o arquivo de backup se lista igual.

```bash
pvesm list <STORAGE> --content backup
```

### Convidado LXC (nós do Swarm e de dev)

```bash
# 1. Restaurar para um VMID livre (999), sem iniciar.
#    ⚠️ A ordem dos argumentos é o INVERSO do qmrestore: aqui o VMID vem
#    primeiro, o arquivo depois.
pct restore 999 <STORAGE>:backup/<ARQUIVO> --unique 1

# 2. Subir com a rede DESCONECTADA, para não haver dois nós Swarm com a mesma
#    identidade na rede ao mesmo tempo
pct set 999 --net0 name=eth0,bridge=vmbr0,link_down=1
pct start 999

# 3. Entrar no container (não por SSH — a rede está desligada)
pct enter 999
```

⚠️ `pct enter` abre um shell de root direto, e `pct console 999` abre o console
do container. **Nenhum dos dois depende de console serial configurado** — a
pendência de acrescentar `serial0` ao convidado antes de restaurar, que o
caminho QEMU exigia, simplesmente não existe para LXC.

### Convidado QEMU (firewall e NAS)

```bash
# 1. Restaurar para um VMID livre (999), sem iniciar
qmrestore <STORAGE>:backup/<ARQUIVO> 999 --unique 1

# 2. Subir com a rede DESCONECTADA
qm set 999 --net0 model=virtio,bridge=vmbr0,link_down=1
qm start 999

# 3. Entrar pelo console (não por SSH — a rede está desligada)
qm terminal 999
```

⚠️ `qm terminal` fala com um console **serial**, e a VM só tem um se alguém o
configurou. Se o comando travar sem prompt, ou a VM não tiver `serial0`, use o
console gráfico (noVNC) da GUI do Proxmox, ou acrescente o dispositivo antes de
iniciar:

```bash
qm set 999 --serial0 socket
```

### Dentro do convidado restaurado

Confirmar que o estado veio junto:

```bash
ls -la /srv/containers/traefik/data/acme.json   # existe e não está vazio
ls /srv/containers/portainer/                    # BoltDB presente
docker info 2>/dev/null | grep -i 'swarm'        # estado do Swarm preservado
```

Esperado: os três respondem. Se `acme.json` estiver vazio ou ausente, o backup
não cobre o que se pensa que cobre — **pare e investigue antes de confiar nele**.

### Destruir a cópia de teste

```bash
# LXC
pct stop 999 && pct destroy 999

# QEMU
qm stop 999 && qm destroy 999
```

⚠️ Este passo não é opcional. Uma cópia de teste esquecida ligada, com a mesma
identidade de Swarm, entra em conflito no momento em que alguém reconectar a
rede dela.

## Recuperação real

Só em caso de perda. O convidado de produção precisa estar **parado** antes.

```bash
# LXC — VMID primeiro, arquivo depois
pct stop <VMID>
pct restore <VMID> <STORAGE>:backup/<ARQUIVO> --force 1
pct start <VMID>
```

```bash
# QEMU — arquivo primeiro, VMID depois
qm stop <VMID>
qmrestore <STORAGE>:backup/<ARQUIVO> <VMID> --force 1
qm start <VMID>
```

Depois de subir, confirmar o cluster:

```bash
docker node ls          # o nó aparece como Ready/Active, Leader
docker service ls       # todos os serviços com réplica esperada
```

## Conjunto de recuperação

O backup do convidado **não** cobre estes arquivos, que vivem na estação de
trabalho e sem os quais a automação não roda:

| arquivo | sem ele |
|---|---|
| `IaC/ansible/.ansible_vault_pass` | os vaults são irrecuperáveis |
| `IaC/homelab.local.yml` | regenera inventário e tfvars — é o único que basta guardar |

Guarde os dois no mesmo cofre, e confira a cópia junto com a verificação
trimestral acima.

`IaC/terraform/proxmox/terraform.tfstate` — **ainda não existe.** Hoje nada
está sob gestão do Terraform (só provisionamento manual); o arquivo passa a
existir, e a entrar de fato neste conjunto de recuperação, assim que o
procedimento de `terraform import` descrito no aviso crítico de
[`IaC/terraform/proxmox/README.md`](../../IaC/terraform/proxmox/README.md) for
executado. Até lá, perder a estação de trabalho não perde rastro nenhum de
Terraform — porque não há rastro para perder.

## Limite conhecido

⚠️ O backup vai para um share no NAS. Se o NAS morrer junto com o hypervisor —
incêndio, surto elétrico, ransomware que alcance o share — perde-se os dois.
Isto **não** satisfaz a regra 3-2-1. Uma cópia fora do local é trabalho
pendente; até lá, o mecanismo protege contra falha de disco e erro de operação,
não contra perda do site.

## Histórico de verificação

| data | resultado | quem |
|---|---|---|

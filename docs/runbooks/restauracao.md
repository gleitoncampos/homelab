# Restauração da VM do Swarm

Procedimento de verificação do backup, e de recuperação real.

⚠️ **Este runbook precisa ser executado pelo menos uma vez antes de qualquer
mudança em produção.** Backup nunca restaurado é uma suposição, não um
mecanismo.

## Verificação periódica (trimestral)

Restaura para um **VMID novo**, sem tocar na VM de produção.

```bash
# 1. Listar os backups disponíveis
pvesm list <STORAGE> --content backup

# 2. Restaurar para um VMID livre (999), sem iniciar
qmrestore <STORAGE>:backup/<ARQUIVO> 999 --unique 1

# 3. Subir com a rede DESCONECTADA, para não haver dois nós Swarm
#    com a mesma identidade na rede ao mesmo tempo
qm set 999 --net0 model=virtio,bridge=vmbr0,link_down=1
qm start 999

# 4. Entrar pelo console (não por SSH — a rede está desligada)
qm terminal 999
```

Dentro da VM restaurada, confirmar que o estado veio junto:

```bash
ls -la /srv/containers/traefik/data/acme.json   # existe e não está vazio
ls /srv/containers/portainer/                    # BoltDB presente
docker info 2>/dev/null | grep -i 'swarm'        # estado do Swarm preservado
```

Esperado: os três respondem. Se `acme.json` estiver vazio ou ausente, o backup
não cobre o que se pensa que cobre — **pare e investigue antes de confiar nele**.

```bash
# 5. Destruir a cópia de teste
qm stop 999 && qm destroy 999
```

⚠️ O passo 5 não é opcional. Uma VM de teste esquecida ligada, com a mesma
identidade de Swarm, entra em conflito no momento em que alguém reconectar a
rede dela.

## Recuperação real

Só em caso de perda. A VM de produção precisa estar **parada** antes.

```bash
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

O backup da VM **não** cobre estes arquivos, que vivem na estação de trabalho e
sem os quais a automação não roda:

| arquivo | sem ele |
|---|---|
| `IaC/ansible/.ansible_vault_pass` | os vaults são irrecuperáveis |
| `IaC/homelab.local.yml` | regenera inventário e tfvars — é o único que basta guardar |
| `IaC/terraform/proxmox/terraform.tfstate` | o Terraform perde o rastro da infraestrutura |

Guarde os três no mesmo cofre, e confira a cópia junto com a verificação
trimestral acima.

## Limite conhecido

⚠️ O backup vai para um share no NAS. Se o NAS morrer junto com o hypervisor —
incêndio, surto elétrico, ransomware que alcance o share — perde-se os dois.
Isto **não** satisfaz a regra 3-2-1. Uma cópia fora do local é trabalho
pendente; até lá, o mecanismo protege contra falha de disco e erro de operação,
não contra perda do site.

## Histórico de verificação

| data | resultado | quem |
|---|---|---|

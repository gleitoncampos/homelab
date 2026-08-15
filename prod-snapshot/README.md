# prod-snapshot — ferramentas de coleta do estado real

Aqui ficam só os **scripts**, versionados. O que eles coletam vai para
`.local/snapshot/`, fora do controle de versão.

Existem para uma coisa: comparar o que **de fato roda** nos hosts com o que está
descrito no repositório.

O motivo: os arquivos de config em `docker/monitoring/swarm/` nunca foram
implantados por automação nenhuma. O `docker/monitoring/swarm/prometheus.yml`
do repo contém sintaxe **Alloy River**, não YAML do Prometheus — `scrape_configs`
no repositório inteiro: zero. Logo o arquivo que o Prometheus lê hoje no host é
outro, e não existe no git. Templatar a partir da cópia do repo seria templatar
ficção.

**Nada do que é coletado deve ser commitado.** Se um arquivo for interessante
como referência, ele entra no repositório já **sanitizado** e por caminho normal,
nunca copiado direto de `.local/`.

---

## Como coletar

```bash
cd prod-snapshot
./collect.sh -n            # dry-run: mostra o que buscaria, sem escrever nada
./collect.sh               # coleta de verdade
```

Os hosts **não são hardcoded**: saem do inventário do Ansible. Isso mantém IP
real fora de arquivo rastreado (o repositório é público) e faz o mesmo script
servir prod e dev.

| flag | efeito |
|---|---|
| `-i <arquivo>` | outro inventário (default: `ansible/inventories/prod/hosts.ini`) |
| `-l node1,node2` | só estes hosts |
| `-g proxmox,omv` | só hosts destes grupos |
| `-n` | dry-run |
| `--no-portainer` | pula a parte de API |

Cada host vira um diretório próprio em `.local/snapshot/` (`node1/`, `opnsense/`) e
o que é coletado depende do grupo a que ele pertence no inventário:

| grupo | o que vem | como |
|---|---|---|
| `homelab_managers`, `homelab_workers` | configs sidecar de `/srv/containers`, `FILE-TYPES.txt`, `OWNERSHIP.txt`, `/etc/rsyslog.d`, estado do Swarm, portas publicadas, labels de serviço, `ufw status` | ssh + `sudo tar` |
| `proxmox` | `interfaces`, `storage.cfg`, `datacenter.cfg`, `user.cfg`, configs de LXC/VM, `pvesh get /cluster/resources` | ssh + `pvesh` |
| `omv` | `config.xml` do OMV, `smb.conf`, `exports`, `fstab`, `blkid`, `lsblk` | ssh |
| `opnsense` | `/conf/config.xml`, `ifconfig`, regras do `pfctl` | ssh **ou** API |
| `openwrt` | `/etc/config/` inteiro, `ubus call system board`, `iwinfo` | ssh (scp/tar) |
| — | `.local/snapshot/_portainer/stacks.json`, `endpoints.json`, `env-keys.txt` | API |

Ausência é informação: caminho que não existe vira `AUSENTE` no `MANIFEST.txt`,
não erro.

### Dois itens merecem atenção

**`FILE-TYPES.txt`** responde qual caminho é arquivo e qual virou **diretório**.
O Docker materializa diretório quando o bind mount aponta para caminho
inexistente. Onde isso já aconteceu, o módulo `template` da Fase 3 falha com
`Destination is a directory` e a fase trava até o diretório sair.

**`.local/snapshot/_portainer/stacks.json`** é o item de maior valor da coleta: é onde vivem os
**FQDNs reais** e os segredos digitados no formulário da stack — classe B, só no
BoltDB em `/srv/containers/portainer/`, fora do git, sem registro nem rotação.
Sem ele não dá para saber quais chaves o vault precisa de verdade, só as que dá
para inferir dos `${VAR}` dos compose.

## Autenticação

Nenhuma senha é lida de arquivo. O script pergunta a do Portainer no terminal,
ou aceita variáveis de ambiente para rodar sem interação:

```bash
PORTAINER_PASSWORD='...' ./collect.sh              # sem prompt
PORTAINER_URL=https://portainer.dominio ./collect.sh   # em vez de http://<manager>:9000

# OPNsense por API (System > Access > Users > API keys), alternativa ao SSH root:
OPNSENSE_KEY=... OPNSENSE_SECRET=... ./collect.sh -g opnsense
```

## ⚠️ O que vem daqui é dado real

`stacks.json` traz os **valores** do `Env`, não só os nomes. O `config.xml` da
OPNsense traz hashes de senha e chaves. O script põe tudo em `600`/`700` ao
terminar.

Nunca cole esses arquivos inteiros num chat, num issue ou num commit. Para
compartilhar quais chaves existem sem expor valor, use o arquivo que o próprio
script gera:

```bash
cat ../.local/snapshot/_portainer/env-keys.txt   # só nomes de chave, seguro
```

É essa saída que fecha o conteúdo dos templates da Fase 3 e a lista de chaves do
`vault.yml.example`.

## Guardas

O script se recusa a rodar se `.local/` não estiver sendo ignorada pelo git — a
checagem acontece **antes** de escrever o primeiro byte. Ao terminar, ele roda
`git status` e sai com erro se algo aparecer. Se isso
acontecer, o `.gitignore` está errado e o próximo `git add -A` vazaria arquivos
reais num repositório público: pare e conserte antes de commitar.

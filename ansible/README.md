# 🚀 Infraestrutura como Código (IaC) com Ansible - Homelab

Este diretório centraliza toda a automação de **Infraestrutura como Código (IaC)** do Homelab. O objetivo é garantir que o provisionamento do servidor, a segurança do sistema operacional e o deploy das aplicações sejam **100% automatizados, idênticos, modulares e reprodutíveis**.

---

## 📂 Estrutura Modular: Inventários, Playbooks & Tasks

```text
ansible/
├── README.md                  # Este arquivo (Guia de uso e comandos)
├── site.yml                   # Master Playbook (Executa todo o Homelab do zero em sequência)
├── ansible.cfg                # inventory = inventories/prod/hosts.ini (default explícito)
├── requirements.yml            # Collections Galaxy exigidas (community.docker, community.general, ansible.posix)
├── inventories/
│   ├── _common/
│   │   └── all.yml            # Camada 1: comum aos dois ambientes (nunca segredo, nunca o que difere)
│   ├── vault.yml.example      # Modelo único do Vault — copiar para cada ambiente, ver Passo 0
│   ├── prod/
│   │   ├── hosts.ini          # ⚠️ GITIGNORADO — inventário real (IPs internos), você cria localmente
│   │   ├── hosts.ini.example  # Modelo rastreado, base para criar o hosts.ini real
│   │   └── group_vars/
│   │       ├── all/
│   │       │   ├── 00-common.yml  # symlink -> ../../../_common/all.yml
│   │       │   ├── main.yml       # Camada 2: só o que difere de dev (homelab_env, branch, ACME)
│   │       │   └── vault.yml      # Camada 3: segredos de PROD, cifrado (gitignorado, você cria)
│   │       └── network.yml        # Variáveis de rede (OPNsense/OpenWrt) — só existem em prod
│   └── dev/
│       ├── hosts.ini          # ⚠️ GITIGNORADO, idem prod
│       ├── hosts.ini.example
│       └── group_vars/
│           ├── all/
│           │   ├── 00-common.yml  # symlink -> ../../../_common/all.yml
│           │   ├── main.yml       # homelab_env: dev, branch develop, ACME staging
│           │   └── vault.yml      # segredos de DEV — valores DIFERENTES dos de prod
│           └── network.yml        # dev não tem OPNsense/OpenWrt próprio: só a VLAN do CT 210
├── tasks/
│   ├── portainer_login.yml    # Task reutilizável de Login & Token JWT na API do Portainer
│   ├── portainer_stack.yml    # Create-or-update de stack via API (usada por 02, 04 e 05)
│   └── validate_vault.yml     # Exige que os segredos existam antes de provisionar
└── playbooks/
    ├── 00-node-bootstrap.yml     # Ponte root -> usuário de automação, contra CT recém-criado
    ├── 00-proxmox-setup.yml      # Setup do host Proxmox VE
    ├── 00-omv-setup.yml          # Setup do NAS OpenMediaVault
    ├── 01-bootstrap-os.yml       # Setup do SO, Docker CE, UFW, Fail2ban e diretórios /srv
    ├── 02-bootstrap-cluster.yml  # Swarm + Portainer Native + Traefik Ingress (Bootstrap Base)
    ├── 03-stack-monitoring.yml   # Deploy de Observabilidade (Grafana/Loki/Prometheus/Alloy/Statping)
    ├── 04-stack-management.yml  # Deploy de Gestão & Finanças (Paperless/Actual/Vikunja/Wallos/Thrifty)
    ├── 05-stack-utilities.yml   # Deploy de Utilitários (Cronicle/Linkwarden/Stirling-PDF/ByteStash/IT-Tools)
    └── network/
        ├── 01-opnsense-setup.yml # Provisionamento do firewall (só roda contra o inventário de prod)
        └── 02-openwrt-setup.yml  # Provisionamento do AP/roteador Wi-Fi
```

> `03-stack-monitoring.yml` ainda não foi migrado para `tasks/portainer_stack.yml` — continua
> com os blocos antigos de `POST .../create/swarm/repository` aceitando `[200, 409, 500]` como
> sucesso. Ver `TODO.md` para o acompanhamento dessa pendência.

### Por que `group_vars/` mudou de lugar

Antes deste restructure, o layout era `ansible/group_vars/all/main.yml` com um único
`ansible/inventory/hosts.ini`. Isso **parecia** funcionar porque `site.yml` (que importa todos os
playbooks) carregava as variáveis — mas qualquer comando que rodasse **um playbook isolado**
(`ansible-playbook playbooks/01-bootstrap-os.yml`, por exemplo) falhava com erro de variável
indefinida (`timezone`, `system_user`, etc.).

A causa: o Ansible carrega `group_vars/` **adjacente ao arquivo de inventário** e **adjacente ao
playbook que está rodando** — não existe um `group_vars/` "global" para o projeto todo. Com
`inventory = inventory/hosts.ini` no `ansible.cfg` e `group_vars/all/` vivendo em `ansible/`
(não dentro de `ansible/inventory/`), aquele diretório de variáveis nunca era, de fato,
adjacente a nada que o Ansible resolvesse sozinho — só funcionava via `site.yml`, que por acaso
roda com o cwd em `ansible/`.

A correção foi mover `group_vars/` para **dentro** de cada inventário
(`inventories/<env>/group_vars/`), que é onde o Ansible sempre olha primeiro. Como efeito
colateral (desejado), isso também resolveu a separação entre prod e dev: cada inventário agora
carrega só as suas próprias variáveis.

### Precedência de três camadas

Dentro de `group_vars/all/`, o Ansible faz merge de **todos** os arquivos, em ordem alfabética —
o que carrega depois **sobrescreve** o que carrega antes, chave por chave:

```
00-common.yml  (symlink p/ inventories/_common/all.yml)
      │  comum aos dois ambientes: timezone, pacotes, portas, fqdn_* derivados
      ▼
main.yml       (por ambiente: prod/ ou dev/)
      │  só o que DIFERE entre os dois: homelab_env, github_repo_branch, acme_caserver
      ▼
vault.yml      (por ambiente, cifrado — VENCE por último)
         segredo ou nocivo em repo público: homelab_domain, senhas, tokens, ssh_public_key
```

Nunca redeclare uma variável em mais de uma camada por engano — em especial `fqdn_*`, que é
**derivado** de `homelab_domain` em `00-common.yml`: um `fqdn_*` fixo em `vault.yml` sobrescreveria
o mapa derivado em silêncio, porque `vault.yml` carrega depois.

---

## 🔐 Passo 0 (obrigatório): Ansible Vault

**Nenhum playbook roda sem isto.** Todos os segredos (senha do Portainer, token da
Cloudflare, senhas de banco, PSKs de Wi-Fi) vivem em arquivos criptografados, **um por
ambiente**. Não existe valor default: playbook sem vault falha na largada, de propósito.

### Instalar as collections (uma vez, ou após clonar)

```bash
cd ansible/
ansible-galaxy collection install -r requirements.yml
```

### Criar o vault (uma vez por ambiente)

```bash
cd ansible/

# 1. Senha que protege o vault (arquivo gitignorado, permissão 600) — a MESMA
#    senha abre o vault de prod e o de dev (ver inventories/vault.yml.example).
openssl rand -base64 32 > .ansible_vault_pass
chmod 600 .ansible_vault_pass

# 2. Partir do modelo único e preencher com valores reais — um arquivo por ambiente
cp inventories/vault.yml.example inventories/prod/group_vars/all/vault.yml
cp inventories/vault.yml.example inventories/dev/group_vars/all/vault.yml
$EDITOR inventories/prod/group_vars/all/vault.yml   # valores de PRODUÇÃO
$EDITOR inventories/dev/group_vars/all/vault.yml    # valores de DEV — nunca reaproveitar os de prod

# 3. Criptografar os dois
ansible-vault encrypt inventories/prod/group_vars/all/vault.yml
ansible-vault encrypt inventories/dev/group_vars/all/vault.yml
```

Gerar valores fortes: `openssl rand -base64 32` para senhas, `openssl rand -hex 32`
para chaves de assinatura (`paperless_secret_key`).

### Operar no dia a dia

O `ansible.cfg` já aponta `vault_password_file = .ansible_vault_pass`, então nenhum
comando precisa de `--ask-vault-pass`:

```bash
ansible-vault edit inventories/prod/group_vars/all/vault.yml    # editar prod
ansible-vault view inventories/prod/group_vars/all/vault.yml    # apenas ver
ansible-vault rekey inventories/prod/group_vars/all/vault.yml   # trocar a senha do vault (repita p/ dev)
ansible-playbook site.yml                                       # roda PROD (inventário default do ansible.cfg)
ansible-playbook -i inventories/dev/hosts.ini site.yml          # roda DEV (sempre explícito)
```

> ⚠️ **Nunca reintroduza `| default('...')` em variável de segredo.** O default
> converte "segredo ausente" em "senha pública" e o playbook reporta sucesso. Foi
> exatamente assim que o Portainer — que monta o socket do Docker e portanto é
> root-equivalente nos dois nós — chegou a ser provisionado com uma senha versionada
> no repositório.

### ⚠️ `hosts.ini` é gitignorado — guarde backup junto da senha do vault

`inventories/prod/hosts.ini` e `inventories/dev/hosts.ini` carregam IP interno real do
lab e **não são rastreados** (só os `.example` estão no git — ver `.gitignore`). Um clone
novo deste repositório **não tem como reconstruir o cluster** sem os dois:

- `.ansible_vault_pass` (senha do vault)
- `inventories/prod/hosts.ini` **e** `inventories/dev/hosts.ini` (inventários reais)

Guarde uma cópia dos três arquivos junto, no mesmo gerenciador de senhas/backup. Perder só o
vault não basta para travar a automação — perder o `hosts.ini` também trava, porque
`ansible-playbook` sem inventário real não sabe contra qual host conectar.

### Criar o `hosts.ini` real num clone novo

```bash
cd ansible/
cp inventories/prod/hosts.ini.example inventories/prod/hosts.ini
cp inventories/dev/hosts.ini.example inventories/dev/hosts.ini
$EDITOR inventories/prod/hosts.ini   # preencher com o IP real dos nós de produção
$EDITOR inventories/dev/hosts.ini    # idem, nós de dev
```

### Ainda fora do vault

Estas stacks não têm playbook, então seus segredos existem apenas no banco do Portainer
(digitados na UI), sem registro nem rotação: **firefly3, pricebuddy, cronicle**
e as demais listadas em `TODO.md` secção 1.2.2. Serão migradas aos poucos.
(**grafana** já saiu desta lista: `03-stack-monitoring.yml` injeta `grafana_admin_password`
do vault desde a adequação registrada em `TODO.md`.)

---

## 🚀 Como Executar os Playbooks (Modular vs Completo)

Acesse a pasta `ansible/`:

```bash
cd /home/nuvidio/git/homelab/ansible
```

Todo exemplo abaixo é para **produção**. Para rodar contra **dev**, troque
`-i inventories/prod/hosts.ini` por `-i inventories/dev/hosts.ini` (ou omita o `-i`
em produção, já que é o default do `ansible.cfg`).

### 🔹 Opção A: Subir Bloco por Bloco (Recomendado)

#### 0. Bootstrap de um nó novo (só CTs recém-criados pelo Terraform)
Necessário **antes** de `01-bootstrap-os.yml` em qualquer host que ainda não tenha o
usuário de automação (`nuvidio`) — típico do CT 210 de dev, recém-provisionado:
```bash
ansible-playbook -i inventories/dev/hosts.ini playbooks/00-node-bootstrap.yml --limit novo-ct-dev
```
Nos nós de produção já existentes esse passo não é necessário (o usuário foi criado
manualmente, uma vez, antes deste playbook existir).

#### 1. Setup Base do Sistema Operacional & Docker
```bash
ansible-playbook -i inventories/prod/hosts.ini playbooks/01-bootstrap-os.yml --ask-become-pass
```

#### 2. Bootstrap do Cluster (Swarm + Portainer + Traefik Ingress)
Sobe o Swarm, o Portainer e o Traefik de uma só vez para deixar a infraestrutura 100% pronta:
```bash
ansible-playbook -i inventories/prod/hosts.ini playbooks/02-bootstrap-cluster.yml --ask-become-pass
```

#### 3. Deploy da Stack de Monitoramento (Grafana, Loki, Prometheus)
```bash
ansible-playbook -i inventories/prod/hosts.ini playbooks/03-stack-monitoring.yml --ask-become-pass
```

#### 4. Deploy do Bloco de Gestão & Finanças (Paperless, Actual, Vikunja, etc.)
```bash
ansible-playbook -i inventories/prod/hosts.ini playbooks/04-stack-management.yml --ask-become-pass
```

#### 5. Deploy do Bloco de Utilitários & Ferramentas (Stirling-PDF, IT-Tools, etc.)
```bash
ansible-playbook -i inventories/prod/hosts.ini playbooks/05-stack-utilities.yml --ask-become-pass
```

---

### 🔹 Opção B: Subir Todo o Homelab de Uma Vez (`site.yml`)

Para rodar todo o ecossistema do zero em sequência (produção, inventário default):

```bash
ansible-playbook site.yml --ask-become-pass
```

`site.yml` não importa `00-node-bootstrap.yml` — ele é sempre disparado à mão, uma vez por
CT novo, com `--limit` apontando só para o host recém-criado (ver Opção A, passo 0).

Para dev:

```bash
ansible-playbook -i inventories/dev/hosts.ini site.yml --ask-become-pass
```

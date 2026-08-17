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
│   │   ├── all.yml            # Camada 1: comum aos dois ambientes (nunca segredo, nunca o que difere)
│   │   └── opnsense.yml       # Constante de PROTOCOLO do OPNsense (HOME_NET, privateaddress) — mesmo valor em qualquer instalação, não a rede deste lab
│   ├── vault.yml.example      # Modelo único do Vault — copiar para cada ambiente, ver Passo 0
│   ├── prod/
│   │   ├── hosts.ini          # ⚠️ GITIGNORADO — inventário real (IPs internos), você cria localmente
│   │   ├── hosts.ini.example  # Modelo rastreado, base para criar o hosts.ini real
│   │   └── group_vars/
│   │       ├── all/
│   │       │   ├── 00-common.yml  # symlink -> ../../../_common/all.yml
│   │       │   ├── main.yml       # Camada 2: só o que difere de dev (homelab_env, branch, ACME)
│   │       │   └── vault.yml      # Camada 3: segredos de PROD, cifrado (gitignorado, você cria)
│   │       └── network/           # Variáveis de rede (OPNsense/OpenWrt) — duas camadas
│   │           ├── 10-defaults.yml  # Versionado: estrutura e nomes reais, endereço de documentação
│   │           └── 99-local.yml     # ⚠️ GITIGNORADO: a faixa real. Sem ele o inventário ainda sobe
│   └── dev/
│       ├── hosts.ini          # ⚠️ GITIGNORADO, idem prod
│       ├── hosts.ini.example
│       └── group_vars/
│           ├── all/
│           │   ├── 00-common.yml  # symlink -> ../../../_common/all.yml
│           │   ├── main.yml       # homelab_env: dev, branch develop, ACME staging
│           │   └── vault.yml      # segredos de DEV — valores DIFERENTES dos de prod
│           └── network/           # dev não tem OPNsense/OpenWrt próprio: só a VLAN do CT 210
│               ├── 10-defaults.yml  # Versionado, endereço de documentação
│               └── 99-local.yml     # ⚠️ GITIGNORADO: a faixa real
├── tasks/
│   ├── portainer_login.yml    # Task reutilizável de Login & Token JWT na API do Portainer
│   ├── portainer_stack.yml    # Create-or-update de stack via API (usada por 02, 04 e 05)
│   └── validate_vault.yml     # Exige que os segredos existam antes de provisionar
├── templates/network/
│   ├── opnsense/              # Esqueleto (config.xml.j2) + 10 partials orientados a dado — ver seção abaixo
│   └── openwrt_*.j2           # 4 templates do AP/roteador Wi-Fi, ainda monolíticos (fora deste refactor)
├── tests/                     # Render do OPNsense fora da caixa — ver "make render-opnsense" abaixo
│   ├── render_opnsense.py     # Jinja2 com trim_blocks=True, igual ao Ansible; nenhum inventário real
│   ├── fixtures/opnsense.yml  # Variáveis de exemplo — RFC 5737, MAC de documentação IANA
│   ├── baseline/opnsense-fase0.xml  # Render congelado da fase 0 deste refactor — nunca regenerar
│   └── expected/               # Diff aprovado por fase, comparado byte a byte (ver expected/README.md)
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
> sucesso. A migração é acompanhada no backlog de hardening, mantido fora do versionamento.

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

## 🧩 OPNsense: por que `templates/network/opnsense/` é um esqueleto com partials

`templates/network/opnsense_config.xml.j2` — um único arquivo de 2.486 linhas, dump direto
do `/conf/config.xml` vivo — não existe mais. No lugar, `templates/network/opnsense/` guarda
`config.xml.j2`, que só contém a estrutura XML fixa (IPsec, Kea, VLANs, certificados) e um
`{% include %}` por seção, e dez partials — cada um responsável por uma fatia do config e
consumindo dado de `inventories/_common/opnsense.yml` ou de `group_vars/network/`:

| partial | conteúdo | variável consumida |
|---|---|---|
| `_sysctl.xml.j2` | tunables de kernel | `opnsense_tunables` |
| `_system.xml.j2` | hostname, WebGUI, SSH, usuários e grupos | `opnsense_hostname`, `opnsense_users`, `opnsense_groups` |
| `_interfaces.xml.j2` | LAN nativa e WAN | `lan_ipaddr`, `parent_interface` |
| `_dhcp.xml.j2` | `dhcpd`, Kea, reservas estáticas | `lan_dhcp_range_from/to`, `dhcp_static_maps` |
| `_nat.xml.j2` | redirecionamento de porta e outbound | `opnsense_dns_redirect_*` |
| `_aliases.xml.j2` | aliases de firewall | `opnsense_aliases` |
| `_filter.xml.j2` | regras de filtro | `opnsense_rules` |
| `_plugins.xml.j2` | lista única de plugins — incluído em 5 pontos do esqueleto, um por seção do XML onde o OPNsense declara plugin | `opnsense_plugins` |
| `_dns.xml.j2` | Unbound, DNSBL, DoT, host overrides | `opnsense_unbound_privateaddress`, `opnsense_ids_homenet` |
| `_certs.xml.j2` | certificado da WebGUI | `opnsense_gui_cert_crt_b64` |

Nenhum partial declara endereço de rede, alias ou usuário como literal dentro do `.j2` — é a
mesma razão do split de `group_vars/network.yml` em duas camadas (ver árvore acima): um
template Jinja versionado não tem licença para carregar a topologia real de um repositório
destinado a ser público. O split em si (um monolito → onze arquivos) existe porque um único
arquivo de 2.486 linhas concentrava aliases, regras, DHCP, DNS e usuários no mesmo lugar —
editar qualquer seção exigia revisar tudo em volta, e foi assim que ele acumulou tunable
duplicado, `ifgroup` morto e um `<monit>` com dois blocos conflitantes sem que ninguém notasse.
Partido por seção, cada `_*.xml.j2` só cresce quando a variável que ele consome muda.

### Provar uma mudança sem hardware: `make render-opnsense`

Nenhum destes templates roda contra um OPNsense de verdade em CI. O alvo `render-opnsense` do
`Makefile` (raiz do repositório) prova a mudança fora da caixa:

1. Renderiza `templates/network/opnsense/config.xml.j2` com
   [`tests/render_opnsense.py`](./tests/render_opnsense.py) — mesmo motor Jinja2
   (`trim_blocks=True`), sem depender do Ansible instalado — usando as constantes de
   `inventories/_common/opnsense.yml` e o fixture sintético em `tests/fixtures/opnsense.yml`
   (RFC 5737 e MAC de documentação IANA, nunca topologia real).
2. Normaliza o XML com `xmllint --c14n | xmllint --format`, para que o diff não reaja a
   diferença cosmética de atributo ou espaço.
3. Compara o resultado contra `tests/baseline/opnsense-fase0.xml`, o render congelado da fase
   0 deste refactor — **nunca regenerado depois**, para que uma regressão estrutural não vire
   "baseline da fase seguinte" por omissão.
4. Se o diff bater exatamente com o que já está registrado em `tests/expected/*.diff` (ver
   [`tests/expected/README.md`](./tests/expected/README.md) para a convenção de nome dos
   arquivos), passa. Se o diff for novo e nenhum `expected/*.diff` existir, falha e imprime o
   hunk — sinal de que a fase precisa registrar sua própria aprovação antes de seguir.

Rodar isolado, da raiz do repositório:

```bash
make render-opnsense
```

Encadeado em `make check`, então roda em toda CI e a cada verificação local antes do PR.

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

### Caminho curto: `make bootstrap`

Da raiz do repositório, um comando faz tudo o que esta seção descreve — gera a
senha do vault, os segredos, os dois vaults cifrados, os dois `hosts.ini` e a
chave SSH:

```bash
make bootstrap
```

Ele pede só o que não tem como ser gerado (tokens de Cloudflare e Proxmox) e
preserva segredo que já exista. Ver [`IaC/bootstrap/README.md`](../bootstrap/README.md).

O resto desta seção descreve o **caminho manual**, que continua válido e é o que
o bootstrap automatiza. Vale ler para entender o que ele faz — e é o caminho a
seguir quando só um valor precisa mudar, já que `ansible-vault edit` é mais
direto que regenerar tudo.

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
- `inventories/prod/group_vars/network/99-local.yml` **e** o de `dev` (a topologia real)

Guarde uma cópia dos cinco arquivos junto, no mesmo gerenciador de senhas/backup. Perder só o
vault não basta para travar a automação — perder o `hosts.ini` também trava, porque
`ansible-playbook` sem inventário real não sabe contra qual host conectar. Perder o
`99-local.yml` não trava: o inventário continua válido e cai para os endereços de
documentação do `10-defaults.yml` ao lado. É pior que travar — o playbook roda e
configura a rede errada.

### Criar o `hosts.ini` real num clone novo

`make bootstrap` gera os dois, com os IPs de HOJE — que é o que importa aqui,
porque é por eles que o Ansible alcança os hosts. O plano de VLANs descreve o
destino e vive em `group_vars/network/`; confundir os dois produz timeout de
SSH, cujo sintoma não diz nada sobre a causa.

Manualmente:

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
e as demais anotadas no backlog de hardening, que fica fora do versionamento.
Serão migradas aos poucos. (**grafana** já saiu desta lista:
`03-stack-monitoring.yml` injeta `grafana_admin_password` do vault.)

---

## 🚀 Como Executar os Playbooks (Modular vs Completo)

Acesse a pasta `IaC/ansible/`:

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

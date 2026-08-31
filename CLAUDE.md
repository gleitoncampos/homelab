# CLAUDE.md - Homelab Repository Guidelines & Architecture Premises

Este arquivo estabelece os princípios de arquitetura, padrões de projeto, convenções de código e boas práticas para manutenção e evolução deste repositório de **HomeLab**. Qualquer assistente de IA ou desenvolvedor deve seguir rigorosamente estas diretrizes ao propor alterações ou criar novas stacks.

---

## 🎯 Visão Geral & Filosofia do Projeto

- **Propósito**: Repositório centralizador de configurações, automações e definições de serviços containerizados (**Docker Swarm / Docker Compose**) do HomeLab.
- **Princípios Fundamentais**:
  - **Soberania de Dados**: Substituição de serviços de nuvem proprietários (Google Photos/Drive, 1Password, etc.) por alternativas *self-hosted* auto-hospedadas.
  - **Privacidade & Segurança**: Roteamento 100% criptografado (HTTPS/TLS), isolamento de redes e ausência de segredos commitados no repositório.
  - **Reprodutibilidade**: Qualquer serviço deve poder ser recriado do zero a partir das definições deste repositório e do diretório de dados persistentes `/srv/containers/`.
  - **IaC é a fonte da verdade**: o estado descrito aqui vence o estado que existe no equipamento. Ver a seção abaixo.

---

## 🧨 IaC é a fonte da verdade — divergência é destruída, não preservada

Este repositório **não** descreve o que está configurado nos equipamentos. Ele
**define** o que deve estar. Quando os dois divergem, quem cede é o equipamento.

⚠️ **Consequência direta, e ela é destrutiva:** qualquer configuração feita à mão
— pela GUI do OPNsense, pelo LuCI do OpenWrt, pela interface do Proxmox, pelo
Portainer, por `ssh` no host — que não tenha sido parametrizada no IaC **DEVE ser
perdida** na próxima execução. Isso não é um efeito colateral a ser mitigado: é o
mecanismo. Um playbook que preserva ajuste manual não está sendo cuidadoso, está
sendo inútil, porque o repositório deixa de responder pela configuração real no
exato momento em que alguém mexe fora dele.

Por isso a substituição total é o padrão aceito onde ela existe: o playbook do
OPNsense escreve `/conf/config.xml` **inteiro**, e não faz merge com o que estava
lá. O mesmo vale para `docker stack deploy`, que reconcilia o serviço contra o
compose, e para os templates de `/etc/network/interfaces` do Proxmox.

**O contrato que isso cria:**

1. Mudança de configuração começa **no repositório**, nunca no equipamento.
2. Se você precisou mexer na GUI para resolver algo agora — está certo, resolva
   agora. Mas o ajuste só existe de verdade quando virar variável ou template no
   mesmo dia. Até lá ele é dívida, e a próxima execução a cobra.
3. Descobrir o que se perdeu é trabalho de arqueologia. **Descobrir antes** é
   `--check --diff`: rode-o quando suspeitar que o equipamento andou divergindo.
4. Nada que é **estado** entra nessa regra — dado de aplicação, banco, mídia,
   log e certificado emitido vivem em `/srv/containers/` e em volume, jamais em
   arquivo de configuração renderizado.

O que **não** é dispensa desta regra: "é só um ajuste pequeno", "eu anoto
depois", "ninguém mais mexe nesse equipamento". Foi exatamente assim que
`opnsense_config.xml.j2` nasceu — um dump de 86.468 bytes (2.486 linhas) do
`/conf/config.xml` vivo, commitado para não perder o que havia sido feito à
mão, carregando junto senha, chave privada e a topologia inteira.

### O repositório define o ALVO. Produção é backup, não autoridade

⚠️ **O alvo é o que está sendo construído nos templates, não o que o equipamento
faz hoje. Ele vai e deve divergir da produção — a divergência é o objetivo, não
um defeito a corrigir.**

O retrato da produção existe (`prod-snapshot/` coleta, o resultado fica em
`.local/`) e serve para duas coisas: **backup** e **garimpo**. Nunca para
decidir o que o repositório deveria dizer.

Ao comparar produção e repositório, só uma das três leituras é achado:

| situação | leitura |
|---|---|
| existe no template, não em produção | o alvo ainda não foi aplicado — **normal** |
| existe nos dois, com valores diferentes | o alvo decidiu divergir — **normal** |
| existe em produção, e o template **não tem nada equivalente** | ⚠️ **garimpar, ou decidir matar** |

Só a terceira linha vira trabalho, e o motivo é preciso: aplicar o IaC hoje
faria aquilo desaparecer **sem que ninguém tivesse decidido** que devia
desaparecer. É perda por omissão. As outras duas são o projeto funcionando.

⚠️ **A confusão entre "alvo" e "o que está no ar" já custou muito tempo aqui.**
Durante meses o repositório foi tratado como retrato da infraestrutura quando
era, em boa parte, uma lista de desejos — e foi isso que deixou playbooks
apontando para stacks inexistentes sem ninguém perceber. A correção **não** é
fazer o repositório descrever a produção: é manter explícito que ele descreve o
alvo. Por isso o inventário consolidado rotula **todo** número de estado como
`ALVO` ou `REAL`: sem o rótulo, os dois viram fatos contraditórios sobre o mesmo
objeto.

⚠️ **Ao reconciliar produção → template, cada campo do render sai do estado
vivo observado, não de "boa prática genérica".** Ao portar uma configuração
que já roda para o IaC, a tentação é preencher os campos com o que "geralmente
se faz" — e a boa prática genérica pode ser incompatível com a topologia real.
Exemplo real: `blockpriv=1` numa interface WAN é boa prática de manual, mas a
segunda WAN entra por DHCP e o `blockpriv` derruba a própria negociação — o
vivo tinha `blockbogons=1` só, e era essa a resposta. Antes de escrever um
valor de config que o equipamento vivo já tem, **leia o valor no equipamento**;
não o deduza do que costuma ser certo. É a mesma premissa de "não adivinhar,
checar", aplicada ao campo individual de um template de reconciliação.

---

## 🏗️ Premissas de Arquitetura & Padrões Tecnológicos

### 1. Orquestração e Modo de Execução (Docker Swarm)
- **Modo Padrão**: Todas as stacks da pasta `docker/` devem ser formatadas prioritariamente para **Docker Swarm**.
- **Sintaxes Proibidas em Stacks Swarm**:
  - ❌ **NÃO** usar `container_name:` (atributo incompatível com réplicas do Swarm).
  - ❌ **NÃO** usar `restart: always` ou `restart: unless-stopped` no topo do serviço (deve ser convertido para `deploy.restart_policy`).
  - ❌ **NÃO** usar `version: '2'` ou `version: '3'` legados em novas stacks (omitir o cabeçalho `version:` ou usar especificação moderna do Compose Spec).
  - ❌ **NÃO** usar a diretiva `links:` (obsoleta; utilizar redes overlay unificadas).
- **Blocos de Implantação Obrigatórios (`deploy`)**:
  ```yaml
  deploy:
    mode: replicated
    replicas: 1
    placement:
      constraints:
        - node.role == manager
    restart_policy:
      condition: on-failure
  ```

### 2. Ingress, Proxy & Certificados SSL (Traefik v3)
- **Rede Externa Padrão**: Todo serviço que necessita de exposição pública/web deve se conectar à rede overlay externa `traefik_public`:
  ```yaml
  networks:
    traefik_public:
      external: true
  ```
- **Convenção de Labels do Traefik**: em Docker Swarm, o provider `swarm` do Traefik só lê labels de **serviço** dentro de `deploy.labels` — labels soltas em `labels:` no nível do serviço (fora de `deploy:`) são as labels de **container**, que o provider swarm não enxerga. Colocar ali é o erro mais comum ao portar uma stack de Compose puro para Swarm: o serviço sobe, o Traefik não reporta erro nenhum, mas a rota simplesmente nunca aparece.
  ```yaml
  deploy:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.<servico>.rule=Host(\"${FQDN}\")"
      - "traefik.http.routers.<servico>.entrypoints=websecure"
      - "traefik.http.services.<servico>.loadbalancer.server.port=<porta-interna>"
      - "traefik.http.routers.<servico>.service=<servico>"
      - "traefik.http.routers.<servico>.tls.certresolver=cloudflare"
  ```
- **NUNCA** expor portas públicas no host (`ports: "8080:8080"`) em serviços que devam ser acessados via Traefik. Apenas o Traefik expõe as portas 80 e 443.

### 3. Persistência de Dados & Caminhos de Volumes
- **Caminho Absoluto Padrão**: Todos os dados persistentes no host devem ser montados estritamente sob o diretório:
  ```text
  /srv/containers/<nome-do-servico>/<subpasta>
  ```
- ❌ **Proibido**: Mapeamento de caminhos relativos (ex: `./config`, `./data`).
- **Isolamento de Estado**: Bancos de dados (PostgreSQL, MariaDB, Redis) devem possuir subpastas dedicadas em `/srv/containers/<servico>/<db-data>`.

### 4. Gestão de Segredos & Variáveis de Ambiente
- ❌ **NUNCA** expor senhas, tokens de API ou chaves de criptografia em texto puro dentro de arquivos `compose.yml`.
- **Parametrizar**: Utilizar interpolação de variáveis de ambiente do Portainer/Docker (ex: `${POSTGRES_PASSWORD}`, `${PAPERLESS_SECRET_KEY}`, `${FQDN}`).
- **Proteção do Git**: O arquivo `.gitignore` deve proibir commits de arquivos `.env`, `.tfvars`, chaves TLS e dados sensíveis.

### 5. Resiliência & Gestão de Recursos
- **Resource Limits**: Toda stack de produção deve limitar a memória e CPU para proteger a estabilidade do nó:
  ```yaml
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1024M
  ```
- **Healthchecks**: Sempre que possível, incluir verificações de saúde ativas:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:<porta>/ || exit 1"]
    interval: 30s
    timeout: 10s
    retries: 3
  ```

### 6. Imagens Docker & Atualizações
- **Tag Fixa**: Sempre fixar a versão da imagem (ex: `image: postgres:16-alpine` ou `image: ghcr.io/paperless-ngx/paperless-ngx:2.14.0`).
- ❌ **Proibido em Produção**: Evitar o uso da tag `:latest` sem controle de versão.
- **Automação**: O bot **Renovate** ([`.renovaterc.json`](./.renovaterc.json)) é responsável por abrir Pull Requests automáticos de atualização de versão das imagens.

---

## 📁 Estrutura do Repositório

```text
.
├── AGENTS.md               # Regras de estilo para assistentes (CLAUDE.md é symlink)
├── CLAUDE.md               # Este arquivo (diretrizes e premissas do repositório)
├── CONTRIBUTING.md         # Para quem vem de fora; aponta para cá, não duplica
├── LICENSE                 # MIT
├── Makefile                # Entrada única: make hooks, make check, make secrets, make ssh-keys
├── README.md               # Porta de entrada: o que é o projeto e como subir
├── SECURITY.md             # Como reportar, e o que NÃO é vulnerabilidade aqui
├── .ansible-lint           # Perfil production fixado (bump de versão não muda o gate)
├── .editorconfig           # Convenções de espaço e fim de linha (no editor)
├── .gitattributes          # Convenções de fim de linha (no git, valem sempre)
├── .github/
│   ├── CODEOWNERS          # Revisão do mantenedor em tudo
│   ├── ISSUE_TEMPLATE/     # bug, nova-stack; issue em branco desativada
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/ci.yml    # Chama o Makefile; não repete os comandos
├── .githooks/              # Gate de qualidade; ativar com core.hooksPath
│   ├── pre-commit          # Vazamento, referências, sintaxe, linters
│   ├── commit-msg          # Conventional Commits + varredura da mensagem
│   ├── lib.sh              # Leitura dos padrões, compartilhada pelos hooks
│   ├── scan-refs.sh        # stack_compose_path e links (usado por make links)
│   ├── scan-tree.sh        # Árvore inteira (usado por make secrets e pela CI)
│   ├── validate-patterns.sh   # Forma dos padrões (usado por make validate-patterns)
│   ├── secret-patterns.txt # Genérico; VERSIONADO e público
│   ├── secret-patterns.samples.txt  # Amostra ruim por padrão; prova que o regex casa
│   ├── secret-patterns.local.txt.example  # Sua topologia; a cópia é ignorada
│   └── secret-patterns.local.txt.samples.example  # As amostras dos padrões locais
├── .gitignore              # vault.yml, hosts.ini, .tfvars, .tfstate, .local/
├── .yamllint               # Estilo de YAML; `yes`/`no` truthy é erro
├── .local/                 # IGNORADO. Diretório padrão de documentação de trabalho
│   ├── README.local.md     # Índice comentado, com o estado de cada documento
│   ├── docs/               # Backlog, pré-publicação, pendências, plano de hardening
│   ├── memoria/            # Symlinks para a memória de longo prazo (~/.claude)
│   └── snapshot/           # Coleta da produção e o levantamento do que roda
├── .superpowers/           # IGNORADO. Workspace do subagent-driven-development (brief, report, diff)
├── .renovaterc.json        # Atualização automática de versão de imagem
├── docs/
│   ├── instalacao.md       # Ordem de execução das ferramentas e uso do Ansible por alvo
│   └── runbooks/           # Procedimento operacional genérico, sem dado real
│       └── restauracao.md  # Verificação e recuperação do backup dos convidados
├── IaC/                    # TODA ferramenta de infraestrutura como código
│   ├── bootstrap/          # Gera as ENTRADAS que as duas ferramentas consomem
│   │   ├── bootstrap.py    # Fonte única -> vault, hosts.ini, tfvars, padrões
│   │   ├── distribuir_chave.sh  # make ssh-keys: instala a chave pública nos hosts
│   │   ├── test_render.py  # Testa contra ansible-inventory e terraform fmt
│   │   └── templates/      # Modelo do arquivo de respostas (.tmpl: não é YAML ainda)
│   ├── ansible/            # Sistema operacional, cluster Swarm e stacks
│   │   ├── requirements.yml    # Collections Galaxy (community.docker/general, ansible.posix)
│   │   ├── inventories/        # Um diretório por ambiente — ver IaC/ansible/README.md
│   │   │   ├── _common/        # Variáveis comuns a prod e dev (camada 1 de 3)
│   │   │   │   └── opnsense.yml    # Constante de protocolo do OPNsense (HOME_NET, privateaddress) — não a rede deste lab
│   │   │   ├── prod/           # hosts.ini (gitignorado) + group_vars/; network/ e omv/ em duas camadas (10-defaults.yml versionado e fictício, 99-local.yml gitignorado com o dado real)
│   │   │   └── dev/            # idem, ambiente de desenvolvimento
│   │   ├── tasks/              # Tasks reutilizáveis, incluídas por playbook:
│   │   │                       #   validate_vault, Portainer, regras do usuário docker,
│   │   │                       #   detect/elect_nut_primary (07), node_exporter (00-proxmox),
│   │   │                       #   omv_mount_filesystems + mergerfs_pools (00-omv),
│   │   │                       #   windows_oem_smb (convidado Windows; sem playbook ainda)
│   │   ├── templates/network/  # openwrt_{system,network,dhcp,firewall,wireless}.j2, failsafe.sh.j2 (rollback da troca de rede), led_health.sh.j2 + led_health_init.j2 (sinalização de saúde nos LEDs do AP) + opnsense/: config.xml.j2 (esqueleto de includes) e 10 partials orientados a dado — ver IaC/ansible/README.md
│   │   ├── templates/nut/      # ups.conf, upsd.{conf,users}, upsmon.conf, nut.conf, udev, tmpfiles (07-nut-ups)
│   │   ├── templates/omv/      # mergerfs-cache-mover.{sh,service,timer}.j2 (00-omv)
│   │   ├── templates/proxmox/  # proxmox_{datacenter,interfaces,storage}.j2
│   │   ├── templates/terraform/  # tokens.auto.tfvars.j2 (escrito por 00-proxmox-api-token)
│   │   ├── templates/windows/  # install.bat.j2 — mapeia o SMB do NAS no convidado (§3.11 do roadmap)
│   │   ├── tests/               # Render de templates e gates de playbook fora da caixa — make render-*, make gates-ordem-vlan
│   │   │   ├── render_opnsense.py  # Jinja com trim_blocks=True, igual ao Ansible; compara com baseline congelado
│   │   │   ├── render_monitoring.py  # Render da stack de monitoramento com 2 hosts por grupo
│   │   │   ├── render_proxmox_ifaces.py  # (1) afirma que o assert de sub-rede do 00-proxmox-setup.yml existe e tem as 3 condições; (2) renderiza proxmox_interfaces.j2 (RFC 5737). O gate EFETIVO é o assert do playbook
│   │   │   ├── gates_ordem_vlan.py  # Gates da ORDEM da migração de VLANs: (1) afirma que os 3 asserts existem em 01-opnsense/02-openwrt e mantêm a forma; (2) renderiza e confere que o template cria uma interface por VLAN, DHCP só onde dhcp_enabled, Unbound em todas, e que cada `politica` vira as regras prometidas NA ORDEM certa. Os gates EFETIVOS são os asserts dos playbooks
│   │   │   ├── fixtures/       # Variáveis de exemplo (RFC 5737, MAC de documentação IANA); isenção por ARQUIVO no gate, nunca por diretório
│   │   │   ├── baseline/       # Render congelado da fase 0 — nunca regenerar
│   │   │   └── expected/       # Diff aprovado por fase, comparado byte a byte
│   │   └── playbooks/          # 00-node-bootstrap/-omv-setup/-proxmox-setup/-proxmox-api-token, 01-05 (SO, cluster, stacks), 06-backup, 07-nut-ups, network/ (OPNsense, OpenWrt)
│   └── terraform/          # Hardware virtual (Proxmox VE — VMs e CTs LXC)
│       └── proxmox/        # Provider bpg/proxmox; no README de lá, o runbook de import e a exceção da VM do NAS, que é recriada
├── prod-snapshot/          # Ferramentas de coleta do estado real (só os scripts)
│   ├── collect.sh          # Coleta config por host, conforme o grupo do inventário
│   ├── collect-omv-fs.py   # make omv-fs: propõe os UUIDs de dados do NAS para o group_vars
│   └── discover.sh         # Descobre hosts na rede e sugere o hosts.ini
└── docker/                 # Stacks de serviço
    ├── README.md           # Instruções de deploy
    ├── _catalog/           # Definidas; parte já implantada por playbook — ver o README de lá
    └── <servico>/swarm/compose.yml
```

> `docker/` vs. `docker/_catalog/` não é "implantado" vs. "não implantado": é
> "convenção de Swarm ok para promover" vs. "ainda precisa adequação" — a
> maioria dos dois lados sobe por playbook do Ansible; só o que não tem
> playbook sobe pelo formulário de Stacks do Portainer. Ver
> [`docker/README.md`](./docker/README.md).

### Regra: toda ferramenta de IaC vive sob `IaC/`

Ansible, Terraform e qualquer ferramenta futura — Packer, Vagrant, Pulumi,
cloud-init — entram como um subdiretório de `IaC/`, **nunca** na raiz.

O motivo: a raiz deve dizer *o que este repositório é* — aqui se provisiona
(`IaC/`), aqui se definem serviços (`docker/`), aqui se documenta (`docs/`) —
nunca *quais ferramentas ele usa*.

A ordem de execução é: **`make bootstrap`** (gera as entradas) → `terraform`
(cria VMs e CTs) → `ansible` (sistema, cluster e stacks).

Ao acrescentar uma ferramenta:

1. `IaC/<ferramenta>/` com um `README.md` próprio explicando o que ela provisiona
   e qual a ordem de execução em relação às outras.
2. Se ela guarda estado ou credencial (`*.tfstate`, `*.tfvars`, inventário real),
   acrescente a regra ao `.gitignore` **antes** do primeiro arquivo existir.
3. Atualize esta árvore e a tabela do [`README.md`](./README.md).

### Regra: documento de trabalho vive em `.local/`

`.local/` é o **diretório padrão de documentação** deste repositório. Análise,
auditoria, levantamento da produção, plano de execução e qualquer texto que cite
dado real entram lá — nunca na raiz, nunca em `docs/`. O índice comentado, com o
estado de cada documento, está em `.local/README.local.md`.

A divisão:

| onde | o que | versionado |
|---|---|---|
| `README.md`, `CLAUDE.md`, `docs/` | o que o sistema **deve ser**: permanente, público, sem dado real | sim |
| `.local/` | o que **aconteceu**: datado, com hostname, IP e trecho de segredo | não (`.gitignore`) |
| `.local/memoria/` | symlinks para `~/.claude/.../memory/` — o que precisa estar em contexto antes da próxima conversa | não |

Regra de decisão: **se o documento perde sentido depois de resolvido, ou cita
dado real, é `.local/`.** Todo documento novo leva **data no cabeçalho** — sem
ela, em três meses não há como saber se descreve o estado atual. Documento que
ficou obsoleto é marcado como tal no índice, não apagado: o raciocínio de por que
uma decisão foi tomada sobrevive ao fato de ela já ter sido executada.

#### Um documento por pergunta, não por assunto

⚠️ **Antes de criar um documento, diga em voz alta a pergunta que ele responde.
Se a pergunta já tem dono, ele não é um arquivo novo — é uma seção num arquivo
existente.**

Organizar por *assunto* parece natural e é a origem do problema: "segurança",
"rede" e "backup" são assuntos, e cada documento novo sobre eles se acha
legítimo. Assim nasceram cinco listas de pendências concorrentes neste
repositório, cada uma se achando a boa, e três delas acabaram cedendo por
escrito às outras.

As perguntas que já têm dono:

| pergunta | dono |
|---|---|
| o que cada equipamento **é** hoje | `.local/docs/INVENTARIO.md` |
| o que está **quebrado**, e o que queremos **construir** | `.local/docs/TODO-E-ROADMAP.md` |
| o que **poderia** melhorar (bruto, sem triagem) | `.local/docs/SUGESTOES.md` |
| **como** se faz | `.local/docs/runbooks/` |

O fluxo entre eles não é óbvio e é o que impede a mistura: o inventário descreve
o estado, as sugestões propõem sobre esse estado, e **só o que sobrevive à
triagem entra no roadmap**. Sugestão bruta nunca vira item de roadmap — é por
isso que a parte de backlog triado nasceu vazia.

#### Registro de execução vive em `docs/historico/`, e a regra é física

Plano executado, spec cumprida, sessão encerrada e auditoria superada **não
ficam soltos na raiz**: vão para `.local/docs/historico/`, no subdiretório do
seu tipo (`planos/`, `specs/`, `sessoes/`, `auditorias/`).

A fronteira é um diretório, não uma convenção a lembrar. Antes disso, documento
vivo e registro de execução dividiam a mesma raiz e só a tabela do índice os
separava — o que faz a raiz acumular a cada sessão, porque ninguém consulta o
índice antes de criar um arquivo. Com a pasta, a regra se aplica sozinha.

⚠️ Arquivar **não** é apagar. Documento no histórico continua legível e continua
indexado; alguns seguem em uso ativo mesmo arquivados, e o índice diz quais.

#### Documento que enumera fraqueza não corrigida NUNCA é versionado

⚠️ Lista de pendências de segurança, inventário do que ainda está aberto, plano
de hardening em andamento, runbook de rotação de credencial comprometida — todos
vivem em `.local/`, sem exceção e independentemente de quão útil pareça
publicá-los.

O critério é este: **se o documento ajuda um atacante mais do que ajuda um
contribuidor, ele não é público.** Um repositório público publica o que o sistema
**deve ser**; nunca a lista do que ainda não é. As duas coisas parecem
documentação e se lêem parecido, mas só uma delas é segura de abrir.

O erro concreto que originou a regra: um arquivo de pendências com 15 itens do
tipo *"isto ainda não está protegido"*, cada um com o comando pronto para
verificar, foi versionado num repositório destinado a ser público — junto de um
roadmap que continha uma senha literal em texto puro. Somados, os dois eram um
checklist de invasão para o homelab de uma pessoa nomeada.

⚠️ **O gate cobre `docs/` como cobre qualquer outro diretório.** Não existe
isenção por ser documentação: já existiu em
[`.githooks/secret-patterns.txt`](./.githooks/secret-patterns.txt), e foi
exatamente por ela que a senha passou. Ao adicionar padrão novo, nunca isente um
diretório inteiro — isente o caminho específico onde o valor é legítimo.

---

## ⚙️ Workflow de Implementação de Novas Stacks

Ao adicionar uma nova aplicação ao repositório, siga o checklist:

1. **Criar a pasta**: `docker/<servico>/swarm/`
2. **Criar o arquivo**: `docker/<servico>/swarm/compose.yml`
3. **Verificar os requisitos**:
   - [ ] Usa a rede externa `traefik_public` e rede overlay isolada interna (se houver banco/redis).
   - [ ] Labels do Traefik configuradas com `${FQDN}` e `certresolver=cloudflare`.
   - [ ] Volumes apontando para `/srv/containers/<servico>/...`.
   - [ ] Variáveis sensíveis usando sintaxe `${VAR_NAME}`.
   - [ ] Imagem Docker com tag de versão fixada (sem `:latest`).
   - [ ] Blocos `deploy.resources.limits` e `deploy.placement.constraints` configurados.
4. **Documentação**:
   - Atualizar a tabela de catálogo no [`README.md`](./README.md).
   - Atualizar o guia de stacks em [`docker/README.md`](./docker/README.md).

---

## ✅ Gate de qualidade

Três camadas, com alcances diferentes de propósito:

| camada | alcance | quando |
|---|---|---|
| `pre-commit` | só as linhas **adicionadas** no índice | a cada commit |
| `make check` / `make secrets` | a **árvore inteira** rastreada | antes do PR |
| CI (`.github/workflows/ci.yml`) | idem, mas **não pulável** | em todo push e PR |

O hook é incremental de propósito: rápido, e não pune você por problema que já
estava lá. A varredura de árvore é o complemento — foi ela que achou um IP de
gateway literal em dois playbooks, escrito antes de o gate existir e portanto
invisível para o hook. E a CI existe porque hook é local: `--no-verify` some com
ele, e num PR de terceiro nem chega a rodar.

Ativar uma vez por clone — o Git não compartilha `.git/hooks/`, e sem isso os
hooks existem no repositório e não rodam, o modo de falha mais silencioso daqui:

```bash
make hooks     # core.hooksPath = .githooks; cria o secret-patterns.local.txt
make check     # o mesmo conjunto que a CI roda
```

`make check` encadeia, nesta ordem: `lint` (yamllint, `ansible-lint`,
`shellcheck`, `terraform fmt`), `validate-compose`, `validate-ansible`,
`validate-patterns` (a forma dos dois arquivos de padrões),
`check-common-copies` (as cópias de `00-common.yml` em `prod/` e `dev/` não
divergiram de `_common/all.yml`), `links`, `test-bootstrap` e os três renders de
template fora da caixa: `render-opnsense` (contra o baseline congelado da fase 0),
`render-monitoring` (render da stack de monitoramento com 2 hosts por grupo) e
`render-proxmox-ifaces` (afirma que o assert de sub-rede do
`00-proxmox-setup.yml` existe e mantém as 3 condições — apagar/descaracterizar o
assert deixa o `make check` vermelho — e renderiza `proxmox_interfaces.j2`; o
gate EFETIVO é o assert do playbook, este teste prova que ele está lá).

Fecha com `gates-ordem-vlan`, que é da mesma família e protege a ORDEM DE
APLICAÇÃO da migração de VLANs: afirma que os três asserts de ordem continuam
em `01-opnsense-setup.yml` e `02-openwrt-setup.yml` com a forma certa, e
renderiza o config do OPNsense para provar que o template cria de fato uma
interface por VLAN — sem essa segunda camada o asserto do playbook compararia
contra `vlans` e passaria a mentir no dia em que o template parasse de emitir.

`make secrets` fica de fora,
e a CI o chama por último em passo próprio: a falha dele não é "corrija o
estilo", é "isto não pode ser publicado".

### `pre-commit`

Na ordem em que os problemas custam caro:

1. **Vazamento.** Compara as linhas *adicionadas* contra
   [`secret-patterns.txt`](./.githooks/secret-patterns.txt) **e**
   `secret-patterns.local.txt`. Também barra nome de arquivo proibido mesmo que
   o `.gitignore` falhe.
2. **Referências.** Todo `stack_compose_path` aponta para arquivo existente, e
   todo link relativo de markdown resolve. As duas já quebraram de verdade nesta
   base, ao mover stacks para `_catalog/` e ao criar `docs/`.
3. **Sintaxe.** Compose validado com **duas réguas**: `docker/` passa por
   `docker stack config`, que é o schema que o Portainer vai aplicar; o
   catálogo passa pela régua frouxa do `docker compose config`, porque parte
   dele ainda não foi adequada ao Swarm (o `zoneminder` declara `version: 2`) e
   cobrar a régua estrita ali travaria todo commit no diretório que também
   guarda rascunho. Mais `yamllint` e `ansible-playbook --syntax-check`.
4. **Linters.** `ansible-lint`, `shellcheck`, `terraform fmt`. Ausente do
   sistema, o linter é pulado com aviso em vez de falhar.

### Os dois arquivos de padrões

⚠️ `secret-patterns.txt` é **versionado e público**, e por isso guarda só o
genérico — chave, hash, token, IP privado, MAC. Hostname e domínio vão em
`secret-patterns.local.txt`, gitignorado: uma lista de termos proibidos
publicada no repositório entrega o esquema de nomes e confirma quais nomes
importam, protegendo contra o vazamento acidental ao custo de facilitar o
deliberado.

Sem o arquivo local o gate ainda roda, com o genérico, e **avisa em amarelo**.
Aviso e não falha, porque em clone de terceiro ele legitimamente não existe —
mas visível, porque proteção que existe e não roda foi o modo de falha do
`core.hooksPath`.

⚠️ Ao mudar hostname, domínio ou faixa de rede, atualize o arquivo local. Como
ele não é versionado, nada além do `.example` vai lembrar você — guarde uma
cópia junto do backup do vault.

O terceiro campo de cada padrão isenta caminhos onde o valor é legítimo — e
sempre por ARQUIVO nomeado, nunca por diretório inteiro nem por sufixo
genérico: um fixture de teste já auditado (`tests/fixtures/opnsense.yml`) e o
baseline congelado que ele deriva, ou um `.example` que precisa mesmo de um
endereço para ilustrar o formato (os três do Traefik). O plano de VLANs e os
templates de rede NÃO estão mais nessa lista — as duas isenções que os
cobriam foram removidas quando o dado real saiu do arquivo versionado (ver
seção seguinte).

### Isenção de gate não é ferramenta de desbloqueio

⚠️ Bloqueado pelo gate, a resposta certa é entender por que o commit carrega o
que carrega — nunca alargar o terceiro campo até ele passar. A lista de isenções
legítimas é a da seção acima, inteira; fora dela, nada. Isenção não é mecanismo
para destravar commit que insiste em falhar, e a pergunta antes de tocar em
[`secret-patterns.txt`](./.githooks/secret-patterns.txt) é sempre "por que este
arquivo precisa mesmo do dado real", nunca "como faço o `make secrets` parar de
reclamar".

Duas ocorrências reais no refactor do OPNsense, e nas duas o erro foi isentar o
**diretório** em vez do **arquivo** — que vale para todo arquivo criado ali
depois, auditado ou não. Isento `tests/fixtures/` inteiro, um revisor plantou ali
senha real, chave de acesso AWS e token pessoal do GitHub, e o `make secrets`
aprovou com `rc=0`; isento o baseline do render, ele carregava gateway da LAN,
range de DHCP e sub-rede de VLAN reais. Na fixture a correção foi remover a
isenção de diretório e trocar o valor por um que nunca precisasse dela. No
baseline só a isenção de **IP privado** saiu; a de **endereço MAC** continua —
é a legítima da seção anterior, porque o baseline ainda carrega um MAC de
documentação depois de o IP ter sido parametrizado.

A única direção legítima de mudança numa isenção é estreitar ou remover: a
isenção de arquivo `group_vars/network\.yml$` e a de diretório inteiro
`^IaC/ansible/templates/network/` — cobrindo o plano de VLANs e os templates de
rede da seção anterior — saíram porque o arquivo que protegiam parou de
carregar dado real, nunca porque alguém decidiu relaxar o gate. O comentário
que isenta `IaC/ansible/tests/fixtures/opnsense.yml` no padrão "endereço MAC" em
[`secret-patterns.txt`](./.githooks/secret-patterns.txt) é o modelo a seguir:
nomeia o arquivo exato e explica o motivo.

⚠️ Uma isenção malformada é pior que uma isenção errada: ela pode desarmar o
padrão inteiro sem avisar ninguém. Um `|` órfão deixado na ponta de um campo
de isenção, depois de uma edição mal revisada, virou alternativa vazia em
ERE — que casa qualquer caminho — e o padrão "IP privado" ficou desarmado por
dez commits antes de alguém notar. É por isso que `make validate-patterns`
existe: valida a FORMA dos dois arquivos de padrões (separador presente nas
duas posições, os dois campos de regex compilam) e confirma, com um
caminho-canário (`zz-canary-not-exempt`), que o campo de isenção nunca casa
um caminho arbitrário — em qualquer motor de regex, não só no que roda nesta
máquina. E fecha o lado do regex: cada padrão precisa de uma amostra
conhecida-ruim em `secret-patterns.samples.txt`, e o alvo falha se ela faltar ou
se o regex não casar com ela — regex que não casa nada é o mesmo defeito do `|`
órfão, do outro lado do separador. ⚠️ A checagem estrita vale para o
`secret-patterns.txt` e para o `.example`; para o `secret-patterns.local.txt` do
operador o alvo só **avisa**, porque o arquivo de amostras dele é gitignorado
como ele — padrão local sem amostra passa em amarelo, e quem confere é você.

### `commit-msg`

Duas checagens:

1. **Vazamento na mensagem**, contra os mesmos padrões. Mensagem é publicada
   junto com o diff e não tem conserto sem reescrever o histórico — é o pior
   lugar para um dado real cair. Sem isenção por caminho: mensagem não tem
   caminho. Quando o commit precisa falar de um host, descreva o papel ("o nó
   manager", "o firewall"), não o nome.
2. **Conventional Commits**: `<tipo>(<escopo>): <descrição>`, com `!` antes dos
   dois pontos para mudança incompatível. Tipos aceitos: `feat`, `fix`, `docs`,
   `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`,
   `security`. Assunto até 72 caracteres — acima disso o GitHub trunca
   justamente onde estaria a informação. Particípio ("adicionado") gera aviso,
   não bloqueio.

### Escapar do gate

`git commit --no-verify` funciona e às vezes é o certo. Quando usar, escreva o
motivo no corpo da mensagem — o hook existe para tornar a exceção visível, não
impossível. Num PR, `--no-verify` sem justificativa volta para revisão, e a CI
pega de qualquer forma.

---

## 🧭 Contrato de contribuição (obrigatório)

As seções acima descrevem *como as coisas são*. Esta descreve *o que precisa ser
verdade antes de cada commit*. Vale para pessoas e para assistentes de IA, sem
exceção — quando uma delas precisar ser quebrada, a exceção vai escrita na
mensagem do commit.

### 1. Ativar o gate, uma vez por clone

`make hooks`. O que ele faz e o modo de falha que ele evita estão na seção
"Gate de qualidade".

### 2. Modelo de branches

| branch | papel |
|---|---|
| `main` | o que o Portainer de **produção** clona (`refs/heads/main`) |
| `development` | trabalho corrente; base de todo commit novo |
| `backup` | histórico antigo com dados reais. **Local. Nunca empurrar.** |

Consequência prática: `main` não é uma branch de integração, é um **deploy**.
Merge para `main` publica. O primeiro push é sempre explícito
(`git push -u origin main`), **nunca** `git push --all`, que arrastaria `backup`
junto.

### 3. Nenhum dado real em arquivo versionado

Exemplos usam as faixas de documentação da RFC 5737 (`192.0.2.0/24`,
`198.51.100.0/24`, `203.0.113.0/24`), o MAC de documentação da IANA
(faixa `00:00:5e:00:53:xx`) e domínios `.example`. Hostname de equipamento, faixa
interna, e-mail pessoal e chave SSH real ficam em `.local/`, no vault ou no
inventário gitignorado.

A regra vale para **conteúdo de arquivo e mensagem de commit**. O e-mail de
autoria dos commits não entra aqui: identidade em commit é o funcionamento
normal do Git, e não é dado sensível neste repositório.

⚠️ **Ao mudar hostname, domínio ou faixa de rede, atualizar
`.githooks/secret-patterns.local.txt` no mesmo dia** — os três vão nele, que é
gitignorado, então não há commit que o registre. O [`secret-patterns.txt`](./.githooks/secret-patterns.txt)
versionado só muda quando aparece uma CLASSE genérica nova de segredo: o padrão
"IP privado" dele já cobre 10/8, 172.16/12 e 192.168/16 inteiros, então trocar a
sua faixa não pede edição nenhuma ali. Padrão desatualizado é pior que padrão
nenhum: dá sensação de proteção sem proteger.

### 4. Linter limpo é o estado normal, não a meta

`make check` fecha em **zero** antes de qualquer PR. Achado que não dá para
resolver vira `# noqa: <regra>` com a justificativa escrita na linha de cima —
nunca uma supressão global no [`.ansible-lint`](./.ansible-lint), cujo `profile:
production` está fixado justamente para que um bump de versão do linter não
mude o gate sozinho.

⚠️ **Lint limpo não é o mesmo que correto.** Ao normalizar a indentação dos
composes, dois arquivos foram quebrados de um jeito que o yamllint aprovou:
dedentar até o fim arrastou o bloco `networks:` de topo, transformando
`traefik_public` em chave de topo e `networks` em chave nula — e o resultado
continuava consistentemente indentado, portanto sem erro. Depois de
reformatação em massa, **compare a estrutura parseada**, antes e depois, em vez
de confiar no linter.

⚠️ **Vale para teste também: teste verde não é o mesmo que alvo testado.** Um
teste que **reimplementa** a lógica do alvo passa mesmo com o alvo apagado — ele
valida a própria cópia, não o código. Um teste de gate (o `assert` de um
playbook, uma regra de CI) deve afirmar a **presença e a forma do alvo real** —
ex.: fazer parse do playbook e conferir que a condição ainda está lá. Prove com
o **controle negativo**: apague o alvo e veja o teste ficar vermelho. Se
continuar verde, o teste não testa o que promete.

⚠️ `ignore_errors: true` só entra quando a falha é **esperada por definição** —
por exemplo `kldload coretemp` contra `amdtemp`, em que um dos dois sempre falha
conforme o fabricante da CPU. Nesse caso, use `failed_when:` com a condição
exata e escreva o motivo ao lado. Ignorar erro em bloco foi o que escondeu 53
falhas potenciais nesta base.

### 5. Conventional Commits

Formato e tipos aceitos estão na seção do `commit-msg` acima. O que o hook não
verifica e ainda assim vale: o **corpo** explica *por que*, não *o que* — o
diff já diz o que mudou. Escopo é o diretório afetado (`ansible`, `docker`,
`githooks`, `terraform`, `readme`).

### 6. Documentação acompanha o código, no mesmo commit

Stack nova ou movida entre `docker/` e `docker/_catalog/` atualiza a tabela do
[`README.md`](./README.md) e o [`docker/README.md`](./docker/README.md).
Diretório novo atualiza a árvore desta seção. Os dois já divergiram do real
nesta base, e o hook passou a checar `stack_compose_path` e links relativos
justamente por isso.

### 7. Formatação delegada ao `.editorconfig`

UTF-8, LF, 2 espaços, sem espaço no fim da linha, newline final.
Markdown e `.j2` ficam de fora do `trim_trailing_whitespace`: no primeiro os dois
espaços finais são quebra de linha significativa, no segundo a indentação
pertence ao formato de destino (XML, UCI, INI) e reformatar destrói.

### 8. Versão de imagem sobe por Pull Request do Renovate

Bump manual de tag só quando houver um motivo escrito no commit — CVE, quebra de
compatibilidade, funcionalidade específica. Fora isso, deixe o bot abrir o PR:
é ele quem mantém o rastro de quando e por que cada versão mudou.

---

## 🗣️ Estilo de resposta dos assistentes

Este arquivo é a fonte única: `AGENTS.md` é um symlink para cá — uma fonte,
todas as ferramentas.

- Responder de forma direta e comprimida. Toda substância técnica permanece; só
  o enfeite sai.
- Cortar: artigos supérfluos, muletas ("apenas", "basicamente"), saudações e
  hedging. Fragmentos são aceitáveis.
- Termos técnicos, nomes de API, mensagens de erro e blocos de código ficam
  **literais**, nunca comprimidos.
- Padrão: `[coisa] [ação] [motivo]. [próximo passo].`
  Em vez de "Claro! Ficarei feliz em ajudar com isso", escrever
  "Bug no middleware de auth. Corrige assim:".

**Voltar ao texto normal** em avisos de segurança, ações irreversíveis, sequências
de passos em que a ordem importa, e quando a compressão criar ambiguidade.
Mensagens de commit, PRs e documentação são sempre escritas por extenso.

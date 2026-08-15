# CLAUDE.md - Homelab Repository Guidelines & Architecture Premises

Este arquivo estabelece os princípios de arquitetura, padrões de projeto, convenções de código e boas práticas para manutenção e evolução deste repositório de **HomeLab**. Qualquer assistente de IA ou desenvolvedor deve seguir rigorosamente estas diretrizes ao propor alterações ou criar novas stacks.

---

## 🎯 Visão Geral & Filosofia do Projeto

- **Propósito**: Repositório centralizador de configurações, automações e definições de serviços containerizados (**Docker Swarm / Docker Compose**) do HomeLab.
- **Princípios Fundamentais**:
  - **Soberania de Dados**: Substituição de serviços de nuvem proprietários (Google Photos/Drive, 1Password, etc.) por alternativas *self-hosted* auto-hospedadas.
  - **Privacidade & Segurança**: Roteamento 100% criptografado (HTTPS/TLS), isolamento de redes e ausência de segredos commitados no repositório.
  - **Reprodutibilidade**: Qualquer serviço deve poder ser recriado do zero a partir das definições deste repositório e do diretório de dados persistentes `/srv/containers/`.

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
├── README.md               # Porta de entrada: o que é o projeto e como subir
├── .editorconfig           # Convenções de espaço e fim de linha
├── .githooks/              # Gate de qualidade; ativar com core.hooksPath
├── .gitignore              # vault.yml, hosts.ini, .tfvars, .tfstate, .local/
├── .local/                 # IGNORADO. Diretório padrão de documentação de trabalho
│   ├── README.local.md     # Índice comentado, com o estado de cada documento
│   ├── memoria/            # Symlinks para a memória de longo prazo (~/.claude)
│   └── snapshot/           # Coleta da produção e o levantamento do que roda
├── .renovaterc.json        # Atualização automática de versão de imagem
├── docs/
│   ├── ROADMAP.md          # Backlog por fase
│   └── PRE-DEPLOY.md       # Pré-requisitos do primeiro push público
├── IaC/                    # TODA ferramenta de infraestrutura como código
│   ├── ansible/            # Sistema operacional, cluster Swarm e stacks
│   │   ├── requirements.yml    # Collections Galaxy (community.docker/general, ansible.posix)
│   │   ├── inventories/        # Um diretório por ambiente — ver IaC/ansible/README.md
│   │   │   ├── _common/        # Variáveis comuns a prod e dev (camada 1 de 3)
│   │   │   ├── prod/           # hosts.ini (gitignorado) + group_vars/
│   │   │   └── dev/            # idem, ambiente de desenvolvimento
│   │   ├── tasks/              # Tasks reutilizáveis (Portainer, validação do vault)
│   │   └── playbooks/          # 00-node-bootstrap, 01-05, network/
│   └── terraform/          # Hardware virtual (Proxmox VE — VMs e CTs LXC)
│       └── proxmox/        # Provider bpg/proxmox; runbook de import no README de lá
├── prod-snapshot/          # Ferramentas de coleta do estado real (só os scripts)
│   ├── collect.sh          # Coleta config por host, conforme o grupo do inventário
│   └── discover.sh         # Descobre hosts na rede e sugere o hosts.ini
└── docker/                 # Stacks de serviço
    ├── README.md           # Instruções de deploy
    ├── _catalog/           # Definidas mas NÃO implantadas — ver o README de lá
    └── <servico>/swarm/compose.yml
```

> `docker/` contém apenas o que está no ar. Tudo o mais vive em
> `docker/_catalog/`, para que a árvore não descreva serviços inexistentes.

### Regra: toda ferramenta de IaC vive sob `IaC/`

Ansible, Terraform e qualquer ferramenta futura — Packer, Vagrant, Pulumi,
cloud-init — entram como um subdiretório de `IaC/`, **nunca** na raiz.

O motivo é o mesmo que separou `docker/` de `docker/_catalog/`: a raiz deve
responder *o que este repositório é*, não *quais ferramentas ele usa*. Com
`ansible/` e `terraform/` soltos no topo, cada ferramenta nova custava mais uma
entrada na raiz, e a leitura passava a ser um inventário de tecnologias em vez de
um mapa do sistema. Agrupadas, a raiz diz: aqui se provisiona infraestrutura
(`IaC/`), aqui se definem serviços (`docker/`), aqui se documenta (`docs/`).

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

## ✅ Gate de qualidade (`.githooks/`)

Ativar uma vez por clone — o Git não compartilha `.git/hooks/`:

```bash
git config core.hooksPath .githooks
```

### `pre-commit`

Roda só sobre o que está no índice, na ordem em que os problemas custam caro:

1. **Vazamento.** Compara as linhas *adicionadas* contra
   [`.githooks/secret-patterns.txt`](./.githooks/secret-patterns.txt): chave
   privada, chave SSH real, hash de senha, token de 64 hex, senha literal,
   e-mail pessoal, MAC, IP privado, hostname de dispositivo e domínio real.
   Também barra nome de arquivo proibido mesmo que o `.gitignore` falhe.
2. **Referências.** Todo `stack_compose_path` aponta para arquivo existente, e
   todo link relativo de markdown resolve. As duas já quebraram de verdade nesta
   base, ao mover stacks para `_catalog/` e ao criar `docs/`.
3. **Sintaxe.** `docker stack config` nos composes alterados, YAML parseável,
   `ansible-playbook --syntax-check`.
4. **Linters.** `ansible-lint`, `shellcheck`, `terraform fmt`. Ausente do
   sistema, o linter é pulado com aviso em vez de falhar.

Os padrões são **deste** repositório, não uma lista genérica: ferramenta de
prateleira pega chave da AWS, não pega o hostname do seu hypervisor. Ao mudar
hostname, domínio ou faixa de rede, atualize o arquivo de padrões — um padrão
desatualizado é pior que padrão nenhum, porque dá a sensação de proteção.

O terceiro campo de cada padrão isenta caminhos onde o valor é legítimo: o plano
de VLANs precisa declarar gateways, os templates de rede descrevem a topologia
atual, e os `.example` existem para mostrar o formato.

### `commit-msg`

Conventional Commits: `<tipo>(<escopo>): <descrição>`, com `!` antes dos dois
pontos para mudança incompatível. Tipos aceitos: `feat`, `fix`, `docs`, `style`,
`refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`, `security`.
Assunto até 72 caracteres — acima disso o GitHub trunca justamente onde estaria
a informação. Descrição no particípio ("adicionado") gera aviso, não bloqueio.

### Escapar do gate

`git commit --no-verify` funciona e às vezes é o certo. Quando usar, escreva o
motivo no corpo da mensagem — o hook existe para tornar a exceção visível, não
impossível.

---

## 🧭 Contrato de contribuição (obrigatório)

As seções acima descrevem *como as coisas são*. Esta descreve *o que precisa ser
verdade antes de cada commit*. Vale para pessoas e para assistentes de IA, sem
exceção — quando uma delas precisar ser quebrada, a exceção vai escrita na
mensagem do commit.

### 1. Ativar o gate, uma vez por clone

```bash
git config core.hooksPath .githooks
```

O Git não versiona `.git/hooks/`. Sem esse comando os hooks existem no
repositório e não rodam — é o modo de falha silencioso mais comum aqui.

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

⚠️ **Ao mudar hostname, domínio ou faixa de rede, atualizar
[`.githooks/secret-patterns.txt`](./.githooks/secret-patterns.txt) no mesmo
commit.** Padrão desatualizado é pior que padrão nenhum: dá sensação de proteção
sem proteger.

### 4. Linter limpo é o estado normal, não a meta

`ansible-lint` deve fechar em **zero**. Achado que não dá para resolver vira
`# noqa: <regra>` com a justificativa escrita na linha de cima — nunca uma
supressão global no `.ansible-lint`. `shellcheck -S warning`, `terraform fmt` e
`docker stack config` seguem a mesma regra.

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

Este arquivo é a fonte única: `AGENTS.md` é um symlink para cá, de modo que
todas as ferramentas leiam as mesmas regras. Antes existiam seis cópias
(`.clinerules/`, `.cursor/`, `.github/`, `.opencode/`, `.windsurf/` e a raiz),
quatro idênticas byte a byte e duas já divergidas.

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

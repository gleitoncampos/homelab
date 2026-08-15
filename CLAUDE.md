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
`opnsense_config.xml.j2` nasceu — um dump de 3.8 MB do `/conf/config.xml` vivo,
commitado para não perder o que havia sido feito à mão, carregando junto senha,
chave privada e a topologia inteira.

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
├── Makefile                # Entrada única: make hooks, make check, make secrets
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
│   ├── secret-patterns.txt # Genérico; VERSIONADO e público
│   └── secret-patterns.local.txt.example  # Sua topologia; a cópia é ignorada
├── .gitignore              # vault.yml, hosts.ini, .tfvars, .tfstate, .local/
├── .yamllint               # Estilo de YAML; `yes`/`no` truthy é erro
├── .local/                 # IGNORADO. Diretório padrão de documentação de trabalho
│   ├── README.local.md     # Índice comentado, com o estado de cada documento
│   ├── docs/               # Backlog, pré-publicação, pendências, plano de hardening
│   ├── memoria/            # Symlinks para a memória de longo prazo (~/.claude)
│   └── snapshot/           # Coleta da produção e o levantamento do que roda
├── .renovaterc.json        # Atualização automática de versão de imagem
├── docs/
│   └── runbooks/           # Procedimento operacional genérico, sem dado real
│       └── restauracao.md  # Verificação e recuperação do backup dos convidados
├── IaC/                    # TODA ferramenta de infraestrutura como código
│   ├── bootstrap/          # Gera as ENTRADAS que as duas ferramentas consomem
│   │   ├── bootstrap.py    # Fonte única -> vault, hosts.ini, tfvars, padrões
│   │   ├── test_render.py  # Testa contra ansible-inventory e terraform fmt
│   │   └── templates/      # Modelo do arquivo de respostas (.tmpl: não é YAML ainda)
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

Ativar uma vez por clone — o Git não compartilha `.git/hooks/`:

```bash
make hooks     # equivale a: git config core.hooksPath .githooks
make check     # o mesmo conjunto que a CI roda
```

### `pre-commit`

Na ordem em que os problemas custam caro:

1. **Vazamento.** Compara as linhas *adicionadas* contra
   [`secret-patterns.txt`](./.githooks/secret-patterns.txt) **e**
   `secret-patterns.local.txt`. Também barra nome de arquivo proibido mesmo que
   o `.gitignore` falhe.
2. **Referências.** Todo `stack_compose_path` aponta para arquivo existente, e
   todo link relativo de markdown resolve. As duas já quebraram de verdade nesta
   base, ao mover stacks para `_catalog/` e ao criar `docs/`.
3. **Sintaxe.** Compose validado com **duas réguas**: o que está no ar passa por
   `docker stack config`, que é o schema que o Portainer vai aplicar; o catálogo
   passa por `docker compose config`, porque estar lá significa que a definição
   ainda não foi adequada. Mais `yamllint` e `ansible-playbook --syntax-check`.
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

O terceiro campo de cada padrão isenta caminhos onde o valor é legítimo: o plano
de VLANs precisa declarar gateways, os templates de rede descrevem a topologia
atual, e os `.example` existem para mostrar o formato.

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

```bash
make hooks
```

Ativa `core.hooksPath` e cria o `secret-patterns.local.txt` a partir do
`.example`. O Git não versiona `.git/hooks/`: sem esse passo os hooks existem no
repositório e não rodam — o modo de falha silencioso mais comum aqui.

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

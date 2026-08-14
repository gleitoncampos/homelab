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
- **Automação**: O bot **Renovate** ([`renovate.json`](file:///home/nuvidio/git/homelab/renovate.json)) é responsável por abrir Pull Requests automáticos de atualização de versão das imagens.

---

## 📁 Estrutura do Repositório

```text
.
├── CLAUDE.md               # Este arquivo (Diretrizes e premissas do repositório)
├── README.md               # Documentação principal e catálogo de serviços
├── TODO.md                 # Roadmap e backlog de tarefas/melhorias
├── bootstrap.sh            # Script de setup do SO base (Debian 12/13)
├── renovate.json           # Configuração de atualização de dependências pelo Renovate
├── .gitignore              # Arquivos ignorados pelo Git (vault.yml, hosts.ini, .tfvars, .tfstate, ...)
├── ansible/                # Playbooks e papéis do Ansible (IaC do Host e Swarm)
│   ├── requirements.yml    # Collections Galaxy exigidas (community.docker, community.general, ansible.posix)
│   ├── inventories/        # Um diretório por ambiente — ver ansible/README.md
│   │   ├── _common/        # Variáveis comuns a prod e dev (camada 1 de 3)
│   │   ├── prod/            # hosts.ini (gitignorado) + group_vars/ (main.yml, vault.yml)
│   │   └── dev/              # idem, ambiente de desenvolvimento (CT isolado, branch develop)
│   ├── tasks/               # Tasks reutilizáveis (login/deploy no Portainer, validação do vault)
│   └── playbooks/           # 00-node-bootstrap, 01-05 (bootstrap OS/cluster/stacks), network/
├── terraform/              # IaC do hardware virtual (Proxmox VE — VMs e CTs LXC)
│   └── proxmox/            # Provider bpg/proxmox; ver terraform/proxmox/README.md p/ runbook de import
└── docker/                 # Stacks de Serviços (1 diretório por aplicação)
    ├── README.md           # Instruções de deploy das stacks
    └── <servico>/          # Pasta do serviço
        └── swarm/
            └── compose.yml # Definição da stack Docker Swarm
```

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
   - Atualizar a tabela de catálogo no [`README.md`](file:///home/nuvidio/git/homelab/README.md).
   - Atualizar o guia de stacks em [`docker/README.md`](file:///home/nuvidio/git/homelab/docker/README.md).

# Docker & Docker Swarm Stacks

Esta pasta centraliza todas as definições de serviços e infraestrutura containerizada do HomeLab, padronizados principalmente para execução em **Docker Swarm** com ingress via **Traefik** e gerenciamento visual via **Portainer**.

---

## 🚀 Fluxo de Inicialização do Cluster

### 1. Ingress & Gerenciamento Base
1. **Traefik** (`docker/traefik/`): Reverse Proxy central, resolve certificados SSL/TLS (Cloudflare / Let's Encrypt) e roteia domínios.
2. **Portainer** (`docker/portainer/`): Interface visual de gerenciamento das Stacks.
   - O `${FQDN}` desta stack é injetado pelo Ansible (`02-bootstrap-cluster.yml`) via `environment:` no `docker stack deploy` — não há `.env` para copiar; o valor vem de `fqdn_portainer`, derivado de `homelab_domain` no vault.

A rede overlay `traefik_public` e o `docker swarm init` são feitos por `02-bootstrap-cluster.yml`, não à mão — ver o aviso no [`README.md`](../README.md) da raiz.

### 2. Monitoramento & Observabilidade
- **Monitoring Stack** ([`docker/monitoring/`](./monitoring/README.md)): Grafana + Prometheus + Loki + Grafana Alloy para centralização de métricas e logs de containers e do host.
  - **Só o Grafana é publicado pelo Traefik** (rota `${FQDN_GRAFANA}`). **Prometheus, Loki e InfluxDB não têm mais label de roteamento** e ficam acessíveis apenas dentro da rede overlay interna `monitoring` — nenhum dos três tem autenticação própria, e o middleware `forwardAuth` de um futuro SSO não serve para eles: o portal de login redireciona para uma página HTML, o que quebra qualquer cliente de máquina (Grafana consultando o Prometheus como datasource, Alloy fazendo `remote_write` para o Loki). Expor os três exigiria autenticação nativa própria de cada um — nenhum tem hoje — então a decisão de arquitetura foi não publicá-los.
- **Statping** (`docker/_catalog/statping/`): Dashboard de status de serviços e alertas de uptime.

### 3. Demais stacks
Das 26 no total, tirando Traefik e Portainer já apresentadas na §1, sobram 24. Elas chegam ao cluster por um destes dois caminhos, nunca por edição manual do serviço já no ar: 12 são implantadas pelos playbooks `03` a `05` (11 de `_catalog/` mais a Monitoring Stack); as outras 12 — Dozzle inclusive, apesar de já estar fora de `_catalog/` — não têm playbook e sobem pelo formulário de Stacks do Portainer. `docker/` vs. `docker/_catalog/` não é "implantado" vs. "não implantado": é "convenção de Swarm ok para promover" vs. "ainda precisa adequação" — ver [`docker/_catalog/README.md`](./_catalog/README.md).

---

## 🛠️ Convenções das Stacks

- **Rede Externa**: Todas as aplicações expostas utilizam a rede `traefik_public` (`external: true`).
- **Rotas Traefik — nome de variável**: a maioria das stacks usa `${FQDN}` genérico (uma stack = um domínio). Stacks com **mais de um serviço público no mesmo compose** usam um nome de variável específico por serviço, ex.: `${FQDN_GRAFANA}` em `docker/monitoring/swarm/compose.yml` — necessário porque essa stack tem vários serviços (Grafana, Prometheus, Loki, InfluxDB) e só o Grafana é roteado (ver secção de Monitoramento acima); um `${FQDN}` genérico não deixaria claro qual serviço ele endereça. As variáveis são preenchidas pelo Ansible (quando existe playbook, via `tasks/portainer_stack.yml`) ou digitadas manualmente no formulário da Stack no Portainer (quando ainda não existe playbook).
- **Persistência de Dados**: Volumes são mapeados em caminhos padronizados do host (ex: `/srv/containers/<servico>`).

---

## 📋 Lista Completa de Serviços

| Categoria | Serviço | Caminho | Descrição |
| :--- | :--- | :--- | :--- |
| **Infra & Ingress** | Traefik | `docker/traefik/` | Reverse Proxy & SSL Auto-Certificates |
| | Portainer | `docker/portainer/` | Interface de Gerenciamento do Swarm |
| **Monitoramento** | Monitoring Stack | `docker/monitoring/` | Grafana, Prometheus, Loki, Alloy |
| | Dozzle | `docker/dozzle/` | Visualizador de Logs de Containers em Tempo Real |
| | Statping | `docker/_catalog/statping/` | Status Page & Uptime Monitoring |
| | Glance | `docker/_catalog/glance/` | Dashboard Unificado de Homelab |
| | Organizr | `docker/_catalog/organizr/` | Hub de Serviços e Dashboard |
| **Produtividade & Cloud**| Nextcloud | `docker/_catalog/nextcloud/` | Armazenamento em Nuvem & Fotos |
| | Paperless-ngx | `docker/_catalog/paperless-ngx/` | Gestão e OCR de Documentos |
| | Obsidian Sync | `docker/_catalog/obsidian/` | Sincronização de Notas |
| | Syncthing | `docker/_catalog/syncthing/` | Sincronização de Arquivos P2P entre Dispositivos |
| | Stirling-PDF | `docker/_catalog/stirling-pdf/` | Ferramentas Avançadas para PDF |
| | Linkwarden | `docker/_catalog/linkwarden/` | Arquivamento de Links e Bookmarks |
| | ByteStash | `docker/_catalog/bytestash/` | Gerenciador de Snippets de Código |
| | IT-Tools | `docker/_catalog/it-tools/` | Utilitários para Devs e Sysadmins |
| | Composerize | `docker/_catalog/composerize/` | Conversor de `docker run` para Compose |
| **Gestão & Finanças** | Vikunja | `docker/_catalog/vikunja/` | Gestão de Tarefas e Projetos |
| | Actual Budget | `docker/_catalog/actual-budget/` | Controle Financeiro Pessoal |
| | Firefly III | `docker/_catalog/firefly3/` | Gestão de Finanças Avançada |
| | Wallos | `docker/_catalog/wallos/` | Rastreador de Assinaturas e Mensalidades |
| | Thrifty | `docker/_catalog/thrifty/` | Gerenciador de Despesas Leve |
| | PriceBuddy | `docker/_catalog/pricebuddy/` | Acompanhamento de Preços |
| **Utilitários & Mídia** | Cronicle | `docker/_catalog/cronicle/` | Agendador de Tarefas Distribuído |
| | JDownloader | `docker/_catalog/jdownloader/` | Gerenciador de Downloads |
| | KASM Workspaces | `docker/_catalog/kasm/` | Ambientes Desktop / Navegador isolados |
| | ZoneMinder | `docker/_catalog/zoneminder/` | Sistema NVR / Monitoramento de Câmeras |

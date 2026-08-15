# Docker & Docker Swarm Stacks

Esta pasta centraliza todas as definições de serviços e infraestrutura containerizada do HomeLab, padronizados principalmente para execução em **Docker Swarm** com ingress via **Traefik** e gerenciamento visual via **Portainer**.

---

## 🚀 Fluxo de Inicialização do Cluster

Para subir a infraestrutura do zero, siga a ordem de prioridade de inicialização:

### 1. Ingress & Gerenciamento Base
1. **Rede Externa Swarm**: Certifique-se de que a rede overlay `traefik_public` exista no Docker Swarm:
   ```bash
   docker network create --driver=overlay traefik_public
   ```
2. **Traefik** (`docker/traefik/`): Atua como Reverse Proxy central, resolvendo certificados SSL/TLS (Cloudflare / Let's Encrypt) e roteando domínios.
3. **Portainer** (`docker/portainer/`): Interface visual de gerenciamento das Stacks.
   - O `${FQDN}` desta stack é injetado pelo Ansible (`02-bootstrap-cluster.yml`) via `environment:` no `docker stack deploy` — não há `.env` para copiar; o valor vem de `fqdn_portainer`, derivado de `homelab_domain` no vault.

### 2. Monitoramento & Observabilidade
- **Monitoring Stack** (`docker/monitoring/`): Grafana + Prometheus + Loki + Grafana Alloy para centralização de métricas e logs de containers e do host.
  - **Só o Grafana é publicado pelo Traefik** (rota `${FQDN_GRAFANA}`). **Prometheus, Loki e InfluxDB não têm mais label de roteamento** e ficam acessíveis apenas dentro da rede overlay interna `monitoring` — nenhum dos três tem autenticação própria, e o middleware `forwardAuth` de um futuro SSO não serve para eles: o portal de login redireciona para uma página HTML, o que quebra qualquer cliente de máquina (Grafana consultando o Prometheus como datasource, Alloy fazendo `remote_write` para o Loki). Expor os três exigiria autenticação nativa própria de cada um — nenhum tem hoje — então a decisão de arquitetura foi não publicá-los.
- **Statping** (`docker/_catalog/statping/`): Dashboard de status de serviços e alertas de uptime.

### 3. Demais Stacks de Aplicação
Qualquer outro serviço nesta pasta é considerado um **serviço fixo e de produção** do cluster. O deploy é feito importando os arquivos `compose.yml` da pasta correspondente através da interface de Stacks do Portainer.

---

## 🛠️ Convenções das Stacks

- **Rede Externa**: Todas as aplicações expostas utilizam a rede `traefik_public` (`external: true`).
- **Rotas Traefik — nome de variável**: a maioria das stacks usa `${FQDN}` genérico (uma stack = um domínio). Stacks com **mais de um serviço público no mesmo compose** usam um nome de variável específico por serviço, ex.: `${FQDN_GRAFANA}` em `docker/monitoring/swarm/compose.yml` — necessário porque essa stack tem vários serviços (Grafana, Prometheus, Loki, InfluxDB) e só o Grafana é roteado (ver secção de Monitoramento acima); um `${FQDN}` genérico não deixaria claro qual serviço ele endereça. As variáveis são preenchidas pelo Ansible (quando existe playbook, via `tasks/portainer_stack.yml`) ou digitadas manualmente no formulário da Stack no Portainer (quando ainda não existe playbook).
- **Persistência de Dados**: Volumes são mapeados em caminhos padronizados do host (ex: `/srv/containers/<servico>`).

---

## 📋 Lista Completa de Serviços Expostos

| Categoria | Serviço | Caminho | Descrição |
| :--- | :--- | :--- | :--- |
| **Infra & Ingress** | Traefik | `docker/traefik/` | Reverse Proxy & SSL Auto-Certificates |
| | Portainer | `docker/portainer/` | Interface de Gerenciamento do Swarm |
| **Monitoramento** | Monitoring Stack | `docker/monitoring/` | Grafana, Prometheus, Loki, Alloy |
| | Statping | `docker/_catalog/statping/` | Status Page & Uptime Monitoring |
| | Glance | `docker/_catalog/glance/` | Dashboard Unificado de Homelab |
| | Organizr | `docker/_catalog/organizr/` | Hub de Serviços e Dashboard |
| **Produtividade & Cloud**| Nextcloud | `docker/_catalog/nextcloud/` | Armazenamento em Nuvem & Fotos |
| | Paperless-ngx | `docker/_catalog/paperless-ngx/` | Gestão e OCR de Documentos |
| | Obsidian Sync | `docker/_catalog/obsidian/` | Sincronização de Notas |
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

---

## 📌 TO-DO / Próximos Passos
- [ ] Implementar IaC para provisionamento automático do nó Swarm e Portainer.
- [ ] Automação de backup dos volumes em `/srv/containers/` via Restic ou BorgBackup.
---

## 📚 Referências de monitoramento

Migradas de `docker/monitoring/README.md`.

- [Loki: instalação por pacote](https://grafana.com/docs/loki/latest/setup/install/local/#install-using-apt-or-rpm-package-manager)
- [Alloy: instalação em Linux](https://grafana.com/docs/alloy/latest/set-up/install/linux/) · [migração do Promtail](https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/)
- [Centralizar logs de aplicação no Grafana](https://dev.to/onticdani/how-to-centralize-and-visualize-your-app-logs-in-grafana-483c) · [logs de containers com Loki](https://dev.to/onticdani/centralize-and-visualize-docker-logs-in-grafana-with-loki-27c9)
- [6 formas de melhorar dashboards de log](https://grafana.com/blog/2023/05/18/6-easy-ways-to-improve-your-log-dashboards-with-grafana-and-grafana-loki/)
- [Monitorar OpenWrt com Prometheus](https://www.cloudrocket.at/posts/monitor-openwrt-nodes-with-prometheus/) · [com Grafana Cloud](https://grafana.com/blog/2021/02/09/how-i-monitor-my-openwrt-router-with-grafana-cloud-and-prometheus/)
- [Rotação de log por tamanho no rsyslog](https://www.rsyslog.com/doc/tutorials/log_rotation_fix_size.html)
- [Setup de referência de homelab](https://giuliomagnifico.blog/post/2024-07-08-home-setup-v5/#log-monitoring-in-grafana)

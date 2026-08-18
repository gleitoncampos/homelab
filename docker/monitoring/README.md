# Monitoring Stack

Grafana, Prometheus, Loki, Alloy, cAdvisor, node_exporter, InfluxDB e o
exporter do Proxmox — métricas e logs de containers e host num único compose;
só o Grafana é roteado pelo Traefik (ver [`docker/README.md`](../README.md)).

## 📚 Referências

- [Loki: instalação por pacote](https://grafana.com/docs/loki/latest/setup/install/local/#install-using-apt-or-rpm-package-manager)
- [Alloy: instalação em Linux](https://grafana.com/docs/alloy/latest/set-up/install/linux/) · [migração do Promtail](https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/)
- [Centralizar logs de aplicação no Grafana](https://dev.to/onticdani/how-to-centralize-and-visualize-your-app-logs-in-grafana-483c) · [logs de containers com Loki](https://dev.to/onticdani/centralize-and-visualize-docker-logs-in-grafana-with-loki-27c9)
- [6 formas de melhorar dashboards de log](https://grafana.com/blog/2023/05/18/6-easy-ways-to-improve-your-log-dashboards-with-grafana-and-grafana-loki/)
- [Monitorar OpenWrt com Prometheus](https://www.cloudrocket.at/posts/monitor-openwrt-nodes-with-prometheus/) · [com Grafana Cloud](https://grafana.com/blog/2021/02/09/how-i-monitor-my-openwrt-router-with-grafana-cloud-and-prometheus/)
- [Rotação de log por tamanho no rsyslog](https://www.rsyslog.com/doc/tutorials/log_rotation_fix_size.html)
- [Setup de referência de homelab](https://giuliomagnifico.blog/post/2024-07-08-home-setup-v5/#log-monitoring-in-grafana)

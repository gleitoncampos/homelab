# ==============================================================================
# OUTPUTS
# ------------------------------------------------------------------------------
# Todos os valores de IP abaixo referenciam o atributo do próprio recurso
# (o que foi de fato configurado via cloud-init/initialization), nunca uma
# string literal duplicada aqui. Antes, outputs.tf tinha os IPs digitados à
# mão em paralelo ao main.tf (inclusive um IP de OMV que era ficção - não
# correspondia a nenhum atributo real do recurso) - bastava um dos dois lados
# ficar desatualizado para o output mentir.
# ==============================================================================

output "opnsense_vm_id" {
  value       = proxmox_virtual_environment_vm.opnsense.vm_id
  description = "VMID da VM do OPNsense Firewall (nó primário)."
}

output "docker_swarm_node01_id" {
  value       = proxmox_virtual_environment_container.docker_swarm_node01.vm_id
  description = "CTID do Container LXC do Docker Swarm Node 01 (nó primário)."
}

output "docker_swarm_node01_ip" {
  value       = proxmox_virtual_environment_container.docker_swarm_node01.initialization[0].ip_config[0].ipv4[0].address
  description = "IP estático (CIDR) do Docker Swarm Node 01 na VLAN homelab, lido da configuração do próprio recurso."
}

output "omv_nas_vm_id" {
  value       = proxmox_virtual_environment_vm.omv_nas.vm_id
  description = "VMID da VM do OpenMediaVault NAS Storage (nó secundário)."
}

output "omv_nas_ip" {
  value       = proxmox_virtual_environment_vm.omv_nas.initialization[0].ip_config[0].ipv4[0].address
  description = "IP estático (CIDR) do OpenMediaVault NAS na VLAN homelab, lido da configuração do próprio recurso (cloud-init)."
}

output "docker_swarm_node02_id" {
  value       = proxmox_virtual_environment_container.docker_swarm_node02.vm_id
  description = "CTID do Container LXC do Docker Swarm Node 02 (nó secundário)."
}

output "docker_swarm_node02_ip" {
  value       = proxmox_virtual_environment_container.docker_swarm_node02.initialization[0].ip_config[0].ipv4[0].address
  description = "IP estático (CIDR) do Docker Swarm Node 02 na VLAN homelab, lido da configuração do próprio recurso."
}

output "docker_dev_node_id" {
  value       = proxmox_virtual_environment_container.docker_dev_node.vm_id
  description = "CTID do Container LXC do nó de desenvolvimento (nó secundário) - fora do Swarm de produção."
}

output "docker_dev_node_ip" {
  value       = proxmox_virtual_environment_container.docker_dev_node.initialization[0].ip_config[0].ipv4[0].address
  description = "IP estático (CIDR) do nó de desenvolvimento na VLAN homelab."
}

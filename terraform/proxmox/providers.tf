# ------------------------------------------------------------------------------
# Versionamento travado: nunca deixar o range aberto (">= x.y.z" sozinho).
# Um "major" novo do provider/Terraform pode renomear atributo e quebrar plan
# silenciosamente em produção. O arquivo .terraform.lock.hcl gerado pelo
# `terraform init` DEVE ser commitado (ver .gitignore) para fixar o build hash
# exato junto com este range.
# ------------------------------------------------------------------------------
terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.66"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_api_url
  api_token = "${var.proxmox_api_token_id}=${var.proxmox_api_token_secret}"
  insecure  = true # Permite conexões com certificado autoassinado do Proxmox

  ssh {
    agent = true
    # Usuário de SO usado pelo provider via SSH no host Proxmox (upload de
    # ISO/imagens, importação de disco, etc.) - é DISTINTO do usuário/token da
    # API (var.proxmox_api_token_id). Parametrizado para não fixar "root" em
    # texto puro no HCL; ver README para a alternativa de usuário com sudo.
    username = var.proxmox_ssh_username
  }
}

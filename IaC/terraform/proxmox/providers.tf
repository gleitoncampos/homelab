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

# ------------------------------------------------------------------------------
# UM PROVIDER POR NÓ - os dois hipervisores são INDEPENDENTES, não um cluster
# ------------------------------------------------------------------------------
# Dois nós não formam quórum, então não há cluster aqui - e isso não é detalhe
# de operação, é o que decide a forma deste arquivo. Sem cluster:
#
#   - cada nó expõe a PRÓPRIA API, num endpoint próprio;
#   - cada nó tem usuário e token próprios — um `terraform@pve` criado
#     num deles não existe no outro;
#   - `node_name` não seleciona nó dentro de um cluster, porque não há cluster
#     para atravessar. Ele apenas nomeia o nó ao qual o provider já está ligado.
#
# Com um provider só, todo recurso marcado com `var.proxmox_node_secondary`
# seria enviado ao endpoint do primário, que responderia que esse nó não existe.
# Daí os dois alias abaixo: cada recurso declara explicitamente contra qual
# hipervisor ele fala.
provider "proxmox" {
  alias     = "primary"
  endpoint  = var.proxmox_api_url_primary
  api_token = "${var.proxmox_api_token_id_primary}=${var.proxmox_api_token_secret_primary}"
  insecure  = true # Permite conexões com certificado autoassinado do Proxmox

  ssh {
    agent = true
    # Usuário de SO usado pelo provider via SSH no host Proxmox (upload de
    # ISO/imagens, importação de disco, etc.) - é DISTINTO do usuário/token da
    # API. Parametrizado para não fixar "root" em texto puro no HCL; ver README
    # para a alternativa de usuário com sudo.
    username = var.proxmox_ssh_username
  }
}

provider "proxmox" {
  alias     = "secondary"
  endpoint  = var.proxmox_api_url_secondary
  api_token = "${var.proxmox_api_token_id_secondary}=${var.proxmox_api_token_secret_secondary}"
  insecure  = true

  ssh {
    agent    = true
    username = var.proxmox_ssh_username
  }
}

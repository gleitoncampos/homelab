#!/bin/sh
# Gerado pelo Ansible (00-proxmox-setup.yml). NAO editar a mao.
#
# Remove o aviso de assinatura da GUI web e da GUI movel. Roda pelo Ansible e,
# via /etc/apt/apt.conf.d/no-nag-script, depois de TODO dpkg: uma atualizacao
# do proxmox-widget-toolkit reinstala o proxmoxlib.js original, e sem o gancho
# o aviso voltava ate a proxima execucao do playbook. Ideia do gancho e bloco
# movel: community-scripts/ProxmoxVE (tools/pve/post-pve-install.sh).
# Idempotente pelas marcas; imprime "aplicado" so quando muda algo.

WEB_JS=${WEB_JS:-/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js}
if [ -s "$WEB_JS" ] && ! grep -q "void({ //Ext.Msg.show" "$WEB_JS"; then
    sed -Ezi "s/(Ext.Msg.show\(\{\s+title: gettext\('No valid sub)/void\(\{ \/\/\1/g" "$WEB_JS"
    echo "aplicado: GUI web"
    systemctl reload-or-restart pveproxy.service >/dev/null 2>&1 || true
fi

MOBILE_TPL=${MOBILE_TPL:-/usr/share/pve-yew-mobile-gui/index.html.tpl}
MARKER="<!-- MANAGED BLOCK FOR MOBILE NAG -->"
if [ -f "$MOBILE_TPL" ] && ! grep -q "$MARKER" "$MOBILE_TPL"; then
    cat >>"$MOBILE_TPL" <<'BLOCO'
<!-- MANAGED BLOCK FOR MOBILE NAG -->
<script>
  function removeSubscriptionElements() {
    document.querySelectorAll('dialog.pwt-outer-dialog').forEach(dialog => {
      if ((dialog.textContent || '').toLowerCase().includes('subscription')) dialog.remove();
    });
    // Remove o cartão de assinatura, mas não os de Reboot/Shutdown/Console (têm botão)
    document.querySelectorAll('.pwt-card.pwt-p-2.pwt-d-flex.pwt-interactive.pwt-justify-content-center').forEach(card => {
      if (!card.querySelector('button') && (card.textContent || '').toLowerCase().includes('subscription')) card.remove();
    });
  }
  const observer = new MutationObserver(removeSubscriptionElements);
  observer.observe(document.body, { childList: true, subtree: true });
  removeSubscriptionElements();
  setInterval(removeSubscriptionElements, 300);
  setTimeout(() => { observer.disconnect(); }, 10000);
</script>
BLOCO
    echo "aplicado: GUI movel"
fi
exit 0

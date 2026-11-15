curl -fsSL @BASE_URL@/lan-party.asc | pacman-key --add -
pacman-key --lsign-key @FINGERPRINT@
grep -q '^\[lan-party\]' /etc/pacman.conf || printf '\n[lan-party]\nServer = @BASE_URL@/arch/$arch\n' >> /etc/pacman.conf
pacman -Sy

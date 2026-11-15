mkdir -p /etc/apt/keyrings
curl -fsSL @BASE_URL@/lan-party.gpg -o /etc/apt/keyrings/lan-party.gpg
chmod 644 /etc/apt/keyrings/lan-party.gpg
cat > /etc/apt/sources.list.d/lan-party.list <<'EOT'
deb [signed-by=/etc/apt/keyrings/lan-party.gpg] @BASE_URL@/deb @SUITE@ main
EOT
apt-get update

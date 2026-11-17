mkdir -p /etc/apt/keyrings
wget -qO /etc/apt/keyrings/lan-party.gpg @BASE_URL@/lan-party.gpg
chmod 644 /etc/apt/keyrings/lan-party.gpg
cat > /etc/apt/sources.list.d/lan-party.list <<'EOT'
deb [signed-by=/etc/apt/keyrings/lan-party.gpg] @BASE_URL@/deb @SUITE@ main
EOT
apt-get update

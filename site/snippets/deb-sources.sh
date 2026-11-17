mkdir -p /etc/apt/keyrings
wget -qO /etc/apt/keyrings/lan-party.gpg @BASE_URL@/lan-party.gpg
chmod 644 /etc/apt/keyrings/lan-party.gpg
cat > /etc/apt/sources.list.d/lan-party.sources <<'EOT'
Types: deb
URIs: @BASE_URL@/deb
Suites: @SUITE@
Components: main
Signed-By: /etc/apt/keyrings/lan-party.gpg
EOT
apt-get update

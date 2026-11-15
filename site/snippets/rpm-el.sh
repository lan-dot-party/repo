cat > /etc/yum.repos.d/lan-party.repo <<'EOT'
[lan-party]
name=lan.party package repository
baseurl=@BASE_URL@/rpm/el/$releasever/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=@BASE_URL@/lan-party.asc
EOT

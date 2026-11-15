wget -qO /etc/apk/keys/lan-party.rsa.pub @BASE_URL@/lan-party.rsa.pub
grep -qxF '@BASE_URL@/alpine/main' /etc/apk/repositories || echo '@BASE_URL@/alpine/main' >> /etc/apk/repositories
apk update

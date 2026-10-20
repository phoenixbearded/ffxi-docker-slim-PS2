# 24.04's libdwarf-dev has no pkg-config file, which the build (cpptrace) requires; 26.04's does
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# Same package set that "Setup Linux.sh" installs on apt systems (no Tk: headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-venv python3-pip cmake ninja-build pkg-config \
        libluajit-5.1-dev libzmq3-dev libssl-dev zlib1g-dev libzstd-dev libdwarf-dev \
        mariadb-server mariadb-client libmariadb-dev-compat binutils-dev g++ \
    && rm -rf /var/lib/apt/lists/*

# MariaDB refuses to run as root, so everything runs as a normal user
# the Ubuntu image already has a user "ubuntu" with UID 1000: remove it, then create ours
RUN userdel -r ubuntu 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash ffxi \
    && mkdir -p /ffxi && chown ffxi:ffxi /ffxi

# backups look for mariadb-dump next to mariadbd (/usr/sbin on Ubuntu), the package puts it in /usr/bin
RUN ln -sf /usr/bin/mariadb-dump /usr/sbin/mariadb-dump

WORKDIR /ffxi
COPY --chown=ffxi:ffxi Server /ffxi/Server
USER ffxi

# Build the four LandSandBoat programs (20-60 min, cached after the first build)
RUN for v in 15 14 13; do command -v g++-$v >/dev/null && { export CC=gcc-$v CXX=g++-$v; break; }; done; \
    bash /ffxi/Server/setup/build_server_programs.sh

# Created here so the named volume inherits the right owner
RUN mkdir -p /ffxi/Server/data

COPY --chown=ffxi:ffxi --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chown=ffxi:ffxi webui.py /webui.py
ENTRYPOINT ["/entrypoint.sh"]

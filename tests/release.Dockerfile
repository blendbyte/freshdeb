FROM debian:trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
        apt ca-certificates dpkg-dev git gnupg python3 python3-debian python3-yaml \
        reprepro rsync shellcheck tini \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH="$(dpkg --print-architecture)" \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://github.com/mikefarah/yq/releases/download/v4.45.1/yq_linux_${ARCH}" -o /usr/local/bin/yq \
    && case "$ARCH" in \
         amd64) sum=654d2943ca1d3be2024089eb4f270f4070f491a0610481d128509b2834870049 ;; \
         arm64) sum=ceea73d4c86f2e5c91926ee0639157121f5360da42beeb8357783d79c2cc6a1d ;; \
       esac \
    && echo "$sum  /usr/local/bin/yq" | sha256sum -c - \
    && chmod +x /usr/local/bin/yq \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
# reprepro's gpgme spawns gpg/gpgconf helpers that get re-parented to PID 1;
# without an init reaping them, tens of thousands of zombies pile up until
# the runner's process limit is hit and nothing can fork anymore.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]

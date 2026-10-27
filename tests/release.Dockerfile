FROM debian:trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
        apt ca-certificates dpkg-dev git gnupg python3 python3-debian python3-yaml \
        reprepro rsync shellcheck \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH="$(dpkg --print-architecture)" \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://github.com/mikefarah/yq/releases/download/v4.45.1/yq_linux_${ARCH}" -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]

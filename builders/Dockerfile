# One builder for every distribution: the base image is the only difference.
# Package build-dependencies are not baked in; container-build.sh installs
# them from each package's debian/control, so adding a package never needs a
# builder change.
#
#   docker build --build-arg BASE_IMAGE=debian:bookworm -t freshdeb-builder:bookworm builders/

ARG BASE_IMAGE=debian:trixie
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        debhelper \
        dpkg-dev \
        fakeroot \
        git \
        patch \
        python3 \
        python3-yaml \
        quilt \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged build user. Ubuntu 23.04+ images already have uid 1000
# ("ubuntu"); reuse it instead of failing on a duplicate uid.
RUN if getent passwd 1000 >/dev/null; then \
        usermod -l builder -d /home/builder -m "$(getent passwd 1000 | cut -d: -f1)"; \
    else \
        useradd -m -u 1000 builder; \
    fi

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /build

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc \
    libreoffice-common \
    python3 \
    python3-pip \
    python3-venv \
    xvfb \
    xdotool \
    scrot \
    imagemagick \
    x11-utils \
    iptables \
    && rm -rf /var/lib/apt/lists/*

COPY sandbox/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY sandbox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd -m -s /bin/bash sandbox
RUN mkdir -p /workspace /output /tmp/screenshots && chown -R sandbox:sandbox /workspace /output /tmp/screenshots

WORKDIR /workspace

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python3 -c "import pandas; import odf; print('ok')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]

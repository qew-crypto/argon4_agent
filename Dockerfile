FROM node:24-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates git zip unzip curl tini dbus libsecret-1-0 gnome-keyring && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://antigravity.google/cli/install.sh -o /tmp/agy-install.sh && bash /tmp/agy-install.sh --dir /usr/local/bin && rm /tmp/agy-install.sh
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt && playwright install --with-deps chromium && chmod -R a+rX /opt/browsers
RUN useradd -m -u 10001 agent && mkdir -p /app/data && chown agent:agent /app/data
COPY . /app
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]

FROM python:3.12-slim

WORKDIR /app

# На хосте нет маршрута IPv6, а api.telegram.org отдаёт AAAA.
RUN printf 'precedence ::ffff:0:0/96  100\n' >> /etc/gai.conf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY assets ./assets
COPY docker/telegram_ip.py /usr/local/bin/telegram_ip.py

RUN mkdir -p /app/output

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/usr/local/bin/telegram_ip.py"]
CMD ["python", "-m", "agent.bots.telegram_bot"]

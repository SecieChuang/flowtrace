FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

COPY server.py shifts.py detection.py manage_cli.py ./
COPY config.json .
COPY static/ static/

RUN mkdir -p data

EXPOSE 8000

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8000", "--timeout", "30", "server:app"]

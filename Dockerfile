FROM python:3.11-slim
WORKDIR /app
# 依赖: websockets (OKX/HL WS) + requests (REST) + eth-account (HL 签名)
RUN pip install --no-cache-dir websockets requests eth-account
COPY 04_preipo_cross_arb/core/ant_cross_arb_service.py /app/
COPY 04_preipo_cross_arb/scripts/ /app/scripts/
COPY 04_preipo_cross_arb/recon/ /app/recon/
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/ant_cross_arb_service.py"]

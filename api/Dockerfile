# Imagem base fixada por DIGEST (M48): a tag `3.12-slim` e mutavel e mudava o
# runtime de producao sem PR. O digest e o do indice multi-arquitetura
# (publicado em 2026-10-06). Origem: espelho oficial da Docker Official Image
# no ECR Public (AWS-12), o MESMO indice/digest do Docker Hub, fora do Docker
# Hub (o deploy de 2026-10-09 falhou com 504/429 la). O pull anonimo do ECR
# Public tem cota propria (500 GB/mes por IP; ver
# vendor/aws-lambda-web-adapter/README.md): o deploy-producao.yml autentica no
# ECR Public quando a role permite e faz o pull com novas tentativas.
# Para atualizar (o Dependabot tambem abre o PR, ecosystem docker):
#   docker buildx imagetools inspect public.ecr.aws/docker/library/python:3.12-slim
#   -> linha "Digest:"
# e troque o MESMO digest em TODOS os api/Dockerfile* e em
# api/scripts/lock-requirements.sh (os locks sao compilados nessa imagem).
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# SEG-16: usuario SEM privilegio (uid/gid 10001) para rodar o processo. No
# Lambda o efeito e nenhum: a AWS ja roda a imagem com um usuario padrao de
# privilegio minimo (docs: "you don't need to specify a USER") e so exige que
# os arquivos sejam legiveis por qualquer usuario. Vale para `docker run` local,
# smoke e varredura (Trivy). Codigo, libs e a extensao do Web Adapter seguem
# de root e so leitura para o app; o unico disco gravavel e o /tmp, como no Lambda.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/app \
       --shell /usr/sbin/nologin app

# Sem as libs apt do WeasyPrint (Pango/Cairo/GDK-Pixbuf/shared-mime-info/
# fonts-dejavu-core) desde que o relatorio deixou de ser PDF e virou Markdown
# gerado na hora (`app/reports.py`): ele era o unico consumidor delas. Fica so
# o libffi-dev, a mesma base apt de `api/Dockerfile.engines`.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Lambda Web Adapter: faz a ponte entre o evento do API Gateway e um servidor
# HTTP comum (uvicorn) rodando na porta 8000 - permite manter o FastAPI
# inalterado, sem reescrever para o formato de handler nativo da Lambda.
# Binario vendorizado em api/vendor/aws-lambda-web-adapter/<versao>/ em vez de
# `COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1`: o pull anonimo
# do ECR Public estoura a cota de 500 GB/mes por IP dos runners do GitHub e
# quebra o build com "429 toomanyrequests: Data limit exceeded", e o CI de PR
# nao tem (nem pode ter) credencial AWS para autenticar o pull. Proveniencia,
# digest da imagem de origem e como atualizar: api/vendor/aws-lambda-web-adapter/README.md
ARG LWA_VERSION=0.9.1
COPY vendor/aws-lambda-web-adapter/${LWA_VERSION} /tmp/lwa
RUN cd /tmp/lwa && sha256sum -c SHA256SUMS \
    && install -D -m 0755 lambda-adapter-x86_64 /opt/extensions/lambda-adapter \
    && rm -rf /tmp/lwa
ENV PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/health

# Lock com hashes (M47): todas as transitivas fixadas e conferidas por hash;
# `--require-hashes` recusa qualquer pacote fora do lock. Gerado a partir de
# requirements.in por api/scripts/lock-requirements.sh.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

COPY app /app/app
# Templates dos prompts de sistema (M103 P1): SO a pasta prompts/ do submodulo
# privado api/privado (o RAG e o resto ficam fora; Dockerfile.dockerignore e o
# passo "RAG privado fora da imagem" do ci-docker-images.yml). Lidos por
# app/prompt_store.py no caminho default <pacote>/../privado/prompts.
COPY privado/prompts /app/privado/prompts

# Processo sem privilegio (SEG-16; ver o useradd acima).
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

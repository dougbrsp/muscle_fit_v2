# Lambda Web Adapter (LWA) vendorizado

Binario da extensao [AWS Lambda Web Adapter](https://github.com/aws/aws-lambda-web-adapter)
que `api/Dockerfile` e `api/Dockerfile.engines` instalam em
`/opt/extensions/lambda-adapter`.

## Por que o binario esta no repositorio

Ate 2026-09-28 as duas imagens faziam:

    COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

O pull era **anonimo**. A cota de usuario nao autenticado do ECR Public e de
**500 GB por mes, nao ajustavel** (ver
<https://docs.aws.amazon.com/AmazonECR/latest/public/public-service-quotas.html>),
contada por endereco IP de origem. Os runners `ubuntu-latest` do GitHub saem
por um pool de IPs compartilhado com o mundo inteiro, entao essa cota estoura
e o build quebra com:

    ERROR: failed to copy: httpReadSeeker: failed open: unexpected status from GET request to
    https://public.ecr.aws/v2/awsguru/aws-lambda-adapter/manifests/sha256:46d6625e... : 429 Too Many Requests
    toomanyrequests: Data limit exceeded

O upstream **nao publica o binario em GitHub Releases** (todas as releases do
`aws/aws-lambda-web-adapter` tem `assets: []`), e o Docker Hub
`awsguru/aws-lambda-adapter` parou na 0.8.3. Os unicos canais oficiais da
0.9.1 sao a imagem do ECR Public e a Lambda layer publica
`arn:aws:lambda:<regiao>:753240598075:layer:LambdaAdapterLayerX86:24` - e
ambos exigem credencial AWS para serem baixados de forma confiavel.

`.github/workflows/ci-develop.yml` e `.github/workflows/ci-docker-images.yml`
**nao tem** `id-token: write` nem `aws-actions/configure-aws-credentials`, por
decisao de projeto. Logo, qualquer solucao baseada em autenticar no ECR Public
ou em espelhar a imagem num ECR privado conserta apenas o deploy de producao e
deixa o CI de PR quebrado. Vendorizar o binario, fixado por versao e sha256,
conserta os dois caminhos com a mesma mudanca e remove a dependencia de rede
do build.

A imagem base Python dos `api/Dockerfile*` (`FROM public.ecr.aws/docker/library/python:...@sha256:...`)
sofre da mesma cota e nao da para vendorizar: desde o M82 a CI e o deploy a
buscam na primeira origem que responde com o MESMO digest (`mirror.gcr.io`,
`public.ecr.aws`, `docker.io`), por contexto nomeado do BuildKit, sem mudar o
FROM; ver `.github/scripts/escolher-base-python.sh`.

## Proveniencia do arquivo

| item | valor |
| --- | --- |
| origem | `public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1` |
| digest da imagem | `sha256:46d6625e68cbbdd2efab4a20245977664513f13ffef47915b000d431adcea0b4` |
| caminho na imagem | `/lambda-adapter` |
| plataforma | `linux/amd64` (as Lambdas do projeto sao x86_64) |
| tamanho | 3.571.784 bytes |
| modo na imagem | `755` |
| sha256 | `f4c9748d551878ddd4e612457bb23b869e0a47a53d14e66ed6cab1fd1f797699` |

O arquivo e **byte a byte** o mesmo que o `COPY --from` entregava: o sha256
acima foi conferido tanto no arquivo extraido quanto dentro de um build que
ainda usava `COPY --from` a imagem do ECR Public.

## Como conferir / atualizar

De uma maquina cuja cota anonima do ECR Public nao esteja estourada (ou
autenticada com `aws ecr-public get-login-password --region us-east-1 | docker
login --username AWS --password-stdin public.ecr.aws`):

```bash
V=0.9.1
CID=$(docker create --platform linux/amd64 --entrypoint /bin/true \
  public.ecr.aws/awsguru/aws-lambda-adapter:$V)
docker cp "$CID:/lambda-adapter" api/vendor/aws-lambda-web-adapter/$V/lambda-adapter-x86_64
docker rm "$CID"
(cd api/vendor/aws-lambda-web-adapter/$V && sha256sum lambda-adapter-x86_64 > SHA256SUMS)
```

Ao subir de versao: crie um diretorio novo `<versao>/`, atualize o `ARG
LWA_VERSION` dos dois Dockerfiles e o `SHA256SUMS`, e remova o diretorio
antigo em commit separado.

FROM pcr.teskalabs.com/alpine:3.22 AS building
LABEL maintainer="TeskaLabs Ltd (support@teskalabs.com)"

# Include build environment variables from GitLab CI/CD
ARG CI_COMMIT_BRANCH
ARG CI_COMMIT_TAG
ARG CI_COMMIT_REF_NAME
ARG CI_COMMIT_SHA
ARG CI_COMMIT_TIMESTAMP
ARG CI_JOB_ID
ARG CI_PIPELINE_CREATED_AT
ARG CI_RUNNER_ID
ARG CI_RUNNER_EXECUTABLE_ARCH
ARG GITHUB_HEAD_REF
ARG GITHUB_JOB
ARG GITHUB_SHA
ARG GITHUB_REPOSITORY

ENV LANG=C.UTF-8

RUN set -ex \
  && apk update \
  && apk upgrade

RUN apk add --no-cache \
  python3 \
  py3-pip

RUN pip3 install --break-system-packages --upgrade pip
RUN pip3 install --break-system-packages --no-cache-dir git+https://github.com/TeskaLabs/asab.git[mcp]

RUN mkdir -p /app/markdown-notes-mcp

COPY . /app/markdown-notes-mcp
RUN (cd /app/markdown-notes-mcp && asab-manifest.py ./MANIFEST.json)

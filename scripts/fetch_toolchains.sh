#!/usr/bin/env bash
# Fetch the large toolchain artefacts the base image needs, into .triad/toolchains/.
#
# Why here and not in the Dockerfile: on some networks the container's route to the Apache
# mirrors collapses to a few KB/s while the host gets 6 MB/s. The tarball is verified against
# the publisher's SHA-512 both here and inside the image, so moving the download does not
# weaken the supply chain. Nothing downloaded here is ever committed (.gitignore).
set -euo pipefail

SPARK_VERSION="${SPARK_VERSION:-4.1.3}"
DIST="spark-${SPARK_VERSION}-bin-hadoop3"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/.triad/toolchains"
MIRRORS=(
  "https://dlcdn.apache.org/spark"
  "https://archive.apache.org/dist/spark"
)

mkdir -p "$OUT"

fetch() {
  local path="$1" dest="$2"
  for base in "${MIRRORS[@]}"; do
    echo "fetching ${base}/${path}"
    if curl -fSL --retry 3 --retry-delay 2 -C - -o "$dest" "${base}/${path}"; then
      return 0
    fi
    echo "  mirror failed, trying next" >&2
  done
  return 1
}

if [ ! -s "${OUT}/${DIST}.tgz" ]; then
  fetch "spark-${SPARK_VERSION}/${DIST}.tgz" "${OUT}/${DIST}.tgz"
fi
fetch "spark-${SPARK_VERSION}/${DIST}.tgz.sha512" "${OUT}/${DIST}.tgz.sha512"

expected="$(tr -d ' \n' < "${OUT}/${DIST}.tgz.sha512" | sed 's/.*://' | sed 's/spark.*$//' \
            | tr 'A-Z' 'a-z' | cut -c1-128)"
actual="$(sha512sum "${OUT}/${DIST}.tgz" | cut -d' ' -f1)"
if [ "$actual" != "$expected" ]; then
  echo "SHA512 mismatch for ${DIST}.tgz" >&2
  echo "  expected $expected" >&2
  echo "  actual   $actual" >&2
  exit 1
fi
echo "OK ${DIST}.tgz verified ($(du -h "${OUT}/${DIST}.tgz" | cut -f1))"

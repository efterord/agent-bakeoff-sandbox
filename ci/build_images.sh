#!/usr/bin/env bash
# Build the pilot images on a GitHub-hosted runner and record their ids.
#   ci/build_images.sh OUTDIR   -> OUTDIR/images.json
set -euo pipefail
OUT="$1"; mkdir -p "$OUT"
HERMES_COMMIT=818c13be1dc4fd28987e1e881a9408224afd4535
HERMES_IMAGE="nousresearch/hermes-agent@sha256:ffa4fbb471c2bc8c85fbcfc629347585cc1586c86d2e6db465d01203ce696d95"  # rc.4-v0.21.6-amd64
PY_IMAGE="python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"                     # 3.12-slim
docker pull -q "$HERMES_IMAGE" >/dev/null
docker pull -q "$PY_IMAGE" >/dev/null
docker run --rm --network none --entrypoint cat "$HERMES_IMAGE" /etc/hermes/image-provenance.json > "$OUT/hermes-provenance.json"
if ! grep -q "\"revision\": *\"$HERMES_COMMIT\"" "$OUT/hermes-provenance.json"; then
  echo "Hermes image provenance is not $HERMES_COMMIT - refusing (fail closed)" >&2
  cat "$OUT/hermes-provenance.json" >&2
  exit 3
fi
docker run --rm --network none --entrypoint sh "$HERMES_IMAGE" -c \
  'ls -la /opt/hermes/skills/productivity/powerpoint /opt/hermes/skills/productivity/powerpoint/scripts' \
  > "$OUT/hermes-skill-listing.txt" 2>&1 || true
docker build --pull=false -q -f images/agent/Dockerfile -t pilot-agent:local . > "$OUT/agent-build.txt"
docker build --pull=false -q -f images/gate/Dockerfile -t pilot-gate:local . > "$OUT/gate-build.txt"
AGENT_ID=$(docker image inspect pilot-agent:local --format '{{.Id}}')
GATE_ID=$(docker image inspect pilot-gate:local --format '{{.Id}}')
for f in /opt/apt-pins.txt /opt/pilot/pip-freeze.txt /opt/pilot/skill-final.sha256; do
  docker run --rm --network none --entrypoint cat "$AGENT_ID" "$f" > "$OUT/$(basename "$f")"
done
docker run --rm --network none --entrypoint /opt/pilot/venv/bin/python3 "$AGENT_ID" \
  /opt/pilot/harness/agent/typed_skill.py --diff > "$OUT/typed-skill-rewrite.diff"
docker run --rm --network none --entrypoint fc-list "$AGENT_ID" : family > "$OUT/fonts.txt"
cat > "$OUT/images.json" <<JSON
{"hermes_image": "$HERMES_IMAGE", "hermes_commit": "$HERMES_COMMIT", "python_image": "$PY_IMAGE",
 "agent_image_id": "$AGENT_ID", "gate_image_id": "$GATE_ID"}
JSON
cat "$OUT/images.json"
grep -E "Noto Sans CJK JP|Noto Serif CJK JP|IPAGothic|Noto Sans$|Noto Serif$" "$OUT/fonts.txt" | sort -u || true

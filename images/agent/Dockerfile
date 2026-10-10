# Agent image for BOTH arms (only the entrypoint differs) and for the sealed render container.
# Base: Hermes rc.4-v0.21.6-amd64, pinned by digest; provenance revision checked in ci/build_images.sh.
ARG HERMES_IMAGE=nousresearch/hermes-agent@sha256:ffa4fbb471c2bc8c85fbcfc629347585cc1586c86d2e6db465d01203ce696d95
FROM ${HERMES_IMAGE}
USER root
# LibreOffice Impress + poppler for rendering, Noto/Noto CJK/IPA fonts (Japanese renders).
RUN apt-get -o Acquire::Retries=3 update \
 && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
      libreoffice-impress libreoffice-core poppler-utils fonts-noto-core fonts-noto-cjk fonts-ipafont-gothic \
      fonts-dejavu-core fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f >/dev/null \
 && dpkg-query -W -f='${Package}=${Version}\n' libreoffice-impress poppler-utils fonts-noto-cjk fonts-noto-core \
      fonts-ipafont-gothic > /opt/apt-pins.txt
# Document libraries for the skill scripts, in a dedicated venv (pinned versions, wheels only).
COPY images/requirements-agent.txt /opt/pilot/requirements.txt
RUN /usr/bin/python3 -m venv /opt/pilot/venv \
 && /opt/pilot/venv/bin/pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: \
      -r /opt/pilot/requirements.txt \
 && /opt/pilot/venv/bin/pip freeze > /opt/pilot/pip-freeze.txt
COPY agent /opt/pilot/harness/agent
COPY pptxbench /opt/pilot/harness/pptxbench
COPY images/skill-blobs.txt /opt/pilot/skill-blobs.txt
ENV SKILL=/opt/hermes/skills/productivity/powerpoint
# Fail the build unless the pinned skill files are byte-identical to Hermes 818c13be.
RUN /usr/bin/python3 /opt/pilot/harness/agent/verify_skill.py /opt/pilot/skill-blobs.txt "$SKILL" \
 && cp /opt/pilot/harness/agent/helpers/pptx_inspect.py /opt/pilot/harness/agent/helpers/pptx_shape_ops.py \
       /opt/pilot/harness/agent/helpers/pptx_check.py "$SKILL/scripts/" \
 && cat /opt/pilot/harness/agent/skill_appendix.md >> "$SKILL/SKILL.md" \
 && sha256sum "$SKILL"/SKILL.md "$SKILL"/scripts/*.py > /opt/pilot/skill-final.sha256 \
 && mkdir -p /opt/pilot/bin \
 && chmod 0755 /opt/pilot/harness/agent/counting_wrapper.py \
 && ln -s /opt/pilot/harness/agent/counting_wrapper.py /opt/pilot/bin/soffice \
 && ln -s /opt/pilot/harness/agent/counting_wrapper.py /opt/pilot/bin/pdftoppm \
 && printf 'export PATH=/opt/pilot/bin:/opt/pilot/venv/bin:$PATH\n' > /etc/profile.d/00-pilot.sh \
 && chmod -R a+rX /opt/pilot "$SKILL" \
 && mkdir -p /work/in /work/out /run/gate /home/agent \
 && useradd -u 10001 -M -d /home/agent -s /bin/bash agent
ENV PATH=/opt/pilot/bin:/opt/pilot/venv/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/home/agent USER=agent LOGNAME=agent LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work/out
USER 10001:10001
ENTRYPOINT ["/opt/pilot/venv/bin/python3", "/opt/pilot/harness/agent/typed_driver.py"]

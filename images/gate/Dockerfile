# Model gate. python:3.12-slim pinned by digest; stdlib only.
FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
COPY gate /opt/harness/gate
COPY agent/__init__.py agent/typed_ops.py /opt/harness/agent/
COPY pptxbench/__init__.py pptxbench/design.py pptxbench/fixtures.py pptxbench/tasks.py /opt/harness/pptxbench/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/harness
ENTRYPOINT ["python", "-m", "gate.gate"]

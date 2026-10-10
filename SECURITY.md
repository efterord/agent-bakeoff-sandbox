# Security

This repository is an **experiment harness** for comparing two agent runtimes on
synthetic document tasks. It contains:

- harness code, synthetic fixtures (invented companies, numbers and reserved
  `0X0-0000-0000` phone shapes) and generic documentation;
- **no secrets**, no API keys, no credentials, no private data.

## How it is meant to run

- Only on GitHub-hosted, ephemeral runners. Every agent task runs in a fresh
  container with `--network none`, a read-only root filesystem, non-root user,
  `--cap-drop ALL`, `no-new-privileges`, Docker's default seccomp profile,
  pids/memory/CPU/file-size limits, a 15-minute wall clock, and is removed
  afterwards. The only mounts are a read-only input tmpfs, a size-capped output
  tmpfs and one per-run unix socket to the model gate.
- The model gate is the only component that can reach a model provider. In
  **fake** mode (the default, used by `pptx-pilot-fake`) it uses no network and no
  key. In **real** mode it refuses to start unless a key file is present; the key
  is a dedicated, spend-limited key stored only as a secret of the protected
  `bakeoff` environment (required reviewer), referenced by exactly one step of
  `pptx-pilot-paid`, and mounted only into the gate container.
- The gate pins the model ids, strips sampling parameters, forces thinking off,
  clamps `max_tokens`, refuses server tools, URL/file sources, MCP and beta
  parameters, never forwards client headers, enforces per-run and per-pilot USD
  caps, and logs every request.
- Isolation is re-verified by `pilot/selftest.py` on every run before any task
  starts; any failure aborts the run. Probes are harmless negative controls
  (a DNS lookup, a TCP connect to the unroutable TEST-NET-3 address 203.0.113.7).

## Repository settings

- Workflows declare `permissions: contents: read`; there is no
  `pull_request_target` trigger; actions are pinned by commit SHA and only
  GitHub-owned actions are used.
- Workflow runs from outside collaborators' fork pull requests require approval.

## Reporting

Open a GitHub issue for harness bugs. Do not post secrets or real personal data
in issues; this project never needs either.

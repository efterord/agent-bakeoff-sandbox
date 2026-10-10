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
  clamps `max_tokens`, normalizes prompt caching identically for every arm,
  refuses server tools, documents, URL/file sources, MCP and beta parameters,
  never forwards client headers, enforces per-run and per-pilot USD caps
  (worst-case reservation for the pilot cap; unknown-cost requests are charged
  their worst case), and logs every request with the upstream request id.

## Known limits (accepted for the pilot, documented on purpose)

- **Gate egress.** In real mode the gate container sits on Docker's default
  bridge network, so at the network layer it could reach any host. The gate is
  harness code (no agent code runs in it); it connects only to the constant
  `api.anthropic.com:443` with TLS certificate verification, and agents reach
  it only through their per-run unix socket, whose handler serves three paths.
  A stricter setup would put the gate on an `--internal` network behind an
  egress proxy that allows only `api.anthropic.com:443`; not done for the pilot.
- **The pilot cap is per gate process.** A re-run or a new dispatch of the paid
  workflow starts a fresh gate at USD 0. The cross-run hard limit is the
  provider-side spend limit on the dedicated key's workspace.
- **Reviewer identity.** The `bakeoff` environment reviewer is a person's
  account. Any automation that holds that account's token could dispatch and
  approve the paid job, so the key is stored only right before an approved run
  and deleted/revoked right after it (see README operator steps).
- **Render budget.** The 4-render limit is a wrapper; the Hermes arm (which has
  a shell) can bypass it. The binding limit for both arms is the gate's 32
  distinct images per run, which bounds what the model can see.
- **Artifacts are public.** Workflow artifacts of a public repository can be
  downloaded by any signed-in GitHub user. They contain only synthetic data,
  model outputs and gate logs (never headers or keys).
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

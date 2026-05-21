# Security Policy

## Supported Versions

Only the latest published image tag is supported with security fixes.

| Tag pattern | Supported |
|---|---|
| `v*-gb10.*` (latest) | Yes |
| Older tags | No |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately via GitHub:
[Security Advisories](https://github.com/timothystewart6/vllm-gb10/security/advisories/new)

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected versions / image tags

You can expect an acknowledgement within 72 hours.

## Scope

This repo ships a pre-built Docker image. Security concerns most relevant here:

- **Upstream dependency vulnerabilities** - PyTorch, NCCL, vLLM, FlashInfer, CUDA base image
- **Supply chain integrity** - all inputs are pinned by commit SHA or digest in `versions.env`; verify with `checksums/`
- **Workflow security** - GitHub Actions workflows use SHA-pinned actions and a scoped `GITHUB_TOKEN`

<!--
SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
SPDX-License-Identifier: Apache-2.0
-->

# Release pattern

This page describes how a change moves from a commit to a running pod, which gate
checks each step, and how you verify what you install. octo-pod is the reference
release pattern for the projects that follow it, so each gate is listed with its real
status: enforced by the platform, automated, done by hand, or not yet in place.

## From commit to running pod

1. **Commit.** Every commit carries a DCO sign-off (`git commit -s`). Commits that reach
   `main` must be signed.
2. **Pull request.** A change reaches `main` only through a pull request. These checks
   must pass, and the branch must be up to date with `main`:
   - `test`: `cargo fmt`, `cargo clippy` with warnings as errors, and the workspace tests.
   - `msrv`: the build on the minimum supported Rust version.
   - `offline`: ShellCheck on every shell script, template and ACL checks, playbook
     syntax, the release payload, Dockerfile checks, and image SBOM checks.
   - `security`: third-party notices, `cargo-deny` (licenses, banned crates, sources),
     `cargo-audit`, Semgrep, and gitleaks.
   - `Developer Certificate of Origin`, `Conventional Commits`, and `PR base must be main`.
3. **Merge.** Pull requests are squash-merged. History on `main` is linear, and force
   pushes and deletion are refused.
4. **Images.** After a merge that changes `arms/`, `images.yml` builds every arm for
   amd64 and arm64, attaches an SBOM and build provenance, signs a provenance
   attestation for each multi-arch manifest, publishes one manifest per arm, and checks
   that both platform SBOMs are present.
5. **Pin.** `tools/pin-images.sh` records the new image digests in
   `platform/group_vars/all/10-infrastructure.yml`, but only digests that `images.yml`
   signed on `main`. The pin reaches `main` through a pull request, which passes the same
   checks as step 2 and the `signed-pins` check.
6. **Tag.** A release starts from a signed, annotated tag named `octo-pod-v<version>`,
   created with `git tag -s` by a maintainer whose signing key is registered on GitHub.
   Only maintainers can create, move, or delete tags.
7. **Approve.** The release job waits in the `release` environment until a release
   approver other than the person who pushed the tag approves it.
8. **Release.** `release.yml` refuses a tag that is lightweight, that GitHub does not
   verify as signed, or that does not match `VERSION`. It then builds the package and
   the carried image bundle, refuses a bundle that does not carry every pinned digest,
   signs `SHA256SUMS` with a keyless Sigstore certificate issued to this repository's
   release workflow, verifies that signature the way a pod will, publishes the release,
   and attaches SLSA provenance for every file that `SHA256SUMS` lists.
9. **Install.** `tools/install-to-pod.sh` downloads the release, verifies the signature
   on `SHA256SUMS`, checks each file against it, and only then copies the package to the
   pod. It checks the hash again before `dpkg` reads the file. The checks have no skip
   option.
10. **Bring up.** On the pod: `bootstrap` prepares the host and loads the carried
   images, `init` or `join` gives the pod its identity, `check` and `apply` run the
   digest-pinned services, and `verify` and `verify-flowing` prove that the pod works
   and that data moves.
11. **Run.** Certificates renew on the pod and supported service faults are repaired.
    Neither changes the pod's declaration.
12. **Upgrade.** `octo-pod upgrade` verifies the release signature before it switches
    versions, and keeps the previous version for rollback.

## Who signs, and who trusts it

A release is signed by the release workflow of the repository that publishes it. The
installer and the upgrade command accept a signature only from the release workflow of
the repository they install from, on an `octo-pod-v` tag. A pod therefore trusts
exactly one repository: the one its installer or its installed release names.

Owner and repository names match in any letter case, because GitHub treats them that
way and writes its own spelling into the certificate. The workflow path and the tag
must match exactly.

## Verify a release yourself

Download the release files:

```sh
gh release download octo-pod-v<version> --repo usareur-af/octo-pod \
  -p SHA256SUMS -p SHA256SUMS.cosign-bundle -p 'octo-pod_<version>_all.deb'
```

Check who signed it:

```sh
cosign verify-blob --bundle SHA256SUMS.cosign-bundle \
  --certificate-identity-regexp '^https://github\.com/(?i:usareur-af/octo-pod)/\.github/workflows/release\.yml@refs/tags/octo-pod-v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com SHA256SUMS
```

Check that the files are what was signed:

```sh
sha256sum -c --ignore-missing SHA256SUMS
```

## Gate status

| Gate | Status |
|---|---|
| Signed commits, pull request required, linear history, no force push | Enforced on `main` |
| Required checks in step 2, branch up to date | Enforced on `main` |
| Translation catalogs (`catalogs`) | Runs when translatable files change; not yet required |
| Image builds on pull requests, CodeQL | Run on pull requests; not required |
| Tag creation | Limited to maintainers |
| Image build, SBOM, provenance, multi-arch | Automated after merge |
| Signed provenance attestation on each image | Automated after merge |
| Image digest pins signed by `images.yml` on `main` (`signed-pins`) | Runs on every pull request; not yet required |
| Signed, verified release tag | Enforced by the release workflow |
| Approval before a release, by a second person | Enforced by the `release` environment; administrators can bypass |
| Release signing and self-verification | Automated |
| SLSA provenance for every release file | Automated |
| Signature and checksum check on install and upgrade | Enforced by the tools |
| Image provenance check when a pod pulls from a registry | Not yet in place |

## Planned

- Verify each image's provenance when a pod pulls from a registry.
- Open the pin pull request from `images.yml` instead of running `tools/pin-images.sh` by
  hand.
- Require the `signed-pins` check on every pull request.

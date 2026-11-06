#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Write, or check, the sigstore keys that every octo-pod release carries.

    tools/sigstore-trusted-root.py           write platform/sigstore/trusted_root.json
    tools/sigstore-trusted-root.py --check   exit 1 if that file is not sigstore's current one

WHY A RELEASE CARRIES THEM. cosign checks a release signature against sigstore's public
keys: the Fulcio certificate authority, the Rekor transparency log and the certificate
transparency log. Without a local copy, cosign downloads them from tuf-repo-cdn.sigstore.dev
first, so a pod with no route to the internet could verify nothing. With this file, a pod
verifies the next release offline, against the keys of the release it already runs.

WHERE THEY COME FROM. `gh attestation trusted-root` reads sigstore's TUF repository, which
signs this file and which gh checks against the TUF root built into it. Its output has one
JSON document per sigstore instance; this keeps the public instance, the one that signs
octo-pod releases.

WHY --check FAILS A RELEASE. When sigstore adds or rotates a key, a pod with the old file
cannot verify a release signed with the new key. The release workflow runs --check, so a
release never ships keys older than the ones that exist when it is built.
"""
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "platform" / "sigstore" / "trusted_root.json"
PUBLIC_REKOR = "https://rekor.sigstore.dev"
MEDIA_TYPE = "application/vnd.dev.sigstore.trustedroot+json;version=0.1"


def live():
    try:
        out = subprocess.run(["gh", "attestation", "trusted-root"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        sys.exit(f"sigstore-trusted-root: gh attestation trusted-root failed: {detail.strip()}")
    for line in out.splitlines():
        if not line.strip():
            continue
        doc = json.loads(line)
        if any(t.get("baseUrl") == PUBLIC_REKOR for t in doc.get("tlogs", [])):
            if doc.get("mediaType") != MEDIA_TYPE:
                sys.exit(f"sigstore-trusted-root: unexpected mediaType {doc.get('mediaType')!r}")
            return doc
    sys.exit("sigstore-trusted-root: gh returned no trusted root for the public sigstore instance")


def render(doc):
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main():
    want = render(live())
    if "--check" in sys.argv[1:]:
        have = OUT.read_text() if OUT.exists() else ""
        if have != want:
            print(f"{OUT.relative_to(REPO)} is not sigstore's current trusted root.\n"
                  f"Run tools/sigstore-trusted-root.py and commit the result.", file=sys.stderr)
            sys.exit(1)
        print(f"ok: {OUT.relative_to(REPO)} is sigstore's current trusted root")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(want)
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()

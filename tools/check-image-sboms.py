#!/usr/bin/env python3
"""Download and check the published SPDX SBOM for each supported platform."""
import argparse
import json
import pathlib
import re
import subprocess
import sys

PLATFORMS = ("linux/amd64", "linux/arm64")


def inspect(image, template):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image, "--format", template],
        check=True, capture_output=True, text=True, timeout=180,
    )
    return json.loads(result.stdout)


def validate(sboms):
    """Reject missing, empty, or OS-only inventories before promoting an image."""
    documents = {}
    for platform in PLATFORMS:
        entry = sboms.get(platform, {}) if isinstance(sboms, dict) else {}
        document = entry.get("SPDX") if isinstance(entry, dict) else None
        if not isinstance(document, dict) or not str(document.get("spdxVersion", "")).startswith("SPDX-2."):
            raise ValueError(f"{platform}: missing SPDX 2.x SBOM")
        packages = document.get("packages", [])
        if not isinstance(packages, list) or not packages:
            raise ValueError(f"{platform}: SBOM has no packages")
        purls = {
            ref.get("referenceLocator", "")
            for package in packages if isinstance(package, dict)
            for ref in package.get("externalRefs", []) if isinstance(ref, dict)
            if ref.get("referenceType") == "purl"
            and isinstance(ref.get("referenceLocator"), str)
        }
        for ecosystem in ("apk", "cargo"):
            if not any(purl.startswith(f"pkg:{ecosystem}/") for purl in purls):
                raise ValueError(f"{platform}: SBOM has no {ecosystem} packages")
        documents[platform] = document
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="published image tag or digest")
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()
    # Resolve a mutable tag once. Every subsequent read and saved report uses that
    # immutable index, so moving a tag cannot mix inventories from different builds.
    digest = inspect(args.image, "{{json .Manifest.Digest}}")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("registry returned an invalid image digest")
    repository = args.image.split("@", 1)[0]
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    image = f"{repository}@{digest}"
    documents = validate(inspect(image, "{{json .SBOM}}"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for platform, document in documents.items():
        filename = platform.replace("/", "-") + ".spdx.json"
        (args.output_dir / filename).write_text(json.dumps(document, indent=2) + "\n")
        print(f"{image}: {platform} SBOM contains OS and Rust packages")
    (args.output_dir / "image.json").write_text(
        json.dumps({"image": image, "platforms": list(documents)}, indent=2) + "\n"
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"SBOM verification failed: {error}", file=sys.stderr)
        sys.exit(1)

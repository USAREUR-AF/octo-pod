#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Select arm images affected by a PR; shared build inputs select every image."""
import json
import subprocess
import sys
from pathlib import Path


def select_images(paths, images):
    selected = set()
    for path in paths:
        if path in {'.github/workflows/pr-images.yml', 'tools/pr-image-matrix.py',
                    'tools/check-pr-image.sh'}:
            return sorted(images)
        if not path.startswith('arms/'):
            continue
        parts = path.split('/')
        if len(parts) > 2 and parts[1] in images:
            selected.add(parts[1])
        else:
            # Workspace manifests, common/, license notices and future shared inputs.
            return sorted(images)
    return sorted(selected)


def main():
    images = {p.parent.name for p in Path('arms').glob('*/Dockerfile')}
    if not images:
        raise SystemExit('No arm Dockerfiles found')
    changed = subprocess.check_output(
        ['git', 'diff', '--name-only', '-z', sys.argv[1], sys.argv[2], '--'],
    ).decode().split('\0')
    print(json.dumps(select_images(filter(None, changed), images)))


if __name__ == '__main__':
    main()

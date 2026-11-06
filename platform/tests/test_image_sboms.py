# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Exercise missing inventories and registry reads without registry credentials."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("image_sboms", REPO / "tools/check-image-sboms.py")
sbom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbom)


def inventory():
    document = {
        "spdxVersion": "SPDX-2.3",
        "packages": [
            {"name": "glibc", "externalRefs": [{"referenceType": "purl", "referenceLocator": "pkg:apk/wolfi/glibc@2.44-r9"}]},
            {"name": "zenoh", "externalRefs": [{"referenceType": "purl", "referenceLocator": "pkg:cargo/zenoh@1.9.0"}]},
        ],
    }
    return {platform: {"SPDX": copy.deepcopy(document)} for platform in sbom.PLATFORMS}


class ImageSboms(unittest.TestCase):
    def test_both_architectures_with_os_and_rust_packages(self):
        self.assertEqual(set(sbom.validate(inventory())), set(sbom.PLATFORMS))

    def test_missing_architecture_is_rejected(self):
        for platform in sbom.PLATFORMS:
            data = inventory()
            del data[platform]
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, platform):
                sbom.validate(data)

    def test_empty_or_invalid_documents_are_rejected(self):
        for value in (None, {}, [], {"SPDX": {}}, {"SPDX": {"spdxVersion": "SPDX-2.3", "packages": []}}):
            data = inventory()
            data["linux/amd64"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                sbom.validate(data)

    def test_os_only_and_rust_only_inventories_are_rejected(self):
        for missing, index in (("apk", 0), ("cargo", 1)):
            data = inventory()
            del data["linux/arm64"]["SPDX"]["packages"][index]
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, missing):
                sbom.validate(data)

    def test_downloads_are_pinned_and_documents_are_preserved(self):
        digest = "sha256:" + "a" * 64
        data = inventory()
        for image in ("registry.example:5000/team/image:release", "registry.example:5000/team/image@" + digest):
            with self.subTest(image=image), tempfile.TemporaryDirectory() as tmp:
                with patch.object(sbom, "inspect", side_effect=[digest, data]) as inspect:
                    with patch("sys.argv", ["check-image-sboms.py", image, "--output-dir", tmp]):
                        sbom.main()
                pinned = "registry.example:5000/team/image@" + digest
                self.assertEqual(inspect.call_args_list[1].args, (pinned, "{{json .SBOM}}"))
                for platform in sbom.PLATFORMS:
                    document = json.loads((pathlib.Path(tmp) / (platform.replace("/", "-") + ".spdx.json")).read_text())
                    self.assertEqual(document, data[platform]["SPDX"])
                self.assertEqual(json.loads((pathlib.Path(tmp) / "image.json").read_text())["image"], pinned)

    def test_partial_inventory_writes_no_success_artifacts(self):
        data = inventory()
        del data["linux/arm64"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(sbom, "inspect", side_effect=["sha256:" + "a" * 64, data]):
            output = pathlib.Path(tmp) / "output"
            with patch("sys.argv", ["check-image-sboms.py", "registry.example/image:tag", "--output-dir", str(output)]):
                with self.assertRaises(ValueError):
                    sbom.main()
            self.assertFalse(output.exists())

    def test_registry_failure_is_not_treated_as_an_empty_inventory(self):
        with patch.object(sbom.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["docker"])):
            with self.assertRaises(subprocess.CalledProcessError):
                sbom.inspect("registry.example/image:tag", "{{json .SBOM}}")

    def test_a_registry_answer_that_is_not_a_digest_reads_nothing_further(self):
        for answer in ("latest", "sha256:" + "A" * 64, ["sha256:" + "a" * 64], None):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as tmp:
                with patch.object(sbom, "inspect", side_effect=[answer]) as inspect:
                    with patch("sys.argv", ["check-image-sboms.py", "registry.example/image:tag",
                                            "--output-dir", tmp + "/out"]):
                        with self.assertRaisesRegex(ValueError, "invalid image digest"):
                            sbom.main()
                self.assertEqual(inspect.call_count, 1)
                self.assertFalse(pathlib.Path(tmp, "out").exists())

    def test_inspect_asks_buildx_for_the_template_and_parses_json(self):
        done = subprocess.CompletedProcess([], 0, stdout='{"linux/amd64": {}}\n', stderr="")
        with patch.object(sbom.subprocess, "run", return_value=done) as run:
            self.assertEqual(sbom.inspect("registry.example/image:tag", "{{json .SBOM}}"),
                             {"linux/amd64": {}})
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["docker", "buildx", "imagetools", "inspect"])
        self.assertEqual(argv[-2:], ["--format", "{{json .SBOM}}"])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_workflow_generates_sboms_before_latest_promotion(self):
        workflow = yaml.safe_load((REPO / ".github/workflows/images.yml").read_text())
        build = next(step for step in workflow["jobs"]["build"]["steps"] if step.get("id") == "build")
        self.assertIs(build["with"]["sbom"], True)
        self.assertEqual(build["with"]["provenance"], "mode=max")
        steps = workflow["jobs"]["merge"]["steps"]
        verify = next(i for i, step in enumerate(steps) if "tools/check-image-sboms.py" in step.get("run", ""))
        promote = next(i for i, step in enumerate(steps) if step.get("name") == "promote a verified main build")
        self.assertLess(verify, promote)
        self.assertNotIn("continue-on-error", steps[verify])
        upload = next(step for step in steps[verify + 1:promote] if step.get("with", {}).get("path") == "/tmp/sboms/*")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")


if __name__ == "__main__":
    unittest.main()

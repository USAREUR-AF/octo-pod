#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Test actual bundle validation and archive extraction without registry credentials."""
import hashlib
import importlib.util
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('delivery', ROOT/'tools/push-images.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.blobs = self.root/'images/oci/blobs/sha256'
        self.blobs.mkdir(parents=True)
        self.layer = b'actual layer data'
        layer = self.blob(self.layer, 'application/vnd.oci.image.layer.v1.tar')
        config = self.blob(b'{}', 'application/vnd.oci.image.config.v1+json')
        manifest = self.blob(json.dumps({'schemaVersion': 2, 'config': config, 'layers': [layer]}).encode(),
                             'application/vnd.oci.image.manifest.v1+json')
        index = self.blob(json.dumps({'schemaVersion': 2, 'manifests': [
            dict(manifest, platform={'os': 'linux', 'architecture': a}) for a in ('amd64', 'arm64')]}).encode(),
            'application/vnd.oci.image.index.v1+json')
        (self.root/'images/oci/index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [index]}))
        self.expected = {tag: 'ghcr.io/example/'+tag+'@'+index['digest'] for tag in delivery.PINS}
        (self.root/'images/MAP').write_text(''.join(
            tag+' example/'+tag+' '+index['digest']+'\n' for tag in delivery.PINS))
        for arch in ('amd64', 'arm64'):
            for tool in ('registry', 'skopeo'):
                (self.root/f'images/{tool}-{arch}.tar').write_bytes(b'tool fixture')
        (self.root/'images/REGISTRY_IMAGE').write_text('registry:2\n')
        (self.root/'images/SKOPEO_IMAGE').write_text('quay.io/skopeo/stable:latest\n')

    def blob(self, data, media):
        digest = hashlib.sha256(data).hexdigest()
        (self.blobs/digest).write_bytes(data)
        return {'digest': 'sha256:'+digest, 'size': len(data), 'mediaType': media}

    def test_complete_bundle(self):
        delivery.validate(self.root, self.expected)

    def test_layer_corruption(self):
        (self.blobs/hashlib.sha256(self.layer).hexdigest()).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'corrupt OCI'):
            delivery.validate(self.root, self.expected)

    def test_wrong_installed_pin(self):
        self.expected['sapient'] = 'ghcr.io/example/sapient@sha256:'+'a'*64
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            delivery.validate(self.root, self.expected)

    def test_missing_platform_tool(self):
        (self.root/'images/skopeo-arm64.tar').unlink()
        with self.assertRaisesRegex(ValueError, 'skopeo for arm64'):
            delivery.validate(self.root, self.expected)

    def test_duplicate_map(self):
        path = self.root/'images/MAP'
        path.write_text(path.read_text()+path.read_text().splitlines()[0]+'\n')
        with self.assertRaisesRegex(ValueError, 'duplicate MAP'):
            delivery.validate(self.root, self.expected)

    def test_missing_arm(self):
        path = self.root/'images/MAP'
        path.write_text('\n'.join(path.read_text().splitlines()[:-1])+'\n')
        with self.assertRaisesRegex(ValueError, 'all eight'):
            delivery.validate(self.root, self.expected)

    def test_symlinks_not_carried(self):
        (self.root/'images/secret').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'link or special'):
            delivery.files(self.root)

    def test_credential_file_not_carried(self):
        (self.root/'auth.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'only images'):
            delivery.files(self.root)

    def test_bad_tool_reference(self):
        (self.root/'images/SKOPEO_IMAGE').write_text('image; touch /tmp/unsafe')
        with self.assertRaisesRegex(ValueError, 'tool image'):
            delivery.validate(self.root, self.expected)

    def archive(self, names, link=False):
        archive = self.root/'archive.tgz'
        with tarfile.open(archive, 'w:gz') as tf:
            for name in names:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                    tf.addfile(item)
                else:
                    item.size = 4
                    tf.addfile(item, io.BytesIO(b'data'))
        return archive

    def delivery_archive(self):
        receipt = {'schema': 1, 'pins': self.expected, 'files': delivery.files(self.root)}
        transfer = tempfile.TemporaryDirectory(prefix='octo-images.', dir='/tmp')
        self.addCleanup(transfer.cleanup)
        path = Path(transfer.name)/'bundle.tar.gz'
        with tarfile.open(path, 'w:gz') as tf:
            for name in receipt['files']:
                tf.add(self.root/name, arcname=name)
            data = json.dumps(receipt).encode()
            info = tarfile.TarInfo('DELIVERY.json'); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        return path

    def receive(self, archive, digest, load=False):
        stage = self.root/'destination'
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == stage:
                values = list(result); values[4] = 0
                return os.stat_result(values)
            return result
        with patch.object(delivery, 'STAGES', stage), \
             patch.object(delivery, 'preflight', return_value={'pins': self.expected}), \
             patch.object(Path, 'stat', stat):
            return delivery.receive_unlocked(str(archive), digest, load)

    def test_receiver_accepts_verified_stage_without_loading(self):
        archive = self.delivery_archive()
        with patch.object(delivery.subprocess, 'run') as run:
            stage = self.receive(archive, delivery.sha(archive))
            self.assertEqual(delivery.map_rows(stage), delivery.map_rows(self.root))
            run.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'already staged'):
                self.receive(archive, delivery.sha(archive))
            self.assertTrue((stage/'DELIVERY.json').is_file())

    def test_receiver_refuses_transport_corruption_before_loading(self):
        archive = self.delivery_archive()
        with patch.object(delivery.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'archive digest mismatch'):
                self.receive(archive, 'a'*64, True)
            run.assert_not_called()

    def test_receiver_rechecks_installed_pins(self):
        archive = self.delivery_archive()
        self.expected['sapient'] = 'registry.invalid/changed@sha256:'+'b'*64
        with self.assertRaisesRegex(ValueError, 'installed pins changed'):
            self.receive(archive, delivery.sha(archive), True)

    def test_archive_roundtrip(self):
        archive = self.archive(['images/MAP', 'DELIVERY.json'])
        dest = self.root/'dest'; dest.mkdir()
        delivery.extract(archive, dest)
        self.assertEqual((dest/'images/MAP').read_bytes(), b'data')

    def test_archive_traversal_before_writes(self):
        for name in ('../escape', '/absolute', 'images/../../escape'):
            with self.subTest(name=name):
                archive = self.archive(['images/good', name])
                dest = self.root/'dest'; dest.mkdir(exist_ok=True)
                with self.assertRaisesRegex(ValueError, 'unsafe archive'):
                    delivery.extract(archive, dest)
                self.assertEqual(list(dest.iterdir()), [])

    def test_archive_link(self):
        archive = self.archive(['images/link'], link=True)
        with self.assertRaisesRegex(ValueError, 'unsafe archive'):
            delivery.extract(archive, self.root)

    def test_archive_duplicate(self):
        archive = self.archive(['images/MAP', 'images/MAP'])
        with self.assertRaisesRegex(ValueError, 'unsafe archive'):
            delivery.extract(archive, self.root)


if __name__ == '__main__':
    unittest.main()

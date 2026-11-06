#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""
How the CLI renders a human message: language choice, catalog loading, and the rules that
keep a translation from changing what a message means.

WHY THIS EXISTS
    bin/octo_pod_i18n.py sits under every message the CLI prints, in both the private and
    the public tree. A bad catalog must never stop an operation or hide an error, a
    translation must never drop a flag name or an OCTO_* variable an operator has to type,
    and nothing machine-readable may be translated. Each is checked here against a
    synthetic catalog written by the test, so it runs where no real catalog exists.

RUN
    python3 platform/tests/test_messages.py
"""
import array
import contextlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin"))


def load():
    spec = importlib.util.spec_from_file_location("octo_pod_i18n_under_test",
                                                  REPO / "bin" / "octo_pod_i18n.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_mo(path, messages):
    """A GNU .mo file, as msgfmt writes one: header, two offset tables, the strings."""
    keys = sorted(messages)
    ids = strs = b""
    offsets = []
    for k in keys:
        kb, vb = k.encode(), messages[k].encode()
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b"\0"
        strs += vb + b"\0"
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("Iiiiiii", 0x950412de, 0, len(keys), 7 * 4,
                                 7 * 4 + len(keys) * 8, 0, 0)
                     + array.array("i", koffsets).tobytes()
                     + array.array("i", voffsets).tobytes() + ids + strs)


HEADER = "Content-Type: text/plain; charset=UTF-8\n"
CATALOG = {
    "Pod {pod} is ready.": "Le pod {pod} est pret.",
    "Run --json for machine output.": "Lancez --json pour la sortie machine.",
    "Set OCTO_POD_ETC first.": "Definissez d'abord le repertoire.",      # drops the variable
    "Removed {n} arms.": "Retire {count} bras.",                          # renames a field
    "Broken {x}.": "Casse {x}} .",                                        # a stray brace
    "Width {x}.": "Largeur {x:d}.",                                       # a different spec
    "Unchanged.": "Unchanged.",
    "Show the status.": "Afficher l'etat.",
}


class Language(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_the_first_set_variable_wins_in_order(self):
        env = {"LANG": "de_DE.UTF-8", "LC_MESSAGES": "es_ES", "LC_ALL": "it_IT",
               "GOAT_LANG": "uk", "OCTO_POD_LANG": "fr"}
        self.assertEqual(self.m.language(env), "fr")
        del env["OCTO_POD_LANG"]
        self.assertEqual(self.m.language(env), "uk")
        del env["GOAT_LANG"]
        self.assertEqual(self.m.language(env), "it_IT")
        del env["LC_ALL"]
        self.assertEqual(self.m.language(env), "es_ES")
        del env["LC_MESSAGES"]
        self.assertEqual(self.m.language(env), "de_DE")

    def test_normalises_and_refuses_what_is_not_a_language_tag(self):
        cases = {"fr-ca": "fr_CA", "uk_UA.UTF-8@euro": "uk_UA", "zh_Hant_TW": "zh_Hant_TW",
                 "C": "en", "POSIX.UTF-8": "en", "../etc": "en", "fr;rm": "en", "  ": "en"}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.m.language({"OCTO_POD_LANG": raw}), want)
        self.assertEqual(self.m.language({}), "en")

    def test_reads_the_process_environment_by_default(self):
        with mock_env(OCTO_POD_LANG="fr", LANG=None, LC_ALL=None):
            self.assertEqual(self.m.language(), "fr")


@contextlib.contextmanager
def mock_env(**values):
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class Translate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.m = load()
        self.m.ROOT = Path(tmp.name)
        self.m.catalog.cache_clear()
        self.mo = Path(tmp.name) / "locale" / "fr" / "LC_MESSAGES" / "octo-pod.mo"
        write_mo(self.mo, {"": HEADER, **CATALOG})
        self.env = mock_env(OCTO_POD_LANG="fr", OCTO_POD_I18N_NO_WARN=None,
                            OCTO_POD_BILINGUAL=None, GOAT_BILINGUAL=None)
        self.env.__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)
        self.argv = sys.argv
        sys.argv = ["octo-pod"]
        self.addCleanup(setattr, sys, "argv", self.argv)

    def t(self, *a, **k):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = self.m.t(*a, **k)
        return out, err.getvalue()

    def test_translates_and_interpolates_after(self):
        out, err = self.t("Pod {pod} is ready.", pod="pod-a")
        self.assertEqual(out, "Le pod pod-a est pret.")
        self.assertIn("fr translations are not native-reviewed; English is authoritative", err)

    def test_warns_once_per_process(self):
        self.t("Pod {pod} is ready.", pod="a")
        _, err = self.t("Pod {pod} is ready.", pod="b")
        self.assertEqual(err, "")

    def test_no_warning_for_a_native_reviewed_catalog(self):
        write_mo(self.mo, {"": HEADER + "X-Review-Status: native-reviewed\n", **CATALOG})
        self.m.catalog.cache_clear()
        out, err = self.t("Pod {pod} is ready.", pod="a")
        self.assertEqual((out, err), ("Le pod a est pret.", ""))

    def test_no_warning_when_silenced_or_writing_json(self):
        with mock_env(OCTO_POD_I18N_NO_WARN="1"):
            self.assertEqual(self.t("Unchanged.")[1], "")
            self.assertEqual(self.t("Pod {pod} is ready.", pod="a")[1], "")
        for argv in (["--json"], ["-o", "json"], ["--output", "json"], ["--output=json"],
                     ["-ojson"]):
            with self.subTest(argv=argv):
                m = load()
                m.ROOT = self.m.ROOT
                sys.argv = ["octo-pod", "status", *argv]
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    m.t("Pod {pod} is ready.", pod="a")
                self.assertEqual(err.getvalue(), "")

    def test_json_after_a_double_dash_is_an_argument_not_a_format(self):
        sys.argv = ["octo-pod", "edit", "--", "--json"]
        self.assertTrue(self.t("Pod {pod} is ready.", pod="a")[1])

    def test_a_translation_that_drops_a_contract_token_is_not_used(self):
        self.assertEqual(self.t("Run --json for machine output.")[0],
                         "Lancez --json pour la sortie machine.")
        self.assertEqual(self.t("Set OCTO_POD_ETC first.")[0], "Set OCTO_POD_ETC first.")

    def test_a_translation_that_renames_a_field_is_not_used(self):
        self.assertEqual(self.t("Removed {n} arms.", n=2)[0], "Removed 2 arms.")

    def test_a_malformed_translation_falls_back_to_english(self):
        self.assertEqual(self.t("Broken {x}.", x="text")[0], "Broken text.")
        self.assertEqual(self.t("Width {x}.", x="text")[0], "Width text.")

    def test_untranslated_and_empty_text(self):
        self.assertEqual(self.t("Never in any catalog.")[0], "Never in any catalog.")
        self.assertEqual(self.t("")[0], "")

    def test_bilingual_shows_the_english_under_the_translation(self):
        with mock_env(OCTO_POD_BILINGUAL="yes"):
            self.assertEqual(self.t("Pod {pod} is ready.", pod="a")[0],
                             "Le pod a est pret.\n[en] Pod a is ready.")
        with mock_env(GOAT_BILINGUAL="true"):
            self.assertIn("\n[en] ", self.t("Pod {pod} is ready.", pod="a")[0])

    def test_critical_always_carries_the_english(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.m.critical("Pod {pod} is ready.", pod="a"),
                             "Le pod a est pret.\n[en] Pod a is ready.")
            self.assertEqual(self.m.critical("Never translated {x}.", x=1), "Never translated 1.")
            with mock_env(OCTO_POD_BILINGUAL="1"):
                self.assertEqual(self.m.critical("Pod {pod} is ready.", pod="a"),
                                 "Le pod a est pret.\n[en] Pod a is ready.")

    def test_a_corrupt_catalog_falls_back_to_english(self):
        self.mo.write_bytes(b"\xde\x12\x04\x95not a catalog")
        self.m.catalog.cache_clear()
        self.assertEqual(self.t("Pod {pod} is ready.", pod="a"), ("Pod a is ready.", ""))

    def test_a_missing_catalog_falls_back_to_english(self):
        with mock_env(OCTO_POD_LANG="de"):
            self.assertEqual(self.t("Pod {pod} is ready.", pod="a"), ("Pod a is ready.", ""))

    def test_the_command_line_renderer(self):
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = self.m.main(list(argv))
            return rc, out.getvalue()
        self.assertEqual(run("message", "Pod {pod} is ready.", "pod=a"),
                         (0, "Le pod a est pret."))
        self.assertEqual(run("critical", "Pod {pod} is ready.", "pod=a"),
                         (0, "Le pod a est pret.\n[en] Pod a is ready."))
        rc, out = run("help", "", "ETC=/etc/octo-pod", "ANSIBLE_DIR=/usr/share/octo-pod")
        self.assertEqual(rc, 0)
        self.assertIn("Identity:  /etc/octo-pod", out)
        self.assertIn("Playbooks: /usr/share/octo-pod", out)
        self.assertNotIn("${", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

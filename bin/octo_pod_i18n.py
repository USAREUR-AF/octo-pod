# SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
# SPDX-License-Identifier: Apache-2.0
"""Offline gettext support for human prose; never apply to structured output."""
import argparse
from functools import lru_cache
import gettext
import os
from pathlib import Path
import re
import string
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = 'octo-pod'
_WARNED = False


def language(environ=None):
    """Explicit CLI selection is exported as OCTO_POD_LANG by the wrapper."""
    env = os.environ if environ is None else environ
    for key in ('OCTO_POD_LANG', 'GOAT_LANG', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        raw = env.get(key, '').strip()
        if not raw:
            continue
        raw = raw.split('.', 1)[0].split('@', 1)[0]
        if raw.upper() in ('C', 'POSIX'):
            return 'en'
        if not re.fullmatch(r'[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*', raw):
            return 'en'
        parts = raw.replace('-', '_').split('_')
        return '_'.join([parts[0].lower()] + [p.upper() if len(p) == 2 else p for p in parts[1:]])
    return 'en'


@lru_cache(maxsize=16)
def catalog(code):
    try:
        return gettext.translation(DOMAIN, ROOT / 'locale', languages=[code], fallback=True)
    except (OSError, EOFError, ValueError, UnicodeError, LookupError, struct.error):
        # An absent or invalid catalog must not prevent an operation or hide an error.
        return gettext.NullTranslations()


def N_(text):
    """Mark English source for extraction without translating it yet."""
    return text


def _fields(text):
    return sorted((name, spec, conversion) for _, name, spec, conversion in string.Formatter().parse(text)
                  if name is not None)


def _tokens(text):
    return set(re.findall(r'`[^`]+`|(?<![\w-])--[a-z][a-z0-9-]*|\b(?:OCTO_[A-Z_]+|GOAT_[A-Z_]+)\b', text))


def _machine_output():
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == '--':
            break
        if arg in ('--json', '--output=json', '-ojson'):
            return True
        if arg in ('-o', '--output') and args[index + 1:index + 2] == ['json']:
            return True
    return False


def t(english, **values):
    """Translate before interpolation, preserving parameter types and contract tokens."""
    if not english:
        return ''
    translated = catalog(language()).gettext(english)
    try:
        if _fields(translated) != _fields(english) or _tokens(translated) != _tokens(english):
            translated = english
        localized = translated.format(**values) if values else translated
    except (KeyError, ValueError, IndexError, TypeError):
        return english.format(**values) if values else english
    canonical = english.format(**values) if values else english
    if localized != canonical:
        global _WARNED
        if not _WARNED and not os.environ.get('OCTO_POD_I18N_NO_WARN') and not _machine_output():
            if catalog(language()).info().get('x-review-status') != 'native-reviewed':
                print(f'note: {language()} translations are not native-reviewed; English is authoritative.', file=sys.stderr)
            _WARNED = True
        bilingual = os.environ.get('OCTO_POD_BILINGUAL', os.environ.get('GOAT_BILINGUAL', '')).lower()
        if bilingual in ('1', 'true', 'yes'):
            return f'{localized}\n[en] {canonical}'
    return localized


def critical(english, **values):
    """Errors and confirmations retain English alongside a non-English rendering."""
    localized = t(english, **values)
    canonical = english.format(**values) if values else english
    if localized == canonical or localized.endswith('\n[en] ' + canonical):
        return localized
    return f'{localized}\n[en] {canonical}'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render Octopod human-language messages.')
    parser.add_argument('mode', choices=('message', 'critical', 'help'))
    parser.add_argument('message', nargs='?', default='')
    parser.add_argument('values', nargs='*')
    args = parser.parse_args(argv)
    values = dict(value.split('=', 1) for value in args.values)
    if args.mode == 'help':
        from octo_pod_help import HELP
        # Text and command fragments have distinct roles. Commands are never translated.
        for command, prose in HELP:
            line = command + t(prose) if prose else command
            line = re.sub(r'\$\{([A-Z_]+)\}', lambda match: values.get(match[1], match[0]), line)
            print(line)
        return 0
    print((critical if args.mode == 'critical' else t)(args.message, **values), end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())

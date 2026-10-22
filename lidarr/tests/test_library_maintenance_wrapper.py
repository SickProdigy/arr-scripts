import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import version

from test_musicbrainz_genres_wrapper import ROOT, BASH, shell_path


class MaintenanceWrapperTests(unittest.TestCase):
    def test_rename_rollback_requires_separate_review_apply_and_verify(self):
        for arguments, action in (
            (('--rename-rollback-plan', '/config/forward.json', '/config/forward-journal.json', '/config/reverse.json'), 'rollback-plan'),
            (('--rename-rollback', '/config/reverse.json', '/config/reverse-journal.json'), 'rollback'),
            (('--rename-rollback-verify', '/config/reverse.json', '/config/reverse-journal.json'), 'rollback-verify'),
        ):
            with self.subTest(action=action):
                result, trace = self.invoke(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(action, trace)
                self.assertNotIn('apply', trace)
                if action == 'rollback-plan':
                    self.assertEqual(trace[trace.index('--output') + 1], '/config/reverse.json')

    def test_custom_local_and_remote_roots_reach_each_planner(self):
        settings = 'libraryMaintenanceRoot="/mnt/library"\nlibraryMaintenanceRemoteRoot="/audio"\n'
        for arguments in (
            ('--plan', '/audio/Artist/Album', '/config/plan.json'),
            ('--refresh-plan', '123', '/config/refresh.json'),
            ('--tags-plan', '123', '/config/tags.json'),
            ('--rename-plan', '123', '/config/rename.json'),
            ('--audit',),
        ):
            with self.subTest(arguments=arguments):
                result, trace = self.invoke(arguments, extra=settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trace[trace.index('--library-root') + 1], '/mnt/library')
                self.assertEqual(trace[trace.index('--remote-root') + 1], '/audio')

    def invoke(self, arguments=('--work',), enabled=True, pin=None, extra=''):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / 'bin'; tools.mkdir()
            trace = root / 'trace'
            config = root / 'extended.conf'
            config.write_text(f'enableLibraryMaintenance="{str(enabled).lower()}"\nnavidromeScriptsVersion="{pin if pin is not None else version("navidrome-scripts")}"\n'
                'enableReplaygainTags="true"\nenableLyrics="true"\n' + extra)
            (root / 'config.xml').write_text('<Config><Port>8686</Port><UrlBase>/lidarr</UrlBase><ApiKey>private-test-key</ApiKey></Config>')
            python = tools / 'python3'
            python.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(sys.executable.replace('\\', '/')) + ' "$@"\n', newline='\n')
            helper = tools / 'maintenance-helper'
            helper.write_text('#!/usr/bin/env bash\n[[ "$LIDARR_URL${LIDARR_API_KEY:-}" != *$\'\\r\'* ]] || exit 42\nprintf "%s\\n" "$@" > "$TRACE"\nprintf "%s\\n" "$LIDARR_URL" >> "$TRACE"\n', newline='\n')
            env = dict(os.environ, ARR_EXTENDED_CONF=shell_path(config), ARR_CONFIG_DIR=shell_path(root),
                ARR_LIBRARY_MAINTENANCE_HELPER=shell_path(helper), ARR_LIBRARY_UNMAPPED_HELPER=shell_path(helper), ARR_LIBRARY_DATES_HELPER=shell_path(helper), ARR_LIBRARY_RENAMES_HELPER=shell_path(helper), ARR_LIBRARY_TAGS_HELPER=shell_path(helper),
                TRACE=shell_path(trace), PATH=shell_path(tools) + ':/usr/bin:/bin')
            env.pop('LIDARR_URL', None); env.pop('LIDARR_API_KEY', None)
            subprocess.run([BASH, '-c', 'chmod +x "$1" "$2"', '--', shell_path(helper), shell_path(python)], env=env, check=True, capture_output=True)
            result = subprocess.run([BASH, shell_path(ROOT / 'LibraryMaintenance.bash'), *arguments],
                env=env, text=True, capture_output=True)
            return result, trace.read_text().splitlines() if trace.exists() else []

    def test_disabled_means_no_helper_or_api_calls(self):
        result, trace = self.invoke(enabled=False, pin='')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(trace, [])

    def test_enabled_uses_exact_pin_and_passes_credentials_only_in_environment(self):
        result, trace = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('work', trace)
        self.assertIn('beets-preview,replaygain,lyrics', trace)
        self.assertIn('http://127.0.0.1:8686/lidarr', trace)
        self.assertNotIn('private-test-key', str(trace) + result.stdout + result.stderr)

    def test_missing_or_mismatched_pin_blocks_work(self):
        for pin in ('', '999.0.0'):
            result, trace = self.invoke(pin=pin)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(trace, [])

    def test_approval_audit_and_scoped_import_remain_separate(self):
        result, trace = self.invoke(('--approve', '/config/review handoff.json'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('approve', trace); self.assertIn('/config/review handoff.json', trace)
        self.assertNotIn('work', trace)
        result, trace = self.invoke(('--audit',), extra='libraryMaintenanceDateAudit="true"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--include-dates', trace)
        result, trace = self.invoke(('--plan', '/music/Artist/Album with spaces', '/config/plan.json'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/music/Artist/Album with spaces', trace)
        self.assertNotIn('apply', trace)

    def test_disabled_steps_are_not_approved_and_mtime_preservation_required(self):
        result, trace = self.invoke(extra='libraryMaintenanceBeetsPreview="false"\nenableLyrics="false"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('replaygain', trace)
        self.assertNotIn('beets-preview,replaygain,lyrics', trace)
        result, trace = self.invoke(extra='replaygainPreserveMtime="false"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace, [])

    def test_date_lookup_apply_and_rollback_are_separate_explicit_commands(self):
        result,trace=self.invoke(('--date-plan','/config/dates.jsonl','/config/plan.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--lookup-dates',trace);self.assertNotIn('apply',trace)
        self.assertNotIn('--apple-review',trace)
        result,trace=self.invoke(('--date-plan','/config/dates.jsonl','/config/plan.json','--apple-review'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--apple-review',trace)
        result,trace=self.invoke(('--date-apply','/config/plan.json','/config/journal.jsonl'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('apply',trace);self.assertIn('--check-lidarr',trace)
        result,trace=self.invoke(('--date-rollback','/config/journal.jsonl'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('rollback',trace);self.assertNotIn('apply',trace)

    def test_renames_are_explicit_and_use_the_configured_prefix(self):
        result,trace=self.invoke(('--rename-plan','123','/config/rename plan.json'),extra='libraryMaintenanceNavidromePrefix="Music-Library"\n')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('plan',trace);self.assertIn('Music-Library',trace)
        self.assertIn('123',trace);self.assertNotIn('apply',trace)
        result,trace=self.invoke(('--rename-apply','/config/plan.json','/config/rename.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('apply',trace);self.assertNotIn('verify',trace)
        result,trace=self.invoke(('--rename-verify','/config/plan.json','/config/rename.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('verify',trace);self.assertNotIn('apply',trace)

    def test_missing_metadata_plan_apply_and_rollback_remain_separate(self):
        result,trace=self.invoke(('--tags-plan','123','/config/tags.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('plan',trace);self.assertIn('123',trace);self.assertNotIn('apply',trace)
        result,trace=self.invoke(('--tags-apply','/config/tags.json','/config/tag-repair.jsonl'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('apply',trace);self.assertNotIn('rollback',trace)
        result,trace=self.invoke(('--tags-rollback','/config/tag-repair.jsonl'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('rollback',trace);self.assertNotIn('apply',trace)

    def test_metadata_refresh_is_scoped_and_separate_from_import(self):
        result,trace=self.invoke(('--refresh-plan','123','/config/refresh.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('refresh-plan',trace);self.assertIn('123',trace);self.assertNotIn('apply',trace)
        result,trace=self.invoke(('--refresh','/config/refresh.json','/config/refresh-journal.json'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('refresh',trace);self.assertNotIn('apply',trace)

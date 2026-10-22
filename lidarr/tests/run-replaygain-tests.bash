#!/usr/bin/env bash
set -euo pipefail

lidarrRoot="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scriptUnderTest="$lidarrRoot/ReplayGainTagger.bash"
tmpRoot="$(mktemp -d)"
trap 'rm -rf "$tmpRoot"' EXIT

passCount=0

assert_equals () {
	local expected="$1"
	local actual="$2"
	local message="$3"
	if [ "$expected" != "$actual" ]; then
		echo "FAIL: $message"
		echo "expected: $expected"
		echo "actual:   $actual"
		exit 1
	fi
}

assert_contains () {
	local needle="$1"
	local file="$2"
	local message="$3"
	if ! grep -F "$needle" "$file" >/dev/null; then
		echo "FAIL: $message"
		echo "missing: $needle"
		echo "file: $file"
		cat "$file"
		exit 1
	fi
}

assert_not_contains () {
	local needle="$1"
	local file="$2"
	local message="$3"
	if grep -F "$needle" "$file" >/dev/null; then
		echo "FAIL: $message"
		echo "unexpected: $needle"
		echo "file: $file"
		cat "$file"
		exit 1
	fi
}

make_fixture () {
	local name="$1"
	local root="$tmpRoot/$name"
	mkdir -p "$root/config" "$root/state" "$root/bin" "$root/music/Artist/Album"
	cat > "$root/config/extended.conf" <<'EOF'
enableReplaygainTags="true"
enableBeetsTagging="false"
replaygainTargetLoudness="-18"
replaygainThreads="1"
replaygainPreserveMtime="true"
replaygainClipMode="p"
replaygainTruePeak="false"
replaygainMaxPeak="0"
EOF
	cat > "$root/config/functions" <<'EOF'
log () {
	printf '%s\n' "$1"
}
EOF
	cat > "$root/bin/rsgain-mock" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$REPLAYGAIN_MOCK_LOG"
if [ "${REPLAYGAIN_MOCK_FAIL:-false}" = "true" ]; then
	echo "mock scanner failure"
	exit 42
fi
if [ "${REPLAYGAIN_MOCK_TAG:-false}" = "true" ]; then
    python3 - "$@" <<'PY'
import sys
from mutagen.flac import FLAC
for path in sys.argv[1:]:
    if path.endswith('.flac'):
        audio = FLAC(path)
        for field, value in {'replaygain_track_gain': '-5 dB', 'replaygain_album_gain': '-5 dB',
                             'replaygain_track_peak': '0.9', 'replaygain_album_peak': '0.9'}.items():
            audio[field] = value
        audio.save()
PY
fi
echo "mock scanner success"
exit 0
EOF
	chmod +x "$root/bin/rsgain-mock"
	touch "$root/music/Artist/Album/01 First.flac"
	printf '%s\n' "$root"
}

run_tagger () {
	local root="$1"
	shift
	PATH="$root/bin:$PATH" \
	EXTENDED_CONF="$root/config/extended.conf" \
	FUNCTIONS_PATH="$root/config/functions" \
	REPLAYGAIN_SCANNER="$root/bin/rsgain-mock" \
	REPLAYGAIN_MOCK_LOG="$root/scanner.log" \
	REPLAYGAIN_MOCK_FAIL="${REPLAYGAIN_MOCK_FAIL:-false}" \
	REPLAYGAIN_MOCK_TAG="${REPLAYGAIN_MOCK_TAG:-false}" \
	REPLAYGAIN_STATE_DIR="$root/state" \
	REPLAYGAIN_MUSIC_ROOT="$root/music" \
	lidarr_eventtype="${lidarr_eventtype:-}" \
	lidarr_artist_path="${lidarr_artist_path:-}" \
	lidarr_album_title="${lidarr_album_title:-}" \
	lidarr_trackfile_path="${lidarr_trackfile_path:-}" \
	lidarr_trackfile_paths="${lidarr_trackfile_paths:-}" \
	lidarr_addedtrackpaths="${lidarr_addedtrackpaths:-}" \
	lidarr_importedtrackpaths="${lidarr_importedtrackpaths:-}" \
	lidarr_deletedpaths="${lidarr_deletedpaths:-}" \
	"$scriptUnderTest" "$@" > "$root/output.log" 2>&1
}

test_disabled_mode () {
	local root
	root="$(make_fixture disabled)"
	sed -i 's/enableReplaygainTags="true"/enableReplaygainTags="false"/' "$root/config/extended.conf"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	[ ! -f "$root/scanner.log" ] || {
		echo "FAIL: disabled mode should not call scanner"
		exit 1
	}
	assert_contains "ReplayGain tagging is disabled" "$root/output.log" "disabled mode logs skip"
	passCount=$((passCount + 1))
}

test_release_import_event () {
	local root
	root="$(make_fixture import)"
	lidarr_eventtype="Download" lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	assert_contains "custom -a -s i -l -18 -c p -m 0 -p" "$root/scanner.log" "release import uses rsgain custom album tagging"
	assert_contains "01 First.flac" "$root/scanner.log" "release import passes imported file"
	passCount=$((passCount + 1))
}

test_configurable_target () {
	local root
	root="$(make_fixture configurable_target)"
	sed -i 's/replaygainTargetLoudness="-18"/replaygainTargetLoudness="-14"/' "$root/config/extended.conf"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	assert_contains "custom -a -s i -l -14 -c p -m 0 -p" "$root/scanner.log" "configured -14 LUFS target reaches rsgain"
	passCount=$((passCount + 1))
}

test_upgrade_event_paths () {
	local root
	root="$(make_fixture upgrade)"
	touch "$root/music/Artist/Album/02 Second.mp3"
	lidarr_eventtype="Upgrade" lidarr_addedtrackpaths="$root/music/Artist/Album/02 Second.mp3" run_tagger "$root"
	assert_contains "02 Second.mp3" "$root/scanner.log" "upgrade event paths are scanned"
	passCount=$((passCount + 1))
}

test_duplicate_event_skips_after_success () {
	local root count
	root="$(make_fixture duplicate)"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	count="$(wc -l < "$root/scanner.log" | awk '{print $1}')"
	assert_equals "1" "$count" "duplicate event should only scan once"
	assert_contains "already completed" "$root/output.log" "duplicate event logs stamp skip"
	passCount=$((passCount + 1))
}

test_target_change_forces_rescan () {
	local root count
	root="$(make_fixture target_change)"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	sed -i 's/replaygainTargetLoudness="-18"/replaygainTargetLoudness="-14"/' "$root/config/extended.conf"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	count="$(wc -l < "$root/scanner.log" | awk '{print $1}')"
	assert_equals "2" "$count" "changing target loudness should force a rescan"
	assert_contains "custom -a -s i -l -14 -c p -m 0 -p" "$root/scanner.log" "rescan uses changed target"
	passCount=$((passCount + 1))
}

test_true_peak_clipping_configuration () {
	local root
	root="$(make_fixture true_peak)"
	sed -i 's/replaygainClipMode="p"/replaygainClipMode="a"/' "$root/config/extended.conf"
	sed -i 's/replaygainTruePeak="false"/replaygainTruePeak="true"/' "$root/config/extended.conf"
	sed -i 's/replaygainMaxPeak="0"/replaygainMaxPeak="-1"/' "$root/config/extended.conf"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	assert_contains "custom -a -s i -l -18 -c a -m -1 -t -p" "$root/scanner.log" "true-peak clipping settings reach rsgain"
	passCount=$((passCount + 1))
}

test_scanner_failure_is_non_fatal () {
	local root count
	root="$(make_fixture failure)"
	REPLAYGAIN_MOCK_FAIL="true" lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	assert_contains "mock scanner failure" "$root/output.log" "scanner failure output is logged"
	assert_contains "exited with status 42" "$root/output.log" "scanner exit status is logged"
	assert_contains "will not be rejected" "$root/output.log" "scanner failure is non-fatal"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	count="$(wc -l < "$root/scanner.log" | awk '{print $1}')"
	assert_equals "2" "$count" "a failed scan should release its lock and retry"
	passCount=$((passCount + 1))
}

test_beets_disabled_still_scans () {
	local root
	root="$(make_fixture beets_disabled)"
	lidarr_trackfile_path="$root/music/Artist/Album/01 First.flac" run_tagger "$root"
	assert_contains "01 First.flac" "$root/scanner.log" "ReplayGain is independent of Beets"
	passCount=$((passCount + 1))
}

test_unicode_and_spaces () {
	local root album
	root="$(make_fixture unicode)"
	album="$root/music/Artist Name/Beyoncé - Café del Mar"
	mkdir -p "$album"
	touch "$album/01 Déjà Vu.flac"
	lidarr_trackfile_path="$album/01 Déjà Vu.flac" run_tagger "$root"
	assert_contains "01 Déjà Vu.flac" "$root/scanner.log" "unicode/space path file is scanned"
	passCount=$((passCount + 1))
}

test_multidisc_album () {
	local root album
	root="$(make_fixture multidisc)"
	album="$root/music/Artist/Album"
	mkdir -p "$album/CD 1" "$album/CD 2"
	touch "$album/CD 1/01 One.flac" "$album/CD 2/01 Two.m4a" "$album/CD 2/02 Three.opus"
	lidarr_trackfile_path="$album/CD 1/01 One.flac" run_tagger "$root"
	assert_contains "CD 1/01 One.flac" "$root/scanner.log" "multidisc disc 1 is scanned"
	assert_contains "CD 2/01 Two.m4a" "$root/scanner.log" "multidisc m4a is scanned"
	assert_contains "CD 2/02 Three.opus" "$root/scanner.log" "multidisc opus is scanned"
	passCount=$((passCount + 1))
}

test_path_escape_rejected () {
	local root outside
	root="$(make_fixture escape)"
	outside="$tmpRoot/outside/Album"
	mkdir -p "$outside"
	touch "$outside/01 Outside.flac"
	lidarr_trackfile_path="$outside/01 Outside.flac" run_tagger "$root"
	assert_contains "Refusing ReplayGain scan outside music root" "$root/output.log" "outside paths are rejected"
	[ ! -f "$root/scanner.log" ] || {
		echo "FAIL: outside path should not call scanner"
		exit 1
	}
	passCount=$((passCount + 1))
}

test_audit_mode_reports_unreadable () {
	local root
	root="$(make_fixture audit)"
	touch "$root/music/Artist/Album/02 Unsupported.wav"
	run_tagger "$root" --audit --path "$root/music"
	assert_contains "UNREADABLE" "$root/output.log" "audit reports unreadable mock audio"
	assert_contains "UNSUPPORTED" "$root/output.log" "audit reports unsupported audio"
	assert_contains "unsupported=1" "$root/output.log" "audit summary counts unsupported audio"
	assert_contains "SUMMARY" "$root/output.log" "audit prints summary"
	passCount=$((passCount + 1))
}

test_backfill_dry_run_does_not_scan () {
	local root
	root="$(make_fixture backfill_dry_run)"
	run_tagger "$root" --backfill --dry-run --path "$root/music"
	assert_contains "Dry run audit only" "$root/output.log" "dry-run backfill logs read-only mode"
	[ ! -f "$root/scanner.log" ] || {
		echo "FAIL: dry-run backfill should not call scanner"
		exit 1
	}
	passCount=$((passCount + 1))
}

test_backfill_groups_multidisc_album () {
	local root count
	root="$(make_fixture backfill_multidisc)"
	mkdir -p "$root/music/Artist/Album/CD 1" "$root/music/Artist/Album/CD 2"
	touch "$root/music/Artist/Album/CD 1/01 One.flac" "$root/music/Artist/Album/CD 2/01 Two.opus"
	run_tagger "$root" --backfill --path "$root/music"
	count="$(wc -l < "$root/scanner.log" | awk '{print $1}')"
	assert_equals "1" "$count" "multidisc backfill should scan the album once"
	assert_contains "CD 1/01 One.flac" "$root/scanner.log" "backfill includes disc 1"
	assert_contains "CD 2/01 Two.opus" "$root/scanner.log" "backfill includes disc 2"
	passCount=$((passCount + 1))
}

write_repair_audit () {
	python3 - "$1" <<'PY'
import os
import sys
root = sys.argv[1]
with open(os.path.join(root, 'audit.log'), 'w', encoding='utf8') as stream:
    stream.write('UNTAGGED\t' + os.path.join(root, 'music/Artist/Album/01 First.flac') + '\n')
    stream.write('COMPLETE\t' + os.path.join(root, 'music/Artist/Other/02 Other.mp3') + '\n')
PY
}

test_repair_preview_only_selects_missing_album () {
	local root
	root="$(make_fixture repair_preview)"
	mkdir -p "$root/music/Artist/Other"
	touch "$root/music/Artist/Other/02 Other.mp3"
	write_repair_audit "$root"
	run_tagger "$root" --repair-audit "$root/audit.log" --dry-run
	assert_contains "selected=1" "$root/output.log" "repair only selects missing-tag album"
	assert_contains "REPAIR_PREVIEW" "$root/output.log" "repair preview lists album"
	[ ! -f "$root/scanner.log" ] || { echo "FAIL: repair preview called scanner"; exit 1; }
	passCount=$((passCount + 1))
}

test_repair_bypasses_stamp_and_detects_incomplete_tags () {
	local root
	root="$(make_fixture repair_stamp)"
	run_tagger "$root" --path "$root/music/Artist/Album"
	write_repair_audit "$root"
	if run_tagger "$root" --repair-audit "$root/audit.log"; then
		echo "FAIL: missing tags must fail repair despite scanner success"
		exit 1
	fi
	assert_equals "2" "$(wc -l < "$root/scanner.log" | awk '{print $1}')" "repair bypasses a matching completion stamp"
	assert_contains "failed=1" "$root/output.log" "incomplete repair is reported"
	passCount=$((passCount + 1))
}

test_repair_verifies_tags_and_resumes_without_rescanning () {
	local root
	root="$(make_fixture repair_success)"
	python3 - "$root/music/Artist/Album/01 First.flac" <<'PY'
import struct
import sys
streaminfo = struct.pack('>HH', 4096, 4096) + b'\0'*6
streaminfo += ((44100 << 44) | (1 << 41) | (15 << 36) | 44100).to_bytes(8, 'big') + b'\0'*16
with open(sys.argv[1], 'wb') as stream:
    stream.write(b'fLaC\x80\0\0\x22' + streaminfo)
PY
	write_repair_audit "$root"
	REPLAYGAIN_MOCK_TAG=true run_tagger "$root" --repair-audit "$root/audit.log"
	assert_contains "repaired=1" "$root/output.log" "repair verifies generated tags"
	run_tagger "$root" --repair-audit "$root/audit.log"
	assert_contains "already_complete=1" "$root/output.log" "resume checks current tags"
	assert_equals "1" "$(wc -l < "$root/scanner.log" | awk '{print $1}')" "resume does not rescan a complete album"
	REPLAYGAIN_MOCK_TAG=true run_tagger "$root" --repair-audit "$root/audit.log" --force
	assert_equals "2" "$(wc -l < "$root/scanner.log" | awk '{print $1}')" "forced recovery rescans complete tags to avoid mixed album gain"
	passCount=$((passCount + 1))
}

test_repair_rejects_paths_outside_root () {
	local root
	root="$(make_fixture repair_escape)"
	python3 - "$root" <<'PY'
import os
import sys
with open(os.path.join(sys.argv[1], 'audit.log'), 'w', encoding='utf8') as stream:
    stream.write('UNTAGGED\t' + os.path.join(sys.argv[1], 'outside.flac') + '\n')
PY
	if run_tagger "$root" --repair-audit "$root/audit.log"; then
		echo "FAIL: repair accepted audit outside root"
		exit 1
	fi
	[ ! -f "$root/scanner.log" ] || { echo "FAIL: invalid audit called scanner"; exit 1; }
	passCount=$((passCount + 1))
}

test_repair_flags_loose_files_without_scanning_artist_tree () {
	local root
	root="$(make_fixture repair_loose)"
	touch "$root/music/Artist/Loose.flac"
	python3 - "$root" <<'PY'
import os
import sys
with open(os.path.join(sys.argv[1], 'audit.log'), 'w', encoding='utf8') as stream:
    stream.write('UNTAGGED\t' + os.path.join(sys.argv[1], 'music/Artist/Loose.flac') + '\n')
PY
	run_tagger "$root" --repair-audit "$root/audit.log"
	assert_contains "REPAIR_REVIEW" "$root/output.log" "loose files need album grouping review"
	assert_contains "selected=0" "$root/output.log" "loose file does not select entire artist tree"
	[ ! -f "$root/scanner.log" ] || { echo "FAIL: loose file scanned artist tree"; exit 1; }
	passCount=$((passCount + 1))
}

test_audit_timing_and_maintenance_schedule () {
    local root
    root="$(make_fixture maintenance)"
    run_tagger "$root" --maintenance
    [ ! -f "$root/state/maintenance.last-attempt" ] || exit 1
    run_tagger "$root" --audit
    assert_contains "AUDIT START" "$root/output.log" "audit records start"
    assert_contains "elapsed_seconds=" "$root/output.log" "audit records duration"
    # Git Bash lacks flock; mock only lock acquisition, not scheduling or auditing.
    printf '#!/usr/bin/env bash\nexit 0\n' > "$root/bin/flock"
    chmod +x "$root/bin/flock"
    echo 'enableReplaygainMaintenance="true"' >> "$root/config/extended.conf"
    REPLAYGAIN_MOCK_TAG=true run_tagger "$root" --maintenance
    assert_contains "MAINTENANCE END" "$root/output.log" "maintenance completes"
    assert_contains "AUDIT END" "$root/state/maintenance-audit.log" "maintenance saves timed audit"
    run_tagger "$root" --maintenance
    assert_not_contains "MAINTENANCE START" "$root/output.log" "schedule survives separate invocations"
    echo 'replaygainMaintenanceDays="bad"' >> "$root/config/extended.conf"
    if run_tagger "$root" --maintenance; then
        echo "FAIL: invalid maintenance interval accepted"
        exit 1
    fi
    passCount=$((passCount + 1))
}

test_audit_timing_and_maintenance_schedule
test_repair_flags_loose_files_without_scanning_artist_tree
test_repair_preview_only_selects_missing_album
test_repair_bypasses_stamp_and_detects_incomplete_tags
test_repair_verifies_tags_and_resumes_without_rescanning
test_repair_rejects_paths_outside_root
test_disabled_mode
test_release_import_event
test_configurable_target
test_upgrade_event_paths
test_duplicate_event_skips_after_success
test_target_change_forces_rescan
test_true_peak_clipping_configuration
test_scanner_failure_is_non_fatal
test_beets_disabled_still_scans
test_unicode_and_spaces
test_multidisc_album
test_path_escape_rejected
test_audit_mode_reports_unreadable
test_backfill_dry_run_does_not_scan
test_backfill_groups_multidisc_album

echo "PASS: $passCount ReplayGain tests"

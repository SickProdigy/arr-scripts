import unittest
import test_musicbrainz_genres_wrapper as wrappers


class RatingsWrapperTests(unittest.TestCase):
    def run_wrapper(self, ratings="true", legacy=False, additional_config="", **kwargs):
        prefix = "navidrome" if legacy else "subsonic"
        return wrappers.GenreWrapperTests.run_wrapper(self, script_name="Ratings.bash", helper_name="musicbrainz-ratings", extra_helpers=("sonicratings",),
            extra_config=f'enableRatings="{ratings}"\n{prefix}Url="http://navidrome:4533"\n'
                         f'{prefix}Username="listener"\n{prefix}Password="fake-nav-secret"\n'
                         'lastfmApiKey="fake-lastfm-key"\nratingsScriptInterval=20m\n' + additional_config, **kwargs)

    def test_generic_settings_and_legacy_aliases_export_same_server(self):
        for legacy in (False, True):
            code, trace = self.run_wrapper(legacy=legacy, arguments=("--work",))
            self.assertEqual(code, 0)
            self.assertIn("http://navidrome:4533", trace)
            self.assertIn("listener", trace)

    def test_generic_settings_take_precedence_over_legacy_aliases(self):
        code, trace = self.run_wrapper(legacy=True, arguments=("--work",),
                                      additional_config='subsonicUrl="https://server.example.test"\nsubsonicUsername="new-user"\n')
        self.assertEqual(code, 0)
        self.assertIn("https://server.example.test", trace)
        self.assertIn("new-user", trace)
        self.assertNotIn("http://navidrome:4533", trace)

    def test_disabled_and_test_event_have_no_effect(self):
        self.assertEqual(self.run_wrapper(ratings="false", enabled="false"), (0, []))
        self.assertEqual(self.run_wrapper(event="Test"), (0, []))

    def test_beets_callbacks_wait_for_completion(self):
        self.assertEqual(self.run_wrapper(beets="true"), (0, []))
        code, trace = self.run_wrapper(beets="true", arguments=("--after-beets",))
        self.assertEqual(code, 0)
        self.assertIn("enqueue", trace)
        self.assertIn("123", trace)

    def test_generation_can_run_with_musicbrainz_submissions_disabled(self):
        code, trace = self.run_wrapper(enabled="false", arguments=("--work",))
        self.assertEqual(code, 0)
        self.assertIn("work", trace)
        self.assertIn("false", trace)
        self.assertIn("sonicratings", trace)
        self.assertNotIn("musicbrainz-ratings", trace)
        self.assertIn("20m", trace)
        self.assertNotIn("fake-nav-secret", trace)
        self.assertNotIn("fake-password", trace)
        self.assertNotIn("fake-lastfm-key", trace)

    def test_retag_and_provider_import_queue(self):
        for kwargs in ({}, {"beets": "true", "event": "TrackRetag"}):
            code, trace = self.run_wrapper(**kwargs)
            self.assertEqual(code, 0)
            self.assertIn("enqueue", trace)

    def test_queue_failure_does_not_fail_import_but_worker_reports_failure(self):
        self.assertEqual(self.run_wrapper(fail=True)[0], 0)
        self.assertEqual(self.run_wrapper(fail=True, arguments=("--work",))[0], 1)

    def test_success_output_is_not_reported_as_lidarr_error(self):
        code, _, stdout, stderr = self.run_wrapper(helper_message="INFO queued", capture_output=True)
        self.assertEqual(code, 0)
        self.assertIn("INFO queued", stdout)
        self.assertEqual(stderr, "")

    def test_library_backfill_needs_no_album(self):
        code, trace = self.run_wrapper(beets="true", album="", arguments=("--library",))
        self.assertEqual(code, 0)
        self.assertIn("library", trace)
        self.assertNotIn("--album-id", trace)


    def test_musicbrainz_contributions_can_run_without_generation(self):
        code, trace = self.run_wrapper(ratings="false", arguments=("--work",))
        self.assertEqual(code, 0)
        self.assertIn("musicbrainz-ratings", trace)
        self.assertNotIn("sonicratings", trace)

    def test_explicit_provider_and_discogs_consent_are_forwarded(self):
        code, trace = self.run_wrapper(arguments=("--work",),
            additional_config='ratingsProvider="discogs"\ndiscogsInheritAlbumRating="true"\n')
        self.assertEqual(code, 0)
        self.assertIn("discogs", trace)
        self.assertIn("--discogs-inherit-album-rating", trace)

    def test_preview_keeps_both_packages_in_preview_mode(self):
        code, trace = self.run_wrapper(arguments=("--library", "--preview"))
        self.assertEqual(code, 0)
        self.assertIn("--preview", trace)
        self.assertIn("false", trace)
        self.assertNotIn("--submit", trace)

    def test_both_packages_are_enqueued_and_worker_runs_generation_first(self):
        for arguments in ((), ("--work",)):
            code, trace = self.run_wrapper(arguments=arguments)
            self.assertEqual(code, 0)
            self.assertLess(trace.index("sonicratings"), trace.index("musicbrainz-ratings"))

    def test_enabled_generation_reports_missing_server_configuration(self):
        code, trace = self.run_wrapper(arguments=("--work",),
            additional_config='subsonicUrl=""\nsubsonicUsername=""\nsubsonicPassword=""\n')
        self.assertEqual(code, 1)
        self.assertEqual(trace, [])

    def test_genre_only_installation_does_not_start_ratings(self):
        code, trace = self.run_wrapper(ratings="false", arguments=("--work",),
            additional_config='subsonicUrl=""\nsubsonicUsername=""\nsubsonicPassword=""\n')
        self.assertEqual((code, trace), (0, []))


if __name__ == "__main__":
    unittest.main()

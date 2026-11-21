from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class VersionedStaticStorage(ManifestStaticFilesStorage):
    """Hashed static file names (manage.3f2a….css), so browsers never keep an old copy after a release.

    Only CSS url() references are rewritten: bundled .js files point at source maps we don't ship.
    Falls back to the plain name when collectstatic hasn't run (tests, development).
    """

    manifest_strict = False
    patterns = tuple(p for p in ManifestStaticFilesStorage.patterns if p[0] == "*.css")

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:  # not collected yet: serve the plain name
            return name

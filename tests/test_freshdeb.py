"""Unit tests for scripts/freshdeb.py: versions, validation, planning, watch.

Run in tests/release.Dockerfile (needs git, dpkg and yq):
    docker run --rm -v "$PWD:/workspace:ro" freshdeb-tests python3 -m unittest tests.test_freshdeb
"""

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import freshdeb  # noqa: E402

TODAY = dt.date(2026, 10, 27)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT)


class VersionTests(unittest.TestCase):
    PAIRS = [
        ("1.0", "1.0"), ("1.0", "1.0-1"), ("1.0~rc1", "1.0"), ("1.0+dfsg", "1.0"),
        ("9.1.3-1+freshdeb~deb13", "8.1.1+dfsg1-3+deb13u2"),
        ("8.1.11-1+freshdeb~deb13", "8.1.1+dfsg1-3+deb13u2"),
        ("9.0.7-1+freshdeb~ubuntu26.04", "9.0.4-0ubuntu0.2"),
        ("8.1.11-1+freshdeb~ubuntu26.04", "9.0.4-0ubuntu0.2"),
        ("9.1.3-1+freshdeb~deb12", "9.1.3-1+freshdeb~deb13"),
        ("9.1.3-1+freshdeb~ubuntu22.04", "9.1.3-1+freshdeb~ubuntu24.04"),
        ("9.1.3-1+freshdeb~ubuntu24.04", "9.1.3-1+freshdeb~ubuntu26.04"),
        ("9.1.3-1+freshdeb~deb13", "9.1.3-1"), ("9.1.3-1+freshdeb~deb13", "9.1.3-1~bpo13+1"),
        ("9.1.3-2+freshdeb~deb13", "9.1.3-10+freshdeb~deb13"),
        ("5:7.0.15-1~deb12u10", "8.0.0-1"), ("1:1.0", "2.0"), ("1.0a", "1.0+"), ("1.0.0", "1.0"),
        ("0010", "10"), ("1.2-3.4", "1.2-3.10"), ("1a", "1b"), ("1~~", "1~~a"), ("1~", "1"),
    ]

    def test_matches_dpkg(self):
        if not shutil.which("dpkg"):
            self.skipTest("dpkg not available")
        for a, b in self.PAIRS:
            for x, y in ((a, b), (b, a), (a, a)):
                ours = freshdeb.compare_versions(x, y)
                for op, expected in (("lt", ours < 0), ("eq", ours == 0), ("gt", ours > 0)):
                    result = subprocess.run(["dpkg", "--compare-versions", x, op, y]).returncode == 0
                    self.assertEqual(result, expected, f"{x} {op} {y}")

    def test_our_builds_beat_distro_packages(self):
        self.assertGreater(freshdeb.compare_versions("9.1.3-1+freshdeb~deb13", "8.1.1+dfsg1-3+deb13u2"), 0)
        self.assertLess(freshdeb.compare_versions("8.1.11-1+freshdeb~ubuntu26.04", "9.0.4-0ubuntu0.2"), 0)

    def test_dist_upgrades_move_to_the_newer_build(self):
        config = freshdeb.load_config(ROOT)
        pkg = freshdeb.load_package("valkey", ROOT)
        for older, newer in (("bookworm", "trixie"), ("jammy", "noble"), ("noble", "resolute")):
            self.assertLess(freshdeb.compare_versions(pkg.version(config, "9.1", older),
                                                      pkg.version(config, "9.1", newer)), 0)

    def test_version_format(self):
        config = freshdeb.load_config(ROOT)
        pkg = freshdeb.load_package("valkey", ROOT)
        self.assertRegex(pkg.version(config, "9.1", "bookworm"), r"^9\.1\.\d+-\d+\+freshdeb~deb12$")
        self.assertRegex(pkg.version(config, "8.0", "jammy"), r"^8\.0\.\d+-\d+\+freshdeb~ubuntu22\.04$")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.yaml = self.tmp / "packages/valkey/package.yaml"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def errors(self):
        return freshdeb.validate_all(self.tmp, TODAY)

    def replace(self, old, new):
        text = self.yaml.read_text()
        self.assertIn(old, text)
        self.yaml.write_text(text.replace(old, new, 1))

    def test_real_catalog_is_valid(self):
        self.assertEqual(freshdeb.validate_all(ROOT, TODAY), [])

    def test_unquoted_track_key(self):
        self.replace('"9.1":\n    tag', '9.1:\n    tag')
        self.assertTrue(any("must be quoted" in e for e in self.errors()))

    def test_tag_outside_its_track(self):
        self.replace('tag: "9.1.3"', 'tag: "9.0.3"')
        self.assertTrue(any("does not belong to track 9.1" in e for e in self.errors()))

    def test_short_commit(self):
        self.replace("commit: 29173b67508b2de4c0f0ed551f3b47cf19fc3ca8", "commit: 29173b6")
        self.assertTrue(any("40-character" in e for e in self.errors()))

    def test_alias_to_unknown_track(self):
        self.replace('"9": "9.1"', '"9": "9.7"')
        self.assertTrue(any("unknown track 9.7" in e for e in self.errors()))

    def test_alias_to_eol_track(self):
        self.replace('"8": "8.1"', '"8": "8.0"')
        errors = freshdeb.validate_all(self.tmp, dt.date(2028, 1, 1))
        self.assertTrue(any("end-of-life" in e for e in errors))

    def test_missing_patches(self):
        self.replace("patches: patches/9.0", "patches: patches/nope")
        self.assertTrue(any("no series file" in e for e in self.errors()))

    def test_tarball_tracks_need_sha256(self):
        y = self.tmp / "packages/memcached/package.yaml"
        y.write_text(y.read_text().replace("    sha256: d362", "    nosha: d362"))
        self.assertTrue(any("sha256 of the release tarball is required" in e for e in self.errors()))

    def test_sha256_without_tarball_is_rejected(self):
        self.replace("    revision: 1\n    eol: 2031-05-19", "    revision: 1\n    sha256: " + "a" * 64 + "\n    eol: 2031-05-19")
        self.assertTrue(any("only used with upstream.tarball" in e for e in self.errors()))

    def test_tarball_url_needs_version_placeholder(self):
        y = self.tmp / "packages/memcached/package.yaml"
        y.write_text(y.read_text().replace("memcached-{version}.tar.gz", "memcached-latest.tar.gz"))
        self.assertTrue(any("upstream.tarball must be" in e for e in self.errors()))

    def test_missing_overlay(self):
        self.replace("overlays/legacy-sysusers.patch", "overlays/missing.patch")
        self.assertTrue(any("does not exist" in e for e in self.errors()))


class ControlTests(unittest.TestCase):
    CONTROL = (
        "Source: demo\nMaintainer: Debian Person <dp@debian.org>\nUploaders: Other <o@debian.org>\n"
        "Build-Depends:\n debhelper-compat (= 13),\n libfoo-dev,\nVcs-Git: https://salsa/x.git\n"
        "Vcs-Browser: https://salsa/x\n\n"
        "Package: demo-bin\nArchitecture: any\nDescription: demo\n long text\n .\n more\n\n"
        "Package: demo-doc\nArchitecture: all\nDescription: docs\n\n"
        "Package: demo-udeb\nPackage-Type: udeb\nArchitecture: any\nDescription: installer\n")

    def test_rewrite(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "control").write_text(self.CONTROL)
        freshdeb.rewrite_control(tmp, "freshdeb <packages@freshdeb.com>")
        text = (tmp / "control").read_text()
        self.assertIn("Maintainer: freshdeb <packages@freshdeb.com>\n", text)
        self.assertIn("XSBC-Original-Maintainer: Debian Person <dp@debian.org>\n", text)
        self.assertNotIn("Uploaders", text)
        self.assertNotIn("Vcs-", text)
        self.assertIn("Build-Depends:\n debhelper-compat (= 13),\n libfoo-dev,\n", text)
        self.assertIn("Description: demo\n long text\n .\n more\n", text)
        self.assertEqual(freshdeb.control_binaries(tmp), {"demo-bin": "any", "demo-doc": "all"})
        self.assertIn("Source: demo\nPriority: optional\n", text)
        shutil.rmtree(tmp)

    def test_rewrite_keeps_existing_priority(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "control").write_text("Source: demo\nSection: net\nPriority: extra\nMaintainer: x <x@x>\n\n"
                                     "Package: demo\nArchitecture: any\nDescription: d\n")
        freshdeb.rewrite_control(tmp, "freshdeb <packages@freshdeb.com>")
        text = (tmp / "control").read_text()
        self.assertIn("Section: net\nPriority: extra\n", text)
        self.assertEqual(text.count("Priority"), 1)
        shutil.rmtree(tmp)

    def test_autopkgtest_plan(self):
        entries = freshdeb.autopkgtest_plan(
            "Tests: one, two\nRestrictions: needs-root, allow-stderr\nDepends: @, curl\n\n"
            "Test-Command: true\n\nTests: vm\nRestrictions: isolation-machine\n")
        self.assertEqual([e["name"] for e in entries], ["one", "two", "command3", "vm"])
        self.assertTrue(entries[0]["allow_stderr"])
        self.assertEqual(entries[0]["depends"], ["curl"])
        self.assertEqual(entries[2]["command"], "true")
        self.assertEqual(entries[3]["skip"], "isolation-machine")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        for name in ("config.yaml",):
            shutil.copy(ROOT / name, self.repo / name)
        for name in ("packages", "scripts", "builders"):
            shutil.copytree(ROOT / name, self.repo / name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.repo)

    def commit(self):
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "change")

    def edit(self, path, old, new):
        target = self.repo / path
        text = target.read_text()
        self.assertIn(old, text)
        target.write_text(text.replace(old, new, 1))

    def plan(self):
        selected, errors, aliases = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        return {k: v["publish"] for k, v in selected.items()}, errors, aliases

    def test_track_bump_selects_only_that_track(self):
        self.edit("packages/valkey/package.yaml", "revision: 1\n    eol: 2031-05-19", "revision: 2\n    eol: 2031-05-19")
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual(errors, [])
        self.assertEqual(selected, {("valkey", "9.1"): True})

    def test_metadata_change_needs_no_build(self):
        self.edit("packages/valkey/package.yaml", 'homepage: https://valkey.io', 'homepage: https://valkey.io/\nmaintainers: [someone]')
        self.commit()
        selected, errors, aliases = self.plan()
        self.assertEqual((selected, errors, aliases), ({}, [], False))

    def test_packaging_change_requires_bump(self):
        self.edit("packages/valkey/debian/rules", "export BUILD_TLS = yes", "export BUILD_TLS = yes\n# changed")
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual(set(selected), {("valkey", k) for k in ("9.1", "9.0", "8.1", "8.0")})
        self.assertEqual(len(errors), 4)
        self.assertIn("bump revision", errors[0])

    def test_packaging_change_with_bumps(self):
        self.edit("packages/valkey/debian/rules", "export BUILD_TLS = yes", "export BUILD_TLS = yes\n# changed")
        text = (self.repo / "packages/valkey/package.yaml").read_text().replace("revision: 1", "revision: 2")
        (self.repo / "packages/valkey/package.yaml").write_text(text)
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual(errors, [])
        self.assertTrue(all(selected.values()))
        self.assertEqual(len(selected), 4)

    def test_eol_tracks_are_not_built(self):
        self.edit("packages/valkey/debian/rules", "export BUILD_TLS = yes", "export BUILD_TLS = yes\n# changed")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, self.base, "HEAD", dt.date(2027, 12, 1))
        self.assertNotIn(("valkey", "8.0"), selected)

    def test_tests_only_change_is_test_only(self):
        self.edit("packages/valkey/tests/smoke.sh", "set -euo pipefail", "set -euo pipefail\n# changed")
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual(errors, [])
        self.assertEqual(selected, {("valkey", "9.1"): False})

    def test_infrastructure_change_builds_a_canary(self):
        self.edit("scripts/build.sh", "set -euo pipefail", "set -euo pipefail\n# changed")
        self.commit()
        selected, _, _ = self.plan()
        # The newest track of every package, never published.
        expected = {(pkg.name, freshdeb.newest_track([k for k, tr in pkg.tracks.items() if tr.active(TODAY)])): False
                    for pkg in freshdeb.load_packages(self.repo)}
        self.assertEqual(selected, expected)
        self.assertIn(("valkey", "9.1"), expected)

    def test_canary_is_one_job_per_package_without_upstream_tests(self):
        self.edit("scripts/build.sh", "set -euo pipefail", "set -euo pipefail\n# changed")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        matrix = freshdeb.expand_matrix(self.repo, selected, None, TODAY)
        self.assertEqual(len(matrix), len(freshdeb.package_names(self.repo)))
        self.assertTrue(all((j["codename"], j["arch"], j["nocheck"], j["publish"]) == ("bookworm", "amd64", True, False)
                            for j in matrix))

    def test_tests_only_change_covers_every_distro_without_upstream_tests(self):
        self.edit("packages/valkey/tests/smoke.sh", "set -euo pipefail", "set -euo pipefail\n# changed")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        matrix = freshdeb.expand_matrix(self.repo, selected, None, TODAY)
        self.assertEqual(len(matrix), 5 * 2)
        self.assertTrue(all(j["nocheck"] and not j["publish"] for j in matrix))

    def test_releases_run_upstream_tests_even_with_a_canary(self):
        self.edit("packages/valkey/package.yaml", "revision: 1\n    eol: 2031-05-19", "revision: 2\n    eol: 2031-05-19")
        self.edit("scripts/build.sh", "set -euo pipefail", "set -euo pipefail\n# changed")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        matrix = freshdeb.expand_matrix(self.repo, selected, None, TODAY)
        valkey = [j for j in matrix if j["package"] == "valkey"]
        self.assertEqual(len(valkey), 5 * 2)
        self.assertTrue(all(j["publish"] and not j["nocheck"] for j in valkey))

    def test_alias_change_needs_release_but_no_build(self):
        self.edit("packages/valkey/package.yaml", '"8": "8.1"', '"8": "8.0"')
        self.commit()
        selected, errors, aliases = self.plan()
        self.assertEqual((selected, errors, aliases), ({}, [], True))

    def test_new_codename_builds_every_track_there(self):
        self.edit("config.yaml", "  resolute:\n", "  stonking:\n    vendor: ubuntu\n    version: \"28.04\"\n"
                                                  "    image: ubuntu:stonking\n  resolute:\n")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        tracks = {(pkg.name, key) for pkg in freshdeb.load_packages(self.repo)
                  for key, track in pkg.tracks.items() if track.active(TODAY)}
        self.assertEqual(set(selected), tracks)
        self.assertTrue(all(entry["publish"] for entry in selected.values()))
        matrix = freshdeb.expand_matrix(self.repo, selected, None, TODAY)
        self.assertEqual({job["codename"] for job in matrix}, {"stonking"})
        # Architecture: all packages (composer) build once, on the first architecture.
        arch_indep = {pkg.name for pkg in freshdeb.load_packages(self.repo)
                      if all(a == "all" for a in freshdeb.control_binaries(pkg.path / "debian").values())}
        self.assertEqual(len(matrix), sum(1 if name in arch_indep else 2 for name, _ in tracks))

    def test_unreleased_changes_are_planned_again(self):
        # Commit 1 bumps 9.1 but its release failed; commit 2 only touches docs.
        self.edit("packages/valkey/package.yaml", "revision: 1\n    eol: 2031-05-19", "revision: 2\n    eol: 2031-05-19")
        self.commit()
        previous = git(self.repo, "rev-parse", "HEAD").strip()
        self.edit("packages/valkey/README.md", "# valkey", "# valkey\n")
        self.commit()
        selected, _, _ = freshdeb.plan_changes(self.repo, previous, "HEAD", TODAY)
        self.assertEqual(selected, {})
        selected, _, _ = freshdeb.plan_changes(self.repo, previous, "HEAD", TODAY, {"valkey": self.base})
        self.assertEqual({k: v["publish"] for k, v in selected.items()}, {("valkey", "9.1"): True})

    def test_released_bases_from_registry(self):
        registry = self.repo / "released.json"
        registry.write_text(json.dumps({"projects": {"valkey": {"commit": self.base},
                                                     "gone": {"commit": "f" * 40}}}))
        self.assertEqual(freshdeb.released_bases(self.repo, registry, "HEAD"), {"valkey": self.base})
        registry.write_text("not json")
        self.assertEqual(freshdeb.released_bases(self.repo, registry, "HEAD"), {})

    def test_matrix_shape(self):
        matrix = freshdeb.expand_matrix(self.repo, {("valkey", "9.1"): {"publish": True, "codenames": {"*"}}},
                                        None, TODAY)
        self.assertEqual(len(matrix), 5 * 2)
        self.assertEqual({job["runner"] for job in matrix}, {"ubuntu-24.04", "ubuntu-24.04-arm"})


class TarballTests(unittest.TestCase):
    """Release-tarball sources, served from file:// so no network is needed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        tree = self.tmp / "upstream/memcached-1.6.99"
        tree.mkdir(parents=True)
        (tree / "configure").write_text("#!/bin/sh\n")
        self.tarball = self.tmp / "upstream/memcached-1.6.99.tar.gz"
        subprocess.run(["tar", "-czf", str(self.tarball), "-C", str(tree.parent), tree.name], check=True)
        self.sha256 = hashlib.sha256(self.tarball.read_bytes()).hexdigest()
        y = self.tmp / "packages/memcached/package.yaml"
        y.write_text(y.read_text().replace("https://memcached.org/files/", f"file://{self.tmp}/upstream/"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def package(self, sha256):
        pkg = freshdeb.load_package("memcached", self.tmp)
        pkg.tracks["main"].tag, pkg.tracks["main"].sha256 = "1.6.99", sha256
        return pkg

    def test_fetch_verifies_and_unpacks(self):
        orig, dest = self.tmp / "orig.tar.gz", self.tmp / "src"
        pkg = self.package(self.sha256)
        freshdeb.fetch_tarball(pkg, pkg.tracks["main"], orig, dest)
        self.assertTrue((dest / "configure").is_file())
        self.assertEqual(orig.read_bytes(), self.tarball.read_bytes())

    def test_fetch_refuses_wrong_checksum(self):
        pkg = self.package("0" * 64)
        with self.assertRaisesRegex(freshdeb.FreshdebError, "package.yaml pins"):
            freshdeb.fetch_tarball(pkg, pkg.tracks["main"], self.tmp / "orig.tar.gz", self.tmp / "src")
        self.assertFalse((self.tmp / "src").exists())

    def test_watch_pins_the_new_tarball(self):
        if not shutil.which("yq"):
            self.skipTest("yq not available")
        freshdeb.apply_change(self.tmp, {"package": "memcached", "kind": "bump", "track": "main",
                                         "tag": "1.6.99", "commit": "c" * 40})
        track = freshdeb.load_package("memcached", self.tmp).tracks["main"]
        self.assertEqual((track.tag, track.commit, track.sha256, track.revision),
                         ("1.6.99", "c" * 40, self.sha256, 1))


class PrepareHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.pkg = freshdeb.load_package("neovim", self.tmp)
        self.src = self.tmp / "src"
        self.src.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def hook(self, body):
        path = self.pkg.path / "hooks/test-hook.sh"
        path.write_text(body)
        return "hooks/test-hook.sh"

    def test_hook_runs_in_the_source_tree(self):
        hook = self.hook('set -e\necho "$FRESHDEB_PACKAGE $FRESHDEB_UPSTREAM_VERSION" > fetched.txt\n')
        freshdeb.run_prepare_hook(self.pkg, self.pkg.tracks["main"], self.src, hook)
        self.assertEqual((self.src / "fetched.txt").read_text().strip(), "neovim 0.12.6")

    def test_failing_hook_stops_the_build(self):
        hook = self.hook('echo "checksum mismatch" >&2\nexit 1\n')
        with self.assertRaisesRegex(freshdeb.FreshdebError, "checksum mismatch"):
            freshdeb.run_prepare_hook(self.pkg, self.pkg.tracks["main"], self.src, hook)

    def test_missing_hook_is_a_validation_error(self):
        y = self.pkg.path / "package.yaml"
        y.write_text(y.read_text().replace("hooks/prefetch-deps.py", "hooks/nope.py"))
        self.assertTrue(any("build.prepare" in e for e in freshdeb.validate_all(self.tmp, TODAY)))


class GoTests(unittest.TestCase):
    """Go mode: pinned toolchains, and watch.yml keeping them current."""

    RELEASES = {
        "1.25.14": {"amd64": "a" * 64, "arm64": "b" * 64},
        "1.25.15": {"amd64": "c" * 64, "arm64": "d" * 64},
        "1.26.3": {"amd64": "e" * 64, "arm64": "f" * 64},
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.pkg = freshdeb.load_package("restic", self.tmp)
        self.original = freshdeb.required_go_minor

    def tearDown(self):
        freshdeb.required_go_minor = self.original
        shutil.rmtree(self.tmp)

    def test_track_needs_a_pinned_toolchain(self):
        y = self.tmp / "packages/restic/package.yaml"
        y.write_text(y.read_text().replace('go: "1.25.14"', 'go: "1.25.99"'))
        self.assertTrue(any("not in config.yaml toolchains.go" in e for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_toolchain_needs_every_architecture(self):
        c = self.tmp / "config.yaml"
        c.write_text(re.sub(r"\n      arm64: [0-9a-f]{64}", "", c.read_text(), count=1))
        self.assertTrue(any("sha256 for every architecture" in e for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_go_patch_release_proposes_a_rebuild(self):
        extra = freshdeb.watch_go(self.pkg, [], self.RELEASES)
        self.assertEqual(extra, [{"package": "restic", "kind": "toolchain", "track": "main", "from": "1.25.14",
                                  "go": "1.25.15", "go_sha256": self.RELEASES["1.25.15"]}])

    def test_newest_patch_needs_no_rebuild(self):
        releases = {k: v for k, v in self.RELEASES.items() if k != "1.25.15"}
        self.assertEqual(freshdeb.watch_go(self.pkg, [], releases), [])

    def test_bump_follows_the_go_minor_upstream_requires(self):
        freshdeb.required_go_minor = lambda url, tag: "1.26"
        change = {"package": "restic", "kind": "bump", "track": "main", "tag": "v0.20.0", "commit": "c" * 40}
        self.assertEqual(freshdeb.watch_go(self.pkg, [change], self.RELEASES), [])
        self.assertEqual(change["go"], "1.26.3")

    def test_bump_keeps_the_minor_but_takes_its_newest_patch(self):
        freshdeb.required_go_minor = lambda url, tag: "1.24"
        change = {"package": "restic", "kind": "bump", "track": "main", "tag": "v0.19.2", "commit": "c" * 40}
        freshdeb.watch_go(self.pkg, [change], self.RELEASES)
        self.assertEqual(change["go"], "1.25.15")

    def test_apply_toolchain_rebuild(self):
        if not shutil.which("yq"):
            self.skipTest("yq not available")
        freshdeb.apply_change(self.tmp, {"package": "restic", "kind": "toolchain", "track": "main",
                                         "go": "1.25.15", "go_sha256": self.RELEASES["1.25.15"]})
        track = freshdeb.load_package("restic", self.tmp).tracks["main"]
        self.assertEqual((track.go, track.revision), ("1.25.15", 2))
        config = freshdeb.load_config(self.tmp)
        self.assertEqual(config.toolchains["go"]["1.25.15"], self.RELEASES["1.25.15"])
        self.assertIn("1.25.14", config.toolchains["go"])
        self.assertIn("# toolchains:", (self.tmp / "config.yaml").read_text())
        self.assertEqual(freshdeb.validate_all(self.tmp, TODAY), [])

    def test_install_verifies_the_toolchain(self):
        tree = self.tmp / "dl/go/bin"
        tree.mkdir(parents=True)
        (tree / "go").write_text("#!/bin/sh\necho go1.25.99\n")
        (tree / "go").chmod(0o755)
        archive = self.tmp / "dl/go1.25.99.linux-amd64.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(self.tmp / "dl"), "go"], check=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        config = freshdeb.load_config(self.tmp)
        original = freshdeb.GO_DOWNLOAD
        freshdeb.GO_DOWNLOAD = f"file://{self.tmp}/dl/go{{version}}.linux-{{arch}}.tar.gz"
        try:
            config.toolchains["go"]["1.25.99"] = {"amd64": "0" * 64}
            with self.assertRaisesRegex(freshdeb.FreshdebError, "config.yaml pins"):
                freshdeb.install_go(config, "1.25.99", "amd64", self.tmp / "bad")
            config.toolchains["go"]["1.25.99"] = {"amd64": digest}
            goroot = freshdeb.install_go(config, "1.25.99", "amd64", self.tmp / "good")
            self.assertTrue((goroot / "bin/go").is_file())
        finally:
            freshdeb.GO_DOWNLOAD = original

    def test_build_environment_is_offline(self):
        env = freshdeb.go_env(Path("/opt/go"), Path("/build/cache"), offline=True)
        self.assertEqual((env["GOFLAGS"], env["GOPROXY"], env["GOTOOLCHAIN"], env["GOWORK"]),
                         ("-mod=vendor", "off", "local", "off"))
        self.assertTrue(env["PATH"].startswith("/opt/go/bin:"))
        self.assertNotIn("GOPROXY", freshdeb.go_env(Path("/opt/go"), Path("/c"), offline=False))


class LineTrackTests(unittest.TestCase):
    """Named tracks that stay on one release line (prometheus "lts")."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.yaml = self.tmp / "packages/prometheus/package.yaml"
        self.pkg = freshdeb.load_package("prometheus", self.tmp)
        self.tags = {t.tag: t.commit for t in self.pkg.tracks.values()}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_lts_gets_only_its_line(self):
        tags = {**self.tags, "v3.13.6": "a" * 40, "v3.16.0": "b" * 40, "v3.16.0-rc.0": "c" * 40}
        changes = freshdeb.watch_package(self.pkg, tags, TODAY)
        self.assertEqual(sorted((c["track"], c["tag"]) for c in changes), [("lts", "v3.13.6"), ("main", "v3.16.0")])

    def test_main_follows_every_release(self):
        changes = freshdeb.watch_package(self.pkg, {**self.tags, "v4.0.0": "a" * 40}, TODAY)
        self.assertEqual([(c["track"], c["tag"]) for c in changes], [("main", "v4.0.0")])

    def test_tag_outside_the_line(self):
        self.yaml.write_text(self.yaml.read_text().replace('tag: "v3.13.5"', 'tag: "v3.14.0"'))
        self.assertTrue(any("does not belong to track lts (line 3.13)" in e
                            for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_line_is_only_for_named_tracks(self):
        y = self.tmp / "packages/valkey/package.yaml"
        y.write_text(y.read_text().replace('    tag: "9.1.3"', '    line: "9.1"\n    tag: "9.1.3"'))
        self.assertTrue(any("line is only for named tracks" in e for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_moving_the_line_is_a_track_change(self):
        old = self.pkg.tracks["lts"]
        moved = freshdeb.parse_package("prometheus", self.pkg.path, yaml_replace(self.yaml, '"3.13"', '"3.15"')).tracks["lts"]
        self.assertNotEqual(freshdeb.track_fields(old), freshdeb.track_fields(moved))


def yaml_replace(path, old, new):
    return freshdeb.yaml.safe_load(path.read_text().replace(old, new))


class AssetTests(unittest.TestCase):
    """Pinned release assets (prometheus' web UI), served from file://."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        releases = self.tmp / "releases"
        releases.mkdir()
        self.asset = releases / "prometheus-web-ui-3.99.0.tar.gz"
        self.asset.write_bytes(b"built web ui")
        self.sha256 = hashlib.sha256(self.asset.read_bytes()).hexdigest()
        self.yaml = self.tmp / "packages/prometheus/package.yaml"
        self.yaml.write_text(re.sub(r"https://github.com/prometheus/prometheus/releases/download/v\{version\}/",
                                    f"file://{releases}/", self.yaml.read_text()))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def track(self, sha256):
        pkg = freshdeb.load_package("prometheus", self.tmp)
        track = pkg.tracks["main"]
        track.tag, track.assets = "v3.99.0", {"web-ui": sha256}
        return pkg, track

    def test_fetch_verifies_into_the_source_tree(self):
        pkg, track = self.track(self.sha256)
        src = self.tmp / "src"
        fetched = freshdeb.fetch_assets(pkg, track, src)
        self.assertEqual((src / "freshdeb-assets/web-ui.tar.gz").read_bytes(), b"built web ui")
        self.assertEqual(fetched[0]["sha256"], self.sha256)

    def test_fetch_refuses_wrong_checksum(self):
        pkg, track = self.track("0" * 64)
        with self.assertRaisesRegex(freshdeb.FreshdebError, "package.yaml pins"):
            freshdeb.fetch_assets(pkg, track, self.tmp / "src")

    def test_every_track_pins_every_asset(self):
        self.yaml.write_text(self.yaml.read_text().replace(
            "      web-ui: c84c2c7eed84e184309cb5a07effade15a30ff767770eb9474e6fb4688ee02e1\n", "      other: " + "a" * 64 + "\n"))
        self.assertTrue(any("track lts: assets needs the sha256 of every upstream asset (web-ui)" in e
                            for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_assets_without_upstream_assets_are_rejected(self):
        y = self.tmp / "packages/restic/package.yaml"
        y.write_text(y.read_text().replace("    revision: 1\n", "    revision: 1\n    assets: {web-ui: " + "a" * 64 + "}\n"))
        self.assertTrue(any("only used with upstream.assets" in e for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_changelog_records_the_asset(self):
        config = freshdeb.load_config(self.tmp)
        pkg = freshdeb.load_package("prometheus", self.tmp)
        entry = freshdeb.changelog_entry(pkg, config, pkg.tracks["main"], "trixie", "3.15.1-1+freshdeb~deb13", 0)
        self.assertIn(f"Release asset web-ui: file://{self.tmp}/releases/prometheus-web-ui-3.15.1.tar.gz", entry)
        self.assertIn("(commit bae3eced0a5369e050e83ea73bc381d7c4b1a719).", entry)

    def test_watch_pins_the_new_assets(self):
        if not shutil.which("yq"):
            self.skipTest("yq not available")
        freshdeb.apply_change(self.tmp, {"package": "prometheus", "kind": "bump", "track": "main",
                                         "tag": "v3.99.0", "commit": "c" * 40, "go": "1.27.3",
                                         "go_sha256": freshdeb.load_config(self.tmp).toolchains["go"]["1.27.3"]})
        track = freshdeb.load_package("prometheus", self.tmp).tracks["main"]
        self.assertEqual((track.tag, track.revision, track.assets), ("v3.99.0", 1, {"web-ui": self.sha256}))
        self.assertIn("# The built web UI", self.yaml.read_text())
        # Apart from this test's file:// URL.
        errors = [e for e in freshdeb.validate_all(self.tmp, TODAY) if "must be an https:// URL" not in e]
        self.assertEqual(errors, [])


class WatchReportTests(unittest.TestCase):
    """The watch log: ours, upstream and the decision per track."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def report(self, pkg, extra_tags, go=None, today=TODAY):
        tags = {**{t.tag: t.commit for t in pkg.tracks.values()}, **extra_tags}
        changes = freshdeb.watch_package(pkg, tags, today)
        if go:
            changes += freshdeb.watch_go(pkg, changes, go)
        return freshdeb.watch_report(pkg, tags, changes, go, today)

    @staticmethod
    def next_patch(version):
        *head, last = version.split(".")
        return ".".join(head + [str(int(last) + 1)])

    def test_update_and_up_to_date(self):
        pkg = freshdeb.load_package("prometheus", self.tmp)
        main, lts = pkg.tracks["main"], pkg.tracks["lts"]
        ours = pkg.upstream_version(main.tag)
        newer = self.next_patch(ours)
        lines = self.report(pkg, {f"v{newer}": "a" * 40})
        self.assertIn(f"  prometheus main: ours {ours}, upstream {newer} -> update to v{newer}", lines)
        lts_ours = pkg.upstream_version(lts.tag)
        self.assertIn(f"  prometheus lts (line {lts.line}): ours {lts_ours}, upstream {lts_ours} -> up to date", lines)
        self.assertIn(f"  prometheus lts (line {lts.line}): Go {lts.go}, newest {freshdeb.go_minor(lts.go)}.x - "
                      "-> not checked", lines)

    def test_go_rebuild(self):
        pkg = freshdeb.load_package("prometheus", self.tmp)
        main, lts = pkg.tracks["main"], pkg.tracks["lts"]
        newer = self.next_patch(lts.go)
        go = {v: {"amd64": "a" * 64, "arm64": "b" * 64} for v in (lts.go, newer, main.go)}
        lines = self.report(pkg, {}, go=go)
        label = f"prometheus lts (line {lts.line})"
        self.assertIn(f"  {label}: Go {lts.go}, newest {freshdeb.go_minor(lts.go)}.x {newer} "
                      f"-> rebuild with Go {newer}", lines)
        self.assertIn(f"  prometheus main: Go {main.go}, newest {freshdeb.go_minor(main.go)}.x {main.go} "
                      "-> up to date", lines)

    def test_end_of_life(self):
        pkg = freshdeb.load_package("valkey", self.tmp)
        key, track = min(pkg.tracks.items(), key=lambda item: item[1].eol)
        lines = self.report(pkg, {}, today=track.eol + dt.timedelta(days=1))
        self.assertIn(f"  valkey {key}: ours {pkg.upstream_version(track.tag)}, end of life since {track.eol} "
                      "-> skipped", lines)

    def test_new_release_line(self):
        pkg = freshdeb.load_package("valkey", self.tmp)
        newest = max(pkg.tracks, key=freshdeb._VersionKey)
        major, minor = newest.split(".")
        line = f"{major}.{int(minor) + 1}"
        lines = self.report(pkg, {f"{line}.0": "a" * 40})
        self.assertIn(f"  valkey: new release line {line} ({line}.0) -> add track {line} based on {newest}, "
                      f"alias {major} -> {line}", lines)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.pkg = freshdeb.load_package("valkey", ROOT)
        self.tags = {self.pkg.tracks[k].tag: self.pkg.tracks[k].commit for k in self.pkg.tracks}

    def test_nothing_new(self):
        self.assertEqual(freshdeb.watch_package(self.pkg, self.tags, TODAY), [])

    def test_patch_release_bumps_its_track(self):
        tags = {**self.tags, "9.1.4": "a" * 40, "9.1.5-rc1": "b" * 40, "9.0.6": "c" * 40}
        changes = freshdeb.watch_package(self.pkg, tags, TODAY)
        self.assertEqual(changes, [{"package": "valkey", "kind": "bump", "track": "9.1", "from": "9.1.3",
                                    "tag": "9.1.4", "commit": "a" * 40}])

    def test_new_minor_becomes_a_track_and_moves_the_alias(self):
        tags = {**self.tags, "9.2.0-rc1": "a" * 40, "9.2.0": "b" * 40, "9.2.1": "c" * 40}
        changes = freshdeb.watch_package(self.pkg, tags, TODAY)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["kind"], "new-track")
        self.assertEqual((changes[0]["track"], changes[0]["tag"]), ("9.2", "9.2.1"))
        self.assertEqual(changes[0]["aliases"], {"9": "9.2"})

    def test_new_major_gets_its_own_alias(self):
        changes = freshdeb.watch_package(self.pkg, {**self.tags, "10.0.0": "d" * 40}, TODAY)
        self.assertEqual(changes[0]["track"], "10.0")
        self.assertEqual(changes[0]["aliases"], {"10": "10.0"})

    def test_release_candidates_are_ignored(self):
        self.assertEqual(freshdeb.watch_package(self.pkg, {**self.tags, "9.2.0-rc1": "a" * 40}, TODAY), [])

    def test_apply_keeps_comments(self):
        if not shutil.which("yq"):
            self.skipTest("yq not available")
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", tmp / "packages")
        freshdeb.apply_change(tmp, {"package": "valkey", "kind": "bump", "track": "9.1", "tag": "9.1.4",
                                    "commit": "a" * 40})
        freshdeb.apply_change(tmp, {"package": "valkey", "kind": "new-track", "track": "9.2", "tag": "9.2.0",
                                    "commit": "b" * 40, "based_on": "9.1", "aliases": {"9": "9.2"}})
        text = (tmp / "packages/valkey/package.yaml").read_text()
        self.assertIn("# Valkey: https://valkey.io", text)
        pkg = freshdeb.load_package("valkey", tmp)
        self.assertEqual(list(pkg.tracks)[:2], ["9.2", "9.1"])
        self.assertEqual((pkg.tracks["9.1"].tag, pkg.tracks["9.1"].revision), ("9.1.4", 1))
        self.assertEqual(pkg.tracks["9.2"].patches, "patches/9.2")
        self.assertTrue((tmp / "packages/valkey/patches/9.2/series").is_file())
        self.assertEqual(pkg.aliases["9"], "9.2")
        self.assertEqual(freshdeb.validate_all(tmp, TODAY), [], text)
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()

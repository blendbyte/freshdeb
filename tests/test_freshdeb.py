"""Unit tests for scripts/freshdeb.py: versions, validation, planning, watch.

Run in tests/release.Dockerfile (needs git, dpkg and yq):
    docker run --rm -v "$PWD:/workspace:ro" freshdeb-tests python3 -m unittest tests.test_freshdeb
"""

import contextlib
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
        self.replace("\n    eol: 2031-05-19", "\n    sha256: " + "a" * 64 + "\n    eol: 2031-05-19")
        self.assertTrue(any("only used with upstream.tarball" in e for e in self.errors()))

    def test_tarball_url_needs_version_placeholder(self):
        y = self.tmp / "packages/memcached/package.yaml"
        y.write_text(y.read_text().replace("memcached-{version}.tar.gz", "memcached-latest.tar.gz"))
        self.assertTrue(any("upstream.tarball must be" in e for e in self.errors()))

    def test_missing_overlay(self):
        self.replace("overlays/legacy-sysusers.patch", "overlays/missing.patch")
        self.assertTrue(any("does not exist" in e for e in self.errors()))

    def test_test_capabilities(self):
        text = self.yaml.read_text()
        self.yaml.write_text(text + "\ntests:\n  capabilities: [NET_ADMIN]\n")
        self.assertEqual([e for e in self.errors() if e.startswith("valkey:")], [])
        self.yaml.write_text(text + "\ntests:\n  capabilities: [SYS_ADMIN]\n")
        self.assertTrue(any("tests.capabilities must be a list of" in e for e in self.errors()))
        self.yaml.write_text(text + "\ntests:\n  privileged: true\n")
        self.assertTrue(any("tests only takes capabilities" in e for e in self.errors()))

    UPGRADE_FROM = ("\ntests:\n  upgrade_from:\n    url: https://example.org/demo_1.0_linux_{arch}.deb\n"
                    "    sha256: {amd64: " + "a" * 64 + ", arm64: " + "b" * 64 + "}\n")

    def test_test_upgrade_from(self):
        text = self.yaml.read_text()
        self.yaml.write_text(text + self.UPGRADE_FROM)
        self.assertEqual([e for e in self.errors() if e.startswith("valkey:")], [])
        pkg = freshdeb.load_package("valkey", self.tmp)
        self.assertEqual(freshdeb.test_upgrade_from(pkg, "arm64"),
                         {"url": "https://example.org/demo_1.0_linux_arm64.deb", "sha256": "b" * 64})
        self.yaml.write_text(text + self.UPGRADE_FROM.replace("_{arch}", ""))
        self.assertTrue(any("{arch} placeholder" in e for e in self.errors()))
        self.yaml.write_text(text + self.UPGRADE_FROM.replace(", arm64: " + "b" * 64, ""))
        self.assertTrue(any("sha256 for every architecture" in e for e in self.errors()))
        self.yaml.write_text(text + self.UPGRADE_FROM + "    version: 1.0\n")
        self.assertTrue(any("needs url and sha256" in e for e in self.errors()))

    def test_no_upgrade_from_in_build_info(self):
        self.assertIsNone(freshdeb.test_upgrade_from(freshdeb.load_package("valkey", self.tmp), "amd64"))


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

    def test_rewrite_renames_the_source(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "control").write_text(self.CONTROL.replace("Source: demo", "Source: demo-1.2"))
        freshdeb.rewrite_control(tmp, "freshdeb <packages@freshdeb.com>", source_name="demo")
        self.assertEqual(freshdeb.control_source(tmp), "demo")
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

    def bump_valkey(self, key):
        """Bump one valkey track's revision, whatever it is now."""
        target = self.repo / "packages/valkey/package.yaml"
        text, count = re.subn(rf'(\n  "{re.escape(key)}":\n(?:    .*\n)*?    revision: )(\d+)',
                              lambda m: f"{m.group(1)}{int(m.group(2)) + 1}", target.read_text(), count=1)
        self.assertEqual(count, 1)
        target.write_text(text)

    def edit(self, path, old, new):
        target = self.repo / path
        text = target.read_text()
        self.assertIn(old, text)
        target.write_text(text.replace(old, new, 1))

    def plan(self):
        selected, errors, aliases = freshdeb.plan_changes(self.repo, self.base, "HEAD", TODAY)
        return {k: v["publish"] for k, v in selected.items()}, errors, aliases

    def test_track_bump_selects_only_that_track(self):
        self.bump_valkey("9.1")
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
        text = re.sub(r"revision: (\d+)", lambda m: f"revision: {int(m.group(1)) + 1}",
                      (self.repo / "packages/valkey/package.yaml").read_text())
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
        self.bump_valkey("9.1")
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
        # Packages that list their codenames (postsrsd skips jammy) don't build on new ones.
        config = freshdeb.load_config(self.repo)
        building = {(pkg.name, key) for pkg in freshdeb.load_packages(self.repo)
                    for key, track in pkg.tracks.items()
                    if (pkg.name, key) in tracks and "stonking" in pkg.codenames(config, track)}
        self.assertTrue(all(entry["publish"] for entry in selected.values()))
        matrix = freshdeb.expand_matrix(self.repo, selected, None, TODAY)
        self.assertEqual({job["codename"] for job in matrix}, {"stonking"})
        # Architecture: all packages (composer) build once, on the first architecture.
        arch_indep = {pkg.name for pkg in freshdeb.load_packages(self.repo)
                      if all(a == "all" for a in freshdeb.control_binaries(pkg.path / "debian").values())}
        self.assertEqual(len(matrix), sum(1 if name in arch_indep else 2 for name, _ in building))

    def test_unreleased_changes_are_planned_again(self):
        # Commit 1 bumps 9.1 but its release failed; commit 2 only touches docs.
        self.bump_valkey("9.1")
        self.commit()
        previous = git(self.repo, "rev-parse", "HEAD").strip()
        (self.repo / "packages/valkey/NOTES.md").write_text("Docs only.\n")
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


class GoBootstrapTests(unittest.TestCase):
    """build.go: bootstrap (Go itself) and packaging.source (Debian's per-minor
    source names), on a copy of restic."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.yaml = self.tmp / "packages/restic/package.yaml"
        self.yaml.write_text(self.yaml.read_text() + "\nbuild:\n  go: bootstrap\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def errors(self):
        return [e for e in freshdeb.validate_all(self.tmp, TODAY) if e.startswith("restic:")]

    def test_bootstrap_package_is_valid(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(freshdeb.load_package("restic", self.tmp).go_mode, "bootstrap")

    def test_bootstrap_needs_a_toolchain(self):
        self.yaml.write_text(re.sub(r'\n    go: "[^"]+"[^\n]*', "", self.yaml.read_text()))
        self.assertTrue(any("needs a go: toolchain" in e for e in self.errors()))

    def test_unknown_go_mode(self):
        self.yaml.write_text(self.yaml.read_text().replace("go: bootstrap", "go: magic"))
        self.assertTrue(any("build.go must be" in e for e in self.errors()))

    def test_bootstrap_environment(self):
        env = freshdeb.go_bootstrap_env(Path("/opt/go"))
        self.assertEqual(env["GOROOT_BOOTSTRAP"], "/opt/go")
        self.assertEqual((env["GOTOOLCHAIN"], env["GOPROXY"]), ("local", "off"))
        self.assertNotIn("GOFLAGS", env)  # nothing vendored

    def test_changelog_says_bootstrapped(self):
        pkg = freshdeb.load_package("restic", self.tmp)
        config = freshdeb.load_config(self.tmp)
        track = pkg.tracks["main"]
        entry = freshdeb.changelog_entry(pkg, config, track, "trixie", "1-1", 0)
        self.assertIn(f"Bootstrapped with Go {track.go} from go.dev.", entry)
        self.assertNotIn("vendored", entry)

    def test_watch_keeps_the_bootstrap_toolchain(self):
        pkg = freshdeb.load_package("restic", self.tmp)
        tags = {t.tag: t.commit for t in pkg.tracks.values()}
        go = {self.next_patch(pkg.tracks["main"].go): {"amd64": "a" * 64, "arm64": "b" * 64}}
        lines = freshdeb.watch_report(pkg, tags, [], go, TODAY)
        self.assertIn(f"  restic main: bootstrap Go {pkg.tracks['main'].go} -> pinned (Go rebuilds itself with it)",
                      lines)
        self.assertFalse(any("rebuild with" in line for line in lines))

    @staticmethod
    def next_patch(version):
        *head, last = version.split(".")
        return ".".join(head + [str(int(last) + 1)])

    def test_source_name_override(self):
        control = self.tmp / "packages/restic/debian/control"
        control.write_text(control.read_text().replace("Source: restic", "Source: restic-0.19", 1))
        self.assertTrue(any("Source must equal the package name" in e for e in self.errors()))
        self.yaml.write_text(self.yaml.read_text().replace("packaging:\n", "packaging:\n  source: restic-0.19\n", 1))
        self.assertEqual(self.errors(), [])
        self.assertEqual(freshdeb.load_package("restic", self.tmp).source_name, "restic-0.19")
        self.yaml.write_text(self.yaml.read_text().replace("source: restic-0.19", "source: restic-0.18"))
        self.assertTrue(any("Source must equal packaging.source (restic-0.18)" in e for e in self.errors()))

    def test_source_name_must_differ(self):
        self.yaml.write_text(self.yaml.read_text().replace("packaging:\n", "packaging:\n  source: restic\n", 1))
        self.assertTrue(any("packaging.source must be" in e for e in self.errors()))


class OwnPackagingTests(unittest.TestCase):
    """packaging.origin: freshdeb, debian/ maintained here (frp), on a copy of
    restic."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.yaml = self.tmp / "packages/restic/package.yaml"
        text = self.yaml.read_text()
        self.imported = text
        self.yaml.write_text(re.sub(r"\npackaging:\n(  .*\n)+", "\npackaging:\n  origin: freshdeb\n", text))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def errors(self):
        return [e for e in freshdeb.validate_all(self.tmp, TODAY) if e.startswith("restic:")]

    def test_own_packaging_needs_no_ref(self):
        self.assertEqual(self.errors(), [])

    def test_ref_is_only_for_imports(self):
        self.yaml.write_text(self.yaml.read_text().replace("  origin: freshdeb\n",
                                                           "  origin: freshdeb\n  ref: " + "a" * 40 + "\n"))
        self.assertTrue(any("packaging.ref is only for imported packaging" in e for e in self.errors()))

    def test_imported_packaging_still_needs_a_ref(self):
        self.yaml.write_text(re.sub(r"\n  ref: [^\n]*", "", self.imported))
        self.assertTrue(any("packaging.ref must be" in e for e in self.errors()))

    def test_changelog_says_maintained_here(self):
        pkg = freshdeb.load_package("restic", self.tmp)
        config = freshdeb.load_config(self.tmp)
        entry = freshdeb.changelog_entry(pkg, config, pkg.tracks["main"], "trixie", "1-1", 0)
        self.assertIn("Packaging: maintained in freshdeb (packages/restic/debian),", entry)


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
        # Known values, whatever restic currently pins: these tests are about
        # the mechanics, with RELEASES above.
        y = self.tmp / "packages/restic/package.yaml"
        text = re.sub(r'^(    go: )"[^"]+"', r'\1"1.25.14"', y.read_text(), count=1, flags=re.M)
        y.write_text(re.sub(r"^(    revision: )\d+", r"\g<1>1", text, count=1, flags=re.M))
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

    def test_unsupported_go_minor_moves_to_the_oldest_supported(self):
        releases = {**self.RELEASES, "1.27.1": {"amd64": "1" * 64, "arm64": "2" * 64}}
        extra = freshdeb.watch_go(self.pkg, [], releases)
        self.assertEqual(len(extra), 1)
        self.assertEqual((extra[0]["kind"], extra[0]["from"], extra[0]["go"]), ("toolchain", "1.25.14", "1.26.3"))
        self.assertIn("no longer supported", extra[0]["reason"])

    def test_supported_go_minors_are_the_two_newest(self):
        releases = {v: {} for v in ("1.24.9", "1.25.14", "1.26.3", "1.27.1")}
        self.assertEqual(freshdeb.supported_go_minors(releases), {"1.26", "1.27"})

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
        y.write_text(re.sub(r"^(    revision: \d+\n)", r"\1    assets: {web-ui: " + "a" * 64 + "}\n",
                            y.read_text(), count=1, flags=re.M))
        self.assertTrue(any("only used with upstream.assets" in e for e in freshdeb.validate_all(self.tmp, TODAY)))

    def test_changelog_records_the_asset(self):
        config = freshdeb.load_config(self.tmp)
        pkg = freshdeb.load_package("prometheus", self.tmp)
        track = pkg.tracks["main"]
        version = pkg.upstream_version(track.tag)
        entry = freshdeb.changelog_entry(pkg, config, track, "trixie", f"{version}-1+freshdeb~deb13", 0)
        self.assertIn(f"Release asset web-ui: file://{self.tmp}/releases/prometheus-web-ui-{version}.tar.gz", entry)
        self.assertIn(f"(commit {track.commit}).", entry)

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


class NetworkRetryTests(unittest.TestCase):
    """Upstream servers fail now and then (a git host's HTTP 502)."""

    def setUp(self):
        self.original = (freshdeb.run, freshdeb.NETWORK_RETRY_DELAY, freshdeb.urllib.request.urlopen)
        freshdeb.NETWORK_RETRY_DELAY = 0

    def tearDown(self):
        freshdeb.run, freshdeb.NETWORK_RETRY_DELAY, freshdeb.urllib.request.urlopen = self.original

    def test_git_is_retried(self):
        calls = []

        def flaky(*args, cwd=None, check=True):
            calls.append(args)
            if len(calls) < 3:
                raise freshdeb.FreshdebError("command failed (128): git ls-remote\nHTTP 502")
            return "ok"

        freshdeb.run = flaky
        self.assertEqual(freshdeb.run_network("git", "ls-remote", "https://example.invalid/x.git"), "ok")
        self.assertEqual(len(calls), 3)

    def test_git_gives_up(self):
        def broken(*args, cwd=None, check=True):
            raise freshdeb.FreshdebError("command failed (128): git ls-remote")

        freshdeb.run = broken
        with self.assertRaises(freshdeb.FreshdebError):
            freshdeb.run_network("git", "ls-remote", "https://example.invalid/x.git")

    def test_checksum_mismatch_is_downloaded_again(self):
        digests = iter(["0" * 64, "a" * 64])
        freshdeb_download = freshdeb.download
        try:
            freshdeb.download = lambda url, dest: next(digests)
            self.assertEqual(freshdeb.download_verified("https://example.invalid/go.tgz", Path("/x"), "a" * 64),
                             "a" * 64)
        finally:
            freshdeb.download = freshdeb_download

    def test_persistent_mismatch_is_returned(self):
        freshdeb_download = freshdeb.download
        try:
            calls = []
            freshdeb.download = lambda url, dest: calls.append(url) or "0" * 64
            self.assertEqual(freshdeb.download_verified("https://example.invalid/go.tgz", Path("/x"), "a" * 64),
                             "0" * 64)
            self.assertEqual(len(calls), freshdeb.NETWORK_ATTEMPTS)
        finally:
            freshdeb.download = freshdeb_download

    def test_download_does_not_retry_a_404(self):
        calls = []

        def not_found(request, timeout):
            calls.append(request)
            raise freshdeb.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        freshdeb.urllib.request.urlopen = not_found
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(freshdeb.urllib.error.HTTPError):
            freshdeb.download("https://example.invalid/x.tar.gz", Path(tmp) / "x")
        self.assertEqual(len(calls), 1)


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


class LatestAliasTests(unittest.TestCase):
    """The latest alias follows the newest track (golang, headscale)."""

    def test_new_track_moves_latest(self):
        pkg = freshdeb.load_package("golang", ROOT)
        newest = max(pkg.tracks, key=freshdeb._VersionKey)
        major, minor = newest.split(".")
        line = f"{major}.{int(minor) + 1}"
        tags = {**{t.tag: t.commit for t in pkg.tracks.values()}, f"go{line}.0": "a" * 40}
        changes = freshdeb.watch_package(pkg, tags, TODAY)
        self.assertEqual([(c["kind"], c["track"], c["aliases"]) for c in changes],
                         [("new-track", line, {"latest": line})])

    def test_numeric_aliases_unchanged(self):
        pkg = freshdeb.load_package("valkey", ROOT)
        self.assertNotIn("latest", pkg.aliases)
        tags = {**{t.tag: t.commit for t in pkg.tracks.values()}, "9.2.0": "a" * 40}
        changes = freshdeb.watch_package(pkg, tags, TODAY)
        self.assertEqual(changes[0]["aliases"], {"9": "9.2"})


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
        header = (ROOT / "packages/valkey/package.yaml").read_text().splitlines()[0]
        self.assertTrue(header.startswith("#"))
        self.assertIn(header, text)
        pkg = freshdeb.load_package("valkey", tmp)
        self.assertEqual(list(pkg.tracks)[:2], ["9.2", "9.1"])
        self.assertEqual((pkg.tracks["9.1"].tag, pkg.tracks["9.1"].revision), ("9.1.4", 1))
        self.assertEqual(pkg.tracks["9.2"].patches, "patches/9.2")
        self.assertTrue((tmp / "packages/valkey/patches/9.2/series").is_file())
        self.assertEqual(pkg.aliases["9"], "9.2")
        self.assertEqual(freshdeb.validate_all(tmp, TODAY), [], text)
        shutil.rmtree(tmp)


class CompanionTests(unittest.TestCase):
    """Companion source packages: proftpd's modules, built against it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.yaml", self.tmp / "config.yaml")
        shutil.copytree(ROOT / "packages", self.tmp / "packages")
        self.yaml = self.tmp / "packages/proftpd/package.yaml"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def replace(self, path, old, new):
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def errors(self):
        return [e for e in freshdeb.validate_all(self.tmp, TODAY) if e.startswith("proftpd")]

    def test_versions_fold_in_the_package_version(self):
        config = freshdeb.load_config(ROOT)
        pkg = freshdeb.load_package("proftpd", ROOT)
        version = pkg.version(config, "main", "trixie")
        vroot = pkg.companion_version(config, "main", "trixie", "proftpd-mod-vroot")
        self.assertEqual(vroot, f"0.9.12+{version}")
        self.assertTrue(vroot.endswith(f"+{version}"))
        # Above the distributions' module packages, rc versions included.
        self.assertGreater(freshdeb.compare_versions(vroot, "0.9.12-2"), 0)
        clamav = pkg.companion_version(config, "main", "trixie", "proftpd-mod-clamav")
        self.assertGreater(freshdeb.compare_versions(clamav, "0.14~rc2-5"), 0)

    def test_every_companion_is_pinned_and_builds_everywhere(self):
        pkg = freshdeb.load_package("proftpd", ROOT)
        self.assertEqual(set(pkg.tracks["main"].companions), set(pkg.companions))
        names = [c.name for c, _ in pkg.companions_for("main", "jammy")]
        self.assertEqual(names, sorted(pkg.companions))

    def test_codenames_limit_where_a_companion_builds(self):
        self.replace(self.yaml, "  proftpd-mod-vroot:\n    upstream:", "  proftpd-mod-vroot:\n    codenames: [trixie]\n    upstream:")
        pkg = freshdeb.load_package("proftpd", self.tmp)
        self.assertIn("proftpd-mod-vroot", [c.name for c, _ in pkg.companions_for("main", "trixie")])
        self.assertNotIn("proftpd-mod-vroot", [c.name for c, _ in pkg.companions_for("main", "jammy")])
        self.assertEqual(self.errors(), [])

    def test_pin_without_companion(self):
        self.replace(self.yaml, "      proftpd-mod-vroot: {tag:", "      proftpd-mod-nope: {tag: \"v1.0\", commit: " + "a" * 40 + "}\n      proftpd-mod-vroot: {tag:")
        self.assertTrue(any("proftpd-mod-nope: not declared under companions" in e for e in self.errors()))

    def test_companion_without_pin(self):
        self.replace(self.yaml, "      proftpd-mod-vroot: {tag:", "      # proftpd-mod-vroot: {tag:")
        self.assertTrue(any("needs a pin for every companion (proftpd-mod-vroot)" in e for e in self.errors()))

    def test_tag_must_match(self):
        self.replace(self.yaml, '{tag: "v0.9.12"', '{tag: "0.9.12"')
        self.assertTrue(any("does not match its upstream.tag_regex" in e for e in self.errors()))

    def test_missing_debian(self):
        shutil.rmtree(self.tmp / "packages/proftpd/companions/proftpd-mod-vroot/debian")
        self.assertTrue(any("companions/proftpd-mod-vroot/debian/control is missing" in e for e in self.errors()))

    def test_binaries_built_twice(self):
        control = self.tmp / "packages/proftpd/companions/proftpd-mod-vroot/debian/control"
        self.replace(control, "Package: proftpd-mod-vroot", "Package: proftpd-mod-case")
        self.assertTrue(any("built twice" in e for e in self.errors()))

    def test_no_epoch(self):
        self.replace(self.yaml, "\ntracks:", "\nepoch: 1\n\ntracks:")
        self.assertTrue(any("can't have an epoch" in e for e in self.errors()))

    def test_tarball_pins_need_sha256(self):
        self.replace(self.yaml, "      git: https://github.com/Castaglia/proftpd-mod_vroot.git\n",
                     "      git: https://github.com/Castaglia/proftpd-mod_vroot.git\n"
                     "      tarball: https://example.org/vroot-{version}.tar.gz\n")
        self.assertTrue(any("proftpd-mod-vroot: sha256 of the release tarball is required" in e for e in self.errors()))

    def test_guard_compares_each_binary(self):
        info_path = self.tmp / "build-info.json"
        info = {"codename": "trixie", "version": "1.3.9e-1+freshdeb~deb13",
                "binaries": {"proftpd-core": "arm64", "proftpd-mod-vroot": "arm64"},
                "binary_versions": {"proftpd-mod-vroot": "0.9.12+1.3.9e-1+freshdeb~deb13"}}
        distro = {"proftpd-core": ["1.3.8.c+dfsg-4+deb13u3"], "proftpd-mod-vroot": ["0.9.12-2"]}
        for vroot, skipped in (("0.9.12-2", False), ("0.9.13-1", True)):
            distro["proftpd-mod-vroot"] = [vroot]
            info_path.write_text(json.dumps(info))
            with mock.patch.object(freshdeb, "distro_versions", lambda binary: distro.get(binary, [])), \
                    contextlib.redirect_stdout(io.StringIO()):
                freshdeb.cmd_guard(SimpleNamespace(info=info_path))
            result = json.loads(info_path.read_text())
            self.assertEqual(result.get("skipped", False), skipped, vroot)
        self.assertIn("proftpd-mod-vroot 0.9.13-1 (ours 0.9.12+1.3.9e-1+freshdeb~deb13)", result["skip_reason"])

    def test_watch_proposes_companion_releases(self):
        pkg = freshdeb.load_package("proftpd", ROOT)
        tags = {name: {pin.tag: pin.commit} for name, pin in pkg.tracks["main"].companions.items()}
        self.assertEqual(freshdeb.watch_companions(pkg, tags, TODAY), [])
        tags["proftpd-mod-vroot"].update({"v0.9.13": "d" * 40, "v0.9.14rc1": "e" * 40, "v1.0.0-beta": "f" * 40})
        changes = freshdeb.watch_companions(pkg, tags, TODAY)
        self.assertEqual(changes, [{"package": "proftpd", "kind": "companion", "track": "main",
                                    "companion": "proftpd-mod-vroot", "from": "v0.9.12", "tag": "v0.9.13",
                                    "commit": "d" * 40}])
        report = freshdeb.companion_report(pkg, tags, changes, TODAY)
        self.assertIn("  proftpd main: companion proftpd-mod-vroot ours 0.9.12, upstream 0.9.13 -> update to v0.9.13",
                      report)
        self.assertIn("  proftpd main: companion proftpd-mod-case ours 0.9.1, upstream 0.9.1 -> up to date", report)

    def test_clamav_follows_release_candidates(self):
        pkg = freshdeb.load_package("proftpd", ROOT)
        pin = pkg.tracks["main"].companions["proftpd-mod-clamav"]
        tags = {"proftpd-mod-clamav": {pin.tag: pin.commit, "v0.14rc3": "a" * 40}}
        changes = freshdeb.watch_companions(pkg, tags, TODAY)
        self.assertEqual([c["tag"] for c in changes], ["v0.14rc3"])

    def test_prepare_builds_the_source_tree(self):
        if not shutil.which("patch"):
            self.skipTest("patch not available")
        upstream = self.tmp / "upstream"
        upstream.mkdir()
        (upstream / "mod_procfs.c").write_text("/* mod_procfs */\n")
        git(upstream, "init", "-q", "-b", "main")
        git(upstream, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git(upstream, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "release")
        git(upstream, "tag", "v0.3")
        commit = git(upstream, "rev-parse", "HEAD").strip()
        config = freshdeb.load_config(self.tmp)
        pkg = freshdeb.load_package("proftpd", self.tmp)
        companion = pkg.companions["proftpd-mod-procfs"]
        companion.data["upstream"]["git"] = f"file://{upstream}"
        pkg.companions = {"proftpd-mod-procfs": companion}
        pkg.tracks["main"].companions = {"proftpd-mod-procfs": freshdeb.CompanionPin("v0.3", commit)}
        out = self.tmp / "out"
        out.mkdir()
        version = pkg.version(config, "main", "jammy")
        with contextlib.redirect_stdout(io.StringIO()):
            prepared = freshdeb.prepare_companions(pkg, config, "main", "jammy", version, out, 0)
        cversion = f"0.3+{version}"
        self.assertEqual(prepared, [{
            "name": "proftpd-mod-procfs", "version": cversion, "upstream_version": "0.3", "tag": "v0.3",
            "commit": commit, "source_dir": f"companions/proftpd-mod-procfs-{cversion.rsplit('-', 1)[0]}",
            "binaries": {"proftpd-mod-procfs": "any"}, "overlays": ["overlays/procfs-debhelper-13.patch"]}])
        src = out / prepared[0]["source_dir"]
        self.assertEqual((src / "mod_procfs.c").read_text(), "/* mod_procfs */\n")
        self.assertTrue((out / f"companions/proftpd-mod-procfs_{cversion.rsplit('-', 1)[0]}.orig.tar.gz").is_file())
        self.assertTrue((src / "debian/changelog").read_text().startswith(f"proftpd-mod-procfs ({cversion}) "))
        self.assertIn("debhelper-compat (= 13)", (src / "debian/control").read_text())

    def test_apply_pins_the_companion(self):
        if not shutil.which("yq"):
            self.skipTest("yq not available")
        freshdeb.apply_change(self.tmp, {"package": "proftpd", "kind": "companion", "track": "main",
                                         "companion": "proftpd-mod-vroot", "from": "v0.9.12", "tag": "v0.9.13",
                                         "commit": "d" * 40})
        pkg = freshdeb.load_package("proftpd", self.tmp)
        pin = pkg.tracks["main"].companions["proftpd-mod-vroot"]
        self.assertEqual((pin.tag, pin.commit), ("v0.9.13", "d" * 40))
        self.assertEqual(pkg.tracks["main"].revision, freshdeb.load_package("proftpd", ROOT).tracks["main"].revision)
        self.assertIn("# The distributions' module packages", self.yaml.read_text())
        self.assertEqual(self.errors(), [])


class CompanionPlanTests(unittest.TestCase):
    """Plan: companion pins select their track; their packaging needs a bump."""

    setUp, tearDown, commit, edit, plan = (PlanTests.setUp, PlanTests.tearDown, PlanTests.commit,
                                           PlanTests.edit, PlanTests.plan)

    def test_companion_pin_selects_the_track(self):
        self.edit("packages/proftpd/package.yaml", '{tag: "v0.9.12", commit: abcc817f322e1fa670b469ef0bc09419336b1d56}',
                  '{tag: "v0.9.13", commit: ' + "d" * 40 + "}")
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual((selected, errors), ({("proftpd", "main"): True}, []))

    def test_companion_packaging_requires_bump(self):
        self.edit("packages/proftpd/companions/proftpd-mod-vroot/debian/rules", "%:", "# changed\n%:")
        self.commit()
        selected, errors, _ = self.plan()
        self.assertEqual(set(selected), {("proftpd", "main")})
        self.assertTrue(errors and "bump revision" in errors[0], errors)


if __name__ == "__main__":
    unittest.main()

"""Release integration tests: real reprepro, gpg, signed indexes and apt.

One repository per project under the remote, plus a registry at its root.
Run in tests/release.Dockerfile so no host APT configuration is touched:
    docker build -f tests/release.Dockerfile -t freshdeb-tests .
    docker run --rm -v "$PWD:/workspace:ro" freshdeb-tests
"""

import json
import os
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
import release  # noqa: E402

VALKEY_BINARIES = ("valkey-server", "valkey-tools", "valkey-sentinel")


def run(*args, **kwargs):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True, **kwargs)


def fake_deb(directory, package, version, arch):
    tree = directory / f"{package}_{arch}_tree"
    (tree / "DEBIAN").mkdir(parents=True)
    (tree / "usr/share/doc" / package).mkdir(parents=True)
    (tree / "usr/share/doc" / package / "freshdeb-test").write_text(f"{package} {version} {arch}\n")
    (tree / "DEBIAN/control").write_text(
        f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n"
        f"Maintainer: test <test@example.invalid>\nSection: database\nPriority: optional\n"
        "Description: test package\n")
    path = directory / f"{package}_{version}_{arch}.deb"
    run("dpkg-deb", "--root-owner-group", "-Zgzip", "--build", str(tree), str(path))
    shutil.rmtree(tree)
    return path


DEMO_YAML = """\
name: demo
description: "Demo package for release tests"
homepage: https://example.invalid
maintainers: [tester]
upstream:
  git: https://example.invalid/demo.git
packaging:
  origin: https://example.invalid/demo-packaging.git
  ref: {sha}
tracks:
  "1":
    tag: "1.2.0"
    commit: {sha}
    revision: 1
"""


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls.temp.name)
        home = cls.tmp / "gnupg"
        home.mkdir(mode=0o700)
        cls.old_home = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = str(home)
        run("gpg", "--batch", "--passphrase", "", "--quick-generate-key",
            "freshdeb release tests <test@example.invalid>", "ed25519", "sign", "0")
        cls.key = next(line.split(":")[9] for line in run("gpg", "--with-colons", "--list-keys").splitlines()
                       if line.startswith("fpr:"))

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--kill", "gpg-agent"], capture_output=True)
        if cls.old_home is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = cls.old_home
        cls.temp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.tmp))
        self.root = self.work / "root"
        self.root.mkdir()
        shutil.copy(ROOT / "config.yaml", self.root / "config.yaml")
        shutil.copytree(ROOT / "packages", self.root / "packages")
        self.remote = self.work / "remote"  # stands in for the R2 bucket
        self.remote.mkdir()
        self.config = freshdeb.load_config(self.root)

    # ─── helpers ────────────────────────────────────────────────────────

    def edit_package(self, expr, name="valkey"):
        run("yq", "-i", expr, str(self.root / "packages" / name / "package.yaml"))

    def add_demo_package(self):
        pkg = self.root / "packages/demo"
        (pkg / "debian/source").mkdir(parents=True)
        (pkg / "tests").mkdir()
        (pkg / "package.yaml").write_text(DEMO_YAML.format(sha="d" * 40))
        (pkg / "debian/control").write_text("Source: demo\nSection: misc\n\nPackage: demo\nArchitecture: any\n"
                                            "Description: demo\n")
        (pkg / "debian/rules").write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n")
        (pkg / "debian/changelog").write_text("demo (1.2.0-1) unstable; urgency=medium\n\n  * x\n\n"
                                              " -- t <t@t>  Mon, 01 Jan 2024 00:00:00 +0000\n")
        (pkg / "debian/source/format").write_text("3.0 (quilt)\n")
        (pkg / "tests/smoke.sh").write_text("true\n")

    def artifacts(self, tracks=None, codenames=None, skipped=(), publish=True, name="valkey"):
        """Fake CI output: one build dir per track/codename/arch."""
        out = Path(tempfile.mkdtemp(dir=self.work))
        pkg = freshdeb.load_package(name, self.root)
        binaries = freshdeb.control_binaries(pkg.path / "debian")
        for key in tracks or [k for k, t in pkg.tracks.items() if t.active()]:
            for codename in codenames or pkg.codenames(self.config, pkg.tracks[key]):
                for arch in self.config.architectures:
                    build = out / f"{name}_{key}_{codename}_{arch}"
                    build.mkdir()
                    version = pkg.version(self.config, key, codename)
                    info = {"package": name, "track": key, "codename": codename, "arch": arch,
                            "version": version, "binaries": binaries, "publish": publish,
                            "skipped": (key, codename) in skipped, "skip_reason": "test", "debs": []}
                    if not info["skipped"]:
                        for binary, declared in binaries.items():
                            deb_arch = "all" if declared == "all" else arch
                            info["debs"].append(fake_deb(build, binary, version, deb_arch).name)
                    (build / "build-info.json").write_text(json.dumps(info))
        return out

    def merge(self, *dirs):
        out = Path(tempfile.mkdtemp(dir=self.work))
        for directory in dirs:
            for child in directory.iterdir():
                shutil.copytree(child, out / child.name)
        return out

    def publish(self, artifacts, bootstrap=False, all_builds=False, commit="c0ffee", dry_run=False):
        work = Path(tempfile.mkdtemp(dir=self.work))
        return release.publish(self.root, work, artifacts, str(self.remote), self.key, bootstrap=bootstrap,
                               commit=commit, all_builds=all_builds, dry_run=dry_run)

    def registry(self):
        return json.loads((self.remote / "status.json").read_text())

    def indexes(self, name="valkey"):
        project = release.Project(self.config, freshdeb.load_package(name, self.root))
        return release.read_indexes(self.remote / name, project)

    def version_in(self, records, codename, component, arch="amd64", package="valkey-server"):
        record = records.get((codename, component, arch, package))
        return record and record["Version"]

    def bootstrap(self):
        self.assertEqual(self.publish(self.artifacts(), bootstrap=True), {})
        return self.indexes()

    # ─── layout ─────────────────────────────────────────────────────────

    def test_bootstrap_publishes_tracks_and_aliases(self):
        records = self.bootstrap()
        pkg = freshdeb.load_package("valkey", self.root)
        for codename in self.config.distributions:
            for key in pkg.tracks:
                self.assertEqual(self.version_in(records, codename, f"v{key}"),
                                 pkg.version(self.config, key, codename))
            for alias, target in pkg.aliases.items():
                for arch in self.config.architectures:
                    for binary in VALKEY_BINARIES:
                        alias_record = records[(codename, f"v{alias}", arch, binary)]
                        track_record = records[(codename, f"v{target}", arch, binary)]
                        self.assertEqual((alias_record["Version"], alias_record["SHA256"]),
                                         (track_record["Version"], track_record["SHA256"]))
        self.assertEqual(self.registry()["projects"]["valkey"]["commit"], "c0ffee")
        self.assertTrue((self.remote / "freshdeb-archive-keyring.gpg").stat().st_size > 0)
        self.assertIn("v9.1", json.loads((self.remote / "valkey/status.json").read_text())["components"])
        release_file = (self.remote / "valkey/dists/trixie/Release").read_text()
        self.assertRegex(release_file, r"(?m)^Label: freshdeb valkey$")
        self.assertRegex(release_file, r"(?m)^Components: v9\.1 v9\.0 v8\.1 v8\.0 v9 v8$")

    def test_apt_installs_from_project_repository(self):
        self.bootstrap()
        codename = "trixie"
        arch = run("dpkg", "--print-architecture").strip()
        if arch not in self.config.architectures:
            self.skipTest(f"no builds for {arch}")
        apt = self.work / "apt"
        (apt / "lists/partial").mkdir(parents=True)
        (apt / "cache/archives/partial").mkdir(parents=True)
        (apt / "sources.list").write_text(
            f"deb [signed-by={self.remote}/freshdeb-archive-keyring.gpg] file://{self.remote}/valkey {codename} v9\n")
        options = [f"-o=Dir::Etc::SourceList={apt / 'sources.list'}", "-o=Dir::Etc::SourceParts=/dev/null",
                   f"-o=Dir::State::Lists={apt / 'lists'}", f"-o=Dir::Cache={apt / 'cache'}",
                   "-o=Dir::State::status=/dev/null", "-o=APT::Sandbox::User=root"]
        run("apt-get", *options, "update")
        policy = run("apt-cache", *options, "policy", "valkey-server")
        pkg = freshdeb.load_package("valkey", self.root)
        self.assertIn(pkg.version(self.config, "9.1", codename), policy)

    # ─── bootstrap and registry ─────────────────────────────────────────

    def test_refuses_empty_bucket_without_bootstrap(self):
        with self.assertRaisesRegex(freshdeb.FreshdebError, "use --bootstrap"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_refuses_bootstrap_over_existing_registry(self):
        self.bootstrap()
        with self.assertRaisesRegex(freshdeb.FreshdebError, "already has a project registry"):
            self.publish(self.artifacts(tracks=["9.1"]), bootstrap=True)

    def test_bootstrap_with_nothing_to_publish_fails(self):
        with self.assertRaisesRegex(freshdeb.FreshdebError, "nothing to publish"):
            self.publish(Path(tempfile.mkdtemp(dir=self.work)), bootstrap=True)

    def test_refuses_old_single_repository_layout(self):
        (self.remote / "status.json").write_text('{"commit": "x", "components": {}}')
        with self.assertRaisesRegex(freshdeb.FreshdebError, "not a project registry"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_registered_project_without_database_is_refused(self):
        self.bootstrap()
        shutil.rmtree(self.remote / "valkey/db")
        self.edit_package('.tracks["9.1"].revision = 2')
        failures = self.publish(self.artifacts(tracks=["9.1"]))
        self.assertIn("database is missing", failures["valkey"])

    def test_dry_run_uploads_nothing(self):
        self.publish(self.artifacts(), bootstrap=True, dry_run=True)
        self.assertEqual(list(self.remote.iterdir()), [])

    # ─── releases within a project ──────────────────────────────────────

    def test_partial_release_only_touches_its_track_and_alias(self):
        before = self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 2')
        self.assertEqual(self.publish(self.artifacts(tracks=["9.1"]), commit="beef"), {})
        after = self.indexes()
        for key in set(before) | set(after):
            if key[1] in ("v9.1", "v9"):
                self.assertRegex(after[key]["Version"], r"^9\.1\.\d+-2\+freshdeb~")
            else:
                self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual(self.registry()["projects"]["valkey"]["commit"], "beef")

    def test_alias_follows_a_new_track(self):
        self.bootstrap()
        pkg = freshdeb.load_package("valkey", self.root)
        nine_one = pkg.tracks["9.1"]
        self.edit_package(f'.tracks = ({{"9.2": {{"tag": "9.2.0", "commit": "{nine_one.commit}", '
                          f'"revision": 1, "patches": "patches/9.1"}}}} + .tracks) | .aliases["9"] = "9.2"')
        self.assertEqual(self.publish(self.artifacts(tracks=["9.2"])), {})
        after = self.indexes()
        for codename in self.config.distributions:
            self.assertEqual(self.version_in(after, codename, "v9"), self.version_in(after, codename, "v9.2"))
            self.assertTrue(self.version_in(after, codename, "v9").startswith("9.2.0-1"))
            self.assertTrue(self.version_in(after, codename, "v9.1").startswith("9.1."))

    def test_same_version_is_not_republished(self):
        before = self.bootstrap()
        self.assertEqual(self.publish(self.artifacts(tracks=["9.1"]), commit="beef"), {})
        self.assertEqual(before, self.indexes())
        self.assertEqual(self.registry()["projects"]["valkey"]["commit"], "beef")

    def test_skipped_builds_publish_nothing(self):
        before = self.bootstrap()
        self.edit_package('.tracks["8.0"].revision = 2')
        codenames = list(self.config.distributions)
        self.publish(self.artifacts(tracks=["8.0"], skipped={("8.0", c) for c in codenames}))
        self.assertEqual(before, self.indexes())

    def test_test_only_builds_are_ignored(self):
        before = self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 2')
        self.publish(self.artifacts(tracks=["9.1"], publish=False))
        self.assertEqual(before, self.indexes())

    def test_manual_release_publishes_test_only_builds(self):
        self.publish(self.artifacts(publish=False), bootstrap=True, all_builds=True)
        pkg = freshdeb.load_package("valkey", self.root)
        self.assertEqual(self.version_in(self.indexes(), "bookworm", "v9"), pkg.version(self.config, "9.1", "bookworm"))

    def test_refuses_builds_that_disagree_with_the_manifest(self):
        self.bootstrap()
        artifacts = self.artifacts(tracks=["9.1"])
        self.edit_package('.tracks["9.1"].revision = 3')  # manifest moved on after the build
        self.assertIn("package.yaml now says", self.publish(artifacts)["valkey"])

    def test_refuses_downgrade(self):
        self.edit_package('.tracks["9.1"].revision = 5')
        self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 4')
        self.assertIn("refusing downgrade", self.publish(self.artifacts(tracks=["9.1"]))["valkey"])

    def test_arch_all_packages_come_from_the_primary_architecture(self):
        # Every job builds neovim-runtime (Architecture: all) so it can be
        # tested on its own; only the primary architecture's copy is published.
        self.assertEqual(self.publish(self.artifacts(name="neovim"), bootstrap=True), {})
        records = self.indexes("neovim")
        primary = self.config.primary_arch
        for codename in self.config.distributions:
            runtime = {arch: records[(codename, "main", arch, "neovim-runtime")] for arch in self.config.architectures}
            self.assertEqual({r["SHA256"] for r in runtime.values()}, {runtime[primary]["SHA256"]})
            self.assertEqual({r["Architecture"] for r in runtime.values()}, {"all"})
            for arch in self.config.architectures:
                self.assertEqual(records[(codename, "main", arch, "neovim")]["Architecture"], arch)

    def test_refuses_incomplete_build(self):
        self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 2')
        artifacts = self.artifacts(tracks=["9.1"], codenames=["bookworm"])
        info_path = next(artifacts.glob("valkey_9.1_bookworm_amd64/build-info.json"))
        info = json.loads(info_path.read_text())
        info["debs"] = [d for d in info["debs"] if not d.startswith("valkey-tools")]
        info_path.write_text(json.dumps(info))
        self.assertIn("expected", self.publish(artifacts)["valkey"])

    def test_detects_tampered_index(self):
        self.bootstrap()
        index = self.remote / "valkey/dists/bookworm/v9.1/binary-amd64/Packages"
        index.write_text(index.read_text().replace("Version: 9.1", "Version: 9.9"))
        self.edit_package('.tracks["9.1"].revision = 2')
        self.assertIn("checksum mismatch", self.publish(self.artifacts(tracks=["9.1"]))["valkey"])

    def test_refuses_removed_track(self):
        self.bootstrap()
        self.edit_package('del(.tracks["8.0"])')
        self.assertIn("never remove a track", self.publish(self.artifacts(tracks=["9.1"]))["valkey"])

    # ─── projects are independent ───────────────────────────────────────

    def test_new_project_gets_its_own_repository(self):
        self.bootstrap()
        valkey_before = self.indexes()
        self.add_demo_package()
        self.assertEqual(self.publish(self.artifacts(name="demo"), commit="beef"), {})
        demo = self.indexes("demo")
        self.assertEqual(self.version_in(demo, "trixie", "v1", package="demo"), "1.2.0-1+freshdeb~deb13")
        self.assertEqual(valkey_before, self.indexes())
        projects = self.registry()["projects"]
        self.assertEqual((projects["demo"]["commit"], projects["valkey"]["commit"]), ("beef", "beef"))
        self.assertFalse((self.remote / "valkey/pool/v1").exists())

    def test_failing_project_does_not_block_others(self):
        self.bootstrap()
        self.add_demo_package()
        index = self.remote / "valkey/dists/bookworm/v9.1/binary-amd64/Packages"
        index.write_text(index.read_text().replace("Version: 9.1", "Version: 9.9"))
        self.edit_package('.tracks["9.1"].revision = 2')
        failures = self.publish(self.merge(self.artifacts(tracks=["9.1"]), self.artifacts(name="demo")),
                                commit="beef")
        self.assertEqual(list(failures), ["valkey"])
        self.assertTrue(self.indexes("demo"))
        projects = self.registry()["projects"]
        self.assertEqual(projects["demo"]["commit"], "beef")
        self.assertEqual(projects["valkey"]["commit"], "c0ffee")  # retried by the next plan

    # ─── reset ──────────────────────────────────────────────────────────

    def publish_reset(self, artifacts, reset):
        work = Path(tempfile.mkdtemp(dir=self.work))
        return release.publish(self.root, work, artifacts, str(self.remote), self.key, commit="beef",
                               reset=reset)

    def rename_valkey_tracks(self):
        # Like memcached's 1.6 -> main: the published components disappear.
        self.edit_package('.tracks = {"main": .tracks["9.1"]} | del(.aliases)')

    def test_renamed_components_need_a_reset(self):
        self.bootstrap()
        self.rename_valkey_tracks()
        self.assertIn("never remove a track", self.publish(self.artifacts())["valkey"])

    def test_reset_refuses_while_the_old_repository_exists(self):
        self.bootstrap()
        self.rename_valkey_tracks()
        failures = self.publish_reset(self.artifacts(), ["valkey"])
        self.assertIn("delete that directory first", failures["valkey"])
        self.assertTrue((self.remote / "valkey/dists/trixie/v9.1").is_dir())

    def test_reset_starts_the_project_from_scratch(self):
        self.bootstrap()
        self.add_demo_package()
        self.publish(self.artifacts(name="demo"))
        demo_before = self.indexes("demo")
        self.rename_valkey_tracks()
        shutil.rmtree(self.remote / "valkey")
        self.assertEqual(self.publish_reset(self.artifacts(), ["valkey"]), {})
        records = self.indexes("valkey")
        self.assertEqual({key[1] for key in records}, {"main"})
        release_file = (self.remote / "valkey/dists/trixie/Release").read_text()
        self.assertRegex(release_file, r"(?m)^Components: main$")
        self.assertEqual(self.registry()["projects"]["valkey"]["commit"], "beef")
        self.assertEqual(demo_before, self.indexes("demo"))

    def test_reset_needs_a_registered_project_and_builds(self):
        self.bootstrap()
        self.add_demo_package()
        with self.assertRaisesRegex(freshdeb.FreshdebError, "not a registered project"):
            self.publish_reset(self.artifacts(name="demo"), ["demo"])
        with self.assertRaisesRegex(freshdeb.FreshdebError, "no builds in this run"):
            self.publish_reset(self.artifacts(name="demo"), ["valkey"])

    def test_refuses_removed_project(self):
        self.bootstrap()
        shutil.rmtree(self.root / "packages/valkey")
        with self.assertRaisesRegex(freshdeb.FreshdebError, "never remove a package"):
            self.publish(Path(tempfile.mkdtemp(dir=self.work)))


if __name__ == "__main__":
    unittest.main()

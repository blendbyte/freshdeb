"""Release integration tests: real reprepro, gpg, signed indexes and apt.

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
        self.remote = self.work / "remote"  # stands in for the S3 bucket
        self.remote.mkdir()
        self.config = freshdeb.load_config(self.root)

    # ─── helpers ────────────────────────────────────────────────────────

    def edit_package(self, expr):
        run("yq", "-i", expr, str(self.root / "packages/valkey/package.yaml"))

    def artifacts(self, tracks=None, codenames=None, skipped=(), publish=True):
        """Fake CI output for valkey: one build dir per track/codename/arch."""
        out = Path(tempfile.mkdtemp(dir=self.work))
        pkg = freshdeb.load_package("valkey", self.root)
        binaries = freshdeb.control_binaries(pkg.path / "debian")
        for key in tracks or [k for k, t in pkg.tracks.items() if t.active()]:
            for codename in codenames or pkg.codenames(self.config, pkg.tracks[key]):
                for arch in self.config.architectures:
                    build = out / f"valkey_{key}_{codename}_{arch}"
                    build.mkdir()
                    version = pkg.version(self.config, key, codename)
                    info = {"package": "valkey", "track": key, "codename": codename, "arch": arch,
                            "version": version, "binaries": binaries, "publish": publish,
                            "skipped": (key, codename) in skipped, "skip_reason": "test", "debs": []}
                    if not info["skipped"]:
                        for binary in binaries:
                            info["debs"].append(fake_deb(build, binary, version, arch).name)
                    (build / "build-info.json").write_text(json.dumps(info))
        return out

    def publish(self, artifacts, bootstrap=False):
        repo = Path(tempfile.mkdtemp(dir=self.work)) / "repo"
        repo.mkdir()
        run("bash", str(ROOT / "scripts/sync-repo.sh"), "pull", str(repo), str(self.remote))
        changed = release.release(self.root, repo, artifacts, self.key, bootstrap=bootstrap,
                                  pool_source=str(self.remote))
        run("bash", str(ROOT / "scripts/sync-repo.sh"), "push", str(repo), str(self.remote))
        return changed

    def indexes(self):
        return release.read_indexes(self.remote, release.Catalog(self.root))

    def version_in(self, records, codename, component, arch="amd64", package="valkey-server"):
        record = records.get((codename, component, arch, package))
        return record and record["Version"]

    def bootstrap(self):
        self.publish(self.artifacts(), bootstrap=True)
        return self.indexes()

    # ─── tests ──────────────────────────────────────────────────────────

    def test_bootstrap_publishes_tracks_and_aliases(self):
        records = self.bootstrap()
        pkg = freshdeb.load_package("valkey", self.root)
        for codename in self.config.distributions:
            for key in pkg.tracks:
                self.assertEqual(self.version_in(records, codename, f"valkey-{key}"),
                                 pkg.version(self.config, key, codename))
            for alias, target in pkg.aliases.items():
                for arch in self.config.architectures:
                    for binary in ("valkey-server", "valkey-tools", "valkey-sentinel"):
                        alias_record = records[(codename, f"valkey-{alias}", arch, binary)]
                        track_record = records[(codename, f"valkey-{target}", arch, binary)]
                        self.assertEqual((alias_record["Version"], alias_record["SHA256"]),
                                         (track_record["Version"], track_record["SHA256"]))
        status = json.loads((self.remote / "status.json").read_text())
        self.assertIn("valkey-9.1", status["components"])

    def test_refuses_to_start_without_database(self):
        with self.assertRaisesRegex(freshdeb.FreshdebError, "missing repository database"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_refuses_bootstrap_over_existing_repository(self):
        self.bootstrap()
        with self.assertRaisesRegex(freshdeb.FreshdebError, "already exists"):
            self.publish(self.artifacts(tracks=["9.1"]), bootstrap=True)

    def test_partial_release_only_touches_its_track_and_alias(self):
        before = self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 2')
        self.publish(self.artifacts(tracks=["9.1"]))
        after = self.indexes()
        for key in set(before) | set(after):
            if key[1] in ("valkey-9.1", "valkey-9"):
                self.assertRegex(after[key]["Version"], r"^9\.1\.\d+-2\+freshdeb~")
            else:
                self.assertEqual(before.get(key), after.get(key), key)

    def test_alias_follows_a_new_track(self):
        self.bootstrap()
        pkg = freshdeb.load_package("valkey", self.root)
        nine_one = pkg.tracks["9.1"]
        self.edit_package(f'.tracks = ({{"9.2": {{"tag": "9.2.0", "commit": "{nine_one.commit}", '
                          f'"revision": 1, "patches": "patches/9.1"}}}} + .tracks) | .aliases["9"] = "9.2"')
        self.publish(self.artifacts(tracks=["9.2"]))
        after = self.indexes()
        for codename in self.config.distributions:
            self.assertEqual(self.version_in(after, codename, "valkey-9"),
                             self.version_in(after, codename, "valkey-9.2"))
            self.assertTrue(self.version_in(after, codename, "valkey-9").startswith("9.2.0-1"))
            self.assertTrue(self.version_in(after, codename, "valkey-9.1").startswith("9.1."))

    def test_same_version_is_not_republished(self):
        before = self.bootstrap()
        self.assertEqual(self.publish(self.artifacts(tracks=["9.1"])), 0)
        self.assertEqual(before, self.indexes())

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

    def test_refuses_builds_that_disagree_with_the_manifest(self):
        self.bootstrap()
        artifacts = self.artifacts(tracks=["9.1"])
        self.edit_package('.tracks["9.1"].revision = 3')  # manifest moved on after the build
        with self.assertRaisesRegex(freshdeb.FreshdebError, "package.yaml now says"):
            self.publish(artifacts)

    def test_refuses_downgrade(self):
        self.edit_package('.tracks["9.1"].revision = 5')
        self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 4')
        with self.assertRaisesRegex(freshdeb.FreshdebError, "refusing downgrade"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_refuses_incomplete_build(self):
        self.bootstrap()
        self.edit_package('.tracks["9.1"].revision = 2')
        artifacts = self.artifacts(tracks=["9.1"], codenames=["bookworm"])
        info_path = next(artifacts.glob("valkey_9.1_bookworm_amd64/build-info.json"))
        info = json.loads(info_path.read_text())
        info["debs"] = [d for d in info["debs"] if not d.startswith("valkey-tools")]
        info_path.write_text(json.dumps(info))
        with self.assertRaisesRegex(freshdeb.FreshdebError, "expected"):
            self.publish(artifacts)

    def test_detects_tampered_index(self):
        self.bootstrap()
        index = self.remote / "dists/bookworm/valkey-9.1/binary-amd64/Packages"
        index.write_text(index.read_text().replace("Version: 9.1", "Version: 9.9"))
        self.edit_package('.tracks["9.1"].revision = 2')
        with self.assertRaisesRegex(freshdeb.FreshdebError, "checksum mismatch"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_refuses_removed_component(self):
        self.bootstrap()
        self.edit_package('del(.tracks["8.0"])')
        with self.assertRaisesRegex(freshdeb.FreshdebError, "never be removed"):
            self.publish(self.artifacts(tracks=["9.1"]))

    def test_apt_installs_from_alias_component(self):
        self.bootstrap()
        codename = "trixie"
        arch = run("dpkg", "--print-architecture").strip()
        if arch not in self.config.architectures:
            self.skipTest(f"no builds for {arch}")
        keyring = self.work / "key.gpg"
        run("gpg", "--batch", "--yes", "--output", str(keyring), "--export", self.key)
        apt = self.work / "apt"
        (apt / "lists/partial").mkdir(parents=True)
        (apt / "cache/archives/partial").mkdir(parents=True)
        (apt / "sources.list").write_text(
            f"deb [signed-by={keyring}] file://{self.remote} {codename} valkey-9\n")
        options = [f"-o=Dir::Etc::SourceList={apt / 'sources.list'}", "-o=Dir::Etc::SourceParts=/dev/null",
                   f"-o=Dir::State::Lists={apt / 'lists'}", f"-o=Dir::Cache={apt / 'cache'}",
                   "-o=Dir::State::status=/dev/null", "-o=APT::Sandbox::User=root"]
        run("apt-get", *options, "update")
        policy = run("apt-cache", *options, "policy", "valkey-server")
        pkg = freshdeb.load_package("valkey", self.root)
        self.assertIn(pkg.version(self.config, "9.1", codename), policy)
        release_file = (self.remote / f"dists/{codename}/Release").read_text()
        self.assertRegex(release_file, r"(?m)^Origin: freshdeb$")
        self.assertTrue(re.search(r"(?m)^Components: .*valkey-9\.1 .*valkey-9( |$)", release_file))


if __name__ == "__main__":
    unittest.main()

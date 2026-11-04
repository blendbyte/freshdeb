#!/bin/bash
#
# Smoke test for bun, run as root in a fresh container right after our package
# was installed. Nothing leaves the container: the server is on localhost and
# the install has no registry dependencies.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 1.4.4

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

echo "both commands report the expected version"
[[ "$(bun --version)" == "$expected" ]] || { bun --version >&2; exit 1; }
[[ "$(bunx --version)" == "$expected" ]] || { bunx --version >&2; exit 1; }
[[ "$(bun --revision)" == "$expected"+* ]] || { bun --revision >&2; exit 1; }

echo "runs TypeScript with the built-in APIs"
cat > app.ts <<'EOF'
import { Database } from "bun:sqlite";

const db = new Database(":memory:");
db.run("CREATE TABLE t (name TEXT)");
db.run("INSERT INTO t VALUES (?)", ["sqlite"]);
const { name } = db.query("SELECT name FROM t").get() as { name: string };

const server = Bun.serve({ port: 0, hostname: "127.0.0.1", fetch: () => new Response("http") });
const body = await (await fetch(server.url)).text();
await server.stop(true);

console.log(`${name} ${body} ${Bun.version}`);
EOF
output="$(bun run app.ts)"
[[ "$output" == "sqlite http $expected" ]] || { echo "$output" >&2; exit 1; }

echo "test runner"
cat > math.test.ts <<'EOF'
import { expect, test } from "bun:test";

test("adds", () => {
  expect(1 + 1).toBe(2);
});
EOF
bun test math.test.ts

echo "package manager, with a local workspace"
mkdir -p packages/greet
cat > package.json <<'EOF'
{"name": "freshdeb-smoke", "private": true, "workspaces": ["packages/*"], "dependencies": {"greet": "workspace:*"}}
EOF
cat > packages/greet/package.json <<'EOF'
{"name": "greet", "version": "1.0.0", "main": "index.ts"}
EOF
echo 'export const greeting = "hello";' > packages/greet/index.ts
bun install
output="$(bun -e 'import { greeting } from "greet"; console.log(greeting)')"
[[ "$output" == hello ]] || { echo "$output" >&2; exit 1; }

echo "standalone executable, which starts from the packaged binary"
echo 'console.log("standalone " + Bun.version);' > standalone.ts
bun build --compile standalone.ts --outfile standalone
output="$(./standalone)"
[[ "$output" == "standalone $expected" ]] || { echo "$output" >&2; exit 1; }

echo "files are packaged"
# From the file list: Ubuntu's minimized images skip /usr/share/doc.
files="$(dpkg -L bun)"
for file in /usr/bin/bun /usr/bin/bunx /usr/share/doc/bun/README.Debian /usr/share/bash-completion/completions/bun \
        /usr/share/zsh/vendor-completions/_bun /usr/share/fish/vendor_completions.d/bun.fish; do
    grep -qxF "$file" <<<"$files" || { echo "missing from the package: $file" >&2; exit 1; }
done
grep -qE '^/usr/share/doc/bun/LICENSE\.md(\.gz)?$' <<<"$files" || { echo "missing from the package: LICENSE.md" >&2; exit 1; }
echo "smoke test passed"

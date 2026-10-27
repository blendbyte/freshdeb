#!/bin/bash
#
# Smoke test for neovim, run as root in a fresh container right after our
# packages were installed (and again after an upgrade from the distro's
# version). Everything runs headless.
#
# Env: FRESHDEB_UPSTREAM_VERSION  e.g. 0.12.6

set -euo pipefail

expected="${FRESHDEB_UPSTREAM_VERSION:?}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Run Lua in a clean, headless nvim and print what it writes.
lua() {
    nvim --headless --clean -c "lua $1" -c 'qa!' 2>&1
}

echo "binary reports the expected version"
version="$(nvim --version)"
[[ "$version" == "NVIM v${expected}"* ]] || { echo "$version" >&2; exit 1; }

echo "Lua is LuaJIT"
jit="$(lua 'io.write(jit and jit.version or "no jit")')"
[[ "$jit" == "LuaJIT 2.1"* ]] || { echo "$jit" >&2; exit 1; }

echo "libuv and luv work"
test "$(lua 'io.write(vim.uv.fs_stat("/etc/hostname") and "ok" or "missing")')" = ok

echo "bundled tree-sitter parsers load and parse"
# language, sample (\n ends the line, as these grammars expect), root node
while IFS='|' read -r lang text expected_root; do
    root="$(lua "local p = vim.treesitter.get_string_parser('$text', '$lang'); local t = p:parse()[1]:root(); io.write(t:type() .. (t:has_error() and ' (with errors)' or ''))")"
    [[ "$root" == "$expected_root" ]] || { echo "$lang parser: got '$root', expected '$expected_root'" >&2; exit 1; }
    echo "  $lang: $root"
done <<'SAMPLES'
c|int x;\n|translation_unit
lua|local x = 1\n|chunk
vim|let x = 1\n|script_file
query|(identifier) @x\n|program
markdown|# heading\n|document
SAMPLES
# vimdoc flags short fragments as errors (upstream's release build too), so
# it parses a real help file: neovim's own doc/help.txt.
root="$(lua "local f = vim.api.nvim_get_runtime_file('doc/help.txt', false)[1]; local t = vim.treesitter.get_string_parser(table.concat(vim.fn.readfile(f), '\\n') .. '\\n', 'vimdoc'):parse()[1]:root(); io.write(t:type() .. (t:has_error() and ' (with errors)' or ''))")"
[[ "$root" == help_file ]] || { echo "vimdoc parser on help.txt: got '$root'" >&2; exit 1; }
echo "  vimdoc: $root (doc/help.txt)"

echo "editing and writing a file works"
nvim --headless --clean -c 'normal! ihello from freshdeb' -c 'wq' "$work/file.txt"
test "$(cat "$work/file.txt")" = "hello from freshdeb"

echo "help and runtime files are found"
test "$(lua 'io.write(#vim.api.nvim_get_runtime_file("doc/help.txt", false))')" = 1

echo "registered as editor, vi and vim through alternatives"
for name in editor vi vim; do
    test "$(readlink -f "/usr/bin/$name")" = /usr/bin/nvim || { echo "$name -> $(readlink -f "/usr/bin/$name")" >&2; exit 1; }
done

echo "man page and completions are packaged"
# From the file lists: Ubuntu's minimized images tell dpkg to skip /usr/share/man.
files="$(dpkg -L neovim neovim-runtime)"
for path in /usr/share/man/man1/nvim.1.gz /usr/share/bash-completion/completions/nvim \
            /usr/share/zsh/vendor-completions/_nvim /usr/lib/nvim/parser/c.so; do
    grep -qxF "$path" <<<"$files" || { echo "missing from the packages: $path" >&2; exit 1; }
done
echo "smoke test passed"

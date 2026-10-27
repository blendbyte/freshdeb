#!/usr/bin/env bash
# Transfer only the repository state and the files a release needs.
#
#   scripts/sync-repo.sh pull|push <local-repo> <remote> [--dry-run]
#
# <remote> is s3://bucket[/prefix] in production (Cloudflare R2 through its
# S3 API; endpoint and credentials come from the AWS_* environment), or a
# local directory (used by the release tests; ordering only matters for S3).
set -euo pipefail

mode="${1:?pull or push required}"
repo="${2:?local repository required}"
remote="${3:?remote required}"
remote="${remote%/}"
options=(--only-show-errors)
if [[ "${4:-}" == "--dry-run" ]]; then
    options+=(--dryrun)
elif [[ -n "${4:-}" ]]; then
    echo "Unknown option: $4" >&2
    exit 1
fi
ROOT_FILES=(freshdeb-archive-keyring.gpg freshdeb-archive-keyring.asc status.json)

if [[ "$remote" != s3://* ]]; then
    case "$mode" in
        pull)
            for dir in db dists; do
                [[ -d "$remote/$dir" ]] && mkdir -p "$repo/$dir" && cp -a "$remote/$dir/." "$repo/$dir/"
            done
            ;;
        push)
            [[ " ${options[*]} " == *" --dryrun "* ]] && { echo "(dry run) would update $remote"; exit 0; }
            for dir in pool db dists; do
                [[ -d "$repo/$dir" ]] && mkdir -p "$remote/$dir" && cp -a "$repo/$dir/." "$remote/$dir/"
            done
            for file in "${ROOT_FILES[@]}"; do
                [[ -f "$repo/$file" ]] && cp -a "$repo/$file" "$remote/$file"
            done
            ;;
        *) echo "Unknown mode: $mode" >&2; exit 1 ;;
    esac
    exit 0
fi

case "$mode" in
    pull)
        # Dry runs restore real state too; they only skip uploads.
        aws s3 sync "$remote/db/" "$repo/db/" --only-show-errors
        aws s3 sync "$remote/dists/" "$repo/dists/" --only-show-errors
        ;;
    push)
        # Never delete remote pool files: old versions stay downloadable.
        # Upload payloads before the indexes that reference them, and the
        # signed Release files last.
        #
        # Cache-Control is for the CDN in front of the bucket (Cloudflare on
        # deb.freshdeb.com). Pool files never change once published, so they
        # cache forever; indexes must never be served stale, or apt sees a
        # new Release next to old Packages files ("Hash Sum mismatch").
        immutable=(--cache-control "public, max-age=31536000, immutable")
        fresh=(--cache-control "no-cache")
        aws s3 sync "$repo/pool/" "$remote/pool/" "${immutable[@]}" "${options[@]}"
        aws s3 sync "$repo/db/" "$remote/db/" "${options[@]}"
        aws s3 sync "$repo/dists/" "$remote/dists/" "${fresh[@]}" \
            --exclude '*/Release' --exclude '*/Release.gpg' --exclude '*/InRelease' "${options[@]}"
        aws s3 sync "$repo/dists/" "$remote/dists/" "${fresh[@]}" --exclude '*' \
            --include '*/Release' --include '*/Release.gpg' --include '*/InRelease' "${options[@]}"
        includes=()
        for file in "${ROOT_FILES[@]}"; do includes+=(--include "$file"); done
        aws s3 sync "$repo/" "$remote/" "${fresh[@]}" --exclude '*' "${includes[@]}" "${options[@]}"
        ;;
    *) echo "Unknown mode: $mode" >&2; exit 1 ;;
esac

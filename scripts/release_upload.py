#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Replace files on a rolling GitHub release without a window in which a file
is missing if an upload fails.

`gh release upload --clobber` deletes the existing asset first and then
uploads; if the upload fails, the old file is gone and the catalog points at
a missing download until the next good run. Instead, per file:

  1. upload it as NAME.uploading (the live NAME is untouched meanwhile);
  2. rename the live NAME to NAME.replaced, then NAME.uploading to NAME
     (two quick metadata calls; if the second fails, NAME.replaced is
     renamed back);
  3. delete NAME.replaced.

Leftovers from an interrupted run are cleaned up first: a NAME.replaced
with no NAME is restored to NAME, other leftovers are deleted.

Each file is handled on its own: a failure is reported and the next file is
still tried; the exit code is 1 if any file failed. The release is created
(without files, --latest=false) if it does not exist.

Usage (needs the gh CLI, authenticated; GH_TOKEN in workflows):
    python3 release_upload.py TAG FILE... --repo owner/name --title TITLE
        [--notes-file FILE | --notes TEXT]

Stdlib only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

TMP_SUFFIX = ".uploading"
OLD_SUFFIX = ".replaced"


class GhError(RuntimeError):
    pass


def gh(*args) -> bytes:
    r = subprocess.run(["gh", *args], capture_output=True)
    if r.returncode != 0:
        raise GhError(f"gh {' '.join(args[:3])}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def get_release(tag: str, repo: str):
    try:
        return json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    except GhError as e:
        if "Not Found" in str(e) or "404" in str(e):
            return None
        raise


def list_assets(release_id: int, repo: str) -> dict:
    """{name: asset id} of every asset of the release."""
    out = gh("api", "--paginate", f"repos/{repo}/releases/{release_id}/assets?per_page=100",
             "--jq", ".[] | [.name, .id] | @tsv")
    assets = {}
    for line in out.decode().splitlines():
        if line.strip():
            name, aid = line.rsplit("\t", 1)
            assets[name] = int(aid)
    return assets


def rename_asset(asset_id: int, new_name: str, repo: str) -> None:
    gh("api", "-X", "PATCH", f"repos/{repo}/releases/assets/{asset_id}", "-f", f"name={new_name}")


def delete_asset(asset_id: int, repo: str) -> None:
    gh("api", "-X", "DELETE", f"repos/{repo}/releases/assets/{asset_id}")


def clean_leftovers(name: str, assets: dict, release_id: int, repo: str) -> dict:
    tmp, old = name + TMP_SUFFIX, name + OLD_SUFFIX
    if old in assets and name not in assets:
        print(f"  restoring {old} -> {name} (left by an interrupted run)", file=sys.stderr)
        rename_asset(assets[old], name, repo)
    else:
        if old in assets:
            delete_asset(assets[old], repo)
    if tmp in assets:
        delete_asset(assets[tmp], repo)
    return list_assets(release_id, repo)


def replace_file(tag: str, path: str, release_id: int, repo: str, workdir: str) -> None:
    name = os.path.basename(path)
    tmp, old = name + TMP_SUFFIX, name + OLD_SUFFIX
    assets = clean_leftovers(name, list_assets(release_id, repo), release_id, repo)

    staged = os.path.join(workdir, tmp)
    shutil.copyfile(path, staged)
    try:
        gh("release", "upload", tag, staged, "--repo", repo)
    finally:
        os.remove(staged)
    assets = list_assets(release_id, repo)
    if tmp not in assets:
        raise GhError(f"{tmp} not found on the release after upload")

    if name in assets:
        rename_asset(assets[name], old, repo)
        try:
            rename_asset(assets[tmp], name, repo)
        except GhError:
            rename_asset(assets[name], name, repo)  # put the old file back
            try:
                delete_asset(assets[tmp], repo)
            except GhError:
                pass  # the next run deletes the leftover
            raise
        try:
            delete_asset(assets[name], repo)
        except GhError as e:
            # The new file is live; the next run deletes the leftover.
            print(f"  warning: could not delete {old}: {e}", file=sys.stderr)
    else:
        rename_asset(assets[tmp], name, repo)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tag")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--title", required=True, help="Title if the release has to be created")
    notes = ap.add_mutually_exclusive_group()
    notes.add_argument("--notes-file")
    notes.add_argument("--notes", default="")
    args = ap.parse_args()

    files = [f for f in args.files if os.path.isfile(f)]
    if not files:
        print("No files to upload.", file=sys.stderr)
        return

    release = get_release(args.tag, args.repo)
    if release is None:
        print(f"Release {args.tag} does not exist — creating...", file=sys.stderr)
        cmd = ["release", "create", args.tag, "--repo", args.repo, "--title", args.title,
               "--latest=false"]
        cmd += ["--notes-file", args.notes_file] if args.notes_file else ["--notes", args.notes]
        gh(*cmd)
        release = get_release(args.tag, args.repo)
        if release is None:
            sys.exit(f"Release {args.tag} still not found after creating it")

    failed = []
    with tempfile.TemporaryDirectory() as workdir:
        for path in files:
            name = os.path.basename(path)
            print(f"Uploading {name}...", file=sys.stderr)
            try:
                replace_file(args.tag, path, release["id"], args.repo, workdir)
            except (GhError, OSError) as e:
                print(f"  Upload FAILED for {name}: {e}", file=sys.stderr)
                failed.append(name)
    if failed:
        print(f"{len(failed)} file(s) failed: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

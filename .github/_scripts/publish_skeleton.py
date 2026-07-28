#!/usr/bin/env python3
"""Seed skeleton status JSON files for every recipe in models/en/.

Run by .github/workflows/publish-status.yml as the first step of the
"Build status JSON files" stage. Idempotent: existing real records are
preserved; only recipes without a status JSON yet are seeded.

Why a separate file? The heredoc approach (`python3 - <<'PYEOF'`) inside
a YAML `run: |` block confuses the workflow YAML parser — it reads the
`<<` token as a YAML merge-key marker and fails before bash even sees
the script. Extracting to a real file sidesteps that entirely.

Env:
  HEAD_SHA   — current upstream main sha (used as placeholder head_sha)
  REPO       — "owner/name" (e.g. "vllm-ascend/vllm-ascend-recipes")
  STATUS_DIR — output directory (default: "public/status")
"""

import json
import os
import re
import sys

import yaml


def looks_like_sha40(s):
    return bool(s) and len(s) == 40 and bool(re.fullmatch(r'[0-9a-fA-F]+', s))


def looks_like_run_id(v):
    return isinstance(v, int) and v > 0


def read_status(status_dir, slug):
    p = os.path.join(status_dir, f'{slug}.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p) as fh:
            return json.load(fh)
    except Exception:
        return None


def looks_real(record):
    """A previously-published record is treated as real only if its
    head_sha and workflow_run_id look plausible. Mock fixtures used
    40-hex-char SHAs and large integer run_ids too, so this is a weak
    signal — but combined with the merge step's overwrite-on-real-run
    semantics it is sufficient to avoid resurrecting deleted mocks."""
    if not record:
        return False
    return looks_like_sha40(record.get('head_sha', '')) and looks_like_run_id(record.get('workflow_run_id'))


def build_skip_record(HEAD_SHA, REPO, recipe_path):
    return {
        'kind': 'nightly',
        'status': 'skip',
        'reason': 'atlas_800_a2: unsupported',
        'head_sha': HEAD_SHA,
        'head_sha_url': f'https://github.com/{REPO}/commit/{HEAD_SHA}',
        'workflow_run_id': None,
        'workflow_run_url': None,
        'recipe_path': recipe_path,
        'recipe_yaml_url': f'https://github.com/{REPO}/blob/{HEAD_SHA}/{recipe_path}',
        'params_url': None,
        'started_at': None,
        'finished_at': None,
        'pr_number': None,
        'pr_url': None,
        'pr_title': None,
        'pr_author': None,
    }


def main():
    HEAD_SHA = os.environ.get('HEAD_SHA', '')
    REPO = os.environ.get('REPO', '')
    status_dir = os.environ.get('STATUS_DIR', 'public/status')
    recipes_root = os.environ.get('RECIPES_ROOT', 'models/en')

    if not HEAD_SHA or not REPO:
        print('::warning::HEAD_SHA and REPO env vars are required', file=sys.stderr)
        sys.exit(1)

    os.makedirs(status_dir, exist_ok=True)

    seeded = 0
    skipped_existing = 0
    cleaned_mock = 0

    for root, _, files in os.walk(recipes_root):
        for name in sorted(files):
            if not (name.endswith('.yaml') or name.endswith('.yml')):
                continue
            path = os.path.join(root, name)
            try:
                with open(path) as fh:
                    data = yaml.safe_load(fh)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            slug = name.rsplit('.', 1)[0]
            hw = (data.get('meta') or {}).get('hardware') or {}
            a2 = hw.get('atlas_800_a2', None)
            recipe_path_rel = path

            prev = read_status(status_dir, slug)

            if prev is not None:
                # Existing record — preserve real fields, replace mock ones
                # with the appropriate placeholder. This is what makes the
                # step safe to re-run after a mock fixture ships in git.
                if prev.get('last_pr_run') and not looks_real(prev['last_pr_run']):
                    prev['last_pr_run'] = None
                    cleaned_mock += 1
                if a2 == 'unsupported':
                    if not looks_real(prev.get('last_nightly_run')):
                        prev['last_nightly_run'] = build_skip_record(HEAD_SHA, REPO, recipe_path_rel)
                else:
                    if not looks_real(prev.get('last_nightly_run')):
                        prev['last_nightly_run'] = None
                out = prev
                skipped_existing += 1
            else:
                # No record yet — write a fresh placeholder.
                if a2 == 'unsupported':
                    nightly = build_skip_record(HEAD_SHA, REPO, recipe_path_rel)
                else:
                    nightly = None
                out = {
                    'model': slug,
                    'last_pr_run': None,
                    'last_nightly_run': nightly,
                }
                seeded += 1

            with open(os.path.join(status_dir, f'{slug}.json'), 'w') as fp:
                json.dump(out, fp, indent=2, ensure_ascii=False)
                fp.write('\n')

    print(f'Seeded {seeded} skeleton status files, updated {skipped_existing} existing (cleaned {cleaned_mock} mock fields)')


if __name__ == '__main__':
    main()
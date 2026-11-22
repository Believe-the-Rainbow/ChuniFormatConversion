#!/usr/bin/env python3
"""tj_regress.py -- compare the Python counter against the arcade-official
T_JUDGE numbers embedded in every c2s chart. Fully reproducible from the user's
own HDD data; no game code involved.

usage:
  python scripts/tj_regress.py --hdd <A000 目录> [--corpus out/corpus_ugc]
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'lib'))
import ugccount as U  # noqa: E402

FIELDS = ('ALL', 'TAP', 'HLD', 'SLD', 'AIR', 'FLK')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hdd', required=True, help='A000 目录（生成语料时用的那个）')
    ap.add_argument('--corpus', default=os.path.join(ROOT, 'out', 'corpus_ugc'))
    args = ap.parse_args()

    mpath = os.path.join(os.path.dirname(args.corpus.rstrip('/\\')), 'corpus_manifest.json')
    manifest = json.load(open(mpath, encoding='utf8'))
    ok = have = 0
    divergent = []
    t0 = time.perf_counter()
    for i, m in enumerate(manifest):
        off = m['official']
        if not off or any(v is None for v in off):
            continue
        have += 1
        ch = U.count_text(open(os.path.join(args.corpus, m['file']),
                               encoding='utf-8', errors='replace').read())
        mine = U.counts_of(ch)
        theirs = tuple(int(v) for v in off)
        if mine == theirs:
            ok += 1
        else:
            divergent.append({'file': m['file'], 'title': m['title'],
                              'diff': m['diff'],
                              'delta': {FIELDS[j]: mine[j] - theirs[j]
                                        for j in range(6) if mine[j] != theirs[j]}})
        if (i + 1) % 2000 == 0:
            print('... %d/%d' % (i + 1, len(manifest)), flush=True)

    print('counter vs arcade-official T_JUDGE: %d/%d exact (%.1f%%), %d divergent (%.0fs)'
          % (ok, have, 100.0 * ok / max(have, 1), len(divergent),
             time.perf_counter() - t0))
    dpath = os.path.join(ROOT, 'out', 'tjudge_divergent.json')
    json.dump(divergent, open(dpath, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
    print('divergence list ->', dpath)


if __name__ == '__main__':
    main()

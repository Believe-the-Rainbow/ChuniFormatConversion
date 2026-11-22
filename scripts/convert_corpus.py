#!/usr/bin/env python3
"""convert_corpus.py -- convert every enabled chart of a arcade HDD into
UGC text files (verification corpus). No audio / jacket / library packing.

usage:
  python scripts/convert_corpus.py --hdd <A000 目录> [--out out/corpus_ugc]
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'lib'))

import c2s2sus as C       # noqa: E402
import ugc                # noqa: E402
import pack as P           # noqa: E402


def c2s_maxlane(path):
    mx = 0
    for ln in open(path, encoding='utf8', errors='replace'):
        m = re.match(r'^([A-Z]+)\s+-?\d+\s+-?\d+\s+(\d+)\s', ln)
        if m and m.group(1) not in ('BPM', 'MET', 'SFL', 'SLP') \
                and not m.group(1).startswith('T_'):
            mx = max(mx, int(m.group(2)))
    return mx


class Opts(object):
    format = 'ugc'
    soflan = True
    slide_relay = 'visible'
    slide_subdiv = 0
    exlong_carrier = True
    tail_bpm_fix = True
    first_relay = ''
    clamp_lane = False
    airslide = 'drop'
    ext_field = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hdd', required=True, help='A000 目录（含 music/ 与 cueFile/）')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'corpus_ugc'))
    ap.add_argument('--limit', type=int, default=0, help='只取前 N 首歌')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    music_root = os.path.join(args.hdd, 'music')
    songs = sorted(d for d in os.listdir(music_root) if d.startswith('music'))
    manifest = []
    n_charts = fails = 0
    t0 = time.perf_counter()

    for si, d in enumerate(songs):
        if args.limit and si >= args.limit:
            break
        mdir = os.path.join(music_root, d)
        if not os.path.isdir(mdir):
            continue
        sid = d[5:]
        try:
            info = P.read_music_xml(os.path.join(mdir, 'Music.xml'))
        except Exception as e:
            print('XML FAIL %s: %s' % (d, e))
            continue
        enabled = [f for f in info['fumen'] if f['enable'] and f['file']]
        if not enabled:
            continue
        ext_ids = set()
        for f in enabled:
            if f['id'] in (0, 1, 2, 3, 4):
                p = os.path.join(mdir, f['file'])
                if os.path.exists(p) and c2s_maxlane(p) > 15:
                    ext_ids.add(str(f['id']))
        opts = Opts()
        for f in enabled:
            fid = f['id']
            src = os.path.join(mdir, f['file'])
            if not os.path.exists(src):
                continue
            try:
                ch = C.parse_c2s(src)
                bt = C.BarTable(ch.met, ch.end_tick() + C.SUS_BAR)
            except Exception as e:
                print('PARSE FAIL %s: %s' % (src, e))
                fails += 1
                continue
            opts.ext_field = str(fid) in ext_ids
            we_attr = (info.get('we_tag') or '?') if fid == 5 else None
            level = P.chuni_level(f['level'], f['dec'])
            meta = dict(title=info['title'], artist=info['artist'],
                        designer=P.c2s_creator(src) or 'Unknown',
                        difficulty=P.DIFF_NAMES[fid][2], we_attr=we_attr,
                        level=('5' if fid == 5 else level), songid=sid,
                        wave='CHU_%s.wav' % sid, jacket='CHU_%s.png' % sid,
                        waveoffset='0', preview=None, bg='',
                        const='%.5f' % (f['level'] + f['dec'] / 100.0))
            try:
                text = ugc.UgcWriter(ch, bt, meta, opts).text()
            except Exception as e:
                print('WRITE FAIL %s: %s' % (src, e))
                fails += 1
                continue
            name = '%s_%02d.ugc' % (sid, fid)
            with open(os.path.join(args.out, name), 'w', encoding='utf-8',
                      newline='\n') as fh:
                fh.write(text)
            official = [ch.tstats.get('T_JUDGE_' + k) for k in
                        ('ALL', 'TAP', 'HLD', 'SLD', 'AIR', 'FLK')]
            manifest.append({'file': name, 'sid': sid, 'fid': fid,
                             'diff': P.DIFF_NAMES[fid][0],
                             'title': info['title'], 'official': official,
                             'ext_field': opts.ext_field})
            n_charts += 1
        if (si + 1) % 100 == 0:
            print('... %d/%d songs, %d charts, %.1fs'
                  % (si + 1, len(songs), n_charts, time.perf_counter() - t0))

    mpath = os.path.join(os.path.dirname(args.out.rstrip('/\\')), 'corpus_manifest.json')
    with open(mpath, 'w', encoding='utf8') as fh:
        json.dump(manifest, fh, ensure_ascii=False)
    print('done: %d charts from %d songs, %d fails, %.1fs -> %s'
          % (n_charts, len(songs), fails, time.perf_counter() - t0, args.out))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.py -- turn one arcade song folder of the HDD into a client-library
song folder (charts + audio + jacket).

usage:
  pack.py --music <HDD>/music/music2589 --cue <HDD>/cueFile/cueFile2589
        --out <client>/data/music/<genre>
                   --genre <genre> [--diffs 2,3] [--dist r2168]
"""

from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import c2s2sus as C   # noqa: E402
import ugc            # noqa: E402
import stagebg        # noqa: E402

DIFF_NAMES = {
    0: ('BASIC', 'BAS', 0),
    1: ('ADVANCED', 'ADV', 1),
    2: ('EXPERT', 'EXP', 2),
    3: ('MASTER', 'MAS', 3),
    4: ('ULTIMA', 'ULT', 5),
    5: ("WORLD'S END", 'WE', 4),
}


def read_music_xml(path):
    text = open(path, 'rb').read().decode('utf-8', 'replace')

    def g(pat, default=''):
        m = re.search(pat, text, re.S)
        return m.group(1) if m else default

    info = {
        'title': g(r'<name>\s*<id>-?\d+</id>\s*<str>(.*?)</str>'),
        'artist': g(r'<artistName>\s*<id>-?\d+</id>\s*<str>(.*?)</str>'),
        'genre': g(r'<StringID>\s*<id>\d+</id>\s*<str>(.*?)</str>'),
        'jacket': g(r'<jaketFile>\s*<path>(.*?)</path>'),
        'stage_id': g(r'<stageName>\s*<id>(-?\d+)</id>'),
        'cue': g(r'<cueFileName>\s*<id>-?\d+</id>\s*<str>(.*?)</str>'),
        'we_tag': g(r'<worldsEndTagName>\s*<id>-?\d+</id>\s*<str>(.*?)</str>'),
        'fumen': [],
    }
    for blk in re.findall(r'<MusicFumenData>(.*?)</MusicFumenData>', text, re.S):
        fid = int(re.search(r'<type>\s*<id>(\d+)</id>', blk).group(1))
        enable = re.search(r'<enable>(true|false)</enable>', blk).group(1) == 'true'
        fpath = re.search(r'<path>(.*?)</path>', blk, re.S)
        level = re.search(r'<level>(\d+)</level>', blk)
        dec = re.search(r'<levelDecimal>(\d+)</levelDecimal>', blk)
        info['fumen'].append({
            'id': fid,
            'enable': enable,
            'file': (fpath.group(1) if fpath else ''),
            'level': int(level.group(1)) if level else 0,
            'dec': int(dec.group(1)) if dec else 0,
        })
    return info


def chuni_level(level, dec):
    """Card level style: 14.0-14.5 -> '14', 14.6-14.9 -> '14+'."""
    return '%d+' % level if dec >= 60 else '%d' % level


def c2s_creator(path):
    try:
        text = open(path, 'rb').read().decode('utf-8', 'replace')
        m = re.search(r'^CREATOR\t(.*)$', text, re.M)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return ''


def dds_to_png(src, dst):
    from PIL import Image
    im = Image.open(src)
    im.load()
    if im.mode not in ('RGB', 'RGBA'):
        im = im.convert('RGBA')
    im.save(dst, 'PNG')
    return im.size


def decode_audio(awb, dst, vgmstream, dist):
    # vgmstream-cli uses the ANSI API for file names, so decode to an ASCII
    # temp path first and move the result afterwards.
    tmpdir = tempfile.mkdtemp(prefix='umg', dir=os.path.dirname(vgmstream))
    tmp_wav = os.path.join(tmpdir, 'out.wav')
    cmd = [vgmstream, '-o', tmp_wav, '-i', awb]
    env = dict(os.environ)
    env['PATH'] = os.path.dirname(vgmstream) + os.pathsep + env.get('PATH', '')
    k = subprocess.run(cmd, capture_output=True, env=env)
    out = (k.stdout + k.stderr).decode('utf-8', 'replace')
    if not os.path.exists(tmp_wav) or os.path.getsize(tmp_wav) < 100000:
        raise RuntimeError('audio decode failed:\n' + out[-2000:])
    shutil.move(tmp_wav, dst)
    shutil.rmtree(tmpdir, ignore_errors=True)
    for line in out.splitlines():
        if 'encoding' in line or 'play duration' in line or 'channels' in line:
            print('   ' + line.strip())
    return dst


def safe_folder(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name).strip().rstrip('.')
    return name or 'song'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--music', required=True, help='.../music/musicXXXX')
    ap.add_argument('--cue', required=True, help='.../cueFile/cueFileXXXXXX')
    ap.add_argument('--out', required=True, help='generated client data/music/<genre>')
    ap.add_argument('--vgmstream', default=os.path.join(HERE, 'vgmstream', 'vgmstream-cli.exe'))
    ap.add_argument('--dist', type=int, default=7, help='vgmstream subsong index')
    ap.add_argument('--diffs', default='0,1,2,3')
    ap.add_argument('--format', choices=['ugc', 'sus', 'both'], default='ugc')
    ap.add_argument('--airslide', choices=['drop', 'sus'], default='drop')
    ap.add_argument('--preview', default='', help='song card preview "start,end" in seconds')
    ap.add_argument('--bg', choices=['stage', 'jacket', 'both', 'none'], default='stage')
    ap.add_argument('--exlong-carrier', dest='exlong_carrier', action='store_true', default=True,
                    help='write the x carrier so Ex holds/slides keep their gold head')
    ap.add_argument('--no-exlong-carrier', dest='exlong_carrier', action='store_false')
    ap.add_argument('--slide-subdiv', type=int, default=0,
                    help='split slide segments longer than N ticks (0 = off)')
    ap.add_argument('--slide-relay', choices=['all', 'visible'], default='all',
                    help='all = every slide point is a relay (blue node); visible = only SLD/SXD points')
    ap.add_argument('--no-soflan', dest='soflan', action='store_false', default=True)
    ap.add_argument('--no-tail-bpm-fix', dest='tail_bpm_fix', action='store_false',
                    default=True,
                    help='do not insert the one-tick tempo bump that keeps a long '
                         'note tail judgement points (default: on)')
    ap.add_argument('--first-relay', default='',
                    help='probe: mark the first segment end of slide chains as a '
                         'judged relay, for chains starting in this UGC bar range '
                         '(e.g. 89,90)')
    ap.add_argument('--clamp-lane', dest='clamp_lane', action='store_true',
                    help='clamp lanes into 0..15 (fallback for builds without EXFIELD)')
    ap.add_argument('--ext-field', default='',
                    help='comma list of fumen ids written with the v9 extended '
                         'field (base62 lane, -16..31); e.g. 4 for a ULTIMA chart')
    opts = ap.parse_args()

    music = os.path.abspath(opts.music)
    info = read_music_xml(os.path.join(music, 'Music.xml'))
    mid = re.search(r'music(\d+)', os.path.basename(music))
    songid = mid.group(1) if mid else os.path.basename(music)
    ascii_name = 'CHU_%s' % songid
    folder = safe_folder(info['title'])
    dest = os.path.join(os.path.abspath(opts.out), folder)
    os.makedirs(dest, exist_ok=True)

    print('song   : %s' % info['title'])
    print('artist : %s' % info['artist'])
    print('genre  : %s' % info['genre'])
    print('target : %s' % dest)

    # ---- audio -----------------------------------------------------------
    cue_dir = os.path.abspath(opts.cue)
    if not os.path.isdir(cue_dir):
        alt = os.path.join(os.path.dirname(cue_dir), 'cueFile' + songid.zfill(6))
        if os.path.isdir(alt):
            cue_dir = alt
    cue_id = songid
    if info.get('cue'):
        _m = re.search(r'music(\d+)', info['cue'])
        if _m:
            cue_id = _m.group(1)
    if not os.path.exists(os.path.join(cue_dir, 'music%s.awb' % cue_id)):
        alt = os.path.join(os.path.dirname(os.path.abspath(opts.cue)),
                           'cueFile' + cue_id.zfill(6))
        if os.path.isdir(alt):
            cue_dir = alt
    awo = os.path.join(cue_dir, 'music%s.awb' % cue_id)
    if not os.path.exists(awo):
        alt2 = os.path.join(cue_dir, 'music%04d.awb' % int(cue_id))
        if os.path.exists(alt2):
            awo = alt2
    awb = awo
    wave_name = ascii_name + '.wav'
    if os.path.exists(awb):
        print('audio  : %s' % awb)
        decode_audio(awb, os.path.join(dest, wave_name), opts.vgmstream, opts.dist)
    else:
        print('!! audio not found: %s' % awb)

    # ---- jacket ----------------------------------------------------------
    jacket_src = os.path.join(music, info['jacket']) if info['jacket'] else ''
    jacket_name = ascii_name + '.png'
    if jacket_src and os.path.exists(jacket_src):
        size = dds_to_png(jacket_src, os.path.join(dest, jacket_name))
        print('jacket : %s -> %s %s' % (os.path.basename(jacket_src), jacket_name, size))
    else:
        print('!! jacket not found: %s' % jacket_src)
        jacket_name = ''

    # ---- background (per-stage backdrop) ---------------------------------
    bg_name = ''
    hdd = os.path.dirname(os.path.dirname(os.path.abspath(music)))
    if opts.bg != 'none':
        src_bg = None
        if opts.bg in ('stage', 'both'):
            sid = int(info['stage_id']) if info.get('stage_id') else -1
            src_bg = stagebg.stage_image(hdd, sid)
            if src_bg:
                print('bg     : stage %s (%s)' % (sid, stagebg.stage_name(hdd, sid)))
        if not src_bg and opts.bg in ('jacket', 'both') and jacket_src:
            src_bg = jacket_src
            print('bg     : jacket (no real stage image for this song)')
        if src_bg:
            bg_name = ascii_name + '_bg.png'
            size = dds_to_png(src_bg, os.path.join(dest, bg_name))
            print('bg     : %s -> %s %s' % (os.path.basename(src_bg), bg_name, size))
    if not bg_name:
        print('bg     : none (stage uses the DUMMY placeholder / 3D-only)')

    # ---- charts ----------------------------------------------------------
    wanted = [int(x) for x in opts.diffs.split(',') if x.strip() != '']
    ext_ids = [x.strip() for x in (opts.ext_field or '').split(',') if x.strip()]

    # one shared song-card preview range, derived from the hardest chart
    preview = None
    ref = [f for f in info['fumen'] if f['enable'] and f['file']]
    ref.sort(key=lambda f: f['id'])
    if ref:
        refp = os.path.join(music, ref[-1]['file'])
        if os.path.exists(refp):
            _ch = C.parse_c2s(refp)
            _bt = C.BarTable(_ch.met, _ch.end_tick() + C.SUS_BAR)
            preview = C.pick_preview(_ch, _bt)
            print('preview: %.2fs -> %.2fs' % preview)
    if opts.preview:
        a, b = [float(x) for x in opts.preview.split(',')]
        preview = (a, b)
        print('preview (manual): %.2fs -> %.2fs' % preview)

    made = []
    for f in info['fumen']:
        if f['id'] not in wanted or not f['enable'] or not f['file']:
            continue
        src = os.path.join(music, f['file'])
        if not os.path.exists(src):
            print('!! missing fumen file: %s' % src)
            continue
        label, short, diff_id = DIFF_NAMES[f['id']]
        opts.ext_field = str(f['id']) in ext_ids
        ch = C.parse_c2s(src)
        bt = C.BarTable(ch.met, ch.end_tick() + C.SUS_BAR)
        level = chuni_level(f['level'], f['dec'])
        we_attr = (info.get('we_tag') or '?') if f['id'] == 5 else None
        meta = dict(title=info['title'], artist=info['artist'],
                    designer=c2s_creator(src) or 'Unknown',
                    difficulty=diff_id, we_attr=we_attr,
                    level=('5' if f['id'] == 5 else level), songid=songid,
                    wave=wave_name, jacket=jacket_name, waveoffset='0',
                    preview=preview, bg=bg_name,
                    const='%.5f' % (f['level'] + f['dec'] / 100.0))
        outs = []
        if opts.format in ('ugc', 'both'):
            p = os.path.join(dest, '[%s]%s.ugc' % (short, ascii_name))
            with open(p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(ugc.UgcWriter(ch, bt, meta, opts).text())
            outs.append(p)
        if opts.format in ('sus', 'both'):
            p = os.path.join(dest, '[%s]%s.sus' % (short, ascii_name))
            with open(p, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(C.write_sus(ch, bt, meta, opts))
            outs.append(p)
        if ch.tstats.get('T_JUDGE_ALL'):
            print('         original judgements: ALL=%s TAP=%s HLD=%s SLD=%s AIR=%s FLK=%s'
                  % (ch.tstats.get('T_JUDGE_ALL'), ch.tstats.get('T_JUDGE_TAP'),
                     ch.tstats.get('T_JUDGE_HLD'), ch.tstats.get('T_JUDGE_SLD'),
                     ch.tstats.get('T_JUDGE_AIR'), ch.tstats.get('T_JUDGE_FLK')))
        print('chart  : %-12s lv%-4s -> %s' % (label, level,
                                               ', '.join(os.path.basename(x) for x in outs)))
        made.append((label, outs))

    manifest = {
        'songid': songid, 'title': info['title'], 'artist': info['artist'],
        'genre': info['genre'], 'charts': [m[0] for m in made],
        'source': music, 'dest': dest,
    }
    sidecar = os.path.join(HERE, '..', 'out', 'convert_info_%s.json' % songid)
    os.makedirs(os.path.dirname(os.path.abspath(sidecar)), exist_ok=True)
    with open(sidecar, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print('done: %d chart(s)' % len(made))


if __name__ == '__main__':
    main()

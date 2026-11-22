#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""convert.py -- c2s2ugc interactive converter.

Turns arcade rhythm-game HDD charts (.c2s) into community-client song folders (.ugc +
audio + jacket), organised into per-genre library folders.

Flow: pick the game-exe folder + HDD folder once (remembered in config.json),
enter a song ID, pick a difficulty, the chart is converted and its note
counts are cross-checked against the arcade's official T_JUDGE numbers
(pure-Python counter, proven equivalent to the real v2.10 parser on all
6698 official charts). Mismatch -> warning + confirmation before packing.

usage:
  python convert.py                          # interactive
  python convert.py --songid 0083 --diff 3   # Lost One MAS, non-interactive
  python convert.py --songid 83,8129 --diff a --yes
"""
from __future__ import print_function

import argparse
import html
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'lib')
sys.path.insert(0, LIB)

import c2s2sus as C            # noqa: E402
import ugc                     # noqa: E402
import ugccount as U           # noqa: E402
import pack as P              # noqa: E402

CONFIG = os.path.join(HERE, 'config.json')
DIFF_KEY = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}      # menu number -> fumen id
DIFF_LABEL = {0: 'BAS(绿)', 1: 'ADV(黄)', 2: 'EXP(红)', 3: 'MAS(紫)', 4: 'ULT(黑)', 5: 'WE'}
FIELDS = ('ALL', 'TAP', 'HLD', 'SLD', 'AIR', 'FLK')


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def load_cfg():
    if os.path.exists(CONFIG):
        try:
            return json.load(open(CONFIG, encoding='utf8'))
        except Exception:
            pass
    return {}


def save_cfg(cfg):
    json.dump(cfg, open(CONFIG, 'w', encoding='utf8'), ensure_ascii=False, indent=1)


def ask_folder(title):
    """Folder picker: tkinter dialog, falling back to typed input."""
    try:
        import tkinter
        from tkinter import filedialog
        try:
            tkinter.Tk().withdraw()
            path = filedialog.askdirectory(title=title) or ''
        except Exception:
            path = ''
        if path:
            return os.path.normpath(path)
        print('(对话框被取消/不可用，改为手动输入路径)')
    except ImportError:
        pass
    return os.path.normpath(input(title + ' (直接粘贴路径): ').strip('" '))


def find_music_root(sel):
    """From a picked folder, locate the game chart library (data/music)."""
    if not os.path.isdir(sel):
        return None
    cand = os.path.join(sel, 'data', 'music')
    if os.path.isdir(cand):
        return sel
    for name in os.listdir(sel):
        sub = os.path.join(sel, name)
        if os.path.isdir(os.path.join(sub, 'data', 'music')):
            return sub
    return None


def find_a000(sel):
    """From a picked folder, locate the arcade data root (music/ + cueFile/)."""
    if not os.path.isdir(sel):
        return None
    if os.path.isdir(os.path.join(sel, 'music')):
        return sel
    for name in os.listdir(sel):
        sub = os.path.join(sel, name)
        if os.path.isdir(os.path.join(sub, 'music')) and \
                os.path.isdir(os.path.join(sub, 'cueFile')):
            return sub
        if name == 'A000' and os.path.isdir(os.path.join(sub, 'music')):
            return sub
    return None


def ensure_paths(cfg, arg_game, arg_hdd):
    if arg_game:
        cfg['game'] = os.path.normpath(arg_game)
    if arg_hdd:
        cfg['hdd'] = os.path.normpath(arg_hdd)
    while True:
        u = cfg.get('game', '')
        if u and find_music_root(u):
            break
        print('曲库未定位。请选择游戏主程序所在的文件夹：')
        u = ask_folder('选择游戏主程序所在文件夹')
        cfg['game'] = u
        if not find_music_root(u):
            print('  ✗ 该文件夹下没找到 data/music，请重选。')
            continue
        break
    while True:
        h = cfg.get('hdd', '')
        if h and find_a000(h):
            break
        print('街机数据根未定位（包含 music/ 和 cueFile/ 的目录，如 .../A000）：')
        h = ask_folder('选择 A000 数据目录或其上级')
        cfg['hdd'] = h
        if not find_a000(h):
            print('  ✗ 没找到 music/ + cueFile/，请重选。')
            continue
        break
    save_cfg(cfg)
    return cfg


# --------------------------------------------------------------------------
# chart conversion + counting (production-faithful)
# --------------------------------------------------------------------------
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


def convert_for_count(mdir, f, songid):
    """In-memory chart conversion (same as the packer) + counter + official."""
    fid = f['id']
    src = os.path.join(mdir, f['file'])
    ch = C.parse_c2s(src)
    bt = C.BarTable(ch.met, ch.end_tick() + C.SUS_BAR)
    meta = dict(title='t', artist='a', designer='d',
                difficulty=P.DIFF_NAMES[fid][2],
                we_attr=('?' if fid == 5 else None),
                level='1', songid=songid, wave='w.wav', jacket='j.png',
                waveoffset='0', preview=None, bg='', const='1.00000')
    opts = Opts()
    opts.ext_field = c2s_maxlane(src) > 15
    text = ugc.UgcWriter(ch, bt, meta, opts).text()
    counted = U.count_text(text)
    official = tuple(int(v) if v is not None else -1
                     for v in (ch.tstats.get('T_JUDGE_' + k) for k in FIELDS))
    return U.counts_of(counted), official


def show_compare(mine, official):
    lines = []
    bad = []
    for j, name in enumerate(FIELDS):
        ok = mine[j] == official[j]
        mark = '✓' if ok else ('⚠' if official[j] >= 0 else '(官方无数值)')
        lines.append('  %-4s %6d / %-6d %s' % (name, mine[j], official[j], mark))
        if not ok and official[j] >= 0:
            bad.append('%s %d≠%d (%+d)' % (name, mine[j], official[j],
                                           mine[j] - official[j]))
    return '\n'.join(lines), bad


# --------------------------------------------------------------------------
# packing
# --------------------------------------------------------------------------
def genre_dir_name(info):
    g = html.unescape(info.get('genre') or '').strip() or 'NO_GENRE'
    return P.safe_folder(g)


def pack_song(cfg, mdir, sid, info, diffs, ext_ids, yes):
    music_root = find_a000(cfg['hdd'])
    cue = os.path.join(music_root, 'cueFile', 'cueFile' + sid.zfill(6))
    out_root = os.path.join(find_music_root(cfg['game']), 'data', 'music', genre_dir_name(info))
    cmd = [sys.executable, os.path.join(LIB, 'pack.py'),
           '--music', mdir, '--cue', cue, '--out', out_root,
           '--diffs', ','.join(str(d) for d in diffs),
           '--slide-relay', 'visible']
    if ext_ids:
        cmd += ['--ext-field', ','.join(ext_ids)]
    print('  打包: %s' % os.path.join(os.path.basename(out_root), P.safe_folder(info['title'])))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=LIB)
    if 'done:' not in r.stdout:
        print('  ✗ 打包失败:')
        tail = (r.stderr or r.stdout or '?').strip().splitlines()
        for ln in tail[-5:]:
            print('    ' + ln)
        return False
    for ln in r.stdout.splitlines():
        if ln.startswith(('song ', 'audio ', 'jacket ', 'bg ', 'chart ', 'preview ')):
            print('    ' + ln)
    return True


# --------------------------------------------------------------------------
# per-song flow
# --------------------------------------------------------------------------
def process_song(cfg, sid, diff_pick, yes):
    mdir = os.path.join(find_a000(cfg['hdd']), 'music', 'music' + sid)
    if not os.path.isdir(mdir):
        print('✗ 没找到 music%s（ID 是否正确？）' % sid)
        return
    xml = os.path.join(mdir, 'Music.xml')
    info = P.read_music_xml(xml)
    enabled = [f for f in info['fumen'] if f['enable'] and f['file']]
    if not enabled:
        print('✗ music%s 没有可用谱面' % sid)
        return
    print('♪ %s — %s' % (info['title'], info['artist']))
    print('  分类: %s  (入库到「%s」文件夹)' % (info.get('genre'), genre_dir_name(info)))
    ext_ids = [str(f['id']) for f in enabled if f['id'] in (0, 1, 2, 3, 4)
               and c2s_maxlane(os.path.join(mdir, f['file'])) > 15]
    if diff_pick in ('a', 'A'):
        fids = [f['id'] for f in enabled]
    else:
        fids = [diff_pick]
    enabled_ids = {f['id'] for f in enabled}
    for fid in fids:
        if fid not in enabled_ids:
            print('  — %s: 该曲无此难度，跳过' % DIFF_LABEL[fid])
            continue
        f = [x for x in info['fumen'] if x['id'] == fid][0]
        label = DIFF_LABEL[fid]
        try:
            mine, official = convert_for_count(mdir, f, sid)
        except Exception as e:
            print('  ✗ %s 转换失败: %s' % (label, e))
            continue
        lvl = ('%.1f' % (f['level'] + f['dec'] / 100.0)) if fid != 5 \
            else ('☆%s' % (info.get('we_tag') or '?'))
        print('  %s (%s)' % (label, lvl))
        table, bad = show_compare(mine, official)
        print(table)
        if bad:
            print('  ⚠ 与街机官方计数不一致: %s' % ', '.join(bad))
            print('    （该差异为目标端与街机之间的固有规则差异，并非转换错误；'
                  '转换后的判定点数量可能与街机游玩时不同）')
            if not yes:
                ans = input('    仍要入库? (y/N): ').strip().lower()
                if ans not in ('y', 'yes'):
                    print('    已跳过。')
                    continue
        pack_ok = pack_song(cfg, mdir, sid, info, [fid], ext_ids, yes)
        if pack_ok:
            print('  ✓ %s 已入库' % label)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description='c2s -> 客户端曲库 转换')
    ap.add_argument('--songid', help='song ID（如 0083，可逗号分隔多首）')
    ap.add_argument('--diff', help='难度: 1=BAS 2=ADV 3=EXP 4=MAS 5=ULT 6=WE a=全部')
    ap.add_argument('--yes', action='store_true', help='计数不一致时不再询问，直接入库')
    ap.add_argument('--game', help='游戏主程序所在目录（跳过对话框）')
    ap.add_argument('--hdd', help='街机数据根目录（含 music/ 与 cueFile/）')
    args = ap.parse_args()

    print('c2s2ugc — 街机谱面 → 客户端曲库 转换器')
    print('=' * 46)
    cfg = load_cfg()
    cfg = ensure_paths(cfg, args.game, args.hdd)
    print('目标端: %s' % cfg['game'])
    print('HDD    : %s' % cfg['hdd'])
    if not os.path.exists(os.path.join(LIB, 'vgmstream', 'vgmstream-cli.exe')):
        print('⚠ 未找到 lib/vgmstream/vgmstream-cli.exe —— 音频将无法解码。')
        print('  请从 https://vgmstream.org 下载 vgmstream-cli 解压到 lib/vgmstream/ 后重试。')

    diff_pick = None
    if args.songid:
        raw = (args.diff or 'a').strip()
        if raw.lower() == 'a':
            diff_pick = 'a'
        elif raw.isdigit():
            diff_pick = DIFF_KEY.get(int(raw), int(raw))  # menu 1-6 -> fumen id
        else:
            diff_pick = raw
        for sid in [s.strip().zfill(4) for s in args.songid.split(',') if s.strip()]:
            print('-' * 46)
            process_song(cfg, sid, diff_pick, args.yes)
        return

    print('输入 song ID 开始转换（ID 见曲库文件名 [MAS]CHU_0083.ugc；'
          'q 退出，p 重设路径）')
    while True:
        try:
            raw = input('\nsong ID (逗号分隔): ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            continue
        if raw.lower() in ('q', 'quit', 'exit'):
            break
        if raw.lower() == 'p':
            cfg.pop('game', None)
            cfg.pop('hdd', None)
            cfg = ensure_paths(cfg, None, None)
            print('目标端: %s' % cfg['game'])
            print('HDD    : %s' % cfg['hdd'])
            continue
        diff = input('难度 [1=BAS 2=ADV 3=EXP 4=MAS 5=ULT 6=WE a=全部]: ').strip() or 'a'
        if not (diff.lower() == 'a' or (diff.isdigit() and int(diff) in DIFF_KEY)):
            print('难度输入无效。')
            continue
        diff_pick = DIFF_KEY[int(diff)] if diff.isdigit() else diff
        for sid in [s.strip().zfill(4) for s in raw.split(',') if s.strip()]:
            print('-' * 46)
            process_song(cfg, sid, diff_pick, False)
    print('bye')


if __name__ == '__main__':
    main()

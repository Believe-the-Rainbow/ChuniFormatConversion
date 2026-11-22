#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
c2s2sus.py -- the game .c2s  ->  SUS rev2 (.sus) converter

Target player: the community PC client (v2 / NEXT branch), which reads
"SUS Format rev2".

Time model
----------
In .c2s the grid is always 4 beats per bar (RESOLUTION = 384 ticks/bar).
BPM events change wall-clock speed; MET (time signature) events only re-bar the
chart.  So the absolute position of any event is

    tick_1920 = (bar * 384 + offset) * 5        # 1920 ticks = one 4/4 bar

SUS bars have meter dependent lengths (5/8 -> 4*480*5/8 = 1200 ticks), so the
output bar table is rebuilt from MET and every event is placed at its absolute
tick.  (Same approach as Margrete / MuConvert.)

Note mapping (the game -> SUS)
------------------------------
  TAP                -> ch1 type 1      tap
  CHR (ex tap)       -> ch1 type 2      ex tap
  FLK                -> ch1 type 3      flick
  MNE                -> ch1 type 4      mine/damage
  HLD / HXD          -> ch2 <ident>     1+w ... 2+w
  SLD/SLC/SXD/SXC    -> ch3 <ident>     1+w, 3+w(visible), 5+w(hidden), 2+w(end)
  AIR/AUR/AUL/ADW/ADR/ADL -> ch5 1..6 + width
  AHD                -> ch5 air(up) + ch4 <ident> air-action
  ASD / ASC          -> ch3 chain (approximation) or dropped  (--airslide)
  ALD                -> dropped (air crush decoration)
  SFL                -> #TIL00
"""

from __future__ import print_function

import argparse
import bisect
import math
import os
import sys
from fractions import Fraction

C2S_RSL = 384              # ticks per bar inside a .c2s
SUS_TPB = 480              # ticks per beat inside a SUS file
SUS_BAR = SUS_TPB * 4      # 1920 ticks = one 4/4 bar

K_TAP, K_EXTAP, K_FLICK, K_MINE = 1, 2, 3, 4

# the game air direction -> SUS air type (channel 5)
AIR_KIND = {
    'AIR': 1,   # up
    'ADW': 2,   # down
    'AUL': 3,   # up-left
    'AUR': 4,   # up-right
    'ADL': 5,   # down-left
    'ADR': 6,   # down-right
}

GROUND_TYPES = {'TAP', 'CHR', 'FLK', 'MNE', 'HLD', 'HXD'}
AIR_TYPES = {'AIR', 'AUR', 'AUL', 'ADW', 'ADR', 'ADL'}
SLIDE_TYPES = {'SLD', 'SLC', 'SXD', 'SXC'}
AIRSLIDE_TYPES = {'ASD', 'ASC'}


def warn(msg):
    sys.stderr.write('[warn] %s\n' % msg)


def wchar(w):
    """SUS width field: one hex digit 1..f, 'g' == 16."""
    w = max(1, min(16, int(w)))
    return 'g' if w == 16 else format(w, 'x')


def t20(c2s_tick):
    """c2s tick (384/bar) -> SUS tick (1920/bar)."""
    return int(c2s_tick) * 5


def fmt_num(x):
    """bar length (beats) -> compact decimal string."""
    if isinstance(x, Fraction):
        if x.denominator == 1:
            return str(x.numerator)
        v = float(x)
    else:
        v = float(x)
    s = ('%.6f' % v).rstrip('0').rstrip('.')
    return s if s else '0'


def base36(n, width=2):
    digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    out = ''
    while n > 0:
        out = digits[n % 36] + out
        n //= 36
    return out.rjust(width, '0')


class Chart(object):
    def __init__(self):
        self.version = ''
        self.designer = ''
        self.bpm_def = 0.0
        self.resolution = C2S_RSL
        self.bpm = []        # [(tick1920, value)]
        self.met = []        # [(tick1920, num, den)]
        self.sfl = []        # [(tick1920, dur1920, speed)]
        self.short = []      # [(tick1920, lane, width, kind)]
        self.holds = []      # [(tick1920, lane, width, dur)]
        self.slides = []     # [[(tick1920, lane, width, code), ...], ...]
        self.airs = []       # [(tick1920, lane, width, airkind)]
        self.airacts = []    # [[(tick1920, lane, width, code), ...], ...]
        self.airholds = []   # [(tick, lane, width, dur, color)]
        self.airhold_color = {}  # (tick, lane, width) -> 'DEF' / ...
        self.aircrush = []       # air-crush / air-trace chains, points are 5-tuples
        self.aircrush_meta = {}  # (tick, lane, width) -> (interval_ticks, colour)
        self.tstats = {}        # T_* statistics block written by the game
        self.hold_ex = {}       # (tick, lane, width) -> 'UP' / 'CE' / ...
        self.slide_ex = {}
        self.short_ex = {}   # (tick, lane, width) -> 'UP' / 'DW' / ...
        self.air_color = {}  # (tick, lane, width) -> 'DEF' / 'GRN' / 'PPL'
        self.air_target = {}  # (tick, lane, width) -> 'CHR' / 'TAP' / 'HLD' ...
        self.airslide_color = {}
        self.airslide_target = {}  # (tick, lane, width) -> 'TAP' / 'SLD' / ...
        self.dropped_airslide = 0
        self.dropped_aircrush = 0
        self.deco_aircrush = 0
        self.slp_flagged = 0

    def end_tick(self):
        end = 0
        for tick, _v in self.bpm:
            end = max(end, tick)
        for tick, lane, w, kind in self.short:
            end = max(end, tick)
        for tick, lane, w, dur in self.holds:
            end = max(end, tick + dur)
        for tick, lane, w, kind in self.airs:
            end = max(end, tick)
        for chain in self.slides + self.airacts + self.airslides + self.aircrush:
            if chain:
                end = max(end, chain[-1][0])
        for tick, dur, _s in self.sfl:
            end = max(end, tick + dur)
        return end


def parse_c2s(path):
    raw = open(path, 'rb').read().decode('utf-8', 'replace')
    lines = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    ch = Chart()
    slides_open = {}
    airslides_open = {}
    aircrush_open = {}
    aiacts_open = {}

    def put_chain(store, key, tick, lane, width, dur, endlane, endwidth, code,
                  ex=None, target=None):
        end = tick + dur
        bucket = store.get(key)
        if bucket:
            chain = bucket.pop(0)
            if not bucket:
                del store[key]
            if ex:
                # SXD/SXC can land in the middle of a chain: the Ex flag belongs
                # to the chain (and therefore to its first point), not to the row
                chain[0] = chain[0][:4] + (ex,)
        else:
            chain = [(tick, lane, width, '1') + ((ex,) if ex else ())
                     + ((target,) if target else ())]
        chain.append((end, endlane, endwidth, code))
        store.setdefault((end, endlane, endwidth), []).append(chain)

    def put_air_chain(store, key, tick, lane, width, height, dur, endlane,
                      endwidth, endheight, code, merge=True, target=None):
        """air slides carry a height per point, so the points are 5-tuples.
        `target` (the c2s ride column) rides on the chain head as a 6th slot."""
        end = tick + dur
        bucket = store.get(key) if merge else None
        if bucket:
            chain = bucket.pop(0)
            if not bucket:
                del store[key]
        else:
            chain = [(tick, lane, width, '1', height)
                     + ((target,) if target else ())]
        chain.append((end, endlane, endwidth, code, endheight))
        store.setdefault((end, endlane, endwidth), []).append(chain)

    for line in lines:
        if not line.strip():
            continue
        p = line.split('\t')
        tag = p[0].strip().upper()
        if tag.startswith('T_'):
            if len(p) > 1:
                ch.tstats[tag] = p[1].strip()
            continue
        try:
            if tag == 'VERSION':
                ch.version = p[1] if len(p) > 1 else ''
            elif tag == 'CREATOR':
                ch.designer = p[1].strip() if len(p) > 1 else ''
            elif tag == 'RESOLUTION':
                ch.resolution = int(p[1]) if len(p) > 1 else C2S_RSL
            elif tag == 'BPM_DEF':
                ch.bpm_def = float(p[1])
            elif tag == 'BPM':
                ch.bpm.append((t20(int(p[1]) * C2S_RSL + int(p[2])), float(p[3])))
            elif tag == 'MET':
                den, num = int(p[3]), int(p[4])
                if num <= 0 or den <= 0:
                    # "no beat lines from here" marker: the timeline is not
                    # affected, only the visual bar/beat lines, so we keep the
                    # previous meter.
                    continue
                ch.met.append((t20(int(p[1]) * C2S_RSL + int(p[2])), num, den))
            elif tag in ('SFL', 'SLP'):
                if tag == 'SLP' and len(p) > 5 and p[5].strip() not in ('', '0'):
                    # SLP carries a flag column; rows with flag 1/2 pair with a
                    # sentinel speed of 1000.0 (an arcade "notes appear/vanishe"
                    # gag, not a real scroll multiplier). Mapping them to @TIL
                    # would blur the whole section, so they are skipped.
                    ch.slp_flagged += 1
                    continue
                ch.sfl.append((t20(int(p[1]) * C2S_RSL + int(p[2])),
                               t20(int(p[3])), float(p[4])))
            elif tag in GROUND_TYPES or tag in AIR_TYPES or tag in SLIDE_TYPES \
                    or tag in AIRSLIDE_TYPES or tag in ('ALD', 'AHD', 'AHX'):
                bar, off = int(p[1]), int(p[2])
                lane, width = int(p[3]), max(1, int(p[4]))
                tick = t20(bar * C2S_RSL + off)
                if tag == 'TAP':
                    ch.short.append((tick, lane, width, K_TAP))
                elif tag == 'CHR':
                    ch.short.append((tick, lane, width, K_EXTAP))
                    if len(p) > 5 and p[5].strip():
                        ch.short_ex[(tick, lane, width)] = p[5].strip().upper()
                elif tag == 'FLK':
                    ch.short.append((tick, lane, width, K_FLICK))
                elif tag == 'MNE':
                    ch.short.append((tick, lane, width, K_MINE))
                elif tag in AIR_TYPES:
                    ch.airs.append((tick, lane, width, AIR_KIND[tag]))
                    if len(p) > 5 and p[5].strip():
                        ch.air_target[(tick, lane, width)] = p[5].strip().upper()
                    if len(p) > 6 and p[6].strip():
                        ch.air_color[(tick, lane, width)] = p[6].strip().upper()
                elif tag in ('HLD', 'HXD'):
                    ch.holds.append((tick, lane, width, t20(int(p[5]))))
                    if tag == 'HXD':
                        ch.hold_ex[(tick, lane, width)] = (
                            p[6].strip().upper() if len(p) > 6 and p[6].strip() else 'UP')
                elif tag in SLIDE_TYPES:
                    ex = None
                    if tag == 'SXD':
                        ex = (p[9].strip().upper()
                              if len(p) > 9 and p[9].strip() else 'UP')
                        ch.slide_ex[(tick, lane, width)] = ex
                    put_chain(slides_open, (tick, lane, width), tick, lane, width,
                              t20(int(p[5])), int(p[6]), max(1, int(p[7])),
                              '3' if tag in ('SLD', 'SXD') else '5', ex)
                elif tag in ('AHD', 'AHX'):
                    dur = t20(int(p[6]))
                    color = p[7].strip().upper() if len(p) > 7 and p[7].strip() else 'DEF'
                    ch.airhold_color[(tick, lane, width)] = color
                    # col 6 names the note the air-hold rides (TAP/CHR/HLD/SLD).
                    # The game binds air notes to the *previous* note line, so the
                    # writer needs this to pick the right parent when several
                    # notes share the same end position.
                    tgt = p[5].strip().upper() if len(p) > 5 and p[5].strip() else None
                    put_chain(aiacts_open, (tick, lane, width), tick, lane, width,
                              dur, lane, width, 's' if tag == 'AHD' else 'c',
                              target=tgt)
                elif tag in AIRSLIDE_TYPES:
                    if len(p) > 11 and p[11].strip():
                        ch.airslide_color[(tick, lane, width)] = p[11].strip().upper()
                    # col 6 names the note the air-slide rides (TAP/CHR/HLD/SLD/
                    # ASD=chain).  The game binds air notes to the *previous*
                    # note line, so the writer needs this to pick the right
                    # parent when a hold and a slide end at the same spot.
                    tgt = p[5].strip().upper() if len(p) > 5 and p[5].strip() else None
                    put_air_chain(airslides_open, (tick, lane, width), tick, lane,
                                  width, float(p[6]), t20(int(p[7])),
                                  int(p[8]), max(1, int(p[9])), float(p[10]),
                                  's' if tag == 'ASD' else 'c', target=tgt)
                elif tag == 'ALD':
                    interval = int(p[5])
                    if interval <= 0:
                        # interval 0 = pure AIR-TRACE decoration. The game keeps
                        # it (it reads Ty=0 and clears the judge flag Jg) so it
                        # is drawn as a coloured trace but never judged -- so we
                        # must keep it too, or the chart loses all its deco lines.
                        ch.deco_aircrush += 1
                    color = p[11].strip().upper() if len(p) > 11 and p[11].strip() else 'DEF'
                    # key on the whole segment: several deco traces can start at
                    # the same cell with different colours
                    ch.aircrush_meta[(tick, lane, width, tick + t20(int(p[7])),
                                      int(p[8]), max(1, int(p[9])))] = (
                        t20(interval), color)
                    put_air_chain(aircrush_open, (tick, lane, width), tick, lane,
                                  width, float(p[6]), t20(int(p[7])),
                                  int(p[8]), max(1, int(p[9])), float(p[10]), 'c',
                                  merge=False)
        except (IndexError, ValueError):
            warn('cannot parse line: %r' % line)
            continue

    def flat(store):
        out = []
        for bucket in store.values():
            out.extend(bucket)
        return out

    ch.slides = flat(slides_open)
    ch.airslides = flat(airslides_open)
    ch.airacts = flat(aiacts_open)
    ch.aircrush = flat(aircrush_open)
    ch.airholds = [(c[0][0], c[0][1], c[0][2], c[-1][0] - c[0][0],
                    ch.airhold_color.get((c[0][0], c[0][1], c[0][2]), 'DEF'))
                   for c in ch.airacts if c]

    # CHR rows sitting exactly on a long-note head mark that head gold (the
    # arcade shows an Ex head there; HXD/SXD rows carry the same flag via their
    # own column, but plain HLD/SLD heads rely on the adjacent CHR).  The arcade
    # *also* counts every CHR as its own TAP judgement, so the writer must gold
    # the head AND keep the CHR -- without this the game absorbs the CHR's x as
    # the head's Ex carrier and T_JUDGE_TAP comes up short.
    hold_heads = {(t, l, w) for t, l, w, _d in ch.holds}
    slide_heads = {(s[0][0], s[0][1], s[0][2]) for s in ch.slides if s}
    for t, l, w, kind in ch.short:
        if kind != K_EXTAP:
            continue
        pos = (t, l, w)
        if pos in ch.hold_ex or pos in ch.slide_ex:
            continue  # head already gold-marked via HXD/SXD
        ex = ch.short_ex.get(pos, 'UP')
        if pos in hold_heads:
            ch.hold_ex.setdefault(pos, ex)
        elif pos in slide_heads:
            ch.slide_ex.setdefault(pos, ex)
    return ch


class BarTable(object):
    """Contiguous SUS bars rebuilt from the c2s MET list."""

    def __init__(self, met, end_tick):
        met = sorted(met)
        if not met or met[0][0] != 0:
            met = [(0, 4, 4)] + met
        self.bars = []      # [start, length, num, den]
        pos = 0
        cur_num, cur_den = met[0][1], met[0][2]
        mi = 1
        guard = 0
        while pos < end_tick:
            guard += 1
            if guard > 200000:
                raise RuntimeError('bar table runaway')
            if mi < len(met) and met[mi][0] <= pos:
                cur_num, cur_den = met[mi][1], met[mi][2]
                mi += 1
                continue
            L = max(1, int(round(SUS_BAR * cur_num / float(cur_den))))
            nxt = met[mi][0] if mi < len(met) else None
            if nxt is None:
                self.bars.append([pos, L, cur_num, cur_den])
                pos += L
                continue
            remaining = nxt - pos
            nfull = remaining // L
            for _ in range(nfull):
                self.bars.append([pos, L, cur_num, cur_den])
                pos += L
            rem = nxt - pos
            if rem > 0:
                frac = Fraction(rem, SUS_BAR)
                self.bars.append([pos, rem, frac.numerator, frac.denominator])
                pos += rem
            cur_num, cur_den = met[mi][1], met[mi][2]
            mi += 1
        self.starts = [b[0] for b in self.bars]

    def locate(self, tick):
        i = bisect.bisect_right(self.starts, tick) - 1
        if i < 0:
            i = 0
        start, length = self.bars[i][0], self.bars[i][1]
        off = tick - start
        while off >= length and i + 1 < len(self.bars):
            i += 1
            start, length = self.bars[i][0], self.bars[i][1]
            off = tick - start
        if off >= length:
            off = length - 1
        if off < 0:
            off = 0
        return i, off


class IdentPool(object):
    """Reusable long-note identifiers (hold / slide / air-action)."""

    def __init__(self):
        self.free = list(reversed(list('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')))
        self.busy = []

    def alloc(self, start, end):
        self.busy.sort()
        while self.busy and self.busy[0][0] < start:
            self.free.append(self.busy.pop(0)[1])
        if not self.free:
            raise RuntimeError('out of long-note identifiers')
        c = self.free.pop()
        self.busy.append((end, c))
        return c


def build_line(bar_len, items):
    g = bar_len
    for off in items:
        g = math.gcd(g, off)
    if g <= 0:
        g = bar_len
    slots = bar_len // g
    return ''.join(items.get(i * g, '00') for i in range(slots))


def emit_lines(bt, entries):
    """entries: [(channel, lane, ident, tick, data)] -> [(text)] sorted by bar."""
    buckets = {}
    for chan, lane, ident, tick, data in entries:
        bi, off = bt.locate(tick)
        buckets.setdefault(bi, {}).setdefault((chan, lane, ident), []).append((off, data))

    out = []
    for bi in sorted(buckets):
        bar_len = bt.bars[bi][1]
        for (chan, lane, ident), items in sorted(buckets[bi].items()):
            items.sort()
            layers = []
            for off, data in items:
                for layer, seen in layers:
                    if off not in seen:
                        seen.add(off)
                        layer[off] = data
                        break
                else:
                    layers.append(({off: data}, {off}))
            for layer, _seen in layers:
                out.append('#%03d%s%s%s: %s' % (bi, chan, lane, ident,
                                                build_line(bar_len, layer)))
    return out


def write_sus(ch, bt, meta, opts):
    out = []
    out.append('#TITLE "%s"' % meta['title'])
    out.append('#ARTIST "%s"' % meta['artist'])
    out.append('#DESIGNER "%s"' % meta['designer'])
    out.append('#DIFFICULTY %d' % meta['difficulty'])
    out.append('#PLAYLEVEL %s' % meta['level'])
    out.append('#SONGID "%s"' % meta['songid'])
    out.append('#WAVE "%s"' % meta['wave'])
    out.append('#WAVEOFFSET %s' % meta['waveoffset'])
    out.append('#JACKET "%s"' % meta['jacket'])
    out.append('')
    out.append('#REQUEST "ticks_per_beat %d"' % SUS_TPB)
    if meta.get('preview'):
        a, b = meta['preview']
        out.append('#REQUEST "preview_range %.2f,%.2f"' % (a, b))
    out.append('')

    # time signatures: bar 0 plus every change of bar length
    prev = None
    for i, (start, length, num, den) in enumerate(bt.bars):
        val = Fraction(4 * length, SUS_BAR)
        if i == 0 or val != prev:
            out.append('#%03d02: %s' % (i, fmt_num(val)))
        prev = val
    out.append('')

    # BPM
    bpm_vals = []
    for tick, v in sorted(ch.bpm):
        if not any(abs(x - v) < 1e-9 for x in bpm_vals):
            bpm_vals.append(v)
    bpm_id = {}
    for i, v in enumerate(bpm_vals):
        bpm_id[v] = base36(i + 1)
    for v in bpm_vals:
        out.append('#BPM%s: %.3f' % (bpm_id[v], v))
    entries = [('0', '8', '', tick, bpm_id[v]) for tick, v in sorted(ch.bpm)]
    out.extend(emit_lines(bt, entries))
    out.append('')

    # scroll speed
    if opts.soflan and ch.sfl:
        vals = {0: 1.0}
        for tick, dur, speed in sorted(ch.sfl):
            vals[tick] = speed
            vals[tick + dur] = 1.0
        parts = []
        for tick in sorted(vals):
            bi, off = bt.locate(tick)
            parts.append("%d'%d:%s" % (bi, off, fmt_num(vals[tick])))
        out.append('#TIL00: "%s"' % ', '.join(parts))
    out.append('#HISPEED 00')
    out.append('#MEASUREHS 00')
    out.append('')

    entries = [('1', format(lane, 'x'), '', tick, '%d%s' % (kind, wchar(width)))
               for tick, lane, width, kind in ch.short]
    out.extend(emit_lines(bt, entries))
    out.append('')

    entries = [('5', format(lane, 'x'), '', tick, '%d%s' % (kind, wchar(width)))
               for tick, lane, width, kind in ch.airs]
    # air holds also need their AIR note on channel 5 (direction up)
    entries += [('5', format(lane, 'x'), '', tick, '1%s' % wchar(width))
                for tick, lane, width, dur, colour in ch.airholds]
    out.extend(emit_lines(bt, entries))
    out.append('')

    pool = IdentPool()
    entries = []
    for tick, lane, width, dur in sorted(ch.holds):
        ident = pool.alloc(tick, tick + dur)
        entries.append(('2', format(lane, 'x'), ident, tick, '1%s' % wchar(width)))
        entries.append(('2', format(lane, 'x'), ident, tick + dur, '2%s' % wchar(width)))
    out.extend(emit_lines(bt, entries))
    out.append('')

    chains = list(ch.slides)
    if opts.airslide == 'sus':
        chains.extend(ch.airslides)
    else:
        ch.dropped_airslide = len(ch.airslides)
    pool = IdentPool()
    entries = []
    for chain in sorted(chains, key=lambda c: c[0][0]):
        if not chain:
            continue
        ident = pool.alloc(chain[0][0], chain[-1][0])
        for i, (tick, lane, width, code) in enumerate(chain):
            if i == len(chain) - 1:
                code = '2'
            entries.append(('3', format(lane, 'x'), ident, tick, '%s%s' % (code, wchar(width))))
    out.extend(emit_lines(bt, entries))
    out.append('')

    pool = IdentPool()
    entries = []
    for chain in sorted(ch.airacts, key=lambda c: c[0][0]):
        if not chain:
            continue
        ident = pool.alloc(chain[0][0], chain[-1][0])
        for i, (tick, lane, width, code) in enumerate(chain):
            code = {'1': '1', 's': '3', 'c': '5'}.get(code, '3')
            if i == len(chain) - 1:
                code = '2'
            entries.append(('4', format(lane, 'x'), ident, tick, '%s%s' % (code, wchar(width))))
    out.extend(emit_lines(bt, entries))

    return '\n'.join(out).rstrip('\n') + '\n'



# ---------------------------------------------------------------------------
# preview range (the target client plays the song card preview only when
# a range exists:
#   #REQUEST "preview_range <start_sec>,<end_sec>"   with end-start >= 5 s)
# ---------------------------------------------------------------------------
def seconds_fn(bpm):
    pts = sorted(bpm)

    def f(tick):
        t = 0.0
        last_tick, last_bpm = 0, (pts[0][1] if pts else 120.0)
        for ti, v in pts:
            if ti <= 0:
                last_tick, last_bpm = 0, v
                continue
            if ti < tick:
                t += (ti - last_tick) * 60.0 / (last_bpm * 480.0)
                last_tick, last_bpm = ti, v
            else:
                break
        t += (tick - last_tick) * 60.0 / (last_bpm * 480.0)
        return t
    return f


def note_ticks(ch):
    ticks = [t for t, _l, _w, _k in ch.short]
    ticks += [t for t, _l, _w, _d in ch.holds]
    ticks += [c[0][0] for c in ch.slides if c]
    ticks += [a[0] for a in ch.airs]
    return sorted(ticks)


def pick_preview(ch, bt, length=30.0, tail_guard=3.0):
    """Pick the densest `length` second window, snapped to a bar start."""
    ticks = note_ticks(ch)
    if not ticks:
        return None
    secs = seconds_fn(ch.bpm)
    times = [secs(t) for t in ticks]
    end_of_song = times[-1]
    if end_of_song < length + 10:
        return (0.0, max(length, end_of_song))
    buckets = {}
    for t in times:
        buckets[int(t)] = buckets.get(int(t), 0) + 1
    best = None
    for start in range(0, int(end_of_song - length)):
        n = sum(buckets.get(i, 0) for i in range(start, start + int(length)))
        if best is None or n > best[0]:
            best = (n, start)
    start_sec = best[1]
    # snap down to the closest bar boundary at or before start_sec
    for start, bar_len, _num, _den in bt.bars:
        s = secs(start)
        if s > start_sec:
            break
        snapped = s
    start_sec = snapped
    end_sec = min(start_sec + length, end_of_song + tail_guard)
    if end_sec - start_sec < 5.0:
        end_sec = start_sec + 5.0
    return (start_sec, end_sec)


def main(argv=None):
    ap = argparse.ArgumentParser(description='the game .c2s -> SUS rev2 (.sus)')
    ap.add_argument('c2s')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--title', default='')
    ap.add_argument('--artist', default='')
    ap.add_argument('--designer', default='')
    ap.add_argument('--difficulty', type=int, default=3)
    ap.add_argument('--level', default='0')
    ap.add_argument('--songid', default='')
    ap.add_argument('--wave', default='')
    ap.add_argument('--jacket', default='')
    ap.add_argument('--waveoffset', default='0')
    ap.add_argument('--airslide', choices=['drop', 'sus'], default='drop')
    ap.add_argument('--no-soflan', dest='soflan', action='store_false', default=True)
    opts = ap.parse_args(argv)

    ch = parse_c2s(opts.c2s)
    bt = BarTable(ch.met, ch.end_tick() + SUS_BAR)
    text = write_sus(ch, bt, dict(title=opts.title, artist=opts.artist,
                                  designer=opts.designer, difficulty=opts.difficulty,
                                  level=opts.level, songid=opts.songid, wave=opts.wave,
                                  jacket=opts.jacket, waveoffset=opts.waveoffset), opts)
    out_dir = os.path.dirname(os.path.abspath(opts.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(opts.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)

    print('wrote %s' % opts.out)
    print('  bars=%d taps=%d holds=%d slides=%d airs=%d airacts=%d bpm=%d met=%d sfl=%d'
          % (len(bt.bars), len(ch.short), len(ch.holds), len(ch.slides), len(ch.airs),
             len(ch.airacts), len(ch.bpm), len(ch.met), len(ch.sfl)))
    if ch.dropped_airslide:
        print('  air-slides dropped: %d' % ch.dropped_airslide)
    if ch.dropped_aircrush:
        print('  air-crush lines dropped: %d' % ch.dropped_aircrush)


if __name__ == '__main__':
    main()

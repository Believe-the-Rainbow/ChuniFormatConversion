#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ugc.py -- the game .c2s -> the target client native chart (.ugc) + a .ugc reader.

Unlike SUS (the Project Sekai / community format, which has no air-slide),
UGC has native AIR-SLIDE, AIR-HOLD, AIR and DAMAGE notes, so converting to UGC
keeps every note of the original chart.

Syntax (client chart v9 spec,
https://gist.github.com/inonote/5c01e73781cab17765a1d93641d52298):

    #Bar'Tick:type<x><w><extra>      parent note
    #OffsetTick>type<x><w><extra>    child note, offset counted from the parent

    t tap     x ex tap (U/D/C/A/W/L/R/I)     f flick (A/L/R)     d damage
    h hold (+ >s end)
    s slide (+ >s / >c followers)
    a air (UC/UL/UR/DC/DL/DR + N/I colour)
    H air hold (+ >s / >c)
    S air slide (+ >s / >c, each point carries a height)
    C air crush

x / w are single base-36 digits (lane 0..15, width 1..16).  Heights are two
base-36 digits of (c2s_height - 1) * 2 * 10, i.e. the default height 5.0 is "28"
(this needs @FLAG HIPRECISION TRUE).
"""

from __future__ import print_function

import argparse
import os
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import c2s2sus as C   # noqa: E402

B36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
NEWLINE = chr(10)

AIRDIR = {1: 'UC', 2: 'DC', 3: 'UL', 4: 'UR', 5: 'DL', 6: 'DR'}
EXDIR = {'UP': 'U', 'DW': 'D', 'CE': 'C', 'LS': 'L', 'RS': 'R', 'RC': 'A',
         'LC': 'W', 'BS': 'I'}
SHORT_KIND = {C.K_TAP: 't', C.K_EXTAP: 'x', C.K_FLICK: 'f', C.K_MINE: 'd'}
# air-crush colour field (client chart spec)
CRUSH_COLOUR = {'DEF': '0', 'NON': 'Z', 'RED': '1', 'ORN': '2', 'YEL': '3',
                'LIM': '4', 'GRN': '5', 'AQA': '6', 'CYN': '7', 'DGR': '8',
                'BLU': '9', 'VLT': 'A', 'PPL': 'Y', 'PNK': 'B', 'GRY': 'C',
                'BLK': 'D'}


B62 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
LANE_BIAS = 16          # v9: X is base62 with -16 offset -> lane -16..31


def enc_lane(n, ext=False):
    """v8: base36 lane digit (0..35). v9 + EXFIELD: base62, lane -16..31."""
    n = int(n)
    if ext:
        i = n + LANE_BIAS
        if not 0 <= i <= 61:
            raise ValueError('lane out of v9 range: %r' % (n,))
        return B62[i]
    if n < 0 or n > 35:
        raise ValueError('lane out of v8 range: %r' % (n,))
    return B36[n]


def b36(n):
    n = int(n)
    if n < 0 or n > 35:
        raise ValueError('base36 digit out of range: %r' % (n,))
    return B36[n]


def h36(ch):
    return int(ch, 36)


def enc_height(h):
    """c2s height (5.0 = default) -> single base-36 digit, 1 char like
    the official EXAMPLE/Example/basic.ugc (@VER 6, no HIPRECISION)."""
    v = int(round((float(h) - 1.0) * 2.0 * 10.0))
    v = max(0, min(1295, v))
    return B36[v // 36] + B36[v % 36]


def dec_height(s):
    v = 0
    for ch in s:
        v = v * 36 + h36(ch)
    return v


def air_colour(kind, c2s_colour):
    """N = normal, I = inverted (down airs are pink by default)."""
    if kind >= 2:
        return 'I' if c2s_colour == 'GRN' else 'N'
    return 'I' if c2s_colour == 'PPL' else 'N'


def mark_of(code):
    """c2s slide point code -> UGC follower marker."""
    return 's' if code in ('3', 's') else 'c'


def bpm_div(b):
    """chartParser's note division from a BPM value."""
    if b is None:
        return 16
    return (32 if b < 60 else 16 if b < 120 else 8 if b < 240
            else 4 if b < 480 else 2 if b < 960 else 1)


class UgcWriter(object):
    def __init__(self, ch, bt, meta, opts):
        self.ch = ch
        self.bt = bt
        self.meta = meta
        self.opts = opts
        self.unlinked_airs = 0

    def L(self, lane):
        ext = getattr(self.opts, 'ext_field', False)
        lane = int(lane)
        if not ext and getattr(self.opts, 'clamp_lane', False):
            # fallback for builds without EXFIELD: squeeze the 48 lane field
            # back into the classic 0..15 lanes
            lane = 0 if lane < 0 else (15 if lane > 15 else lane)
        return enc_lane(lane, ext)

    def bartick(self, tick):
        bar, off = self.bt.locate(tick)
        return "%d'%d" % (bar, off)

    def header(self):
        m = self.meta
        out = ["' Converted from the game c2s (c2s2ugc.py)"]
        if getattr(self.opts, 'ext_field', False):
            out.append('@VER\t9')
        else:
            out.append('@VER\t8')
        out.append('@EXVER\t1')
        out.append('@TITLE\t%s' % m['title'])
        if m.get('sort'):
            out.append('@SORT\t%s' % m['sort'])
        out.append('@ARTIST\t%s' % m['artist'])
        out.append('@DESIGN\t%s' % m['designer'])
        out.append('@DIFF\t%d' % m['difficulty'])
        if m.get('we_attr'):
            # WORLD'S END: the game reads @WEATTR (it sets diff=4 + the attribute
            # glyph) instead of a plain @DIFF and switches to the WE card style.
            out.append('@WEATTR\t%s' % m['we_attr'])
        out.append('@LEVEL\t%s' % m['level'])
        out.append('@CONST\t%s' % m.get('const', '0.00000'))
        out.append('@SONGID\t%s' % m['songid'])
        out.append('@BGM\t%s' % m['wave'])
        out.append('@BGMOFS\t0.00000')
        if m.get('preview'):
            out.append('@BGMPRV\t%.2f\t%.2f' % m['preview'])
        else:
            out.append('@BGMPRV\t0.00000\t0.00000')
        out.append('@JACKET\t%s' % m['jacket'])
        out.append('@BGIMG\t%s' % m.get('bg', ''))
        out.append('@BGMODE\tPASSIVE\tFALSE')
        out.append('@FLDCOL\t0')
        out.append('@FLDIMG\t')
        out.append('@FLAG\tDIFFTTL\tFALSE')
        out.append('@FLAG\tSOFFSET\tFALSE')
        out.append('@FLAG\tCLICK\tTRUE')
        out.append('@FLAG\tEXLONG\tTRUE')
        out.append('@FLAG\tBGMWCMP\tFALSE')
        out.append('@FLAG\tHIPRECISION\tTRUE')
        if getattr(self.opts, 'ext_field', False):
            # v9: notes may sit outside the normal 16 lanes (-16..31)
            out.append('@FLAG\tEXFIELD\tTRUE')
        out.append('@TICKS\t480')
        prev = None
        for i in range(len(self.bt.bars)):
            key = (self.bt.bars[i][2], self.bt.bars[i][3])
            if key != prev:
                out.append('@BEAT\t%d\t%d\t%d' % (i, key[0], key[1]))
                prev = key
        bpm_all = dict(self.ch.bpm)
        bpm_all.update(self.tail_bpm_fix())
        for tick in sorted(bpm_all):
            out.append('@BPM\t%s\t%.5f' % (self.bartick(tick), bpm_all[tick]))
        if self.opts.soflan and self.ch.sfl:
            vals = {0: 1.0}
            for tick, dur, speed in sorted(self.ch.sfl):
                vals[tick] = speed
                vals[tick + dur] = 1.0
            for tick in sorted(vals):
                out.append('@TIL\t0\t%s\t%.5f' % (self.bartick(tick), vals[tick]))
        out.append('@MAINTIL\t0')
        out.append('@ENDHEAD')
        return out

    def build(self):
        if getattr(self, '_built', False):
            return self.objs
        ch = self.ch
        objs = []
        for tick, lane, width, kind in ch.short:
            objs.append(dict(k=SHORT_KIND[kind], tick=tick, lane=lane,
                             width=width, ex=ch.short_ex.get((tick, lane, width)),
                             airs=[]))
        for tick, lane, width, dur in ch.holds:
            objs.append(dict(k='h', tick=tick, lane=lane, width=width, dur=dur,
                             ex=ch.hold_ex.get((tick, lane, width)), airs=[]))
        for chain in ch.slides:
            ex = (chain[0][4] if len(chain[0]) > 4
                  else ch.slide_ex.get((chain[0][0], chain[0][1], chain[0][2])))
            objs.append(dict(k='s', tick=chain[0][0], lane=chain[0][1],
                             width=chain[0][2], chain=chain, ex=ex, airs=[]))
        for chain in ch.aircrush:
            key = (chain[0][0], chain[0][1], chain[0][2], chain[1][0],
                   chain[1][1], chain[1][2])
            interval, colour = ch.aircrush_meta.get(key, (0, 'DEF'))
            objs.append(dict(k='C', tick=key[0], lane=key[1], width=key[2],
                             chain=chain, interval=interval, colour=colour,
                             airs=[]))
        for chain in ch.airacts:
            objs.append(dict(k='H', tick=chain[0][0], lane=chain[0][1],
                             width=chain[0][2], chain=chain,
                             tgt=(chain[0][4] if len(chain[0]) > 4 and
                                  isinstance(chain[0][4], str) and
                                  chain[0][4] not in ('s', 'c', '1') else ''),
                             colour=ch.airhold_color.get(
                                 (chain[0][0], chain[0][1], chain[0][2]), 'DEF'),
                             airs=[]))
        for chain in ch.airslides:
            objs.append(dict(k='S', tick=chain[0][0], lane=chain[0][1],
                             width=chain[0][2], chain=chain,
                             tgt=(chain[0][5] if len(chain[0]) > 5 else ''),
                             colour=ch.airslide_color.get(
                                 (chain[0][0], chain[0][1], chain[0][2]), 'DEF'),
                             airs=[]))
        airs = [dict(k='a', tick=t, lane=l, width=w, dir=k,
                     colour=ch.air_color.get((t, l, w), 'DEF'))
                for t, l, w, k in ch.airs]

        # air notes and air slides must sit right after the note they belong to
        ground = [o for o in objs if o['k'] in 'txfdhs']
        ground.sort(key=lambda o: (o['tick'], o['lane']))

        def end_of(g):
            if g['k'] == 'h':
                return (g['tick'] + g['dur'], g['lane'], g['width'])
            if g['k'] == 's':
                last = g['chain'][-1]
                return (last[0], last[1], last[2])
            return (g['tick'], g['lane'], g['width'])

        # several notes can end at the same position (parallel slide chains do),
        # and every AIR/AIRSLIDE needs a *distinct* one of them: the game marks a
        # parent as used and drops any air that cannot claim its own, so we hand
        # out the parents of one position round-robin instead of all to the first.
        ends = {}
        for g in ground:
            ends.setdefault(end_of(g), []).append(g)
        SHORT_FOR = {'TAP': 't', 'CHR': 'x', 'FLK': 'f', 'MNE': 'd'}
        # the c2s names the long note an AIR/AIR-SLIDE rides (AIR col DEF /
        # ASD col 6).  When a hold and a slide end at the same position the
        # game binds the air to whichever line came *last* before it, so the
        # writer must attach the child to the named parent or the wrong long
        # note loses its end judgement (HLD/SLD swap).
        LONG_FOR = {'SLD': 's', 'HLD': 'h'}
        children = airs + [o for o in objs if o['k'] in 'SH']
        for child in children:
            cand = []
            cpos = (child['tick'], child['lane'], child['width'])
            if child['k'] == 'a':
                # the c2s names the note the air rides; when that is a short note
                # sitting at the same spot, the air belongs there -- not on the
                # long note that merely ends there (that one would reserve a
                # judgement step for the air and lose it)
                tgt = ch.air_target.get(cpos, '')
                kind = SHORT_FOR.get(tgt)
                if kind:
                    cand = [g for g in ground if g['k'] == kind
                            and g['tick'] == child['tick']
                            and g['lane'] == child['lane']
                            and g['width'] == child['width']]
                elif tgt in LONG_FOR:
                    cand = [g for g in ends.get(cpos, ())
                            if g['k'] == LONG_FOR[tgt]]
            elif child['k'] in 'SH':
                tgt = child.get('tgt', '')
                if tgt in LONG_FOR:
                    cand = [g for g in ends.get(cpos, ())
                            if g['k'] == LONG_FOR[tgt]]
                elif tgt in SHORT_FOR:
                    kind = SHORT_FOR[tgt]
                    cand = [g for g in ground if g['k'] == kind
                            and g['tick'] == child['tick']
                            and g['lane'] == child['lane']
                            and g['width'] == child['width']]
            if not cand:
                cand = list(ends.get((child['tick'], child['lane'], child['width']), ()))
            if not cand:
                cand = [g for g in ground if g['tick'] == child['tick']
                        and g['lane'] == child['lane'] and g['width'] == child['width']]
            if not cand:
                cand = [g for g in ground if g['tick'] == child['tick']
                        and g['lane'] <= child['lane'] < g['lane'] + g['width']]
            if not cand:
                earlier = [g for g in ground if g['tick'] <= child['tick']]
                cand = earlier[-1:]
            if cand:
                pick = min(cand, key=lambda g: len(g['airs']))
                pick['airs'].append(child)
            else:
                self.unlinked_airs += 1
        self.objs = sorted([o for o in objs if o['k'] not in 'SH'],
                           key=lambda o: (o['tick'],
                                          0 if o['k'] in 'txfd' else 1, o['lane']))
        self._built = True
        return self.objs

    def subdivide(self, chain):
        limit = getattr(self.opts, 'slide_subdiv', 0)
        if not limit:
            return chain
        out = [chain[0]]
        for p in chain[1:]:
            t0, l0, w0 = out[-1][0], out[-1][1], out[-1][2]
            dt = p[0] - t0
            if dt > limit:
                n = dt // limit
                for i in range(1, n + 1):
                    frac = float(i) / (n + 1)
                    out.append((int(t0 + dt * frac),
                                int(round(l0 + (p[1] - l0) * frac)),
                                max(1, int(round(w0 + (p[2] - w0) * frac))),
                                's', p[4] if len(p) > 4 else 0))
            out.append(p)
        return out

    def note_line(self, o):
        k = o['k']
        cell, width = self.L(o['lane']), b36(o['width'])
        bt = self.bartick(o['tick'])
        if k == 't':
            return '%s:t%s%s' % (bt, cell, width), []
        if k == 'x':
            return '%s:x%s%s%s' % (bt, cell, width,
                                   EXDIR.get(o['ex'] or 'UP', 'U')), []
        if k == 'f':
            return '%s:f%s%sA' % (bt, cell, width), []
        if k == 'd':
            return '%s:d%s%s' % (bt, cell, width), []
        if k == 'h':
            return '%s:h%s%s' % (bt, cell, width), ['%d>s' % o['dur']]
        if k == 'H':
            followers = ['%d>%s' % (p[0] - o['tick'], p[3])
                         for p in o['chain'][1:]]
            return ('%s:H%s%s%s' % (bt, cell, width,
                                    air_colour(1, o.get('colour', 'DEF'))),
                    followers)
        if k == 'C':
            followers = ['%d>c%s%s%s' % (p[0] - o['tick'], self.L(p[1]), b36(p[2]),
                                         enc_height(p[4]))
                         for p in o['chain'][1:]]
            interval = '$' if o['interval'] >= 9600 else str(o['interval'])
            colour = CRUSH_COLOUR.get(o.get('colour', 'DEF'), '0')
            # grammar depends on @VER:
            #   v8: C <x> <w> <hh> <colour> <axis> <interval>   (interval at substr(6))
            #   v9: C <x> <w> <hh> <colour> <axis> , <interval> (interval at substr(7))
            sep = ',' if getattr(self.opts, 'ext_field', False) else ''
            return ('%s:C%s%s%s%sN%s%s' % (bt, cell, width,
                                           enc_height(o['chain'][0][4]),
                                           colour, sep, interval),
                    followers)
        if k == 's':
            all_relay = getattr(self.opts, 'slide_relay', 'all') == 'all'
            chain = self.subdivide(o['chain'])
            fr = getattr(self.opts, 'first_relay', '') or ''
            in_range = False
            if fr:
                try:
                    a, b = [int(x) for x in fr.split(',')]
                    in_range = a <= o['tick'] // 1920 <= b
                except ValueError:
                    in_range = False
            out = []
            for i, p in enumerate(chain[1:]):
                mark = 's' if all_relay else mark_of(p[3])
                if in_range and i == 0:
                    mark = 's'          # probe: judge the very first segment too
                out.append('%d>%s%s%s' % (p[0] - o['tick'], mark,
                                          self.L(p[1]), b36(p[2])))
            followers = out
            return '%s:s%s%s' % (bt, cell, width), followers
        if k == 'S':
            chain = o['chain']
            followers = ['%d>%s%s%s%s' % (p[0] - o['tick'], mark_of(p[3]),
                                           self.L(p[1]), b36(p[2]),
                                           enc_height(p[4]))
                         for p in chain[1:]]
            return ('%s:S%s%s%s%s' % (bt, cell, width,
                                      enc_height(chain[0][4]),
                                      air_colour(1, o.get('colour', 'DEF'))),
                    followers)
        raise ValueError('unknown object kind %r' % k)

    def air_line(self, a):
        return '%s:a%s%s%s%s' % (self.bartick(a['tick']), self.L(a['lane']),
                                 b36(a['width']), AIRDIR[a['dir']],
                                 air_colour(a['dir'], a.get('colour', 'DEF')))

    def body(self):
        self.build()
        out = []
        short_pos = {(t, l, w) for t, l, w, _k in self.ch.short}
        # One Ex carrier per position at most: the game transfers the Ex flag to
        # *one* note at that position (the merge stops once a note is flagged,
        # `t.Jg !== 1`), so a second carrier on a parallel chain would survive as
        # an extra ExTAP and inflate the combo count.
        carrier_done = set()
        # a CHR on a gold long-note head doubles as that head's Ex carrier; the
        # game absorbs the carrier (t.Jg transfer) and drops its judgement, but
        # the arcade counts the CHR as its own TAP, so keep it as a plain t.
        gold_heads = {(o['tick'], o['lane'], o['width'])
                      for o in self.objs if o['k'] in 'hs' and o.get('ex')}
        for o in self.objs:
            pos = (o['tick'], o['lane'], o['width'])
            if (getattr(self.opts, 'exlong_carrier', False) and o.get('ex')
                    and o['k'] in 'hs' and pos not in short_pos
                    and pos not in carrier_done):
                carrier_done.add(pos)
                out.append('#%s:x%s%s%s' % (self.bartick(o['tick']),
                                            self.L(o['lane']), b36(o['width']),
                                            EXDIR.get(o['ex'], 'U')))
            if (o['k'] == 'x' and pos in gold_heads and not o['airs']):
                out.append('#%s:t%s%s' % (self.bartick(o['tick']),
                                          self.L(o['lane']), b36(o['width'])))
            line, followers = self.note_line(o)
            out.append('#' + line)
            out.extend('#' + f for f in followers)
            for child in sorted(o['airs'], key=lambda a: (a['lane'], a['tick'])):
                if child['k'] == 'a':
                    out.append('#' + self.air_line(child))
                else:
                    line, followers = self.note_line(child)
                    out.append('#' + line)
                    out.extend('#' + f for f in followers)
        return out

    def bpm_at(self, tick):
        best = None
        for t, v in self.ch.bpm:
            if t <= tick and (best is None or t >= best[0]):
                best = (t, v)
        if best is None:
            return self.ch.bpm_def or 120.0
        return best[1]

    def tail_bpm_fix(self):
        """Tempo bumps that keep a long note's tail judgement points.

        Only long notes whose END carries an AIR are affected: the parser then
        reserves one step computed from `np(head - 480)`, so a tempo change in
        that window costs judgement points.  A one-tick bump at that position
        makes the sampled tempo match the note itself.
        """
        if not getattr(self.opts, 'tail_bpm_fix', True):
            return {}
        real = dict(self.ch.bpm)
        extra = {}
        for o in self.objs:
            if o['k'] != 'h':
                continue
            end = o['tick'] + o['dur']
            if not any(a['k'] == 'a' and a['tick'] == end
                       and a['lane'] == o['lane'] and a['width'] == o['width']
                       for a in o['airs']):
                continue
            probe = o['tick'] - 480
            if probe < 0:
                continue
            want = self.bpm_at(o['tick'])
            if bpm_div(self.bpm_at(probe)) == bpm_div(want):
                continue
            k = probe
            while k in real or k in extra:
                k -= 1
            extra[k] = want                              # sampled tempo == note tempo
            keep = self.bpm_at(probe)
            if (k + 1) not in real and (k + 1) not in extra:
                extra[k + 1] = keep                      # restore the real tempo
        return extra

    def text(self):
        self.build()
        return NEWLINE.join(self.header() + [''] + self.body()).rstrip(NEWLINE) + NEWLINE


class UgcChart(object):
    def __init__(self):
        self.ver = 0
        self.title = ''
        self.flags = {}
        self.preview = None
        self.beats = []
        self.short = []
        self.holds = []
        self.airholds = []
        self.slides = []
        self.airslides = []
        self.aircrush = []
        self.airs = []
        self.bpm = []


class _Raw(object):
    __slots__ = ('marker', 'prefix', 'payload')

    def __init__(self, marker, prefix, payload):
        self.marker = marker
        self.prefix = prefix
        self.payload = payload


def read_ugc(path):
    raw = open(path, 'rb').read().decode('utf-8', 'replace')
    lines = raw.replace(chr(13) + chr(10), NEWLINE).replace(chr(13), NEWLINE).split(NEWLINE)
    ch = UgcChart()
    in_header = True
    raws = []
    bpm_raw = []
    for line in lines:
        line = line.rstrip()
        if not line or line.startswith("'"):
            continue
        if in_header:
            if line.startswith('@ENDHEAD'):
                in_header = False
                continue
            if not line.startswith('@'):
                continue
            parts = line[1:].split('\t')
            tag, val = parts[0], parts[1:]
            if tag == 'VER':
                ch.ver = int(val[0])
            elif tag == 'TITLE':
                ch.title = val[0] if val else ''
            elif tag == 'BEAT':
                ch.beats.append((int(val[0]), int(val[1]), int(val[2])))
            elif tag == 'BPM':
                bpm_raw.append((val[0], float(val[1])))
            elif tag == 'FLAG':
                ch.flags[val[0]] = (len(val) > 1 and val[1].upper() == 'TRUE')
            elif tag == 'BGMPRV':
                ch.preview = (float(val[0]), float(val[1]))
            continue
        if not line.startswith('#'):
            continue
        body = line[1:]
        sep = -1
        for i, c in enumerate(body):
            if c == ':' or c == '>':
                sep = i
                break
        if sep < 1 or sep + 1 >= len(body):
            continue
        raws.append(_Raw(body[sep], body[:sep], body[sep + 1:]))

    maxbar = 0
    for r in raws:
        if r.marker == ':':
            head = r.prefix.split("'")[0]
            if head.isdigit():
                maxbar = max(maxbar, int(head))
    # EXLONG: an x note stacked on a hold/slide head is the Ex attribute of that
    # long note, not a separate tap -> fold it back for the model
    xs = {}
    for i, (t, l, w, k) in enumerate(ch.short):
        if k == 'x':
            xs.setdefault((t, l, w), []).append(i)
    drop = set()
    for t, l, w, d in ch.holds:
        lst = xs.get((t, l, w))
        if lst:
            drop.add(lst.pop(0))
    for chain in ch.slides:
        if chain:
            key = (chain[0][0], chain[0][1], chain[0][2])
            lst = xs.get(key)
            if lst:
                drop.add(lst.pop(0))
    if drop:
        ch.short = [s for i, s in enumerate(ch.short) if i not in drop]

    for item in bpm_raw:
        head = item[0].split("'")[0]
        if head.isdigit():
            maxbar = max(maxbar, int(head))
    total = maxbar + 2
    beats = sorted(ch.beats) or [(0, 4, 4)]
    lengths = []
    num, den = 4, 4
    bi = 0
    for b in range(total):
        while bi < len(beats) and beats[bi][0] <= b:
            num, den = beats[bi][1], beats[bi][2]
            bi += 1
        lengths.append(int(round(C.SUS_BAR * num / float(den))))
    starts = []
    pos = 0
    for L in lengths:
        starts.append(pos)
        pos += L

    hiprec_flag = ch.flags.get('HIPRECISION', False)

    def hiprec():
        return hiprec_flag

    def air_height(kind, args):
        if kind not in 'SC' or len(args) < 3:
            return 0
        if hiprec() and len(args) >= 4:
            return dec_height(args[2:4])
        return h36(args[2])

    state = {'open': None}

    def flush():
        o = state['open']
        if o is None:
            return
        if o['kind'] == 'h':
            if o.get('dur') is not None:
                ch.holds.append((o['tick'], o['lane'], o['width'], o['dur']))
        elif o['kind'] == 'H':
            if o.get('dur') is not None:
                ch.airholds.append((o['tick'], o['lane'], o['width'], o['dur'],
                                    o.get('colour', 'N')))
        elif o['kind'] == 's':
            ch.slides.append(o['pts'])
        elif o['kind'] == 'S':
            ch.airslides.append(o['pts'])
        elif o['kind'] == 'C':
            ch.aircrush.append(o['pts'])
        state['open'] = None

    for r in raws:
        payload = r.payload
        type_char = payload[0]
        args = payload[1:]
        if r.marker == ':':
            bar_s, _, tick_s = r.prefix.partition("'")
            if not (bar_s.isdigit() and tick_s.isdigit()):
                continue
            tick = starts[int(bar_s)] + int(tick_s)
            lane = h36(args[0]) if len(args) > 0 else 0
            width = h36(args[1]) if len(args) > 1 else 1
            flush()
            if type_char in 'txfd':
                ch.short.append((tick, lane, width, type_char))
            elif type_char == 'a':
                ch.airs.append((tick, lane, width,
                                args[2:4] if len(args) >= 4 else '',
                                args[4] if len(args) > 4 else 'N'))
            elif type_char == 'h':
                state['open'] = dict(kind='h', tick=tick, lane=lane,
                                     width=width, dur=None)
            elif type_char == 'H':
                hip = hiprec()
                state['open'] = dict(kind='H', tick=tick, lane=lane,
                                     width=width, dur=None,
                                     colour=(args[2] if len(args) > 2 else 'N')
                                     if hip else (args[3] if len(args) > 3 else 'N'))
            elif type_char in 'sSC':
                h0 = air_height(type_char, args)
                state['open'] = dict(kind=type_char, tick=tick, lane=lane,
                                     width=width,
                                     pts=[(tick, lane, width, 'start', h0)],
                                     colour=args[-1] if type_char in 'SC' and args else 'N')
            continue

        o = state['open']
        if o is None:
            continue
        off = int(r.prefix) if r.prefix.isdigit() else 0
        tick = o['tick'] + off
        lane = h36(args[0]) if len(args) > 0 else o['lane']
        width = h36(args[1]) if len(args) > 1 else o['width']
        if o['kind'] in 'sSC':
            h = air_height(o['kind'], args)
            o['pts'].append((tick, lane, width, type_char, h))
        elif o['kind'] in 'hH':
            o['dur'] = off
    flush()


    # fold EXLONG carriers: an x line immediately followed by an h/s line at the
    # same bar'tick is that long note's Ex attribute, not a separate tap
    if ch.flags.get('EXLONG'):
        carriers = []
        for i, r in enumerate(raws[:-1]):
            nxt = raws[i + 1]
            if (r.marker == ':' and r.payload[0] == 'x' and nxt.marker == ':'
                    and nxt.payload[0] in 'hs' and nxt.prefix == r.prefix
                    and r.payload[1:3] == nxt.payload[1:3]):
                bar_s, _, tick_s = r.prefix.partition("'")
                if bar_s.isdigit() and tick_s.isdigit():
                    t = starts[int(bar_s)] + int(tick_s)
                    carriers.append((t, h36(r.payload[1]), h36(r.payload[2])))
        for key in carriers:
            for i, s_ in enumerate(ch.short):
                if s_[3] == 'x' and (s_[0], s_[1], s_[2]) == key:
                    del ch.short[i]
                    break

    for item in bpm_raw:
        bar_s, _, tick_s = item[0].partition("'")
        if bar_s.isdigit() and tick_s.isdigit():
            ch.bpm.append((starts[int(bar_s)] + int(tick_s), item[1]))
    ch.starts = starts
    return ch


def main():
    ap = argparse.ArgumentParser(description='c2s -> the target client .ugc')
    ap.add_argument('c2s')
    ap.add_argument('-o', '--out')
    ap.add_argument('--title', default='')
    ap.add_argument('--artist', default='')
    ap.add_argument('--designer', default='')
    ap.add_argument('--difficulty', type=int, default=3)
    ap.add_argument('--level', default='0')
    ap.add_argument('--songid', default='')
    ap.add_argument('--wave', default='')
    ap.add_argument('--jacket', default='')
    ap.add_argument('--const', default='0.00000')
    ap.add_argument('--preview', default='')
    ap.add_argument('--no-soflan', dest='soflan', action='store_false', default=True)
    opts = ap.parse_args()

    ch = C.parse_c2s(opts.c2s)
    bt = C.BarTable(ch.met, ch.end_tick() + C.SUS_BAR)
    preview = None
    if opts.preview:
        a, b = [float(x) for x in opts.preview.split(',')]
        preview = (a, b)
    w = UgcWriter(ch, bt, dict(title=opts.title, artist=opts.artist,
                               designer=opts.designer, difficulty=opts.difficulty,
                               level=opts.level, songid=opts.songid,
                               wave=opts.wave, jacket=opts.jacket,
                               const=opts.const, preview=preview), opts)
    text = w.text()
    if opts.out:
        d = os.path.dirname(os.path.abspath(opts.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(opts.out, 'w', encoding='utf-8', newline=NEWLINE) as fh:
            fh.write(text)
        print('wrote %s' % opts.out)
    else:
        sys.stdout.write(text)
    print('  tap/flick/mine=%d holds=%d slides=%d airholds=%d airslides=%d airs=%d unlinked_airs=%d'
          % (len(ch.short), len(ch.holds), len(ch.slides), len(ch.airholds),
             len(ch.airslides), len(ch.airs), w.unlinked_airs))


if __name__ == '__main__':
    main()

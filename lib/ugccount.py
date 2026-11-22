#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ugccount.py -- pure-Python reimplementation of the the target client v2.10 chart
parser's note counting (the `force_fixedjdg` / EXVER code path, i.e. the path
the game actually runs with default settings and our @EXVER 1 charts).

Counts produced: TOTAL (q$) and the five bH categories
  TAP = Z_   : taps + extap-family + mines + slide heads + hold heads
  HLD = UL   : hold head children (segments+tail, IZ-cut excluded) + beat steps
  SLD = he   : same for slides
  AIR = bR   : air heads + air-slide/air-hold chain points + air-hold steps
  FLK = eS   : flicks

The counting rules were implemented to exactly match the behaviour of the
official parser; equivalence was verified against all 6698 charts of the
official song data (see scripts/ for the reproducible checks).

usage:
  python ugccount.py chart.ugc                 # print TOTAL/TAP/HLD/SLD/AIR/FLK
  python ugccount.py --batch <dir>             # JSONL for a whole corpus
"""
from __future__ import print_function

import argparse
import json
import math
import os
import re
import sys

# ---- note type codes ----
MASK = 0xFF0
CAT_TAP = 0x10          # t2
CAT_HOLD = 0x20         # tD (hold end category)
CAT_SLIDE = 0x30        # th (slide end category)
CAT_AIR = 0x50          # tX

T_TAP = 0x11            # t3
T_EXDEF = 0x12          # t4
T_FLK = 0x13            # t5
T_MINE = 0x14           # t6
T_EX7 = 0x15            # t7  (xO/xC)
T_EX8 = 0x16            # t8  (xD)
T_EX9 = 0x17            # t9  (xA)
T_EXK = 0x18            # tK  (xW)
T_EXM = 0x19            # tM  (xL)
T_EXS = 0x1A            # tS  (xR)
T_EXJ = 0x1B            # tJ  (xI)
T_HOLDHEAD = 0x21       # tc
T_HOLDSEG = 0x22        # tH
T_SLIDEHEAD = 0x31      # tv
T_SLIDESEG = 0x32       # tf
T_SLIDEGREEN = 0x33     # tE
T_SLIDEMARK = 0x34      # 'v' child marker
T_SLIDEINV = 0x35       # ty (invisible interpolation node)
T_AIRCHAIN = 0x41       # tj (air-hold path chain head / wide-hold reclass source)
T_AIRSEG = 0x42         # tb
T_AIRSEG2 = 0x43        # tG
T_AIRTAIL = 0x44        # tu
T_AIR = 0x51            # tP (plain air head)
T_AIRDC = 0x52          # tz
T_AIRUL = 0x53          # tg
T_AIRUR = 0x54          # tA
T_AIRDL = 0x55          # tx
T_AIRDR = 0x56          # tZ
T_RELAY = 0x81          # tR (inserted beat step node)
T_BARLINE = 0x91        # tN
T_EXHOLD = 0x101        # tp
T_EXHOLD_O = 0x102      # tO
T_EXHOLD_D = 0x103      # td
T_EXHOLD_I = 0x104      # tI (invisible)
T_EXHOLD_F = 0x105      # tF
T_AHOLD = 0x201         # tm (C-line air-hold head)
T_AHOLD_K = 0x202       # tk
T_AHOLD_W = 0x203       # tW
T_AIRSTEP = 0x301       # tB
T_OLD_T = 0x401         # GI<8 air-hold
T_OLD_T2 = 0x402
T_CLICK = 0xA1          # 'c' line / generated clicks

VALID_WIDTHS = (1, 2, 3, 4, 6, 8, 16)
TW_SET = {T_EXDEF, T_EX7, T_EX8, T_EX9, T_EXK, T_EXM, T_EXS, T_EXJ}
HEAD_SPECIAL = {T_HOLDSEG, T_SLIDEGREEN, T_SLIDEINV, T_SLIDEMARK, T_AIRSEG2,
                T_AIRSEG, T_AIRTAIL, T_EXHOLD_I, T_AIRSTEP, T_AHOLD_K,
                T_OLD_T2, T_CLICK}
DEFAULT_BPM = 120.0
TICKS_PER_BAR = 1920.0

LINE_RE = re.compile(r"^([0-9']+)([:>])(\S*)$")


def vh(ch):
    """base62 lane char (0-9A-Za-z), invalid -> 0."""
    v = ord(ch[0])
    if 0x30 <= v < 0x3A:
        return v - 0x30
    if 0x41 <= v < 0x5B:
        return v - 0x41 + 10
    if 0x61 <= v < 0x7B:
        return v - 0x61 + 36
    return 0


def vi(s):
    """base-10 integer parse; invalid -> 0."""
    try:
        v = int(s, 10)
    except ValueError:
        return 0
    return v if -9007199254740991 <= v <= 9007199254740991 else 0


class Raw(object):
    """raw parsed note."""
    __slots__ = ('An', 'wc', 'oY', 'Wu', 'ZJ', 'Ru', 'l', 'sr', 'WG', 'HX',
                 '_q', 'oS', '_c', 'IZ', 'iJ', 'qp', 'Yk', 'xK', 'Hu')

    def __init__(self):
        self.An = 0
        self.wc = 0
        self.oY = 0
        self.Wu = 0.0
        self.ZJ = 0
        self.Ru = 0
        self.l = 0
        self.sr = 0
        self.WG = 0
        self.HX = 0
        self._q = 0
        self.oS = 0
        self._c = []
        self.IZ = False
        self.iJ = False
        self.qp = 0
        self.Yk = 0
        self.xK = 0
        self.Hu = False

    def clone(self):
        n = Raw()
        n.An, n.wc, n.oY, n.Wu, n.ZJ, n.Ru, n.l, n.sr = (self.An, self.wc, self.oY,
                                                         self.Wu, self.ZJ, self.Ru,
                                                         self.l, self.sr)
        n.WG, n.HX, n._q, n.oS, n.IZ, n.iJ, n.qp, n.Yk, n.xK = (self.WG, self.HX,
                                                                self._q, self.oS,
                                                                self.IZ, self.iJ,
                                                                self.qp, self.Yk,
                                                                self.xK)
        n._c = [c.clone() for c in self._c]
        return n


class Chart(object):
    def __init__(self):
        # parsed-data defaults
        self.GI = 0
        self.uJ = []            # @BPM: {wc, oY, UA}
        self.pB = []            # @BEAT: {wc, WG(ratio), Bk}
        self.se = []            # $e raw notes
        self.Ir = 0
        self.gt = 0
        self.Vz = False
        self.bV = False
        self.sJ = False
        self.ih = False
        self.QD = False
        self.sq = True
        self.AD = 0
        # chart stats defaults
        self.QY = 0x300
        self.We = True
        self.fa = False
        self.si = False
        self.ET = False
        self.chain = None
        self.q = 0              # q$
        self.Vc = 0
        self.bH = {'Z_': 0, 'he': 0, 'UL': 0, 'bR': 0, 'eS': 0}
        self.bY = []            # BPM changes: {Wu, UA} in file order

    # ---- measure-time conversion ----
    def eh(self, bar, oY):
        acc = 0.0
        prev = 0
        frac = 1.0
        b = bar if bar > 0 else 0
        for p in self.pB:
            if p[0] > b:
                break
            acc += (p[0] - prev) * TICKS_PER_BAR * frac
            frac = p[1]
            prev = p[0]
        acc += (b - prev) * TICKS_PER_BAR * frac
        return math.floor(acc + oY)

    # ---- bpm at tick (PL) ----
    def PL(self, tick):
        bpm = DEFAULT_BPM
        for e in self.bY:
            if tick < e[0]:
                break
            bpm = e[1]
        return bpm

    # ---- bpm subdivision table ----
    @staticmethod
    def div(bpm):
        if bpm < 60:
            return 32
        if bpm < 120:
            return 16
        if bpm < 240:
            return 8
        if bpm < 480:
            return 4
        if bpm < 960:
            return 2
        return 1

    def beat(self, tick):
        return TICKS_PER_BAR / self.div(self.PL(tick))


def lerp(a, b, t):
    return a * (1.0 - t) + b * t


def inv_lerp(a, b, t):
    if b == a:
        return float('nan')
    return (t - a) / (b - a)


# --------------------------------------------------------------------------
# header parsing
# --------------------------------------------------------------------------
FLAG_MAP = {
    'DIFFTTL': 'fa',
    'SOFFSET': 'soffset',
    'CLICK': 'click',
    'EXLONG': 'Vz',
    'BGMWCMP': None,
    'HIPRECISION': 'bV',
    'EXFIELD': 'ET',
}


def header(ch, parts):
    tag = parts[0]
    if tag == 'VER':
        if len(parts) >= 2:
            ch.GI = vi(parts[1])
    elif tag == 'EXVER':
        if len(parts) >= 2 and vi(parts[1]) > 0:
            ch.ih = True
    elif tag == 'TICKS':
        if len(parts) >= 2:
            ch.QY = 4 * vi(parts[1])
    elif tag == 'BPM':
        if len(parts) >= 3:
            bt = parts[1].split("'")
            ch.uJ.append((vi(bt[0]), vi(bt[1]) if len(bt) > 1 else 0,
                          float(parts[2])))
    elif tag == 'BEAT':
        if len(parts) >= 4:
            wc = vi(parts[1])
            num = vi(parts[2])
            den = vi(parts[3])
            ch.pB.append([wc, num / den if den else 0.0, num])
            if wc == 0:
                pass  # ez only matters for the (uncounted) click generation
            if wc <= 0:
                ch.QD = True
    elif tag == 'FLAG':
        if len(parts) >= 3:
            key = FLAG_MAP.get(parts[1])
            val = parts[2].strip().lower()
            truth = val in ('1', 'true', 'y', 'yes', 'enable', 'enabled')
            if key == 'fa':
                ch.fa = truth
                if ch.fa and ch.We:
                    ch.We = False
            elif key == 'soffset':
                ch.si = not truth
                if ch.si:
                    ch.We = True
            elif key == 'click':
                ch.We = truth
            elif key == 'Vz':
                ch.Vz = truth
            elif key == 'bV':
                ch.bV = truth
            elif key == 'ET':
                ch.ET = truth
    elif tag == 'ENDHEAD':
        ch.sJ = True


# --------------------------------------------------------------------------
# chain close
# --------------------------------------------------------------------------
def close_chain(ch):
    chain = ch.chain
    if chain is None:
        return
    ch.chain = None
    if len(chain._c) < 1:
        return
    last = chain._c[-1]
    if chain.An == T_SLIDEHEAD:
        last.An = T_SLIDESEG
    elif chain.An == T_HOLDHEAD:
        last.An = T_HOLDSEG
        if len(chain._c) > 1:
            return                      # multi-child hold chains are dropped
    elif chain.An == T_AIRCHAIN:
        last.An = T_AIRTAIL if last.An == T_AIRTAIL else T_AIRSEG
        for c in chain._c[:-1]:
            c.An = T_AIRSEG2
    elif chain.An == T_EXHOLD:
        last.An = T_EXHOLD_F if last.An == T_EXHOLD_I else T_EXHOLD_O
    elif chain.An == T_AHOLD:
        if last.Ru == 0:
            return                      # dropped
        if last.An == T_AIRSTEP:
            last.WG = 1
        last.An = T_AHOLD_W
        acc = []
        prev = chain
        base = chain.oY
        for c in chain._c:
            if c.An == T_AIRSTEP:
                acc.append(c)
            else:
                for p in acc:
                    x = inv_lerp(prev.oY - base, c.oY - base, p.oY - base)
                    p.ZJ = lerp(prev.ZJ, c.ZJ, x)
                    p.l = lerp(prev.l, c.l, x)
                    p.Ru = lerp(prev.Ru, c.Ru, x)
                acc = []
                prev = c
    elif chain.An == T_OLD_T:
        chain.An = T_AHOLD
        for c in chain._c:
            c.An = T_AHOLD_K
        last.An = T_AHOLD_W
    ch.se.append(chain)


# --------------------------------------------------------------------------
# note lines
# --------------------------------------------------------------------------
def parse_note_line(ch, body):
    m = LINE_RE.match(body.split('\t')[0])
    if not m:
        return
    is_child = m.group(2) == '>'
    bt = [0, 0]
    if not is_child:
        bt = m.group(1).split("'")
        if len(bt) < 2:
            return
    rest = m.group(3)
    if not rest:
        return
    tc = rest[0]
    rem = rest[1:]

    if is_child:
        chain = ch.chain
        if chain is None:
            return
        n = Raw()
        if chain.An == T_SLIDEHEAD:
            if len(rem) < 2:
                return
            n.An = T_SLIDEINV if tc == 'c' else (T_SLIDEMARK if tc == 'v' else T_SLIDEGREEN)
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
        elif chain.An == T_HOLDHEAD:
            n.An = T_HOLDSEG
            n.ZJ = chain.ZJ
            n.Ru = chain.Ru
        elif chain.An == T_AIRCHAIN:
            n.An = T_AIRTAIL if tc == 'c' else T_AIRSEG2
            n.ZJ = chain.ZJ
            n.Ru = chain.Ru
            n.l = chain.l
        elif chain.An == T_EXHOLD:
            if len(rem) < 3:
                return
            n.An = T_EXHOLD_I if tc == 'c' else T_EXHOLD_D
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
            if ch.bV:
                if len(rem) < 4:
                    return
                n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
            else:
                n.l = int(rem[2], 36)
        elif chain.An == T_AHOLD:
            if tc == 'c' or ch.GI >= 8:
                n.An = T_AHOLD_K
            else:
                n.An = T_AIRSTEP
                n.WG = 1
            if len(rem) >= 3:
                n.ZJ = vh(rem[0]) - ch.AD
                n.Ru = int(rem[1], 36)
                if ch.bV:
                    if len(rem) < 4:
                        return
                    n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
                else:
                    n.l = int(rem[2], 36)
        elif chain.An == T_OLD_T:
            if len(rem) < 3:
                return
            n.An = T_OLD_T2
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
            if ch.bV:
                if len(rem) < 4:
                    return
                n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
            else:
                n.l = int(rem[2], 36)
        else:
            return
        if n.An != 0:
            n.wc = chain.wc
            n.oY = chain.oY + vi(m.group(1))
            n.sr = ch.Ir
            ch.gt = n.An
            chain._c.append(n)
        return

    # ---- head line: close any open chain first ----
    close_chain(ch)
    n = Raw()
    eb = False
    if tc == 't':
        if len(rem) < 2:
            return
        n.An = T_TAP
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
    elif tc == 'x':
        if len(rem) < 3:
            return
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        k = rem[2]
        if k == 'D':
            n.An = T_EX8
        elif k in ('O', 'C'):
            n.An = T_EX7
        elif k == 'A':
            n.An = T_EX9
        elif k == 'W':
            n.An = T_EXK
        elif k == 'L':
            n.An = T_EXM
        elif k == 'R':
            n.An = T_EXS
        elif k == 'I':
            n.An = T_EXJ
        else:
            n.An = T_EXDEF
    elif tc == 'f':
        if len(rem) < 3:
            return
        n.An = T_FLK
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        if rem[2] == 'L':
            n.WG = 1
        elif rem[2] == 'R':
            n.WG = 2
    elif tc == 'd':
        if len(rem) < 2:
            return
        n.An = T_MINE
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
    elif tc == 'c':
        n.An = T_CLICK
        n.Ru = 16
    elif tc == 'a':
        if len(rem) < 5:
            return
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        d = rem[2:4]
        if d == 'DC':
            n.An = T_AIRDC
        elif d == 'UL':
            n.An = T_AIRUL
        elif d == 'UR':
            n.An = T_AIRUR
        elif d == 'DL':
            n.An = T_AIRDL
        elif d == 'DR':
            n.An = T_AIRDR
        else:
            n.An = T_AIR
        if rem[4:] == 'I':
            n.WG = 1
        n.xK = ch.gt
        n.l = 8
    elif tc == 's':
        if len(rem) < 2:
            return
        n.An = T_SLIDEHEAD
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        if ch.GI >= 9:
            n.HX = 1 if rem[2:3] == 'Z' else 0
        eb = True
    elif tc == 'h':
        if len(rem) < 2:
            return
        n.An = T_HOLDHEAD
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        eb = True
    elif tc == 'S':
        if ch.GI >= 5:
            if len(rem) < 4:
                return
            air = Raw()
            air.An = T_AIR
            air.ZJ = vh(rem[0]) - ch.AD
            air.Ru = int(rem[1], 36)
            if not ch.ih and air.Ru not in VALID_WIDTHS:
                return
            air.wc = vi(bt[0])
            air.oY = vi(bt[1])
            air.sr = ch.Ir
            if rem[4:5] == 'I' if ch.bV else rem[3:4] == 'I':
                air.WG = 1
            air.xK = ch.gt
            air.l = 8
            air.oS = 5
            ch.gt = air.An
            ch.se.append(air)
            n = Raw()
        elif len(rem) < 3:
            return
        if len(rem) < 3:
            return
        n.An = T_EXHOLD
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        if ch.bV:
            if len(rem) < 4:
                return
            n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
        else:
            n.l = int(rem[2], 36)
        eb = True
    elif tc == 'H':
        if ch.GI >= 8:
            air = Raw()
            air.An = T_AIR
            air.ZJ = vh(rem[0]) - ch.AD
            air.Ru = int(rem[1], 36)
            if not ch.ih and air.Ru not in VALID_WIDTHS:
                return
            air.wc = vi(bt[0])
            air.oY = vi(bt[1])
            air.sr = ch.Ir
            if rem[2:3] == 'I':
                air.WG = 1
            air.xK = ch.gt
            air.l = 8
            air.oS = 5
            ch.gt = air.An
            ch.se.append(air)
            n = Raw()
            n.An = T_AIRCHAIN
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
            n.l = 8
        elif ch.GI >= 5:
            if len(rem) < 4:
                return
            air = Raw()
            air.An = T_AIR
            air.ZJ = vh(rem[0]) - ch.AD
            air.Ru = int(rem[1], 36)
            if not ch.ih and air.Ru not in VALID_WIDTHS:
                return
            air.wc = vi(bt[0])
            air.oY = vi(bt[1])
            air.sr = ch.Ir
            if rem[4:5] == 'I' if ch.bV else rem[3:4] == 'I':
                air.WG = 1
            air.xK = ch.gt
            air.l = 8
            air.oS = 5
            ch.gt = air.An
            ch.se.append(air)
            n = Raw()
            n.An = T_AIRCHAIN
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
            n.l = 8
        else:
            if len(rem) < 3:
                return
            n.An = T_AIRCHAIN
            n.ZJ = vh(rem[0]) - ch.AD
            n.Ru = int(rem[1], 36)
            if ch.bV:
                if len(rem) < 4:
                    return
                n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
            else:
                n.l = int(rem[2], 36)
        eb = True
    elif tc == 'C':
        need = 4
        if ch.GI >= 9:
            need += 1
        if ch.bV:
            need += 1
        if len(rem) < need:
            return
        n.An = T_AHOLD
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        n.WG = 1
        if ch.bV:
            n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
            n._q = int(rem[4], 36)
            tailc = rem[5:6]
        else:
            n.l = int(rem[2], 36)
            n._q = int(rem[3], 36)
            tailc = rem[4:5]
        if tailc == 'Y':
            n.HX = 1
        elif tailc == 'Z':
            n.HX = 2
        else:
            n.HX = 0
        # _q remapping (GI 4/5 tables) affects sound selection only, not counts
        if ch.GI >= 8:
            pos = (6 if ch.bV else 5) + (1 if ch.GI >= 9 else 0)
            tail = rem[pos:]
            n.Yk = 0x7FFFFFFF if tail == '$' else vi(tail)
            if n.Yk == 0:
                n.WG = 0
        eb = True
    elif tc == 'T':
        if len(rem) < 4 or ch.GI >= 8:
            return
        n.An = T_OLD_T
        n.ZJ = vh(rem[0]) - ch.AD
        n.Ru = int(rem[1], 36)
        if ch.bV:
            if len(rem) < 4:
                return
            n.l = (36 * int(rem[2], 36) + int(rem[3], 36)) / 10.0
            n._q = int(rem[4], 36)
        else:
            n.l = int(rem[2], 36)
            n._q = int(rem[3], 36)
        # _q remapping (GI 4/5 tables) affects sound selection only, not counts
        eb = True
    else:
        return

    # ---- common tail (heads) ----
    if n.An == 0:
        return
    if n.Ru <= 0:
        return
    if not (ch.ih or n.An in HEAD_SPECIAL or n.Ru in VALID_WIDTHS):
        return
    if (n.An & MASK) == CAT_AIR:
        n.oS = 5
        n.l = 8
    n.wc = vi(bt[0])
    n.oY = vi(bt[1])
    n.sr = ch.Ir
    ch.gt = n.An
    if eb:
        ch.chain = n
    else:
        ch.se.append(n)


# --------------------------------------------------------------------------
# post-parse passes
# --------------------------------------------------------------------------
def post_parse(ch):
    if not ch.QD:
        ch.pB.append([0, 1.0, 4])
    ch.pB.sort(key=lambda p: p[0])

    # BPM change ticks (finalize head: bY from uJ, eh(wc, oY) unscaled)
    for wc, oY, ua in ch.uJ:
        ch.bY.append((ch.eh(wc, oY), ua))

    # convert raw measures to ticks
    maxtick = 0
    for r in ch.se:
        r.Wu = ch.eh(r.wc, r.oY * TICKS_PER_BAR / ch.QY)
        if r.Wu > maxtick:
            maxtick = r.Wu
        for c in r._c:
            c.Wu = ch.eh(c.wc, c.oY * TICKS_PER_BAR / ch.QY)
            if c.Wu > maxtick:
                maxtick = c.Wu

    # order: reverse + stable sort (Wu asc, oS desc); the bar-line (tN) and
    # click generation only add uncounted objects and are skipped here.
    ch.se.reverse()
    ch.se.sort(key=lambda r: (r.Wu, -r.oS))

    # ---- air binding (xK match) ----
    for air in ch.se:
        if (air.An & MASK) != CAT_AIR:
            continue
        bound = False
        for r2 in ch.se:
            if (air.Wu == r2.Wu and air.ZJ == r2.ZJ and air.Ru == r2.Ru
                    and air.xK == r2.An and (r2.An & MASK) == CAT_TAP
                    and not r2.IZ):
                r2.IZ = True
                air.xK = r2.An
                air.sr = r2.sr
                bound = True
                break
            if ((air.xK & MASK) == CAT_HOLD and r2.An == T_HOLDHEAD) or \
               ((air.xK & MASK) == CAT_SLIDE and r2.An == T_SLIDEHEAD):
                hold = r2.An == T_HOLDHEAD
                tail = None
                for c in reversed(r2._c):
                    if c.An == T_HOLDSEG or c.An == T_SLIDESEG:
                        tail = c
                        break
                if tail is not None and air.Wu == tail.Wu and \
                        air.ZJ == (r2.ZJ if hold else tail.ZJ) and \
                        air.Ru == (r2.Ru if hold else tail.Ru) and not tail.IZ:
                    tail.IZ = True
                    air.xK = tail.An
                    air.sr = tail.sr
                    bound = True
                    break
        if not bound:
            air.An = 0

    # ---- tP <-> tj/tp same-position binding (iJ) ----
    for p in ch.se:
        if p.An != T_AIR or p.iJ:
            continue
        for r2 in ch.se:
            if (p.Wu == r2.Wu and p.ZJ == r2.ZJ and p.Ru == r2.Ru
                    and (r2.An == T_AIRCHAIN or r2.An == T_EXHOLD)
                    and not r2.IZ):
                r2.IZ = True
                p.iJ = True
                p.l = r2.l
                r2.WG = p.WG
                r2.sr = p.sr
                break

    # ---- unbound chains die; lane-boundary check ----
    lim = 0x30 if ch.ET else 0x10
    for r in ch.se:
        if (r.An == T_AIRCHAIN or r.An == T_EXHOLD) and not r.IZ:
            r.An = 0
        if ch.ET:
            if r.ZJ + r.Ru > 0x30:
                ch.sq = False
            else:
                for c in r._c:
                    if round(c.ZJ + c.Ru) > 0x30:
                        ch.sq = False
                        break
        else:
            if r.ZJ + r.Ru > 0x10 or r.ZJ < 0:
                ch.sq = False
            else:
                for c in r._c:
                    if round(c.ZJ + c.Ru) > 0x10 or r.ZJ < 0:
                        ch.sq = False
                        break

    # ---- EXVER/EXLONG: sound-variant taps absorbed into unjudged long heads ----
    if ch.Vz or ch.ih:
        for n in ch.se:
            if n.An in TW_SET and not n.IZ:
                found = False
                for m in ch.se:
                    if (n.Wu == m.Wu and n.ZJ == m.ZJ and n.Ru == m.Ru
                            and (m.An == T_HOLDHEAD or m.An == T_SLIDEHEAD)
                            and m.WG != 1):
                        m.WG = 1
                        m._q = n.An
                        found = True
                if found:
                    n.An = 0

    if not ch.sq:
        ch.se = []


# --------------------------------------------------------------------------
# finalize (default-settings counting path)
# --------------------------------------------------------------------------
def finalize(ch):
    q = 0
    Vc = 0
    Z_ = he = UL = bR = eS = 0
    bm = []
    create_ok = (CAT_TAP, CAT_AIR, T_SLIDEHEAD, T_HOLDHEAD, T_AIRCHAIN,
                 T_EXHOLD, T_AHOLD, T_AIRSTEP, T_CLICK, 0x3, T_BARLINE)

    for raw in ch.se:
        cat = raw.An & MASK
        if cat != CAT_TAP and cat != CAT_AIR and raw.An not in create_ok:
            continue
        if raw.An in (T_SLIDEHEAD, T_HOLDHEAD, T_AIRCHAIN, T_EXHOLD, T_AHOLD) \
                and len(raw._c) == 0:
            continue
        note = raw.clone()
        bm.append(note)

        if raw.An == T_SLIDEHEAD:
            # drop 'v' markers, sort (the invisible interpolation nodes the
            # game inserts here are skipped by the counter below)
            note._c = [c for c in note._c if c.An != T_SLIDEMARK]
            note._c.sort(key=lambda c: c.Wu)
        if raw.An == T_AIRCHAIN and note.l != 8:
            note.An = T_EXHOLD
            for c in note._c:
                if c.An == T_AIRSEG2:
                    c.An = T_EXHOLD_D
                elif c.An == T_AIRSEG:
                    c.An = T_EXHOLD_O
                elif c.An == T_AIRTAIL:
                    c.An = T_EXHOLD_F
        if raw.An == T_AHOLD:
            last = note._c[-1]
            if ch.GI < 8 and last.WG == 1:
                last.An = T_AIRSTEP
                last.WG = 0
            if raw.Yk > 0:
                ins = []
                cur = raw
                en = raw.Wu + raw.Yk
                for child in raw._c:
                    while en <= child.Wu:
                        pos = inv_lerp(cur.Wu, child.Wu, en)
                        tgt = last if last.Wu == en else Raw()
                        tgt.An = T_AIRSTEP
                        tgt.Wu = en
                        tgt.ZJ = lerp(cur.ZJ, child.ZJ, pos)
                        tgt.Ru = lerp(cur.Ru, child.Ru, pos)
                        tgt.l = lerp(cur.l, child.l, pos)
                        tgt.sr = raw.sr
                        if last.Wu > en:
                            ins.append(tgt)
                        en += raw.Yk
                    cur = child
                note._c = ins + note._c
                note._c.sort(key=lambda c: c.Wu)
            if note.WG == 1:
                q += 1
                bR += 1
            for c in note._c:
                if c.An == T_AIRSTEP:
                    q += 1
                    bR += 1

        if cat == CAT_TAP or cat == CAT_AIR or raw.An in (T_SLIDEHEAD, T_HOLDHEAD):
            q += 1
        if cat == CAT_TAP or raw.An in (T_SLIDEHEAD, T_HOLDHEAD):
            Vc += 1
        if raw.An == T_TAP or raw.An in TW_SET or raw.An == T_MINE \
                or raw.An == T_SLIDEHEAD or raw.An == T_HOLDHEAD:
            Z_ += 1
        elif (raw.An & MASK) == CAT_AIR:
            bR += 1
        elif raw.An == T_FLK:
            eS += 1

    # ---- long-note judgment points (branch A: -480 endCut) ----
    for note in bm:
        if note.An == T_SLIDEHEAD or note.An == T_HOLDHEAD:
            if len(note._c) == 0:
                continue
            tail = note._c[-1]
            endwu = tail.Wu
            if tail.IZ:
                prev = note._c[-2] if len(note._c) >= 2 else note
                endwu = tail.Wu - TICKS_PER_BAR / ch.div(ch.PL(prev.Wu - TICKS_PER_BAR / 4))
            steps = []
            prevpt = note
            for child in note._c:
                if child.An == T_SLIDEINV:
                    continue
                if tail.IZ and child.Wu > endwu:
                    child.Hu = True
                else:
                    q += 1
                    Vc += 1
                    if note.An == T_SLIDEHEAD:
                        he += 1
                    else:
                        UL += 1
                t = prevpt.Wu
                while t < child.Wu:
                    t += ch.beat(t)
                    if t < child.Wu and t <= endwu:
                        steps.append(t)
                prevpt = child
            if not (ch.fa and tail.IZ):
                for t in steps:
                    node = Raw()
                    node.An = T_RELAY
                    node.Wu = t
                    note._c.append(node)
                    q += 1
                    Vc += 1
                    if note.An == T_SLIDEHEAD:
                        he += 1
                    else:
                        UL += 1
                note._c.sort(key=lambda c: c.Wu)
        elif note.An == T_AIRCHAIN or note.An == T_EXHOLD:
            if len(note._c) == 0:
                continue
            tail = note._c[-1]
            endwu = tail.Wu
            if tail.An == T_AIRTAIL or tail.An == T_EXHOLD_F:
                prev = note._c[-2] if len(note._c) >= 2 else note
                endwu = tail.Wu - TICKS_PER_BAR / ch.div(ch.PL(prev.Wu - TICKS_PER_BAR / 4))
            steps = []
            prevpt = note
            for child in note._c:
                if child.An == T_EXHOLD_I:
                    continue
                first = True
                t = prevpt.Wu
                while t < child.Wu:
                    t += ch.beat(t)
                    if t < child.Wu and t <= endwu:
                        steps.append((t, first))
                    first = False
                if child.An != T_AIRTAIL and child.An != T_EXHOLD_F:
                    q += 1
                    bR += 1
                prevpt = child
            if not (ch.fa and tail.IZ):
                for t, gr in steps:
                    node = Raw()
                    node.An = T_RELAY
                    node.Wu = t
                    node.WG = 1 if gr else 0
                    note._c.append(node)
                    if not gr:
                        q += 1
                        bR += 1
                note._c.sort(key=lambda c: c.Wu)

    ch.q = q
    ch.Vc = Vc
    ch.bH = {'Z_': Z_, 'he': he, 'UL': UL, 'bR': bR, 'eS': eS}


def count_text(text):
    ch = Chart()
    for line in text.replace('\r\n', '\n').split('\n'):
        line = line.rstrip()
        if not line:
            continue
        c0 = line[0]
        if c0 == '@':
            header(ch, line[1:].split('\t'))
            if ch.GI == 9:
                ch.AD = 16
        elif c0 == '#' and ch.GI >= 4:
            parse_note_line(ch, line[1:])
    if ch.GI >= 4:
        close_chain(ch)
    post_parse(ch)
    if ch.se:
        finalize(ch)
    return ch


def counts_of(ch):
    return (ch.q, ch.bH['Z_'], ch.bH['UL'], ch.bH['he'], ch.bH['bR'], ch.bH['eS'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file', nargs='?')
    ap.add_argument('--batch')
    args = ap.parse_args()

    if args.file:
        ch = count_text(open(args.file, encoding='utf-8', errors='replace').read())
        t, tap, hld, sld, air, flk = counts_of(ch)
        print('TOTAL = %d\nTAP = %d\nHLD = %d\nSLD = %d\nAIR = %d\nFLK = %d'
              % (t, tap, hld, sld, air, flk))
    elif args.batch:
        d = args.batch
        for f in sorted(x for x in os.listdir(d) if x.endswith('.ugc')):
            ch = count_text(open(os.path.join(d, f), encoding='utf-8', errors='replace').read())
            t, tap, hld, sld, air, flk = counts_of(ch)
            print(json.dumps({'f': f, 'total': t, 'tap': tap, 'hld': hld,
                              'sld': sld, 'air': air, 'flk': flk}))
    else:
        ap.print_help()


if __name__ == '__main__':
    main()

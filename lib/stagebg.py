"""stage background helper: game per-stage backdrop -> client @BGIMG"""

import glob
import hashlib
import os
import re

_DUMMY_CACHE = {}


def stage_dir(hdd, stage_id):
    return os.path.join(hdd, "stage", "stage%06d" % int(stage_id))


def _dummy_hash(hdd):
    if hdd in _DUMMY_CACHE:
        return _DUMMY_CACHE[hdd]
    from collections import Counter
    cnt = Counter()
    for p in glob.glob(os.path.join(hdd, "stage", "*", "CHU_UI_Stage_*.dds")):
        try:
            cnt[hashlib.md5(open(p, "rb").read()).hexdigest()] += 1
        except OSError:
            pass
    dummy = cnt.most_common(1)[0][0] if cnt else None
    _DUMMY_CACHE[hdd] = dummy
    return dummy


def stage_image(hdd, stage_id):
    """Return the stage's background .dds when it is a real image (not the
    dummy placeholder shipped for 3D-only stages)."""
    if stage_id is None or int(stage_id) < 0:
        return None
    d = stage_dir(hdd, stage_id)
    if not os.path.isdir(d):
        return None
    cand = glob.glob(os.path.join(d, "CHU_UI_Stage_*.dds"))
    if not cand:
        return None
    path = cand[0]
    if hashlib.md5(open(path, "rb").read()).hexdigest() == _dummy_hash(hdd):
        return None
    return path


def stage_name(hdd, stage_id):
    xml = os.path.join(stage_dir(hdd, stage_id), "Stage.xml")
    if not os.path.exists(xml):
        return ''
    text = open(xml, "rb").read().decode("utf-8", "replace")
    m = re.search(r"<name>\s*<id>-?\d+</id>\s*<str>(.*?)</str>", text, re.S)
    return m.group(1) if m else ''

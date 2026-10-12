#!/usr/bin/env python3
"""Cut the title art and the mascot out of the poster as clean, layered PNGs.

The poster is a flattened JPEG, so nothing in it can move on its own. This
script pulls out the pieces the countdown animates:

  * the GOOD NEWS! letters, rebuilt as smooth shapes from the poster's red
    pixels and re-rendered as flat colour (red face, cream outline, halftone
    drop shadow), so they stay sharp at 4K and each letter can pop in on its
    own. Where the arrow or the mascot covered a letter, the outline is filled
    in by harmonic interpolation;
  * the arrow, straightened and with the shaft that passes through the heart
    filled in, so it can fly in and pierce the heart;
  * the football mascot, cut out with its own outline.

Everything is positioned in poster pixels and written at a target scale.

    python3 build_assets.py --scale 0.85 --out assets/1080
"""
import argparse
import json
import os

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

HERE = os.path.dirname(os.path.abspath(__file__))

# The poster's ten ink colours, found by k-means on the poster itself.
PALETTE = np.array([
    [93, 34, 27],     # 0 maroon      (frame, arch band, outlines)
    [174, 30, 36],    # 1 red         (letters)
    [236, 190, 147],  # 2 cream
    [147, 36, 35],    # 3 mid red     (mascot body, stripes)
    [212, 64, 54],    # 4 orange red  (sunburst)
    [66, 16, 2],      # 5 dark brown
    [207, 118, 88],   # 6 salmon
    [218, 155, 120],  # 7 light salmon
    [152, 95, 71],    # 8 halftone shadow on cream
    [45, 42, 42],     # 9 screenshot edge
], np.float32)
MAROON, RED, CREAM, MIDRED, ORANGE, DKBROWN, SALMON, LTSALMON, SHADOW = range(9)

RED_RGB = (174, 30, 36)
CREAM_RGB = (236, 190, 147)
INK_RGB = (93, 34, 27)

# Letter shapes are found by the centroid of their red pixels.
LETTERS = {
    'G': [(309, 369)],
    'heart': [(488, 386)],
    'O': [(648, 426)],
    'D': [(803, 450)],
    'N': [(400, 625), (348, 561)],   # the arm splits off the N's top-left corner
    'E': [(514, 621)],
    'W': [(663, 617)],
    'S': [(831, 625)],
    'bang': [(934, 588), (926, 690)],
}
OUTLINE_W = 5.0          # cream outline around each letter, poster px
SHADOW_V = (11.5, 13.0)  # letter drop shadow, poster px (fitted to the poster)
ARROW_SHADOW_V = (7.0, 10.0)
SHADOW_COVER = 0.6       # halftone ink coverage, matches the poster's average tone

# The arrow, in poster px: tail end of the fletching and the tip.
ARROW_TAIL = np.array([368.0, 352.0])
ARROW_TIP = np.array([646.0, 279.0])
ARROW_PIERCE_S = 132.0   # where the rear shaft enters the heart
ARROW_EXIT_S = 159.0     # where the point comes out of the cleft


def disk(r):
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


def classify(rgb):
    sm = cv2.GaussianBlur(rgb.astype(np.float32), (3, 3), 0)
    best = np.full(sm.shape[:2], np.inf, np.float32)
    lab = np.zeros(sm.shape[:2], np.uint8)
    for i, c in enumerate(PALETTE):
        d = ((sm - c) ** 2).sum(-1)
        m = d < best
        best[m] = d[m]
        lab[m] = i
    return lab


def fill_small_holes(m, max_area):
    f = ndi.binary_fill_holes(m)
    holes = f & ~m
    hl, n = ndi.label(holes)
    if n:
        sz = ndi.sum(holes, hl, range(1, n + 1))
        m = m | np.isin(hl, 1 + np.where(sz <= max_area)[0])
    return m


def harmonic_fill(field, unknown, iters=2500):
    """Replace field values inside `unknown` with the harmonic interpolant."""
    if not unknown.any():
        return field
    ys, xs = np.where(unknown)
    y0, y1 = max(ys.min() - 2, 0), min(ys.max() + 3, field.shape[0])
    x0, x1 = max(xs.min() - 2, 0), min(xs.max() + 3, field.shape[1])
    f = field[y0:y1, x0:x1].copy()
    u = unknown[y0:y1, x0:x1]
    # start from the nearest known value
    _, (iy, ix) = ndi.distance_transform_edt(u, return_indices=True)
    f = f[iy, ix]
    for _ in range(iters):
        avg = 0.25 * (np.roll(f, 1, 0) + np.roll(f, -1, 0) + np.roll(f, 1, 1) + np.roll(f, -1, 1))
        f[u] = avg[u]
    out = field.copy()
    out[y0:y1, x0:x1][u] = f[u]
    return out


# ---------------------------------------------------------------- mascot ----

def mascot_mask(lab):
    H, W = lab.shape
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx - 505, yy - 760)
    dark = (lab == MAROON) | (lab == DKBROWN)
    box = np.zeros((H, W), bool)
    box[420:800, 15:440] = True

    bl, _ = ndi.label((lab == MIDRED) & box)
    cnt = np.bincount(bl[440:640, 40:350].ravel())
    cnt[0] = 0
    body = bl == cnt.argmax()

    # outlines and limbs: dark strokes touching the body, minus the frame and arch
    excl = (xx < 52) | (r >= 503) | (yy >= 798)
    d = dark & box & ~excl
    dl, _ = ndi.label(d, structure=np.ones((3, 3)))
    ids = np.unique(dl[ndi.binary_dilation(body, iterations=3) & d])
    core = np.isin(dl, ids[ids > 0]) | body
    filled = ndi.binary_fill_holes(core)

    # where the ball overlaps the frame its outline merges with it; rebuild it
    ball = body | (dark & ndi.binary_dilation(body, disk(6)))
    ball = ndi.binary_fill_holes(ndi.binary_closing(ball, disk(8)))
    m = ndi.binary_closing(filled | ball, disk(2))
    m |= ndi.binary_fill_holes(m) & ~m
    lbl, n = ndi.label(m)
    sz = ndi.sum(m, lbl, range(1, n + 1))
    m = np.isin(lbl, 1 + np.where(sz >= 100)[0])
    m |= ndi.binary_closing(m, disk(2))

    # the pointing glove's outline is a lighter brown
    y0, y1, x0, x1 = 472, 566, 362, 428
    dk = np.isin(lab[y0:y1, x0:x1], [MAROON, DKBROWN, SHADOW])
    gl, _ = ndi.label(dk, structure=np.ones((3, 3)))
    ids = np.unique(gl[m[y0:y1, x0:x1] & dk])
    g = np.isin(gl, ids[ids > 0])
    m[y0:y1, x0:x1] |= ndi.binary_fill_holes(ndi.binary_closing(g, disk(2)))

    # background enclosed between the lower arm and the ball
    nondark = ~dark
    cl, _ = ndi.label(nondark)
    for (x, y) in [(88, 650), (72, 648)]:
        reg = cl == cl[y, x]
        if nondark[y, x] and reg.sum() < 8000:
            m &= ~reg
    m = ndi.binary_opening(m, disk(1))
    lbl, n = ndi.label(m)
    sz = ndi.sum(m, lbl, range(1, n + 1))
    return np.isin(lbl, 1 + np.where(sz >= 100)[0])


# ----------------------------------------------------------------- arrow ----

def arrow_frame():
    u = (ARROW_TIP - ARROW_TAIL)
    u = u / np.linalg.norm(u)
    n = np.array([-u[1], u[0]])  # points below the shaft
    return u, n


def arrow_sample_maps(ss=4, s_range=(-14, 300), q_range=(-26, 26)):
    u, n = arrow_frame()
    s = np.arange(s_range[0], s_range[1], 1.0 / ss)
    q = np.arange(q_range[0], q_range[1], 1.0 / ss)
    S, Q = np.meshgrid(s, q)
    X = ARROW_TAIL[0] + S * u[0] + Q * n[0]
    Y = ARROW_TAIL[1] + S * u[1] + Q * n[1]
    return S, Q, X.astype(np.float32), Y.astype(np.float32)


def arrow_layer(rgb, lab, ss=4):
    S, Q, X, Y = arrow_sample_maps(ss)
    lab_r = cv2.remap(lab, X, Y, cv2.INTER_NEAREST)
    rgb_r = cv2.remap(rgb, X, Y, cv2.INTER_CUBIC)
    env = (((S >= -6) & (S <= 74) & (np.abs(Q) <= 21))
           | ((S >= 60) & (S <= 296) & (np.abs(Q) <= 6.5))
           | ((S >= 244) & (S <= 294) & (np.abs(Q) <= 13)))
    dark = np.isin(lab_r, [MAROON, DKBROWN]) & env
    sil = ndi.binary_fill_holes(ndi.binary_closing(dark, disk(2 * ss)))
    sil = ndi.binary_opening(sil, disk(ss // 2)) & env

    # the stretch hidden inside the heart: repeat a clean slice of the rear shaft
    a, b = ARROW_PIERCE_S - 8, ARROW_EXIT_S + 9
    src_cols = (S[0] >= 100) & (S[0] <= 112)
    prof_rgb = np.median(rgb_r[:, src_cols], axis=1)
    prof_m = sil[:, src_cols].mean(1) > 0.5
    gap = (S[0] >= a) & (S[0] <= b)
    rgb_r[:, gap] = prof_rgb[:, None, :]
    sil[:, gap] = prof_m[:, None]
    return rgb_r, sil, S, Q


def arrow_mask_in_poster(sil, shape, ss=4):
    """Map the straightened arrow silhouette back onto the poster grid."""
    H, W = shape
    u, n = arrow_frame()
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    s = (xx - ARROW_TAIL[0]) * u[0] + (yy - ARROW_TAIL[1]) * u[1]
    q = (xx - ARROW_TAIL[0]) * n[0] + (yy - ARROW_TAIL[1]) * n[1]
    mx = ((s + 14) * ss).astype(np.float32)
    my = ((q + 26) * ss).astype(np.float32)
    return cv2.remap(sil.astype(np.uint8), mx, my, cv2.INTER_NEAREST, borderValue=0) > 0


# --------------------------------------------------------------- letters ----

def letter_masks(lab, occluder):
    red = lab == RED
    roi = np.zeros_like(red)
    roi[235:740, 215:975] = True
    cc, n = ndi.label(red & roi)
    idx = np.arange(1, n + 1)
    area = ndi.sum(np.ones_like(cc), cc, idx)
    cen = np.array(ndi.center_of_mass(np.ones_like(cc), cc, idx))  # (y, x)
    out = {}
    for name, targets in LETTERS.items():
        m = np.zeros_like(red)
        for (tx, ty) in targets:
            d = np.hypot(cen[:, 1] - tx, cen[:, 0] - ty) + (area < 300) * 1e9
            k = int(np.argmin(d))
            assert d[k] < 20, (name, tx, ty, d[k])
            m |= cc == idx[k]
        m = fill_small_holes(m, 600)
        out[name] = m
    return out


def complete_letter(m, unknown, close_r):
    """Guess the parts of a letter hidden under `unknown`.

    Inside the hidden area the letters' edges are gently convex, so a closing
    restricted to that area reconnects them without touching anything visible.
    """
    known = m & ~unknown
    full = known | (ndi.binary_closing(known, disk(close_r)) & unknown)
    f = ndi.binary_fill_holes(full)
    holes = f & ~full
    hl, n = ndi.label(holes)
    for i in range(1, n + 1):
        h = hl == i
        if h.sum() < 150 or (h & unknown).sum() > 0.6 * h.sum():
            full |= h
    return full


# how far a hidden stretch may be bridged, and where (poster px)
REPAIR = {
    'G': (14, (330, 300, 420, 375)),       # under the fletching
    'heart': (28, (360, 290, 502, 380)),   # fletching, rear shaft and its shadow; not the cleft
    'N': (14, (300, 480, 420, 600)),        # under the mascot's arm
}


def rebuild_letter(name, m, unknown):
    if name in REPAIR:
        r, (x0, y0, x1, y1) = REPAIR[name]
        u = np.zeros_like(unknown)
        u[y0:y1, x0:x1] = unknown[y0:y1, x0:x1]
        m = complete_letter(m, u, r)
    return ndi.gaussian_filter(m.astype(np.float32), 0.8)


def sdf_from_soft(soft, ss=4):
    """Signed distance (poster px, +inside) on a grid ss times finer."""
    up = cv2.resize(soft, None, fx=ss, fy=ss, interpolation=cv2.INTER_CUBIC)
    inside = up > 0.5
    return (ndi.distance_transform_edt(inside) - ndi.distance_transform_edt(~inside)) / ss


def shift_sdf(sdf, v, ss=4):
    dx, dy = int(round(v[0] * ss)), int(round(v[1] * ss))
    out = np.full_like(sdf, -1e3)
    H, W = sdf.shape
    out[max(dy, 0):H + min(dy, 0), max(dx, 0):W + min(dx, 0)] = \
        sdf[max(-dy, 0):H - max(dy, 0), max(-dx, 0):W - max(dx, 0)]
    return out


def halftone(h, w, period, cover, angle=45.0, phase=(0.0, 0.0)):
    """Anti-aliased dot screen, alpha in [0,1]."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx += phase[0]
    yy += phase[1]
    a = np.deg2rad(angle)
    u = (xx * np.cos(a) + yy * np.sin(a)) / period
    v = (-xx * np.sin(a) + yy * np.cos(a)) / period
    du = (u - np.round(u)) * period
    dv = (v - np.round(v)) * period
    d = np.sqrt(du * du + dv * dv)
    r = period * np.sqrt(cover / np.pi)
    return np.clip(r - d + 0.5, 0, 1)


def to_target(sdf_ss, box, scale, ss=4):
    """Resample a fine-grid field covering poster box (x0,y0,x1,y1) to target px."""
    x0, y0, x1, y1 = box
    tw = int(np.ceil((x1 - x0) * scale))
    th = int(np.ceil((y1 - y0) * scale))
    # sample at target pixel centres
    tx = (np.arange(tw) + 0.5) / scale
    ty = (np.arange(th) + 0.5) / scale
    mx, my = np.meshgrid(tx * ss - 0.5, ty * ss - 0.5)
    return cv2.remap(sdf_ss.astype(np.float32), mx.astype(np.float32), my.astype(np.float32),
                     cv2.INTER_LINEAR, borderValue=-1e3)


def coverage(sdf_t, scale, grow=0.0):
    # 1 poster px = scale target px; a one-target-pixel ramp at the edge
    return np.clip(0.5 + (sdf_t + grow) * scale, 0, 1)


def rgba(color, alpha):
    h, w = alpha.shape
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = color
    out[..., 3] = np.round(alpha * 255).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def make_grain(w, h, out, seed=7):
    """Print texture like the poster's: pale flecks, dark specks, faint mottling."""
    rng = np.random.default_rng(seed)
    mott = ndi.gaussian_filter(rng.standard_normal((h, w)).astype(np.float32), 60)
    mott = (mott - mott.min()) / (mott.max() - mott.min())
    fine = ndi.gaussian_filter(rng.standard_normal((h, w)).astype(np.float32), 0.8)
    fine /= fine.std()
    light = np.clip((fine - 2.1) * 1.6, 0, 1) * (0.25 + 0.75 * mott) * 0.55
    blob = ndi.gaussian_filter(rng.standard_normal((h, w)).astype(np.float32), 1.6)
    blob /= blob.std()
    dark = np.clip((-blob - 2.3) * 1.4, 0, 1) * (0.3 + 0.7 * (1 - mott)) * 0.35
    rgba(CREAM_RGB, light).save(os.path.join(out, 'grain_light.png'))
    rgba((60, 14, 8), dark).save(os.path.join(out, 'grain_dark.png'))
    # mid-grey noise for an overlay blend: dithers gradients and adds tooth
    g = np.clip(128 + rng.standard_normal((h, w)) * 7 + (mott - 0.5) * 16, 0, 255).astype(np.uint8)
    Image.fromarray(g, 'L').save(os.path.join(out, 'grain_tooth.png'))


# ------------------------------------------------------------------ main ----

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--poster', default=os.path.join(HERE, 'source', 'poster.jpg'))
    ap.add_argument('--scale', type=float, default=0.85, help='target px per poster px')
    ap.add_argument('--halftone', type=float, default=4.0, help='halftone period in target px')
    ap.add_argument('--out', default=os.path.join(HERE, 'assets', '1080'))
    ap.add_argument('--frame', default='1920x1080', help='frame size for the grain textures')
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    s = args.scale
    ss = 4

    rgb = np.asarray(Image.open(args.poster).convert('RGB'))
    lab = classify(rgb)
    H, W = lab.shape
    manifest = {'scale': s, 'halftone': args.halftone, 'pieces': {}}

    # mascot
    mm = mascot_mask(lab)
    ys, xs = np.where(mm)
    mbox = (int(xs.min()) - 3, int(ys.min()) - 3, int(xs.max()) + 4, int(ys.max()) + 4)

    # arrow
    a_rgb, a_sil, S, Q = arrow_layer(rgb, lab, ss)
    a_poster = arrow_mask_in_poster(a_sil, (H, W), ss)

    # what hid parts of the letters
    # the arrow's shadow is the arrow shifted; anything but red there is unknown
    dx, dy = (int(round(v)) for v in ARROW_SHADOW_V)
    foot = np.zeros_like(a_poster)
    foot[dy:, dx:] = a_poster[:-dy, :-dx]
    a_shadow = ndi.binary_dilation(foot, disk(3)) & (lab != RED)
    unknown = (ndi.binary_dilation(a_poster, disk(2)) | a_shadow
               | ndi.binary_dilation(mm, disk(3)))

    masks = letter_masks(lab, unknown)
    for name, m in masks.items():
        soft = rebuild_letter(name, m, unknown)
        ys, xs = np.where(soft > 0.5)
        pad = int(OUTLINE_W + max(SHADOW_V) + 6)
        box = (int(xs.min()) - pad, int(ys.min()) - pad, int(xs.max()) + pad + 1, int(ys.max()) + pad + 1)
        x0, y0, x1, y1 = box
        sub = np.zeros((y1 - y0, x1 - x0), np.float32)
        sy0, sx0 = max(y0, 0), max(x0, 0)
        sub[sy0 - y0:min(y1, H) - y0, sx0 - x0:min(x1, W) - x0] = soft[sy0:min(y1, H), sx0:min(x1, W)]
        sdf = sdf_from_soft(sub, ss)
        sdf_t = to_target(sdf, box, s, ss)
        face = coverage(sdf_t, s)
        outline = coverage(sdf_t, s, OUTLINE_W)
        sh_sdf_t = to_target(shift_sdf(sdf, SHADOW_V, ss), box, s, ss)
        sh = coverage(sh_sdf_t, s, OUTLINE_W)
        # dots locked to the frame grid so neighbouring letters' screens line up
        dots = halftone(*sh.shape, args.halftone, SHADOW_COVER, phase=(round(x0 * s), round(y0 * s)))
        rgba(RED_RGB, face).save(os.path.join(args.out, f'{name}_face.png'))
        rgba(CREAM_RGB, outline).save(os.path.join(args.out, f'{name}_outline.png'))
        rgba(INK_RGB, sh * dots).save(os.path.join(args.out, f'{name}_shadow.png'))
        cy, cx = ndi.center_of_mass(soft > 0.5)
        manifest['pieces'][name] = {'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0,
                                    'cx': float(cx), 'cy': float(cy)}
        print(f'{name:6s} box={box}')

    # arrow image: straightened, at target scale
    u, n = arrow_frame()
    s_min, q_min = -14, -26
    a_alpha = cv2.GaussianBlur(a_sil.astype(np.float32), (0, 0), ss * 0.5)
    tw = int(round(a_sil.shape[1] / ss * s))
    th = int(round(a_sil.shape[0] / ss * s))
    a_rgb_t = cv2.resize(a_rgb, (tw, th), interpolation=cv2.INTER_AREA)
    a_al_t = cv2.resize(a_alpha, (tw, th), interpolation=cv2.INTER_AREA)
    a_al_t = np.clip((a_al_t - 0.5) * 1.6 + 0.5, 0, 1)
    out = np.dstack([a_rgb_t, np.round(a_al_t * 255).astype(np.uint8)])
    Image.fromarray(out, 'RGBA').save(os.path.join(args.out, 'arrow.png'))
    # its shadow silhouette (dots applied in the page, since it moves)
    sh = np.zeros((th, tw, 4), np.uint8)
    sh[..., :3] = INK_RGB
    sh[..., 3] = np.round(a_al_t * 255).astype(np.uint8)
    Image.fromarray(sh, 'RGBA').save(os.path.join(args.out, 'arrow_sil.png'))
    angle = float(np.degrees(np.arctan2(u[1], u[0])))
    manifest['arrow'] = {
        'tail': ARROW_TAIL.tolist(), 'angle': angle, 's_min': s_min, 'q_min': q_min,
        'len': a_sil.shape[1] / ss, 'thick': a_sil.shape[0] / ss,
        'pierce_s': ARROW_PIERCE_S, 'shadow': ARROW_SHADOW_V,
    }

    # mascot
    x0, y0, x1, y1 = mbox
    tw, th = int(round((x1 - x0) * s)), int(round((y1 - y0) * s))
    crop = rgb[y0:y1, x0:x1]
    mk = mm[y0:y1, x0:x1].astype(np.float32)
    up = cv2.resize(ndi.gaussian_filter(mk, 0.7), None, fx=ss, fy=ss, interpolation=cv2.INTER_CUBIC) > 0.5
    # keep the edge a clean outline colour: no cream/red halo from the JPEG
    edge = up & ~ndi.binary_erosion(up, disk(int(1.5 * ss)))
    col = cv2.resize(crop, (up.shape[1], up.shape[0]), interpolation=cv2.INTER_CUBIC)
    col[edge] = (np.array(INK_RGB) * 0.85).astype(np.uint8)
    al = cv2.resize(up.astype(np.float32), (tw, th), interpolation=cv2.INTER_AREA)
    col = cv2.resize(col, (tw, th), interpolation=cv2.INTER_AREA)
    Image.fromarray(np.dstack([col, np.round(al * 255).astype(np.uint8)]), 'RGBA').save(
        os.path.join(args.out, 'mascot.png'))
    manifest['mascot'] = {'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0}

    # geometry of the arch, measured off the poster (poster px)
    manifest['arch'] = {'cx': 505.0, 'cy': 760.0, 'shadow_in': 479.0, 'ring_in': 497.0,
                        'band_in': 511.0, 'band_out': 604.0, 'ring_out': 617.0,
                        'text_base': 548.0, 'text_cap': 37.0}
    with open(os.path.join(args.out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1)

    fw, fh = (int(v) for v in args.frame.split('x'))
    make_grain(fw, fh, args.out)

    if args.debug:
        dbg = rgb.copy()
        dbg[unknown] = (0, 255, 0)
        Image.fromarray(dbg).save(os.path.join(args.out, '_unknown.png'))


if __name__ == '__main__':
    main()

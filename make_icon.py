"""
Generates loca.icns — green gradient squircle with a white neuron symbol.
Run: python3 make_icon.py
"""
import math
import os
import subprocess
from PIL import Image, ImageDraw

SIZE = 1024


def draw_squircle(draw, size, radius_pct=0.22):
    r = int(size * radius_pct)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(255, 255, 255, 255))


def gradient_background(size):
    """Mint green top-left → deep forest green bottom-right."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = img.load()
    # #3DD68C → #0A5C36
    r0, g0, b0 = 0x3D, 0xD6, 0x8C
    r1, g1, b1 = 0x0A, 0x5C, 0x36
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            pixels[x, y] = (
                int(r0 + (r1 - r0) * t),
                int(g0 + (g1 - g0) * t),
                int(b0 + (b1 - b0) * t),
                255,
            )
    return img


def draw_neuron(draw, size):
    """
    A clean white neuron: central soma (circle) with 6 dendrites radiating
    outward, each ending in a small terminal node. One longer axon extends
    downward with a slight curve and a terminal bulb.
    """
    cx, cy = size * 0.5, size * 0.47
    soma_r = size * 0.085
    line_w = max(2, int(size * 0.028))
    node_r = size * 0.038
    WHITE = (255, 255, 255, 255)

    # ── Dendrites (6 arms, evenly spaced, skip the bottom slot for the axon) ──
    # Angles in degrees: 30, 90, 150, 210, 270(axon), 330
    dendrite_angles = [30, 80, 140, 200, 290, 340]
    dendrite_len = size * 0.26

    for deg in dendrite_angles:
        rad = math.radians(deg)
        # Start at soma edge
        x0 = cx + soma_r * math.cos(rad)
        y0 = cy + soma_r * math.sin(rad)
        # End point
        x1 = cx + dendrite_len * math.cos(rad)
        y1 = cy + dendrite_len * math.sin(rad)
        draw.line([(x0, y0), (x1, y1)], fill=WHITE, width=line_w)
        # Terminal node
        draw.ellipse([
            x1 - node_r, y1 - node_r,
            x1 + node_r, y1 + node_r,
        ], fill=WHITE)

    # ── Axon: longer, slight curve toward bottom ──────────────────────────────
    axon_len = size * 0.34
    axon_tip_x = cx + size * 0.06
    axon_tip_y = cy + axon_len
    # Draw as two segments with a slight bend at midpoint
    mid_x = cx + size * 0.04
    mid_y = cy + axon_len * 0.55
    ax_start = (cx, cy + soma_r)
    draw.line([ax_start, (mid_x, mid_y)], fill=WHITE, width=line_w)
    draw.line([(mid_x, mid_y), (axon_tip_x, axon_tip_y)], fill=WHITE, width=line_w)
    # Axon terminal bulb
    bulb_r = node_r * 1.25
    draw.ellipse([
        axon_tip_x - bulb_r, axon_tip_y - bulb_r,
        axon_tip_x + bulb_r, axon_tip_y + bulb_r,
    ], fill=WHITE)

    # ── Soma (drawn last so it sits on top of dendrite roots) ─────────────────
    draw.ellipse([
        cx - soma_r, cy - soma_r,
        cx + soma_r, cy + soma_r,
    ], fill=WHITE)

    # Small dark nucleus inside soma for depth
    nucleus_r = soma_r * 0.42
    # Use the midpoint gradient colour
    draw.ellipse([
        cx - nucleus_r, cy - nucleus_r,
        cx + nucleus_r, cy + nucleus_r,
    ], fill=(0x20, 0x96, 0x60, 200))


def make_icon_png(size):
    bg = gradient_background(size)

    mask = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_squircle(ImageDraw.Draw(mask), size)
    bg.putalpha(mask.split()[3])

    neuron_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_neuron(ImageDraw.Draw(neuron_layer), size)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), bg)
    out.paste(neuron_layer, (0, 0), neuron_layer)
    return out


def build_icns(output_path):
    iconset_dir = output_path.replace(".icns", ".iconset")
    os.makedirs(iconset_dir, exist_ok=True)
    for s in [16, 32, 64, 128, 256, 512, 1024]:
        make_icon_png(s).save(os.path.join(iconset_dir, f"icon_{s}x{s}.png"))
        if s <= 512:
            make_icon_png(s * 2).save(os.path.join(iconset_dir, f"icon_{s}x{s}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", output_path], check=True)
    subprocess.run(["rm", "-rf", iconset_dir])
    print(f"Written: {output_path}")


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "Nous.app", "Contents", "Resources", "loca.icns")
    build_icns(out)

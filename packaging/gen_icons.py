#!/usr/bin/env python3
"""Genera los iconos de Club-AutotalPlay (tray + dock).

Produce PNGs cuadrados con un fondo de estado y un glifo de WiFi + flecha de
reconexion en blanco. Estados:
    online        -> verde
    reconnecting  -> ambar
    offline       -> rojo
Ademas un SVG fuente para el .desktop / AppImage.

Uso:  python3 packaging/gen_icons.py
Requiere: Pillow (esta en el venv [.venv]).
"""
import os

from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
# Escribe en el paquete interno (para AppImage/PyInstaller) Y en la raiz del
# repo (para .desktop en dev). assets.py resuelve cual usar en runtime.
PKG_ASSETS = os.path.abspath(os.path.join(_HERE, "..", "src", "club_autotalplay", "assets"))
ROOT_ASSETS = os.path.abspath(os.path.join(_HERE, "..", "assets"))
TARGETS = [PKG_ASSETS, ROOT_ASSETS]

# Paleta
GREEN = (34, 197, 94)     # online
AMBER = (234, 179, 8)     # reconectando
RED = (239, 68, 68)       # offline
WHITE = (255, 255, 255)

STATES = {
    "online": GREEN,
    "reconnecting": AMBER,
    "offline": RED,
}


def draw_icon(size, color):
    """Icono: fondo redondeado del color de estado + glifo WiFi + refresh."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.22  # radio de la esquina redondeada
    # Fondo redondeado
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=color + (255,))

    cx = size / 2
    cy = size * 0.62
    white = WHITE + (255,)

    # Tres arcos de WiFi (medio circulo) desde el centro
    for i, rad in enumerate((0.34, 0.22, 0.11)):
        rr = size * rad
        width = max(2, int(size * 0.045))
        bbox = [cx - rr, cy - rr, cx + rr, cy + rr]
        # dibuja solo la mitad superior (arco)
        d.arc(bbox, start=180, end=360, fill=white, width=width)

    # punto central del WiFi
    pr = size * 0.045
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=white)

    # flecha de "refresh" arriba-derecha (globo blanco con flecha de color)
    gcx = size * 0.74
    gcy = size * 0.28
    gr = size * 0.20
    d.ellipse([gcx - gr, gcy - gr, gcx + gr, gcy + gr], fill=WHITE + (255,))
    # arco de la flecha en el color de estado
    aw = max(2, int(size * 0.05))
    ar = gr * 0.55
    d.arc([gcx - ar, gcy - ar, gcx + ar, gcy + ar],
          start=-45, end=200, fill=color + (255,), width=aw)
    # punta de la flecha (triangulo) en el extremo del arco
    import math
    ang = math.radians(200)
    tipx = gcx + ar * math.cos(ang)
    tipy = gcy + ar * math.sin(ang)
    hs = size * 0.055
    d.polygon([
        (tipx, tipy),
        (tipx - hs, tipy - hs * 0.2),
        (tipx + hs * 0.1, tipy + hs),
    ], fill=color + (255,))

    return img


def main():
    sizes = [512, 256, 128, 64, 48, 32, 24, 16]
    svg = _svg()
    for assets in TARGETS:
        os.makedirs(assets, exist_ok=True)
        print(f"-> {assets}")
        for name, color in STATES.items():
            for s in sizes:
                draw_icon(s, color).save(os.path.join(assets, f"icon_{name}_{s}.png"))
            draw_icon(256, color).save(os.path.join(assets, f"icon_{name}.png"))
        draw_icon(512, GREEN).save(os.path.join(assets, "icon.png"))
        with open(os.path.join(assets, "icon.svg"), "w") as f:
            f.write(svg)
        print(f"   estados: {list(STATES)} · tamaños: {sizes} · icon.png · icon.svg")
    print("listo")


def _svg():
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <style>
      .bg-online {{ fill: rgb{GREEN}; }}
      .bg-reconnecting {{ fill: rgb{AMBER}; }}
      .bg-offline {{ fill: rgb{RED}; }}
      .fg {{ fill: white; }}
    </style>
  </defs>
  <rect x="0" y="0" width="512" height="512" rx="112" class="bg-online"/>
  <g stroke="white" stroke-width="24" fill="none" stroke-linecap="round">
    <path d="M 96 318 A 174 174 0 0 1 416 318"/>
    <path d="M 148 360 A 110 110 0 0 1 364 360"/>
    <path d="M 200 400 A 56 56 0 0 1 312 400"/>
  </g>
  <circle cx="256" cy="420" r="22" class="fg"/>
  <circle cx="379" cy="143" r="52" class="fg"/>
  <path d="M 352 143 a 30 30 0 1 1 8 20" stroke="rgb{GREEN}" stroke-width="16"
        fill="none" stroke-linecap="round"/>
</svg>
"""


if __name__ == "__main__":
    main()

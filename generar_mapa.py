#!/usr/bin/env python3
"""
Sessió Contínua — genera data/mapa.js, el plano de Barcelona que usa la web.

    python generar_mapa.py

Sólo hace falta ejecutarlo una vez (la ciudad no cambia tanto). Descarga de
OpenStreetMap (Overpass) los distritos, los municipios vecinos, las vías
principales, los parques y los ríos, y los proyecta en un plano girado 45°:
montaña arriba, mar abajo, Besòs a la derecha y Llobregat a la izquierda,
como se orienta uno en Barcelona.

Datos © colaboradores de OpenStreetMap (ODbL).
"""

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "data", "mapa.js")
OVERPASS = "https://overpass-api.de/api/interpreter"

# Proyección local: 1 unidad = 10 m, centro en el Eixample, girada 45° en sentido horario.
LAT0, LON0, GIRO = 41.395, 2.165, 45
KX = math.cos(math.radians(LAT0)) * 111320 / 10
KY = 110574 / 10
COS, SIN = math.cos(math.radians(GIRO)), math.sin(math.radians(GIRO))

DISTRITOS = [3008998, 3765380, 3765489, 3765619, 3772890, 3773080, 3773133, 3775065, 3775146, 3775150]
NOMBRE_DISTRITO = {
    "l'Eixample": "Eixample", "Ciutat Vella": "Ciutat Vella", "Sants-Montjuïc": "Sants-Montjuïc",
    "les Corts": "Les Corts", "Sarrià - Sant Gervasi": "Sarrià-Sant Gervasi", "Gràcia": "Gràcia",
    "Horta-Guinardó": "Horta-Guinardó", "Nou Barris": "Nou Barris", "Sant Andreu": "Sant Andreu",
    "Sant Martí": "Sant Martí",
}
CAJA_GRANDE = (41.27, 1.96, 41.52, 2.31)   # municipios alrededor (sur, oeste, norte, este)
CAJA_CIUDAD = (41.315, 2.05, 41.47, 2.235)  # vías y parques

# Avenidas que llevan nombre en el plano (nombre OSM -> rótulo)
AVENIDAS = {
    "Avinguda Diagonal": "Avinguda Diagonal",
    "Gran Via de les Corts Catalanes": "Gran Via",
    "Avinguda Meridiana": "Meridiana",
    "Avinguda del Paral·lel": "Paral·lel",
    "Passeig de Gràcia": "Passeig de Gràcia",
    "Ronda de Dalt": "Ronda de Dalt",
    "Ronda Litoral": "Ronda Litoral",
    "Via Augusta": "Via Augusta",
    "Carrer d'Aragó": "Aragó",
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def overpass(q):
    cuerpo = urllib.parse.urlencode({"data": f"[out:json][timeout:120];{q}"}).encode()
    for i in range(4):
        try:
            req = urllib.request.Request(OVERPASS, data=cuerpo, headers={"User-Agent": "sessio-continua/1.0"})
            return json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))["elements"]
        except Exception as e:  # noqa: BLE001 — Overpass a veces va cargado
            log("  Overpass ocupado, reintento…", e)
            time.sleep(10 * (i + 1))
    raise SystemExit("No hay manera con Overpass; prueba más tarde.")


def proyectar(lat, lon):
    x, y = (lon - LON0) * KX, (lat - LAT0) * KY
    # giro horario: el noroeste (la montaña) queda arriba; y de SVG crece hacia abajo
    return (x * COS + y * SIN, x * SIN - y * COS)


def simplificar(pts, tol):
    """Douglas-Peucker iterativo."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    pila = [(0, len(pts) - 1)]
    while pila:
        a, b = pila.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy) or 1e-9
        peor, idx = 0, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / n if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > peor:
                peor, idx = d, i
        if idx is not None and peor > tol:
            keep[idx] = True
            pila += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def a_path(anillos, cerrar=True):
    out = []
    for r in anillos:
        if len(r) < 2:
            continue
        pts = [f"{round(x)} {round(y)}" for x, y in r]
        # quitar repetidos consecutivos tras redondear
        limpio = [pts[0]] + [p for i, p in enumerate(pts[1:], 1) if p != pts[i - 1]]
        if len(limpio) < 2:
            continue
        out.append("M" + "L".join(limpio) + ("Z" if cerrar else ""))
    return "".join(out)


def anillos_de(rel):
    """Cose las vías 'outer' (e 'inner') de una relación en anillos cerrados."""
    trozos = {"outer": [], "inner": []}
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            rol = "inner" if m.get("role") == "inner" else "outer"
            trozos[rol].append([(p["lat"], p["lon"]) for p in m["geometry"]])
    res = {}
    for rol, ways in trozos.items():
        anillos, libres = [], [w[:] for w in ways]
        while libres:
            anillo = libres.pop(0)
            cambio = True
            while anillo[0] != anillo[-1] and cambio:
                cambio = False
                for i, w in enumerate(libres):
                    if w[0] == anillo[-1]:
                        anillo += w[1:]
                    elif w[-1] == anillo[-1]:
                        anillo += w[::-1][1:]
                    elif w[-1] == anillo[0]:
                        anillo = w[:-1] + anillo
                    elif w[0] == anillo[0]:
                        anillo = w[::-1][:-1] + anillo
                    else:
                        continue
                    libres.pop(i)
                    cambio = True
                    break
            if len(anillo) > 3:
                anillos.append(anillo)
        res[rol] = anillos
    return res


def proy_anillo(r, tol):
    return simplificar([proyectar(la, lo) for la, lo in r], tol)


def area(r):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))) / 2


def dentro(px, py, r):
    c = False
    for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
        if (y0 > py) != (y1 > py) and px < (x1 - x0) * (py - y0) / (y1 - y0 + 1e-12) + x0:
            c = not c
    return c


def dist_borde(px, py, anillos):
    m = float("inf")
    for r in anillos:
        for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
            dx, dy = x1 - x0, y1 - y0
            t = max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy + 1e-12)))
            m = min(m, math.hypot(px - x0 - t * dx, py - y0 - t * dy))
    return m


def centro_visual(anillos, caja=None):
    """Punto interior más alejado del borde (polylabel a lo bruto), opcionalmente dentro de una caja."""
    grande = max(anillos, key=area)
    xs, ys = [p[0] for p in grande], [p[1] for p in grande]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if caja:
        x0, y0, x1, y1 = max(x0, caja[0]), max(y0, caja[1]), min(x1, caja[2]), min(y1, caja[3])
        if x0 >= x1 or y0 >= y1:
            return None
    mejor, paso = None, max(x1 - x0, y1 - y0) / 24
    for _ in range(3):
        cand = []
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                if dentro(x, y, grande):
                    cand.append((dist_borde(x, y, [grande]), x, y))
                x += paso
            y += paso
        if not cand:
            break
        mejor = max(cand)
        x0, x1, y0, y1 = mejor[1] - paso, mejor[1] + paso, mejor[2] - paso, mejor[2] + paso
        paso /= 4
    return (round(mejor[1]), round(mejor[2])) if mejor else None


def linea_central(vias):
    """De un montón de tramos (calzadas dobles incluidas) saca una línea limpia para rotular."""
    pts = [p for v in vias for p in v]
    if len(pts) < 4:
        return None
    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    syy = sum((p[1] - my) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uy = math.cos(ang), math.sin(ang)
    if ux < 0:
        ux, uy = -ux, -uy  # que el texto se lea de izquierda a derecha
    proy = sorted(((p[0] - mx) * ux + (p[1] - my) * uy, p) for p in pts)
    t0, t1 = proy[0][0], proy[-1][0]
    n = max(4, int((t1 - t0) / 60))
    cubos = [[] for _ in range(n)]
    for t, p in proy:
        cubos[min(n - 1, int((t - t0) / (t1 - t0 + 1e-9) * n))].append(p)
    linea = [(sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)) for c in cubos if c]
    # suavizado ligero para que el textPath no haga eses
    suave = [linea[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                          for a, b, c in zip(linea, linea[1:], linea[2:])] + [linea[-1]]
    return suave, math.hypot(suave[-1][0] - suave[0][0], suave[-1][1] - suave[0][1])


def main():
    s, w, n, e = CAJA_GRANDE
    cs, cw, cn, ce = CAJA_CIUDAD

    log("Distritos…")
    dist_rel = overpass(f"rel(id:{','.join(map(str, DISTRITOS))});out geom;")
    log("Municipios…")
    muni_rel = overpass(f'rel({s},{w},{n},{e})["boundary"="administrative"]["admin_level"="8"];out geom;')
    log("Vías principales…")
    vias = overpass(f'way({cs},{cw},{cn},{ce})["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"];out geom;')
    log("Parques y bosque…")
    verdes = overpass(f'(way({cs},{cw},{cn},{ce})["leisure"="park"];rel({cs},{cw},{cn},{ce})["leisure"="park"];'
                      f'way({cs},{cw},{cn},{ce})["natural"="wood"];rel({cs},{cw},{cn},{ce})["natural"="wood"];'
                      f'way({cs},{cw},{cn},{ce})["landuse"="forest"];rel({cs},{cw},{cn},{ce})["landuse"="forest"];);out geom;')
    log("Ríos…")
    rios = overpass(f'way({s},{w},{n},{e})["waterway"="river"]["name"~"Besòs|Llobregat"];out geom;')

    # -- distritos
    distritos, bcn_anillos = [], []
    for r in dist_rel:
        an = [proy_anillo(a, 1.2) for a in anillos_de(r)["outer"]]
        nombre = NOMBRE_DISTRITO.get(r["tags"].get("name"), r["tags"].get("name"))
        distritos.append(dict(n=nombre, d=a_path(an), l=centro_visual(an)))
        bcn_anillos += an
    xs = [p[0] for a in bcn_anillos for p in a]
    ys = [p[1] for a in bcn_anillos for p in a]
    limites = [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]
    log("  límites de Barcelona:", limites)

    # -- municipios (Barcelona incluida, para dibujar la costa)
    municipios, barcelona = [], None
    marco = (limites[0] - 300, limites[1] - 300, limites[2] + 300, limites[3] + 300)
    for r in muni_rel:
        nombre = r["tags"].get("name", "")
        an = anillos_de(r)
        ext = [proy_anillo(a, 1.5) for a in an["outer"]]
        if not ext:
            continue
        if nombre == "Barcelona":
            barcelona = a_path(ext)
            continue
        cx = [p[0] for a in ext for p in a]
        cy = [p[1] for a in ext for p in a]
        if max(cx) < marco[0] or min(cx) > marco[2] or max(cy) < marco[1] or min(cy) > marco[3]:
            continue
        lab = centro_visual(ext, caja=(limites[0] + 40, limites[1] + 40, limites[2] - 40, limites[3] - 40))
        municipios.append(dict(n=nombre, d=a_path(ext), l=lab))

    # -- vías
    trazos, por_nombre = [], {}
    for v in vias:
        g = v.get("geometry") or []
        if len(g) < 2:
            continue
        pts = simplificar([proyectar(p["lat"], p["lon"]) for p in g], 1.0)
        nom = v["tags"].get("name")
        if v["tags"].get("highway") != "tertiary":
            trazos.append((v["tags"].get("highway"), pts))
        if nom in AVENIDAS:
            por_nombre.setdefault(nom, []).append(pts)
    principales = a_path([p for h, p in trazos if h in ("motorway", "trunk", "primary")], cerrar=False)
    secundarias = a_path([p for h, p in trazos if h == "secondary"], cerrar=False)
    avenidas = []
    for nom, tramos in por_nombre.items():
        lc = linea_central(tramos)
        if lc and lc[1] > 100:
            avenidas.append(dict(n=AVENIDAS[nom], d=a_path([lc[0]], cerrar=False)))

    # -- verdes (sólo lo que se ve: más de ~4 ha)
    manchas = []
    for el in verdes:
        if el["type"] == "way" and el.get("geometry"):
            r = [(p["lat"], p["lon"]) for p in el["geometry"]]
            if r[0] == r[-1]:
                manchas.append(proy_anillo(r, 2))
        elif el["type"] == "relation":
            manchas += [proy_anillo(a, 2) for a in anillos_de(el)["outer"]]
    manchas = [m for m in manchas if len(m) > 3 and area(m) > 400]
    log(f"  {len(manchas)} manchas verdes")

    # -- ríos
    rio_d = {}
    for v in rios:
        nom = "Besòs" if "Besòs" in v["tags"].get("name", "") else "Llobregat"
        rio_d.setdefault(nom, []).append(simplificar([proyectar(p["lat"], p["lon"]) for p in v["geometry"]], 2))
    rios_out = [dict(n=k, d=a_path(v, cerrar=False)) for k, v in rio_d.items()]

    datos = dict(
        proy=dict(lat0=LAT0, lon0=LON0, kx=KX, ky=KY, giro=GIRO),
        limites=limites, barcelona=barcelona, distritos=distritos, municipios=municipios,
        principales=principales, secundarias=secundarias, avenidas=avenidas,
        verde=a_path(manchas), rios=rios_out,
        fuente="© colaboradores de OpenStreetMap",
    )
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as fh:
        fh.write("/* Generado por generar_mapa.py — © colaboradores de OpenStreetMap (ODbL) */\n")
        fh.write("window.MAPA = ")
        json.dump(datos, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    log(f"Listo: {os.path.getsize(SALIDA) // 1024} KB -> {SALIDA}")


if __name__ == "__main__":
    main()

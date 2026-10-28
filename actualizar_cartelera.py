#!/usr/bin/env python3
"""
Sessió Contínua — actualiza data/cartelera.js con la cartelera de Barcelona.

    python actualizar_cartelera.py            # 7 días desde hoy
    python actualizar_cartelera.py --dias 4

Fuentes de horarios
  - Sensacine (casi todos los cines comerciales de Barcelona y alrededores)
  - cinemamalda.com            (Cinema Maldà)
  - espaitexas.cat             (Espai Texas)
  - Programa mensual en PDF de la Filmoteca de Catalunya (necesita `pdftotext`,
    que viene con Git for Windows / poppler; si no está, se salta la Filmoteca)

Los precios NO salen de ninguna API: son las tarifas publicadas de cada cine,
apuntadas a mano más abajo (CINES -> precio). Revisarlas de vez en cuando.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "data", "cartelera.js")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Lunes=0 ... Domingo=6. "espectador" marca qué días son el día barato.
# extra: suplementos por formato. fiable=False -> el precio se enseña como orientativo.
MOOBY_EXTRA = {"3D": 2, "Atmos": 1}
CINESA_EXTRA = {"3D": 2.5, "iSense": 2.5, "IMAX": 3, "4DX": 4.5, "ScreenX": 3}
YELMO_EXTRA = {"3D": 2.5, "Premium": 0}
FUENTE_BU = "Guía de precios de Barcelona Urbana, comprobada el 19/09/2026"
FUENTE_EC = "Ficha de eCartelera (sin fecha, puede estar desactualizada)"

CINES = [
    # ---------------------------------------------------------------- Barcelona
    dict(id="verdi", src="sc:E0608", nombre="Cines Verdi", corto="Verdi", cadena="Verdi",
         zona="bcn", barrio="Vila de Gràcia", distrito="Gràcia", dir="Carrer de Verdi, 32",
         transporte="Fontana (L3)", lat=41.40401, lon=2.15688, web="https://barcelona.cines-verdi.com/",
         siempre_vo=True,
         precio=dict(dias=[5.9, 8.5, 8.5, 8.5, 8.5, 10, 10], espectador=[0], festivo=10,
                     matinal=dict(antes="13:30", precio=7), extra={"Atmos": 1},
                     fuente=FUENTE_BU, fiable=True,
                     nota="Lunes 5,90 €. Matinales 7 €. Amics del Verdi: 5,40 € los lunes.")),
    dict(id="renoir", src="sc:E0581", nombre="Renoir Floridablanca", corto="Renoir", cadena="Renoir",
         zona="bcn", barrio="Sant Antoni", distrito="Eixample", dir="Carrer de Floridablanca, 135",
         transporte="Sant Antoni (L2)", lat=41.38148, lon=2.16244, web="https://www.cinesrenoir.com/",
         siempre_vo=True,
         precio=dict(dias=[5.5, 8.5, 5.5, 8.5, 9.8, 9.8, 9.8], espectador=[0, 2], festivo=9.8,
                     fuente=FUENTE_BU, fiable=True,
                     nota="Lunes y miércoles 5,50 €. Mayores de 65: 2 € los martes (cupo limitado).")),
    dict(id="girona", src="sc:E0747", nombre="Cinemes Girona", corto="Girona", cadena="Independiente",
         zona="bcn", barrio="Dreta de l'Eixample", distrito="Eixample", dir="Carrer de Girona, 175",
         transporte="Verdaguer (L4·L5)", lat=41.39961, lon=2.16460, web="https://www.cinemesgirona.cat/",
         siempre_vo=True,
         precio=dict(dias=[7, 7, 5, 7, 9, 9, 9], espectador=[2], festivo=9,
                     fuente=FUENTE_BU, fiable=True,
                     nota="Miércoles 5 € para todos. Mayores de 65: 2 € los martes. Socios 5,50 €.")),
    dict(id="zumzeig", src="sc:E0850", nombre="Zumzeig Cinema", corto="Zumzeig", cadena="Cooperativa",
         zona="bcn", barrio="Hostafrancs", distrito="Sants-Montjuïc", dir="Carrer de Béjar, 53",
         transporte="Tarragona (L3)", lat=41.37732, lon=2.14503, web="https://www.zumzeigcine.coop/",
         siempre_vo=True,
         precio=dict(dias=[7.5, 7.5, 5.5, 7.5, 7.5, 7.5, 7.5], espectador=[2], festivo=7.5,
                     fuente=FUENTE_BU, fiable=True,
                     nota="Miércoles 5,50 €. Reducida 6 € (paro, estudiantes, pensionistas).")),
    dict(id="malda", src="malda", nombre="Cinema Maldà", corto="Maldà", cadena="Independiente",
         zona="bcn", barrio="Barri Gòtic", distrito="Ciutat Vella", dir="Carrer del Pi, 5",
         transporte="Liceu (L3)", lat=41.38324, lon=2.17390, web="https://www.cinemamalda.com/",
         siempre_vo=True,
         precio=dict(dias=[5.5, 8, 8, 8, 8, 8.5, 8.5], espectador=[0], festivo=8.5,
                     fuente=FUENTE_EC, fiable=False,
                     nota="Entradas solo en taquilla. Martes 2 € para mayores de 65.")),
    dict(id="texas", src="texas", nombre="Espai Texas", corto="Texas", cadena="Independiente",
         zona="bcn", barrio="Vila de Gràcia", distrito="Gràcia", dir="Carrer de Bailèn, 205",
         transporte="Joanic (L4)", lat=41.40205, lon=2.16364, web="https://espaitexas.cat/",
         siempre_vo=False,
         precio=dict(dias=[6, 6, 6, 6, 6, 6, 6], espectador=[], festivo=6,
                     fuente="Precio de reapertura publicado en prensa (2023)", fiable=False,
                     nota="Cine en catalán o subtitulado al catalán. Martes sénior a 2 €.")),
    dict(id="filmoteca", src="filmoteca", nombre="Filmoteca de Catalunya", corto="Filmoteca",
         cadena="Pública", zona="bcn", barrio="El Raval", distrito="Ciutat Vella",
         dir="Plaça de Salvador Seguí, 1-9", transporte="Liceu (L3) · Paral·lel (L2·L3)",
         lat=41.37874, lon=2.17111, web="https://cultura.gencat.cat/ca/actualitat/filmoteca/",
         siempre_vo=True,
         precio=dict(dias=[4, 4, 4, 4, 4, 4, 4], espectador=[], festivo=4,
                     fuente="Programa oficial de la Filmoteca (octubre 2026)", fiable=True,
                     nota="Reducida 3 € (estudiantes, menores de 30, mayores de 65, paro). Cierra los lunes.")),
    dict(id="phenomena", src="sc:E0544", nombre="Sala Phenomena Experience", corto="Phenomena",
         cadena="Independiente", zona="bcn", barrio="Sagrada Família", distrito="Eixample",
         dir="Carrer de Sant Antoni Maria Claret, 168", transporte="Sant Pau | Dos de Maig (L5)",
         lat=41.40909, lon=2.17181, web="https://www.phenomena-experience.com/",
         siempre_vo=True,
         precio=dict(dias=[12] * 7, espectador=[], festivo=12, fuente=FUENTE_BU, fiable=True,
                     nota="Sesiones especiales 15 €. Pantalla 4K y Dolby Atmos.")),
    dict(id="aribau", src="sc:E0091", nombre="Mooby Aribau", corto="Aribau", cadena="Mooby",
         zona="bcn", barrio="Antiga Esquerra de l'Eixample", distrito="Eixample", dir="Carrer d'Aribau, 8",
         transporte="Universitat (L1·L2)", lat=41.38612, lon=2.16253, web="https://moobycinemas.com/",
         precio=dict(dias=[9, 9, 6.5, 9, 9, 10, 10], espectador=[2], festivo=10,
                     matinal=dict(antes="13:30", precio=8), extra=MOOBY_EXTRA,
                     fuente=FUENTE_BU, fiable=True, nota="Mayores de 65: 2 € los martes.")),
    dict(id="bosque", src="sc:E0136", nombre="Mooby Bosque", corto="Bosque", cadena="Mooby",
         zona="bcn", barrio="Vila de Gràcia", distrito="Gràcia", dir="Rambla del Prat, 16",
         transporte="Fontana (L3)", lat=41.40147, lon=2.15183, web="https://moobycinemas.com/",
         precio=dict(dias=[9, 9, 6.5, 9, 9, 10, 10], espectador=[2], festivo=10, vose=6.5,
                     extra=MOOBY_EXTRA, fuente=FUENTE_BU, fiable=True,
                     nota="Toda sesión en VOSE a 6,50 €, cualquier día.")),
    dict(id="balmes", src="sc:E0808", nombre="Mooby Balmes", corto="Balmes", cadena="Mooby",
         zona="bcn", barrio="El Putxet", distrito="Sarrià-Sant Gervasi", dir="Carrer de Balmes, 422-424",
         transporte="FGC El Putxet", lat=41.40718, lon=2.13860, web="https://moobycinemas.com/",
         precio=dict(dias=[9, 9, 6.5, 9, 9, 10, 10], espectador=[2], festivo=10,
                     matinal=dict(antes="13:30", precio=8, dias=[6]), extra=MOOBY_EXTRA,
                     fuente=FUENTE_BU, fiable=True, nota="Matinal del domingo 8 €.")),
    dict(id="arenas", src="sc:E0764", nombre="Mooby Arenas", corto="Arenas", cadena="Mooby",
         zona="bcn", barrio="Plaça d'Espanya", distrito="Eixample", dir="Gran Via, 385 (C.C. Arenas)",
         transporte="Espanya (L1·L3)", lat=41.37630, lon=2.14930, web="https://moobycinemas.com/",
         precio=dict(dias=[9, 9, 6.5, 6.5, 9, 10, 10], espectador=[2, 3], festivo=10,
                     matinal=dict(antes="13:30", precio=8, dias=[6]), extra=MOOBY_EXTRA,
                     fuente=FUENTE_BU, fiable=True,
                     nota="Miércoles y jueves 6,50 €. Menores de 13: 8 €.")),
    dict(id="glories", src="sc:E0442", nombre="Mooby Glòries", corto="Glòries", cadena="Mooby",
         zona="bcn", barrio="El Parc i la Llacuna", distrito="Sant Martí", dir="Av. Diagonal, 208 (C.C. Glòries)",
         transporte="Glòries (L1)", lat=41.40487, lon=2.19260, web="https://moobycinemas.com/",
         precio=dict(dias=[9, 9, 6.5, 6.5, 9, 10, 10], espectador=[2, 3], festivo=10,
                     extra=MOOBY_EXTRA, fuente=FUENTE_BU, fiable=True,
                     nota="Miércoles y jueves 6,50 €.")),
    dict(id="gransarria", src="sc:E0447", nombre="Mooby Gran Sarrià Club", corto="Gran Sarrià",
         cadena="Mooby", zona="bcn", barrio="Les Tres Torres", distrito="Sarrià-Sant Gervasi",
         dir="Ronda del General Mitre, 38-44", transporte="FGC Les Tres Torres",
         lat=41.39453, lon=2.13363, web="https://moobycinemas.com/",
         precio=dict(dias=[11, 11, 11, 11, 13, 13, 13], espectador=[], festivo=13,
                     fuente=FUENTE_BU, fiable=True,
                     nota="Sala club: butacas reclinables. Sin día del espectador.")),
    dict(id="cdiagonal", src="sc:E0381", nombre="Cinesa Diagonal", corto="Cinesa Diagonal",
         cadena="Cinesa", zona="bcn", barrio="Sant Gervasi-Galvany", distrito="Sarrià-Sant Gervasi",
         dir="Carrer de Santa Fe de Nou Mèxic, s/n", transporte="Tram Francesc Macià",
         lat=41.39419, lon=2.13660, web="https://www.cinesa.es/",
         precio=dict(dias=[9, 9, 7, 9, 9, 9.2, 9.2], espectador=[2], festivo=9.2,
                     extra=CINESA_EXTRA, fuente=FUENTE_EC, fiable=False,
                     nota="Cinesa calcula el precio por sesión; con myCinesa suele salir más barato.")),
    dict(id="cdiagonalmar", src="sc:E0382", nombre="Cinesa Diagonal Mar", corto="Diagonal Mar",
         cadena="Cinesa", zona="bcn", barrio="Diagonal Mar", distrito="Sant Martí",
         dir="Av. Diagonal, 3 (C.C. Diagonal Mar)", transporte="El Maresme | Fòrum (L4)",
         lat=41.41120, lon=2.21894, web="https://www.cinesa.es/",
         precio=dict(dias=[9, 9, 6.8, 9, 9, 9.2, 9.2], espectador=[2], festivo=9.2,
                     extra=CINESA_EXTRA, fuente=FUENTE_EC, fiable=False,
                     nota="2x1 myCinesa hasta el 13/12/2026 (no en iSense ni IMAX).")),
    dict(id="csom", src="sc:E0388", nombre="Cinesa SOM Multiespai", corto="SOM", cadena="Cinesa",
         zona="bcn", barrio="Porta", distrito="Nou Barris", dir="Pg. d'Andreu Nin, s/n (C.C. SOM)",
         transporte="Fabra i Puig (L1)", lat=41.43831, lon=2.18044, web="https://www.cinesa.es/",
         precio=dict(dias=[9, 9, 6.8, 9, 9, 9.2, 9.2], espectador=[2], festivo=9.2,
                     matinal=dict(antes="13:00", precio=6), extra=CINESA_EXTRA,
                     fuente=FUENTE_EC, fiable=False, nota="Sesión matinal 6 €.")),
    dict(id="maquinista", src="sc:G01UU", nombre="Yelmo Westfield La Maquinista", corto="La Maquinista",
         cadena="Yelmo", zona="bcn", barrio="Sant Andreu", distrito="Sant Andreu",
         dir="Carrer de Potosí, 2 (C.C. La Maquinista)", transporte="Sant Andreu (L1) · Rodalies Sant Andreu Comtal",
         lat=41.44183, lon=2.19939, web="https://www.yelmocines.es/",
         precio=dict(dias=[9.9, 9.9, 6.9, 9.9, 9.9, 9.9, 9.9], espectador=[2], festivo=9.9,
                     extra=YELMO_EXTRA, fuente=FUENTE_EC, fiable=False,
                     nota="Reducida 7,50 € (carnet jove, sénior, niños).")),
    # ------------------------------------------------------- Área metropolitana
    dict(id="splau", src="sc:E0756", nombre="Kinépolis Full Splau", corto="Splau", cadena="Kinépolis",
         zona="amb", barrio="Cornellà de Llobregat", distrito="Baix Llobregat",
         dir="Av. del Baix Llobregat (C.C. Splau)", transporte="Tram Centre Comercial Splau",
         lat=41.34691, lon=2.08061, web="https://kinepolis.es/",
         precio=dict(dias=[9.4, 7, 9.4, 9.4, 9.4, 9.4, 9.4], espectador=[1], festivo=9.4,
                     extra={"3D": 2}, fuente=FUENTE_EC, fiable=False, nota="Martes día del espectador.")),
    dict(id="lafarga", src="sc:E0391", nombre="Cinesa La Farga", corto="La Farga", cadena="Cinesa",
         zona="amb", barrio="L'Hospitalet", distrito="Barcelonès",
         dir="Av. de Josep Tarradellas i Joan, 145", transporte="Rambla Just Oliveras (L1)",
         lat=41.36339, lon=2.10454, web="https://www.cinesa.es/",
         precio=dict(dias=[7.5, 7.5, 5.7, 7.5, 7.5, 8, 8], espectador=[2], festivo=8,
                     extra=CINESA_EXTRA, fuente=FUENTE_EC, fiable=False, nota="")),
    dict(id="granvia", src="sc:E0439", nombre="Filmax Gran Via", corto="Filmax Gran Via", cadena="Filmax",
         zona="amb", barrio="L'Hospitalet", distrito="Barcelonès", dir="Gran Via, 75 (C.C. Gran Via 2)",
         transporte="Europa | Fira (L9 Sud) · FGC Ildefons Cerdà", lat=41.35956, lon=2.12900,
         web="https://www.filmaxgranvia.com/",
         precio=dict(dias=[7.7, 8.3, 8.3, 8.3, 8.3, 8.3, 8.3], espectador=[0], festivo=8.3,
                     extra={"3D": 2.1}, fuente=FUENTE_EC, fiable=False, nota="Lunes día del espectador.")),
    dict(id="magic", src="sc:E0713", nombre="Ocine Màgic", corto="Màgic", cadena="Ocine",
         zona="amb", barrio="Badalona", distrito="Barcelonès", dir="Carrer de la Concòrdia, 1 (C.C. Màgic)",
         transporte="Bus B24 · Rodalies Badalona", lat=41.44328, lon=2.22981, web="https://www.ocine.es/",
         precio=None),
    dict(id="santcugat", src="sc:E0403", nombre="Cinemes Sant Cugat", corto="Cinemes St. Cugat",
         cadena="Independiente", zona="amb", barrio="Sant Cugat del Vallès", distrito="Vallès Occidental",
         dir="Av. del Pla del Vinyet, s/n", transporte="FGC Sant Cugat", lat=41.46992, lon=2.08952,
         web="https://www.cinemessantcugat.com/", precio=None),
    dict(id="yelmosc", src="sc:E0633", nombre="Yelmo Premium Sant Cugat", corto="Yelmo St. Cugat",
         cadena="Yelmo", zona="amb", barrio="Sant Cugat del Vallès", distrito="Vallès Occidental",
         dir="Av. de la Via Augusta, 2-14 (ONA Sant Cugat)", transporte="FGC Volpelleres",
         lat=41.48289, lon=2.05323, web="https://www.yelmocines.es/",
         precio=dict(dias=[6.8, 8.4, 6.8, 8.4, 8.4, 8.4, 8.4], espectador=[0, 2], festivo=8.4,
                     extra=YELMO_EXTRA, fuente=FUENTE_EC, fiable=False, nota="Lunes y miércoles 6,80 €.")),
    dict(id="cinebaix", src="sc:E0276", nombre="Cinebaix", corto="Cinebaix", cadena="Independiente",
         zona="amb", barrio="Sant Feliu de Llobregat", distrito="Baix Llobregat", dir="Carrer de Joan Batllori, 21",
         transporte="Rodalies Sant Feliu", lat=41.38193, lon=2.04507, web="https://www.cinebaix.com/",
         precio=dict(dias=[5, 6, 6, 6, 6, 6, 6], espectador=[0], festivo=6, extra={"3D": 2},
                     fuente=FUENTE_EC, fiable=False, nota="Socios 7 € en 3D.")),
    dict(id="barnasud", src="sc:E0661", nombre="Cinesa Barnasud", corto="Barnasud", cadena="Cinesa",
         zona="amb", barrio="Gavà", distrito="Baix Llobregat", dir="Carrer del Progrés, 69 (C.C. Barnasud)",
         transporte="Bus L94 · L95", lat=41.29758, lon=2.00883, web="https://www.cinesa.es/",
         precio=dict(dias=[7.5, 7.5, 5.7, 7.5, 7.5, 8, 8], espectador=[2], festivo=8,
                     extra=CINESA_EXTRA, fuente=FUENTE_EC, fiable=False, nota="")),
    dict(id="castelldefels", src="sc:E0806", nombre="Yelmo Premium Castelldefels", corto="Castelldefels",
         cadena="Yelmo", zona="amb", barrio="Castelldefels", distrito="Baix Llobregat",
         dir="Av. del Canal Olímpic, 24 (C.C. Ànec Blau)", transporte="Rodalies Castelldefels",
         lat=41.28211, lon=1.98559, web="https://www.yelmocines.es/",
         precio=dict(dias=[7.7, 7.7, 6, 7.7, 7.7, 7.7, 7.7], espectador=[2], festivo=7.7,
                     extra=YELMO_EXTRA, fuente=FUENTE_EC, fiable=False, nota="")),
    dict(id="montcada", src="sc:E0655", nombre="Cines Montcada", corto="Montcada", cadena="Independiente",
         zona="amb", barrio="Montcada i Reixac", distrito="Vallès Occidental", dir="Carrer de Verdi, 2",
         transporte="Rodalies Montcada i Reixac", lat=41.49491, lon=2.18016, web="", precio=None),
]

# Cines revisados que esta semana no publican horarios en ninguna fuente.
SIN_HORARIOS = ["Verdi Park (Torrijos, 49)", "Casa Montjuïc"]
# Cerrados en los últimos años (para la nota del pie).
CERRADOS = [("Boliche", 2024), ("Yelmo Comedia", 2024), ("Yelmo Icària", 2023)]

FESTIVOS = [
    "2026-01-01", "2026-01-06", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-25",
    "2026-06-24", "2026-08-15", "2026-09-11", "2026-09-24", "2026-10-12", "2026-11-01",
    "2026-12-08", "2026-12-25", "2026-12-26",
    "2027-01-01", "2027-01-06", "2027-03-26", "2027-03-29", "2027-05-01", "2027-05-17",
    "2027-06-24", "2027-08-15", "2027-09-11", "2027-09-24", "2027-10-12", "2027-11-01",
    "2027-12-06", "2027-12-08", "2027-12-25", "2027-12-26",
]

# Títulos en catalán de las webs pequeñas -> título con el que lo conoce Sensacine.
ALIAS = {
    "els nens estan be": "los ninos estan bien",
    "el samurai i el presoner": "el samurai y el prisionero",
    "la invasio dels barbars": "las invasiones barbaras",
    "suportant el paradis": "soportando el paraiso",
}

MESES_CA = ["gener", "febrer", "marc", "abril", "maig", "juny", "juliol", "agost",
            "setembre", "octubre", "novembre", "desembre"]


# ----------------------------------------------------------------- utilidades
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def bajar(url, intentos=6, binario=False):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=40).read()
            time.sleep(0.3)  # no saturar a nadie
            return data if binario else data.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — cualquier fallo de red: reintentar
            if i == intentos - 1:
                log("  ! no se pudo bajar", url, "->", e)
            # 429 = Sensacine pide calma: esperar bastante más
            time.sleep((6 if "429" in str(e) else 1.5) * (i + 1))
    return None


def normal(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return normal(s).replace(" ", "-")[:60]


def minutos(runtime):
    if not runtime:
        return None
    h = re.search(r"(\d+)\s*h", runtime)
    m = re.search(r"(\d+)\s*min", runtime)
    total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return total or None


def texto_plano(h):
    h = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", h))
    return [l.strip() for l in t.split("\n") if l.strip()]


# ------------------------------------------------------------------ Sensacine
VERSION_SC = {
    "original_st": "VOSE", "original_sme": "VOSE", "original": "VO",
    "local": "VOE", "local_st": "VOE", "local_sme": "VOE",
    "dubbed": "DOB", "dubbed_st": "DOB", "dubbed_sme": "DOB",
}


def formatos_sc(st):
    bolsa = " ".join((st.get("tags") or []) + [str(st.get(k)) for k in
                                              ("experience", "comfort", "projection", "picture", "sound")])
    b = bolsa.lower()
    f = []
    if "3d" in b:
        f.append("3D")
    if "imax" in b:
        f.append("IMAX")
    if "isens" in b:
        f.append("iSense")
    if "4dx" in b:
        f.append("4DX")
    if "screenx" in b:
        f.append("ScreenX")
    if "atmos" in b:
        f.append("Atmos")
    if "premium" in b or "vip" in b:
        f.append("Premium")
    if "catal" in b:
        f.append("Català")
    return f


def ficha_sc(m):
    cred = m.get("credits") or []
    directores = [f"{c['person'].get('firstName') or ''} {c['person'].get('lastName') or ''}".strip()
                  for c in cred if (c.get("position") or {}).get("name") == "DIRECTOR"]
    reparto = []
    for e in ((m.get("cast") or {}).get("edges") or [])[:4]:
        a = (e.get("node") or {}).get("actor") or {}
        n = f"{a.get('firstName') or ''} {a.get('lastName') or ''}".strip()
        if n:
            reparto.append(n)
    rel = (m.get("releases") or [{}])[0] or {}
    cert = (rel.get("certificate") or {}).get("code")
    estreno = ((rel.get("releaseDate") or {}).get("date"))
    stats = m.get("stats") or {}
    prensa = ((stats.get("pressReview") or {}).get("score"))
    publico = ((stats.get("userRating") or {}).get("score"))
    poster = (m.get("poster") or {}).get("path")
    return dict(
        id=f"sc{m['internalId']}",
        titulo=m.get("title"),
        original=m.get("originalTitle"),
        duracion=minutos(m.get("runtime")),
        generos=[g.get("translate") for g in (m.get("genres") or []) if g.get("translate")],
        direccion=directores[:2],
        reparto=reparto,
        edad=cert,
        estreno=estreno,
        anio=((m.get("data") or {}).get("productionYear")),
        paises=[c.get("localizedName") for c in (m.get("countries") or []) if c.get("localizedName")],
        idiomas=m.get("languages") or [],
        prensa=round(prensa, 1) if prensa else None,
        publico=round(publico, 1) if publico else None,
        poster=poster,
        sinopsis=(m.get("synopsis") or "").strip(),
        ficha=f"https://www.sensacine.com/peliculas/pelicula-{m['internalId']}/",
    )


def sensacine(codigo, fecha, siempre_vo=False):
    """Devuelve (fichas, sesiones) de un cine y un día.

    Los cines pequeños no etiquetan la versión y Sensacine las marca todas como
    "dubbed"; en los que sólo proyectan VO eso se corrige a VO.
    """
    fichas, sesiones, pagina = {}, [], 1
    while True:
        url = f"https://www.sensacine.com/_/showtimes/theater-{codigo}/d-{fecha}/p-{pagina}/"
        crudo = bajar(url, intentos=4)
        if not crudo:
            return fichas, sesiones, False
        try:
            d = json.loads(crudo)
        except ValueError:
            break
        for r in d.get("results") or []:
            m = r.get("movie")
            if not m:
                continue
            ficha = ficha_sc(m)
            fichas[ficha["id"]] = ficha
            solo_es = set(ficha["idiomas"]) == {"SPANISH"}
            for clave, lista in (r.get("showtimes") or {}).items():
                ver = VERSION_SC.get(clave, "VO")
                if ver == "DOB" and solo_es:
                    ver = "VOE"
                for st in lista or []:
                    ini = st.get("startsAt") or ""
                    if len(ini) < 16:
                        continue
                    compra = ""
                    for tk in ((st.get("data") or {}).get("ticketing") or []):
                        urls = tk.get("urls") or []
                        if urls and (tk.get("provider") == "default" or not compra):
                            compra = urls[0]
                    fmt = formatos_sc(st)
                    if "Català" in fmt and ver == "DOB":
                        ver = "CAT"
                    sin_etiqueta = not any(t.startswith("Localization") for t in (st.get("tags") or []))
                    if ver == "DOB" and siempre_vo and sin_etiqueta:
                        ver = "VO"
                    sesiones.append(dict(peli=ficha["id"], fecha=ini[:10], hora=ini[11:16], ver=ver,
                                         fmt=[f for f in fmt if f != "Català"], url=compra, nota=""))
        tot = (d.get("pagination") or {}).get("totalPages") or 1
        if pagina >= int(tot):
            break
        pagina += 1
    return fichas, sesiones, True


# --------------------------------------------------------------- Cinema Maldà
DIAS_ABR = {"LU": 0, "MA": 1, "MI": 2, "JU": 3, "VI": 4, "SA": 5, "SÁ": 5, "DO": 6}


def malda(fechas):
    h = bajar("https://www.cinemamalda.com/")
    if not h:
        return {}, []
    lineas = texto_plano(h)
    por_numero = {}
    for f in fechas:
        por_numero.setdefault(int(f[8:10]), f)
    fichas, sesiones = {}, []
    titulo, ver, dia = None, None, None
    for i, l in enumerate(lineas):
        if re.fullmatch(r"\((VOSE|VOSC|VOSCAT|VOE|VOCAT|VO|DOB|CAT)\)", l):
            titulo = lineas[i - 1]
            ver = l.strip("()").replace("VOSCAT", "VOSC").replace("VOCAT", "VOC")
            dia = None
            continue
        m = re.fullmatch(r"(LU|MA|MI|JU|VI|SA|SÁ|DO)\s+(\d{1,2})", l.upper())
        if m and titulo:
            dia = int(m.group(2))
            continue
        m = re.match(r"(\d{1,2}):(\d{2})\s*h(.*)", l, flags=re.I)
        if m and titulo and dia and dia in por_numero:
            nota = m.group(3).strip(" -–").capitalize()
            pid = "ml-" + slug(titulo)
            fichas.setdefault(pid, dict(id=pid, titulo=titulo.strip()))
            sesiones.append(dict(peli=pid, fecha=por_numero[dia], hora=f"{int(m.group(1)):02d}:{m.group(2)}",
                                 ver=ver, fmt=[], url="", nota=nota[:80],
                                 gratis="GRATUIT" in nota.upper()))
            continue
        if l.lower().startswith("próximamente"):
            titulo = None
    return fichas, sesiones


# ---------------------------------------------------------------- Espai Texas
def texas(fechas):
    h = bajar("https://espaitexas.cat/")
    if not h:
        return {}, []
    h = re.sub(r"<svg.*?</svg>", "", h, flags=re.S)
    trozos = re.split(r'<div id="day_(\d{4}-\d{2}-\d{2})"', h)
    fichas, sesiones = {}, []
    for k in range(1, len(trozos) - 1, 2):
        fecha, bloque = trozos[k], trozos[k + 1]
        if fecha not in fechas:
            continue
        for clase, href, tit, hora in re.findall(
                r'<a class="event ([a-z ]+)" href="([^"]+)"[^>]*><div class="event-info">'
                r'<div class="event-title[^"]*">\s*([^<]+)</div><div class="event-time[^"]*">\s*([^<]+)</div>',
                bloque):
            tit = html.unescape(tit).strip()
            if "pelicula" not in clase or tit.upper().startswith("CICLE"):
                continue
            s = href.lower()
            ver = ("VOSE" if "-vose-" in s else "VOSC" if "-vosc-" in s else
                   "CAT" if "dob" in s and "cat" in s else "VO")
            titulo = tit.title() if tit.isupper() else tit
            titulo = re.sub(r"\b(I|De|Del|Dels|La|Les|El|Els|L'|D')\b",
                            lambda x: x.group(0).lower(), titulo)
            titulo = titulo[0].upper() + titulo[1:]
            pid = "tx-" + slug(tit)
            fichas.setdefault(pid, dict(id=pid, titulo=titulo))
            sesiones.append(dict(peli=pid, fecha=fecha, hora=hora.strip()[:5], ver=ver, fmt=[],
                                 url=href, nota=""))
    return fichas, sesiones


# ----------------------------------------------------- Filmoteca de Catalunya
DIA_CA = r"(DILLUNS|DIMARTS|DIMECRES|DIJOUS|DIVENDRES|DISSABTE|DIUMENGE)\s+(\d{1,2})"


def _columnas(pagina):
    lineas = pagina.split("\n")
    cortes = []
    for l in lineas:
        ms = list(re.finditer(DIA_CA, l))
        if len(ms) == 2:
            cortes.append(ms[1].start())
        elif len(ms) == 1 and ms[0].start() > 30:
            cortes.append(ms[0].start())
    if not cortes:
        return [lineas]
    corte = max(set(cortes), key=cortes.count)
    izq, der = [], []
    for l in lineas:
        c = corte
        if len(l) > c and l[c - 1] != " " and l[c] != " ":
            # el texto de la izquierda invade la columna: cortar en el hueco doble más cercano
            g = l.find("  ", c - 12)
            c = g + 2 if 0 <= g < c + 12 else c
        izq.append(l[:c].rstrip())
        der.append(l[c:].strip())
    return [izq, der]


def _pdftotext():
    """pdftotext viene con Git for Windows aunque no esté en el PATH de cmd/PowerShell."""
    ruta = shutil.which("pdftotext")
    if ruta:
        return ruta
    for base in (os.environ.get("ProgramFiles", ""), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        candidata = os.path.join(base, "Git", "mingw64", "bin", "pdftotext.exe")
        if base and os.path.exists(candidata):
            return candidata
    return None


def filmoteca(fechas):
    pdftotext = _pdftotext()
    if not pdftotext:
        log("  · pdftotext no está instalado: se salta la Filmoteca")
        return {}, []
    meses = sorted({(int(f[:4]), int(f[5:7])) for f in fechas})
    fichas, sesiones = {}, []
    for anio, mes in meses:
        url = (f"https://cultura.gencat.cat/web/.content/filmoteca/"
               f"filmoteca-programacio-{MESES_CA[mes - 1]}-{anio}.pdf")
        pdf = bajar(url, binario=True)
        if not pdf or not pdf.startswith(b"%PDF"):
            log("  · Filmoteca: sin programa para", MESES_CA[mes - 1], anio)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "f.pdf")
            open(ruta, "wb").write(pdf)
            subprocess.run([pdftotext, "-enc", "UTF-8", "-layout", ruta, ruta + ".txt"], check=False)
            if not os.path.exists(ruta + ".txt"):
                continue
            # NFKC deshace ligaduras tipográficas del PDF ("ﬁ" -> "fi")
            txt = unicodedata.normalize("NFKC", open(ruta + ".txt", encoding="utf-8", errors="replace").read())
        paginas = [p for p in txt.split("\f") if re.search(DIA_CA, p)]
        dia = None
        for p in paginas:
            for col in _columnas(p):
                col = [l.strip() for l in col] + [""]
                actual = None
                for i, s in enumerate(col):
                    m = re.fullmatch(DIA_CA, s)
                    if m:
                        dia = int(m.group(2))
                        actual = None
                        continue
                    m = re.match(r"(\d{1,2})[.:](\d{2})\s*\|\s*(.*)", s)
                    if m and dia:
                        actual = dict(hora=f"{int(m.group(1)):02d}:{m.group(2)}", ciclo=m.group(3).strip(),
                                      titulos=[], dia=dia, gratis=False)
                        continue
                    if actual is None or not s:
                        continue
                    if "gratu" in s.lower():
                        actual["gratis"] = True
                    # "Director, 1999. VOSC. 188’. 35mm." — a veces partido en dos líneas
                    m = re.match(r"(.+?),\s*(\d{4})(?:-\d{4})?\.", s)
                    if m:
                        junto = s + " " + col[i + 1]
                        v = re.search(r"\b(VOSE|VOSC|VOSI|VOE|VE|VOC|VO|SD|Muda)\b", junto)
                        d = re.search(r"(\d+)\s*’", junto)
                        fecha = f"{anio}-{mes:02d}-{actual['dia']:02d}"
                        if fecha in fechas and actual["titulos"]:
                            tit = actual["titulos"][0]
                            alt = actual["titulos"][1] if len(actual["titulos"]) > 1 else ""
                            if tit.endswith(":") and alt:
                                tit, alt = f"{tit} {alt}", ""
                            tit = tit.rstrip(" /.")
                            pid = "fm-" + slug(tit)
                            fichas.setdefault(pid, dict(id=pid, titulo=tit, original=tit, alt=alt,
                                                        direccion=[m.group(1).strip()],
                                                        anio=int(m.group(2)),
                                                        duracion=int(d.group(1)) if d else None))
                            ver = {"VE": "VOE", "SD": "VO", None: "VO"}.get(v and v.group(1), v and v.group(1))
                            sesiones.append(dict(peli=pid, fecha=fecha, hora=actual["hora"], ver=ver, fmt=[],
                                                 url="", nota=("Ciclo: " + actual["ciclo"])[:80],
                                                 gratis=actual["gratis"]))
                        actual = None
                        continue
                    if len(actual["titulos"]) < 2 and "gratu" not in s.lower():
                        actual["titulos"].append(s)
    return fichas, sesiones


# ---------------------------------------------------------------------- main
def fusionar_titulos(fichas, sesiones):
    """Las webs pequeñas usan títulos sueltos: si coincide con otra ficha (mejor si es de
    Sensacine, que trae póster y sinopsis), se une a ella."""
    indice = {}
    for f in sorted(fichas.values(), key=lambda f: not f["id"].startswith("sc")):
        for t in (f.get("titulo"), f.get("original")):
            if t:
                indice.setdefault(normal(t), f["id"])
    cambio = {}
    for f in list(fichas.values()):
        if f["id"].startswith("sc"):
            continue
        for t in (f.get("titulo"), f.get("original"), f.get("alt")):
            n = normal(t or "")
            n = ALIAS.get(n, n)
            if indice.get(n, f["id"]) != f["id"]:
                cambio[f["id"]] = indice[n]
                break
    for viejo, nuevo in list(cambio.items()):
        while nuevo in cambio:  # A->B->C
            nuevo = cambio[nuevo]
        cambio[viejo] = nuevo
    for s in sesiones:
        s["peli"] = cambio.get(s["peli"], s["peli"])
    for viejo in cambio:
        fichas.pop(viejo, None)


def validar(datos):
    """Devuelve un texto con el problema si la cartelera nueva parece incompleta
    (p. ej. Sensacine ha cambiado su HTML o nos ha bloqueado); None si está bien."""
    n_ses, n_pel = len(datos["sesiones"]), len(datos["peliculas"])
    if n_ses < 100 or n_pel < 10:
        return f"solo {n_ses} sesiones y {n_pel} películas"
    try:
        with open(SALIDA, encoding="utf-8") as fh:
            txt = fh.read()
        ant = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except (OSError, ValueError):
        return None  # no hay cartelera previa con la que comparar
    if len(ant["fechas"]) == len(datos["fechas"]) and n_ses < 0.6 * len(ant["sesiones"]):
        return f"{n_ses} sesiones frente a {len(ant['sesiones'])} de la cartelera anterior"
    cines_ant = {f[0] for f in ant["sesiones"]}
    cines_nue = {f[0] for f in datos["sesiones"]}
    if len(cines_nue) < 0.7 * len(cines_ant):
        return f"hay sesiones de {len(cines_nue)} cines frente a {len(cines_ant)} antes"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--desde", help="YYYY-MM-DD (por defecto, hoy)")
    ap.add_argument("--forzar", action="store_true",
                    help="escribir aunque el resultado parezca incompleto")
    a = ap.parse_args()
    inicio = dt.date.fromisoformat(a.desde) if a.desde else dt.date.today()
    fechas = [(inicio + dt.timedelta(days=i)).isoformat() for i in range(a.dias)]
    log(f"Cartelera del {fechas[0]} al {fechas[-1]}")

    fichas, crudas = {}, []  # crudas: (cine_id, sesion)
    tareas = [(c, f) for c in CINES if c["src"].startswith("sc:") for f in fechas]

    def una(t):
        c, f = t
        return t, sensacine(c["src"][3:], f, c.get("siempre_vo", False))

    fallidas = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for n, (t, (fs, ss, ok)) in enumerate(ex.map(una, tareas), 1):
            if ok:
                fichas.update(fs)
                crudas += [(t[0]["id"], s) for s in ss]
            else:
                fallidas.append(t)
            if n % 20 == 0:
                log(f"  Sensacine {n}/{len(tareas)}")
    # Si Sensacine ha pedido calma (429), segunda vuelta despacio con lo que faltó
    if fallidas:
        log(f"  {len(fallidas)} cine-días pendientes; reintento con calma…")
        time.sleep(20)
        for t in fallidas:
            _, (fs, ss, ok) = una(t)
            if ok:
                fichas.update(fs)
                crudas += [(t[0]["id"], s) for s in ss]
            else:
                log(f"  ! sin datos: {t[0]['nombre']} {t[1]}")
            time.sleep(2)

    for cid, fn in (("malda", malda), ("texas", texas), ("filmoteca", filmoteca)):
        try:
            fs, ss = fn(fechas)
        except Exception as e:  # noqa: BLE001 — una web rota no debe tumbar el resto
            log(f"  ! {cid}: {e}")
            fs, ss = {}, []
        log(f"  {cid}: {len(ss)} sesiones")
        fichas.update(fs)
        crudas += [(cid, s) for s in ss]

    sesiones_planas = [s for _, s in crudas]
    fusionar_titulos(fichas, sesiones_planas)

    # quitar duplicados exactos (Sensacine repite a veces la misma sesión en dos listas)
    vistas, limpias = set(), []
    for cid, s in crudas:
        k = (cid, s["peli"], s["fecha"], s["hora"], s["ver"])
        if k in vistas:
            continue
        vistas.add(k)
        limpias.append((cid, s))

    usados = {s["peli"] for _, s in limpias}
    pelis = sorted((f for f in fichas.values() if f["id"] in usados), key=lambda f: normal(f["titulo"]))
    idx_peli = {p["id"]: i for i, p in enumerate(pelis)}
    idx_cine = {c["id"]: i for i, c in enumerate(CINES)}
    idx_fecha = {f: i for i, f in enumerate(fechas)}

    # url de compra: guardar sólo lo que cambia respecto a un prefijo común por cine
    prefijos = {}
    for cid, s in limpias:
        if s["url"]:
            prefijos.setdefault(cid, []).append(s["url"])
    for cid, urls in prefijos.items():
        p = os.path.commonprefix(urls) if len(urls) > 1 else ""
        prefijos[cid] = p
    sesiones = []
    for cid, s in sorted(limpias, key=lambda x: (x[1]["fecha"], x[1]["hora"])):
        if s["fecha"] not in idx_fecha:
            continue
        pre = prefijos.get(cid, "")
        url = s["url"][len(pre):] if pre and s["url"].startswith(pre) else s["url"]
        fila = [idx_cine[cid], idx_peli[s["peli"]], idx_fecha[s["fecha"]], s["hora"], s["ver"],
                ",".join(s["fmt"]), url, s.get("nota", "")]
        if s.get("gratis"):
            fila.append(1)
        sesiones.append(fila)

    cines = []
    for c in CINES:
        c = {k: v for k, v in c.items() if k != "src"}
        c["compra"] = prefijos.get(c["id"], "")
        cines.append(c)

    datos = dict(
        generado=dt.datetime.now().isoformat(timespec="minutes"),
        fechas=fechas, festivos=FESTIVOS, cines=cines, peliculas=pelis, sesiones=sesiones,
        sin_horarios=SIN_HORARIOS, cerrados=CERRADOS,
        poster_base="https://es.web.img3.acsta.net/c_310_420",
    )
    if not a.forzar:
        problema = validar(datos)
        if problema:
            log(f"\nERROR: {problema}. No se sobrescribe {SALIDA} (usa --forzar para ignorarlo).")
            sys.exit(1)
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    tmp = SALIDA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("/* Generado por actualizar_cartelera.py — no editar a mano */\n")
        fh.write("window.CARTELERA = ")
        json.dump(datos, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    os.replace(tmp, SALIDA)  # escritura atómica: nunca queda un fichero a medias
    por_cine = {}
    for f in sesiones:
        por_cine[CINES[f[0]]["corto"]] = por_cine.get(CINES[f[0]]["corto"], 0) + 1
    log(f"\nListo: {len(sesiones)} sesiones, {len(pelis)} películas -> {SALIDA}")
    for k, v in sorted(por_cine.items(), key=lambda x: -x[1]):
        log(f"  {v:5d}  {k}")
    sin = [c["corto"] for c in CINES if c["corto"] not in por_cine]
    if sin:
        log("  sin sesiones:", ", ".join(sin))


if __name__ == "__main__":
    main()

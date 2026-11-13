"""Lecture intelligente des PDF : analyse la mise en page pour rendre un texte dans l'ordre de lecture.

- ignore les images, en-têtes, pieds de page, numéros de page, petites légendes ;
- gère les pages en 2 colonnes et les blocs de texte placés n'importe où (Canva, Word…) ;
- recolle les paragraphes coupés par les fins de ligne et les changements de page ;
- repère les titres de chapitres par la taille de police (même sans le mot « Chapitre »).
"""
import re
from collections import Counter

import pymupdf

from extract import is_heading, RE_TOC_HEAD, RE_TOC_LINE, CHAPTER_WORDS, NUM

RE_BARE = re.compile(rf"^\x00H\s*{CHAPTER_WORDS}\s+{NUM}\s*[:.\-–—]?\s*$", re.I)

RE_PAGE_NUM = re.compile(r"^\s*(page\s*)?[-–—]?\s*\d{1,4}\s*[-–—]?\s*(/\s*\d{1,4})?\s*$", re.I)
END_SENT = re.compile(r"[.!?…:»\"')]\s*$")


def _blocks_of_page(page):
    """Blocs de texte d'une page : (x0, y0, x1, y1, texte, taille_police_dominante, gras)."""
    d = page.get_text("dict", flags=pymupdf.TEXT_DEHYPHENATE | pymupdf.TEXT_PRESERVE_WHITESPACE)
    out = []
    for b in d["blocks"]:
        if b.get("type") != 0:  # image
            continue
        lines, sizes, bold_chars, total = [], Counter(), 0, 0
        for l in b["lines"]:
            if abs(l["dir"][1]) > 0.3:  # texte vertical / tourné : décor
                continue
            txt = "".join(s["text"] for s in l["spans"])
            if not txt.strip():
                continue
            lines.append(txt.strip())
            for s in l["spans"]:
                n = len(s["text"].strip())
                sizes[round(s["size"], 1)] += n
                total += n
                if s["flags"] & 16 or "bold" in s["font"].lower() or "black" in s["font"].lower():
                    bold_chars += n
        if not lines:
            continue
        x0, y0, x1, y1 = b["bbox"]
        out.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1, "lines": lines,
            "size": sizes.most_common(1)[0][0] if sizes else 0,
            "bold": total and bold_chars / total > 0.6,
        })
    return out


def _reading_order(blocks, width):
    """Ordonne les blocs d'une page : gère 1 ou 2 colonnes, titres au-dessus, texte pleine largeur en dessous."""
    if not blocks:
        return []
    mid = width / 2
    left = [b for b in blocks if b["x1"] <= mid + 12]
    right = [b for b in blocks if b["x0"] >= mid - 12]
    chars = lambda bs: sum(len(" ".join(b["lines"])) for b in bs)
    if left and right and chars(left) > 250 and chars(right) > 250:
        # zone où les deux colonnes coexistent
        top = max(min(b["y0"] for b in left), min(b["y0"] for b in right)) - 5
        bottom = min(max(b["y1"] for b in left), max(b["y1"] for b in right)) + 5
        colL = [b for b in left if b["y1"] > top and b["y0"] < bottom]
        colR = [b for b in right if b["y1"] > top and b["y0"] < bottom]
        rest = [b for b in blocks if b not in colL and b not in colR]
        above = sorted([b for b in rest if b["y0"] < top], key=lambda b: (round(b["y0"] / 4), b["x0"]))
        below = sorted([b for b in rest if b["y0"] >= top], key=lambda b: (round(b["y0"] / 4), b["x0"]))
        return above + sorted(colL, key=lambda b: b["y0"]) + sorted(colR, key=lambda b: b["y0"]) + below
    return sorted(blocks, key=lambda b: (round(b["y0"] / 4), b["x0"]))


def read_pdf_layout(data: bytes):
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    for page in doc:
        w, h = page.rect.width, page.rect.height
        bl = _blocks_of_page(page)
        for b in bl:
            b["page_h"] = h
        pages.append((bl, w))
    doc.close()
    n = len(pages)
    if not n:
        return []

    # taille du texte courant (pondérée par le nombre de caractères)
    sizes = Counter()
    for bl, _ in pages:
        for b in bl:
            sizes[b["size"]] += len(" ".join(b["lines"]))
    body = sizes.most_common(1)[0][0] if sizes else 11

    # en-têtes / pieds de page répétés
    def edge_key(b):
        t = re.sub(r"\d+", "#", " ".join(b["lines"])).strip().lower()
        return t if len(t) < 90 else None
    rep = Counter()
    for bl, _ in pages:
        seen = set()
        for b in bl:
            if b["y1"] < b["page_h"] * 0.08 or b["y0"] > b["page_h"] * 0.92:
                k = edge_key(b)
                if k and k not in seen:
                    rep[k] += 1
                    seen.add(k)
    repeated = {k for k, c in rep.items() if n >= 4 and c >= max(3, n * 0.3)}

    # tailles des titres de chapitres : la plus grande taille qui revient plusieurs fois
    cand = Counter()
    for bl, _ in pages:
        for b in bl:
            t = " ".join(b["lines"])
            if b["size"] >= body * 1.25 and len(t) <= 110:
                cand[round(b["size"])] += 1
    heading_sizes = set()
    if cand:
        frequent = [s for s, c in cand.items() if c >= 2]
        if frequent:
            top = max(frequent)
            heading_sizes = {s for s in frequent if s >= top * 0.9}

    blocks = []  # paragraphes finaux, titres marqués "\x00H"
    para = ""

    def flush():
        nonlocal para
        if para.strip():
            blocks.append(para.strip())
        para = ""

    ordered = []
    for bl, w in pages:
        keep = []
        for b in bl:
            t = " ".join(b["lines"]).strip()
            k = edge_key(b)
            if RE_PAGE_NUM.match(t) or (k and k in repeated):
                continue
            if b["size"] and b["size"] < body * 0.78 and len(t) < 300:
                continue
            keep.append(b)
        ordered.append(_reading_order(keep, w))

    for bl in ordered:
        for b in bl:
            text = " ".join(b["lines"]).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue
            if RE_PAGE_NUM.match(text):
                continue
            k = edge_key(b)
            if k and k in repeated:
                continue
            # petites légendes / notes très petites
            if b["size"] and b["size"] < body * 0.78 and len(text) < 300:
                continue
            if RE_TOC_LINE.search(text) and len(text) < 120:
                continue
            big = round(b["size"]) in heading_sizes and len(text) <= 110
            if big or is_heading(text) or RE_TOC_HEAD.match(text):
                flush()
                # titre sur deux blocs ("CHAPITRE 1" puis "Le pouvoir caché" de même taille)
                if blocks and blocks[-1].startswith("\x00H") and big and len(blocks[-1]) < 40:
                    blocks[-1] = blocks[-1].rstrip(" :.-–—") + " : " + text
                else:
                    blocks.append("\x00H" + text)
                continue
            # "CHAPITRE 1" suivi de son sous-titre
            if not para and blocks and RE_BARE.match(blocks[-1]) and len(text) <= 90 and not END_SENT.search(text):
                blocks[-1] = blocks[-1].rstrip(" :.-–—") + " : " + text
                continue
            # sous-titre (gras ou un peu plus grand) : paragraphe à part, avec une pause
            if (b["bold"] or b["size"] >= body * 1.12) and len(text) < 120:
                flush()
                blocks.append(text if END_SENT.search(text) else text + ".")
                continue
            # recoller les paragraphes coupés (fin de bloc / de page au milieu d'une phrase)
            if para and not END_SENT.search(para) and (text[:1].islower() or text[:1] in ",;)"):
                para += " " + text
            else:
                flush()
                para = text
        # fin de page : on garde le paragraphe ouvert s'il n'est pas fini
        if para and END_SENT.search(para):
            flush()
    flush()
    return blocks

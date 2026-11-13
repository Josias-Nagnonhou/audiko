"""Extraction du texte d'un ebook (PDF, Word, EPUB, ODT, RTF, TXT, HTML) et découpage en chapitres."""
import io
import re
import zipfile

CHAPTER_WORDS = r"(?:chapitre|chap\.|chapter|partie|part|livre|section|leçon|lecon|étape|etape|jour|semaine|module)"
NUM = (r"(?:\d{1,3}|[ivxlc]{1,7}|premier|première|premiere|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|"
       r"onze|douze|treize|quatorze|quinze|seize|dix-sept|dix-huit|dix-neuf|vingt)")
SPECIAL = (r"(?:introduction|avant-propos|avant propos|préface|preface|prologue|épilogue|epilogue|conclusion|"
           r"conclusion générale|postface|remerciements|annexes?|bonus|mot de la fin|en guise de conclusion|"
           r"note de l'auteur|à propos de l'auteur|a propos de l'auteur|dédicace)")
RE_NUMBERED = re.compile(rf"^\s*{CHAPTER_WORDS}\s+{NUM}\b", re.I)
RE_SPECIAL = re.compile(rf"^\s*{SPECIAL}\s*[:.\-–—]?\s*$", re.I)
RE_SPECIAL_PREFIX = re.compile(rf"^\s*{SPECIAL}\s*[:\-–—]\s*\S", re.I)
RE_TOC_LINE = re.compile(r"(\.{3,}|…{2,}|\s{3,}|\t|_{3,})\s*\d{1,4}\s*$")
RE_TOC_HEAD = re.compile(r"^\s*(sommaire|table des mati[eè]res|contents|table of contents|plan du livre)\s*[:.]?\s*$", re.I)
RE_ENDS_PAGE = re.compile(r"\s\d{1,3}\s*$")
FRONT_WORDS = re.compile(r"(tous droits r[ée]serv|isbn|copyright|©|d[ée]p[oô]t l[ée]gal|code de la propri[ée]t[ée]|reproduction interdite)", re.I)
RE_PAGE_NUM = re.compile(r"^\s*(page\s*)?\d{1,4}\s*(/\s*\d{1,4})?\s*$", re.I)

MIN_BODY = 250  # un "chapitre" plus court est considéré comme une ligne de sommaire


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 110:
        return False
    if RE_TOC_LINE.search(s):
        return False
    if RE_TOC_HEAD.match(s):
        return True
    return bool(RE_NUMBERED.match(s) or RE_SPECIAL.match(s) or RE_SPECIAL_PREFIX.match(s))


def clean_text(t: str) -> str:
    t = t.replace("­", "").replace("​", "").replace("﻿", "")
    t = re.sub(r"https?://\S+|www\.\S+", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)  # émojis
    t = re.sub(r"[•▪●◆■□►▶✓✔☐☑➤➢→]", "", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def reflow(lines):
    """Recolle les lignes coupées des PDF en paragraphes."""
    out, buf = [], ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if buf:
                out.append(buf)
                buf = ""
            continue
        if RE_PAGE_NUM.match(line):
            continue
        if buf.endswith("-") and line[:1].islower():
            buf = buf[:-1] + line
        elif buf:
            buf += " " + line
        else:
            buf = line
        if re.search(r"[.!?»:…]\s*$", line) and len(line) < 60:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


# ---------- lecteurs par format : renvoient une liste de "blocs" (paragraphes)
# ---------- un bloc peut être marqué titre forcé avec le préfixe "\x00H"

def read_pdf(data: bytes):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    lines = []
    # repérer les en-têtes/pieds de page répétés
    pages = []
    for p in reader.pages:
        try:
            pages.append((p.extract_text() or "").splitlines())
        except Exception:
            pages.append([])
    freq = {}
    for pl in pages:
        for l in set(x.strip() for x in pl if x.strip()):
            freq[l] = freq.get(l, 0) + 1
    n = max(len(pages), 1)
    repeated = {l for l, c in freq.items() if n >= 6 and c >= n * 0.4 and len(l) < 80 and not is_heading(l)}
    bare = re.compile(rf"\s*{CHAPTER_WORDS}\s+{NUM}\s*[:.\-–—]?\s*$", re.I)
    for pl in pages:
        pl = [l for l in pl if l.strip() not in repeated]
        j = 0
        while j < len(pl):
            l = pl[j]
            if is_heading(l):
                title = l.strip()
                # "CHAPITRE 1" suivi d'un sous-titre court sur la ligne d'après
                k = j + 1
                while k < len(pl) and not pl[k].strip():
                    k += 1
                if bare.match(title) and k < len(pl):
                    nxt = pl[k].strip()
                    if 0 < len(nxt) <= 90 and not re.search(r"[.!?,;]$", nxt) and not is_heading(nxt):
                        title = f"{title.rstrip(' :.-–—')} : {nxt}"
                        j = k
                lines.append("")
                lines.append("\x00H" + title)
                lines.append("")
            else:
                lines.append(l)
            j += 1
        lines.append("")
    blocks = []
    cur = []
    for l in lines:
        if l.startswith("\x00H"):
            blocks.extend(reflow(cur))
            cur = []
            blocks.append(l)
        else:
            cur.append(l)
    blocks.extend(reflow(cur))
    return blocks


def read_docx(data: bytes):
    """Lit le Word dans l'ordre exact du document (paragraphes, tableaux, zones de texte), sans doublons."""
    import docx
    from docx.oxml.ns import qn
    d = docx.Document(io.BytesIO(data))
    styles = {}
    for st in d.styles:
        try:
            styles[st.style_id] = (st.name or "").lower()
        except Exception:
            pass
    fallback = qn("mc:Fallback") if False else "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    blocks, last = [], None
    for p in d.element.body.iter(qn("w:p")):
        # ignorer la copie "de secours" des zones de texte et les paragraphes imbriqués dans un autre
        anc, skip = p.getparent(), False
        while anc is not None:
            if anc.tag == fallback or anc.tag == qn("w:p"):
                skip = True
                break
            anc = anc.getparent()
        if skip:
            continue
        t = "".join(x.text or "" for x in p.iter() if x.tag in (qn("w:t"), qn("w:tab")) )
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            continue
        last = t
        sid = p.find(qn("w:pPr") + "/" + qn("w:pStyle"))
        style = styles.get(sid.get(qn("w:val")), "") if sid is not None else ""
        if re.match(r"^(heading|titre)\s*1$", style) or style in ("title", "titre") or is_heading(t):
            blocks.append("\x00H" + t)
        else:
            blocks.append(t)
    return blocks


def _html_blocks(html: str, epub_doc=False):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "head"]):
        tag.decompose()
    blocks = []
    first_heading_done = False
    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "div"]):
        if el.name == "div" and el.find(["p", "div", "h1", "h2", "h3", "li"]):
            continue
        t = el.get_text(" ", strip=True)
        if not t:
            continue
        if el.name in ("h1", "h2") or (el.name == "h3" and is_heading(t)):
            if epub_doc and not first_heading_done:
                blocks.append("\x00H" + t)
                first_heading_done = True
            elif is_heading(t) or el.name == "h1" or not epub_doc:
                blocks.append("\x00H" + t)
            else:
                blocks.append(t)
        elif is_heading(t):
            blocks.append("\x00H" + t)
        else:
            blocks.append(t)
    return blocks


def read_epub(data: bytes):
    """Lecture EPUB sans dépendance lourde : on suit l'ordre du 'spine'."""
    from bs4 import BeautifulSoup
    z = zipfile.ZipFile(io.BytesIO(data))
    container = BeautifulSoup(z.read("META-INF/container.xml"), "xml")
    opf_path = container.find("rootfile")["full-path"]
    base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
    opf = BeautifulSoup(z.read(opf_path), "xml")
    manifest = {i["id"]: i["href"] for i in opf.find_all("item")}
    blocks = []
    for ref in opf.find("spine").find_all("itemref"):
        href = manifest.get(ref.get("idref"))
        if not href:
            continue
        path = base + href.split("#")[0]
        try:
            html = z.read(path).decode("utf-8", "ignore")
        except KeyError:
            continue
        b = _html_blocks(html, epub_doc=True)
        if b and not b[0].startswith("\x00H"):
            # fichier sans titre : on le rattache au précédent
            pass
        blocks.extend(b)
    return blocks


def read_odt(data: bytes):
    from odf.opendocument import load
    doc = load(io.BytesIO(data))

    def txt(node):
        out = []
        for c in node.childNodes:
            if c.nodeType == c.TEXT_NODE:
                out.append(c.data)
            elif c.qname[1] == "s":
                out.append(" ")
            elif c.qname[1] in ("tab", "line-break"):
                out.append(" ")
            else:
                out.append(txt(c))
        return "".join(out)

    blocks = []
    _walk_odt(doc.text, blocks, txt)
    return blocks


def _walk_odt(el, blocks, txt):
    name = getattr(el, "qname", (None, None))[1]
    if name == "h":
        t = txt(el).strip()
        if t:
            lvl = el.getAttribute("outlinelevel") if hasattr(el, "getAttribute") else None
            if str(lvl) in ("1", "None") or is_heading(t):
                blocks.append("\x00H" + t)
            else:
                blocks.append(t)
    elif name == "p":
        t = txt(el).strip()
        if t:
            blocks.append("\x00H" + t if is_heading(t) else t)
    else:
        for c in getattr(el, "childNodes", []):
            _walk_odt(c, blocks, txt)


def read_rtf(data: bytes):
    from striprtf.striprtf import rtf_to_text
    return read_txt(rtf_to_text(data.decode("latin-1", "ignore")).encode("utf-8"))


def read_txt(data: bytes):
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            s = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    blocks = []
    for para in re.split(r"\n\s*\n", s):
        lines = [l for l in para.splitlines() if l.strip()]
        if not lines:
            continue
        cur = []
        for l in lines:
            l2 = l.strip().lstrip("#").strip()
            if is_heading(l2) or l.strip().startswith("# "):
                if cur:
                    blocks.append(" ".join(cur))
                    cur = []
                blocks.append("\x00H" + l2)
            else:
                cur.append(l.strip())
        if cur:
            blocks.append(" ".join(cur))
    return blocks


def read_any(filename: str, data: bytes):
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "pdf" or data[:4] == b"%PDF":
        try:
            from pdf_layout import read_pdf_layout
            blocks = read_pdf_layout(data)
            if sum(len(b) for b in blocks) > 200:
                return blocks
        except Exception:
            pass
        return read_pdf(data)
    if ext == "docx":
        return read_docx(data)
    if ext == "epub":
        return read_epub(data)
    if ext == "odt":
        return read_odt(data)
    if ext == "rtf" or data[:5] == b"{\\rtf":
        return read_rtf(data)
    if ext in ("html", "htm", "xhtml"):
        return _html_blocks(data.decode("utf-8", "ignore"))
    if ext == "doc":
        raise ValueError("Les anciens fichiers Word (.doc) ne sont pas lisibles. Ouvre-le dans Word ou Google Docs et "
                         "enregistre-le en .docx (ou en PDF), puis réessaie.")
    if ext in ("txt", "md", "markdown", "text", ""):
        return read_txt(data)
    raise ValueError(f"Format .{ext} non pris en charge. Formats acceptés : PDF, Word (.docx), EPUB, ODT, RTF, TXT, HTML.")


# ---------- découpage en chapitres

def unmark(b):
    return (b[2:] if b.startswith("\x00H") else b).strip()


def split_chapters(blocks):
    sections = []  # [titre, [paragraphes]]
    cur_title, cur = None, []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.startswith("\x00H"):
            title = b[2:].strip()
            # titre sur deux lignes : "CHAPITRE 1" puis "Le pouvoir caché"
            if (re.fullmatch(rf"\s*{CHAPTER_WORDS}\s+{NUM}\s*[:.\-–—]?\s*", title, re.I) and i + 1 < len(blocks)):
                nxt = unmark(blocks[i + 1])
                if 0 < len(nxt) <= 90 and not re.search(r"[.!?]$", nxt) and not is_heading(nxt):
                    title = f"{title.rstrip(' :.-–—')} : {nxt}"
                    i += 1
            sections.append([cur_title, cur])
            cur_title, cur = title, []
        else:
            cur.append(b)
        i += 1
    sections.append([cur_title, cur])

    # enlever le sommaire, les pages de garde et les entrées de sommaire (titres sans vrai contenu)
    chapters = []
    for title, paras in sections:
        if title is not None and RE_TOC_HEAD.match(title):
            continue  # section "Sommaire" / "Table des matières" : jamais lue
        # retirer les lignes de sommaire restantes ("Chapitre 2 : Titre ..... 14" ou "Chapitre 2 Titre 14")
        paras = [p for p in paras if not (RE_TOC_LINE.search(p) or (len(p) < 110 and RE_ENDS_PAGE.search(p)
                 and (RE_NUMBERED.match(p) or RE_SPECIAL_PREFIX.match(p) or re.match(rf"^\s*{SPECIAL}\b", p, re.I))))]
        body = clean_text("\n\n".join(paras))
        if title is None:
            # texte avant le premier titre : page de titre, droits d'auteur… décoché par défaut
            if len(body) >= 400:
                front = bool(FRONT_WORDS.search(body[:3000])) or len(body) < 2500
                chapters.append({"title": "Début du livre (avant le 1er chapitre)", "text": body, "selected": not front})
            continue
        if len(body) < MIN_BODY:
            continue
        chapters.append({"title": clean_title(title), "text": body, "selected": True})

    # même titre deux fois : la première occurrence courte vient du sommaire
    seen = {}
    for c in chapters:
        seen.setdefault(c["title"].lower(), []).append(c)
    for dup in seen.values():
        if len(dup) > 1:
            for c in dup[:-1]:
                if len(c["text"]) < 1500:
                    chapters.remove(c)

    # titres en double (sommaire puis vrai chapitre) : rien à faire, le sommaire a été retiré.
    if len(chapters) == 1 and chapters[0]["title"].startswith("Début du livre"):
        chapters = auto_split(chapters[0]["text"])
    if not chapters:
        full = clean_text("\n\n".join(unmark(b) for b in blocks))
        chapters = auto_split(full)
    # aucun chapitre détecté : découpe en parties régulières
    if len(chapters) == 1 and len(chapters[0]["text"]) > 40000:
        chapters = auto_split(chapters[0]["text"])
    return chapters


def clean_title(t):
    t = re.sub(r"\s+", " ", t).strip(" .:-–—")
    return t[:120]


def auto_split(text, target=25000):
    if not text.strip():
        return []
    paras = text.split("\n\n")
    parts, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > target and cur:
            parts.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + p
    if cur:
        parts.append(cur)
    return [{"title": f"Partie {i + 1}", "text": t, "selected": True} for i, t in enumerate(parts)]


def extract_chapters(filename: str, data: bytes):
    blocks = read_any(filename, data)
    blocks = [b for b in blocks if b and unmark(b)]
    if not blocks:
        raise ValueError("Aucun texte trouvé dans ce fichier. S'il s'agit d'un PDF scanné (images), il faut d'abord le "
                         "convertir en texte.")
    return split_chapters(blocks)

"""Prépare le texte pour une lecture naturelle : prononciation, ponctuation, rythme régulier."""
import re

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s):
    s = s.upper()
    total, prev = 0, 0
    for ch in reversed(s):
        v = ROMAN.get(ch, 0)
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


ABBR = [
    (r"\bM\.\s+(?=[A-ZÀ-Ý])", "Monsieur "),
    (r"\bMM\.\s+", "Messieurs "),
    (r"\bMme\.?\s+", "Madame "),
    (r"\bMmes\.?\s+", "Mesdames "),
    (r"\bMlle\.?\s+", "Mademoiselle "),
    (r"\bDr\.?\s+(?=[A-ZÀ-Ý])", "Docteur "),
    (r"\bPr\.?\s+(?=[A-ZÀ-Ý])", "Professeur "),
    (r"\bSt\.?\s+(?=[A-ZÀ-Ý])", "Saint "),
    (r"\bSte\.?\s+(?=[A-ZÀ-Ý])", "Sainte "),
    (r"\bcf\.\s*", "voir "),
    (r"\bex\.\s*:?\s*", "par exemple, "),
    (r"\bp\.\s*(?=\d)", "page "),
    (r"\bvol\.\s*(?=\d)", "volume "),
    (r"\bn°\s*", "numéro "),
    (r"\bN°\s*", "numéro "),
    (r"\bav\.\s*J\.-?C\.?", "avant Jésus-Christ"),
    (r"\bapr\.\s*J\.-?C\.?", "après Jésus-Christ"),
    (r"\bJ\.-C\.", "Jésus-Christ"),
    (r"\betc\.\s*(?=[a-zà-ÿ])", "et cetera, "),
    (r"\bvs\.?\s+", "contre "),
    (r"\s&\s", " et "),
]

# sigles qu'on laisse en majuscules (lus lettre par lettre ou comme un mot)
KEEP_CAPS = {"FIFA", "UEFA", "CAN", "PSG", "OM", "OL", "ONU", "USA", "UE", "CAF", "VAR", "ADN", "SIDA", "VIH",
             "OTAN", "UNESCO", "UNICEF", "NBA", "NFL", "CEO", "PDG", "ONG", "RDC", "RCA", "TV", "IA", "BCEAO",
             "CFA", "FCFA", "OK", "SMS", "PDF", "MP3", "URSS", "NASA", "FBI", "CIA", "BBC", "CNN", "RFI", "ORTB"}

BIBLE_BOOKS = (r"Gen[eè]se|Exode|L[ée]vitique|Nombres|Deut[ée]ronome|Josu[ée]|Juges|Ruth|Samuel|Rois|Chroniques|"
               r"Esdras|N[ée]h[ée]mie|Esther|Job|Psaumes?|Proverbes|Eccl[ée]siaste|Cantique|[ÉE]sa[iï]e|J[ée]r[ée]mie|"
               r"Lamentations|[ÉE]z[ée]chiel|Daniel|Os[ée]e|Jo[eë]l|Amos|Abdias|Jonas|Mich[ée]e|Nahum|Habacuc|"
               r"Sophonie|Agg[ée]e|Zacharie|Malachie|Matthieu|Marc|Luc|Jean|Actes|Romains|Corinthiens|Galates|"
               r"[ÉE]ph[ée]siens|Philippiens|Colossiens|Thessaloniciens|Timoth[ée]e|Tite|Phil[ée]mon|H[ée]breux|"
               r"Jacques|Pierre|Jude|Apocalypse|Sourate|Mt|Mc|Lc|Jn|Ac|Rm|Ps|Pr|Gn|Ex|Ap")


def fix_caps(m):
    w = m.group(0)
    if w in KEEP_CAPS or len(w) <= 3:
        return w
    return w.capitalize()


def fix_caps_run(m):
    """Suite de mots en majuscules (titres) : on écrit normalement, sauf les sigles connus."""
    run = m.group(0)
    words = re.findall(r"[A-ZÀ-Ý]+", run)
    if len(words) < 2 or sum(len(w) for w in words) < 6:
        return run
    out = re.sub(r"[A-ZÀ-Ý]+", lambda w: w.group(0) if w.group(0) in KEEP_CAPS else w.group(0).lower(), run)
    return out[0].upper() + out[1:]


def normalize(text: str) -> str:
    t = text
    # notes de bas de page et références [1], (1), ¹
    t = re.sub(r"\[\d{1,3}\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", t)
    t = re.sub(r"(?<=[a-zà-ÿ.,;!?»])\d{1,2}(?=\s|$)", "", t)  # appel de note collé à un mot : "pouvoir12 "
    # symboles lus de travers
    t = re.sub(r"[*_#~^|<>{}\[\]]", " ", t)
    t = t.replace("…", "...").replace("’", "'").replace("‘", "'")
    t = re.sub(r"\s*[–—]\s*", ", ", t)          # tirets de dialogue / incises -> petite pause
    t = re.sub(r"(?m)^\s*[-•]\s+", "", t)       # puces
    t = re.sub(r"\s*/\s*", " / ", t)
    # abréviations
    for pat, rep in ABBR:
        t = re.sub(pat, rep, t)
    # chiffres romains : Chapitre III, Partie IV, Louis XIV, XIXe siècle
    t = re.sub(r"\b(chapitre|partie|livre|tome|section|acte|leçon|jour|semaine|module)\s+([IVXLC]{1,6})\b",
               lambda m: f"{m.group(1)} {roman_to_int(m.group(2))}", t, flags=re.I)
    t = re.sub(r"\b([IVXLC]{1,6})(e|ème|eme)\s+(siècle|millénaire)",
               lambda m: f"{roman_to_int(m.group(1))}ème {m.group(3)}", t)
    t = re.sub(r"\b([A-ZÀ-Ý][a-zà-ÿ]+)\s+(I{2,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV|XVI)\b",
               lambda m: f"{m.group(1)} {roman_to_int(m.group(2))}", t)
    # ordinaux
    t = re.sub(r"\b1er\b", "premier", t)
    t = re.sub(r"\b1(re|ère)\b", "première", t)
    # versets : Jean 3:16  /  Psaume 23:1-4
    t = re.sub(rf"\b({BIBLE_BOOKS})\.?\s+(\d{{1,3}})\s*[:.,]\s*(\d{{1,3}})\s*[-–à]\s*(\d{{1,3}})\b",
               r"\1 chapitre \2, versets \3 à \4", t)
    t = re.sub(rf"\b({BIBLE_BOOKS})\.?\s+(\d{{1,3}})\s*:\s*(\d{{1,3}})\b", r"\1 chapitre \2, verset \3", t)
    # heures 10:30 -> 10 h 30
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1 h \2", t)
    # grands nombres "1 000 000" ou "1.000.000" -> 1000000
    t = re.sub(r"(?<=\d)[  .](?=\d{3}\b)", "", t)
    # MOTS EN MAJUSCULES -> Mots (sinon la voix épelle ou crie)
    t = re.sub(r"\b[A-ZÀ-Ý]{1,}(?:'[A-ZÀ-Ý]+)?(?:[ \t,:'-]+[A-ZÀ-Ý]{1,}(?:'[A-ZÀ-Ý]+)?)+\b", fix_caps_run, t)
    t = re.sub(r"\b[A-ZÀ-Ý]{2,}(?:'[A-ZÀ-Ý]+)?\b", fix_caps, t)
    # ponctuation : rythme régulier
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"\?{2,}", "?", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"\s+([,.])", r"\1", t)
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"[ \t]+", " ", t)
    # paragraphe sans ponctuation finale -> on ajoute un point (pause naturelle, pas de phrase interminable)
    paras = []
    for p in t.split("\n\n"):
        p = " ".join(p.split())
        if not p:
            continue
        if not re.search(r"[.!?:;»\"')]$", p):
            p += "."
        paras.append(p)
    return "\n\n".join(paras)

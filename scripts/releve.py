"""Relève les poules FFBB listées dans equipes.json.

Pour chaque équipe :
- docs/data/<slug>.json : données publiques lues par la page (matchs joués,
  classement officiel, prochaine journée, anomalies, observations chiffrées) ;
- travail/<slug>-contexte.json : chiffres préparés pour l'analyse de Claude
  (jamais publié).
"""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DATA = RACINE / "docs" / "data"
TRAVAIL = RACINE / "travail"

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def plat(o):
    """Transforme un objet de la bibliothèque en dictionnaire simple."""
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="python")
    if isinstance(o, dict):
        return o
    if hasattr(o, "__dict__"):
        return {k: v for k, v in vars(o).items() if not k.startswith("_")}
    return o


def nom(x):
    """Nom d'équipe normalisé (espaces multiples réduits, majuscules)."""
    return re.sub(r"\s+", " ", str(x or "")).strip().upper()


def entier(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def arrondi(x):
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def lire_date(s):
    if not s:
        return None
    s = str(s).replace("T", " ")[:19]
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def date_fr(dt, heure=True):
    if not dt:
        return ""
    txt = f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} {dt.year}"
    if heure and (dt.hour or dt.minute):
        txt += f", {dt.hour} h" + (f" {dt.minute:02d}" if dt.minute else "")
    return txt


def nom_engagement(ligne):
    """Nom de l'équipe dans une ligne de classement."""
    eng = ligne.get("id_engagement")
    if eng is not None and not isinstance(eng, (str, dict)):
        eng = plat(eng)
    if isinstance(eng, dict) and eng.get("nom"):
        return nom(eng["nom"])
    if isinstance(eng, str):
        m = re.search(r"nom='([^']*)'", eng)
        if m:
            return nom(m.group(1))
    return nom(ligne.get("organisme_nom"))


def releve(client, cfg, maintenant):
    poule = client.get_poule(cfg["poule"])
    brut = plat(poule)
    rencontres = [plat(r) for r in (brut.get("rencontres") or [])]
    classements = [plat(c) for c in (brut.get("classements") or brut.get("classement") or [])]
    if not rencontres or not classements:
        raise RuntimeError(f"{cfg['slug']} : poule vide (rencontres={len(rencontres)}, classement={len(classements)})")

    matchs, a_venir, anomalies = [], [], []
    for r in rencontres:
        j = entier(r.get("numeroJournee"))
        h, a = nom(r.get("nomEquipe1")), nom(r.get("nomEquipe2"))
        sh, sa = entier(r.get("resultatEquipe1")), entier(r.get("resultatEquipe2"))
        dt = lire_date(r.get("date_rencontre"))
        joue = str(r.get("joue")) in ("1", "True", "true")
        if joue and sh is not None and sa is not None and (sh or sa):
            if {sh, sa} == {20, 0}:
                anomalies.append(f"J{j} : {h} – {a} terminé {sh}-{sa} (forfait possible), compté tel quel")
            matchs.append({"j": j, "d": date_fr(dt, heure=False), "h": h, "a": a, "sh": sh, "sa": sa, "_dt": dt})
        elif joue:
            anomalies.append(f"J{j} : {h} – {a} marqué joué mais sans score, non compté")
        else:
            a_venir.append({"j": j, "dt": dt, "h": h, "a": a})
            if dt and dt < maintenant:
                anomalies.append(f"J{j} : {h} – {a} ({date_fr(dt, heure=False)}) sans résultat (reporté ?), non compté")

    matchs.sort(key=lambda m: (m["j"], m["_dt"] or datetime.min))
    derniere = max((m["j"] for m in matchs), default=0)
    futurs = [m for m in a_venir if not (m["dt"] and m["dt"] < maintenant)]
    prochaine = min((m["j"] for m in futurs), default=None)
    prochains = sorted((m for m in futurs if m["j"] == prochaine), key=lambda m: m["dt"] or datetime.max)

    classement = sorted(classements, key=lambda c: entier(c.get("position")) or 99)
    standings = [{
        "t": nom_engagement(c), "pts": entier(c.get("points")), "j": entier(c.get("match_joues")),
        "g": entier(c.get("gagnes")), "p": entier(c.get("perdus")),
        "m": entier(c.get("paniers_marques")), "e": entier(c.get("paniers_encaisses")),
    } for c in classement]

    # Contrôle : les matchs relevés doivent redonner le classement officiel
    calc = defaultdict(lambda: [0, 0, 0, 0, 0])
    for m in matchs:
        for t, x, y in ((m["h"], m["sh"], m["sa"]), (m["a"], m["sa"], m["sh"])):
            s = calc[t]
            s[0] += 1; s[3] += x; s[4] += y
            s[1 if x > y else 2] += 1
    for r in standings:
        if tuple(calc.get(r["t"], [0] * 5)) != (r["j"], r["g"], r["p"], r["m"], r["e"]):
            anomalies.append(f"Écart avec le classement officiel pour {r['t']}")

    for m in matchs:
        m.pop("_dt", None)
    return {
        "updatedAt": maintenant.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "lastPlayed": derniere,
        "source": cfg["source"],
        "equipe": cfg["equipe"],
        "titre": cfg["titre"],
        "sous_titre": cfg.get("sous_titre", ""),
        "matches": matchs,
        "standings": standings,
        "next": [{"j": m["j"], "date": date_fr(m["dt"]), "h": m["h"], "a": m["a"]} for m in prochains],
        "anomalies": anomalies,
    }


def contexte(d):
    """Chiffres préparés pour Claude (moyennes arrondies à 0,1, arrondi classique)."""
    us = d["equipe"]
    S = defaultdict(lambda: {k: {"n": 0, "v": 0, "d": 0, "pm": 0, "pe": 0} for k in ("tous", "domicile", "exterieur")})
    for m in d["matches"]:
        for t, ctx, x, y in ((m["h"], "domicile", m["sh"], m["sa"]), (m["a"], "exterieur", m["sa"], m["sh"])):
            for k in ("tous", ctx):
                s = S[t][k]
                s["n"] += 1; s["pm"] += x; s["pe"] += y
                s["v" if x > y else "d"] += 1
    stats = {}
    for t, ks in S.items():
        stats[t] = {k: {**s, "marques_par_match": arrondi(s["pm"] / s["n"]) if s["n"] else None,
                        "encaisses_par_match": arrondi(s["pe"] / s["n"]) if s["n"] else None}
                    for k, s in ks.items()}
    rang = {r["t"]: i + 1 for i, r in enumerate(d["standings"])}
    top5 = [r["t"] for r in d["standings"][:5]]
    nos = [m for m in d["matches"] if us in (m["h"], m["a"])]

    def vue(m, t):
        dom = m["h"] == t
        adv = m["a"] if dom else m["h"]
        x, y = (m["sh"], m["sa"]) if dom else (m["sa"], m["sh"])
        return {"j": m["j"], "lieu": "domicile" if dom else "extérieur", "adversaire": adv,
                "rang_adversaire": rang.get(adv), "score": f"{x}-{y}", "resultat": "V" if x > y else "D", "ecart": x - y}

    nos_vues = [vue(m, us) for m in nos]
    prochain = next((m for m in d["next"] if us in (m["h"], m["a"])), None)
    adv = None
    if prochain:
        a = prochain["a"] if prochain["h"] == us else prochain["h"]
        ses = [vue(m, a) for m in d["matches"] if a in (m["h"], m["a"])]
        communs = sorted({v["adversaire"] for v in nos_vues} & {v["adversaire"] for v in ses})
        adv = {"nom": a, "rang": rang.get(a), "lieu_pour_nous": "domicile" if prochain["h"] == us else "extérieur",
               "date": prochain["date"], "journee": prochain["j"], "stats": stats.get(a),
               "ses_matchs": ses,
               "adversaires_communs": [{"adversaire": c,
                                        "nous": [v for v in nos_vues if v["adversaire"] == c],
                                        "eux": [v for v in ses if v["adversaire"] == c]} for c in communs]}
    return {
        "equipe": us, "rang": rang.get(us), "derniere_journee": d["lastPlayed"],
        "classement": d["standings"], "stats_par_equipe": stats, "top5": top5,
        "nos_matchs": nos_vues,
        "nos_matchs_serres": [v for v in nos_vues if abs(v["ecart"]) <= 5],
        "nos_matchs_contre_top5": [v for v in nos_vues if v["adversaire"] in top5],
        "prochain_adversaire": adv, "anomalies": d["anomalies"],
    }


def main():
    from ffbb_data_client import FFBBDataClient
    client = FFBBDataClient.create()
    maintenant = datetime.now(timezone.utc).replace(tzinfo=None)
    DATA.mkdir(parents=True, exist_ok=True)
    TRAVAIL.mkdir(exist_ok=True)
    equipes = json.loads((RACINE / "equipes.json").read_text(encoding="utf-8"))
    erreurs = 0
    for cfg in equipes:
        try:
            d = releve(client, cfg, maintenant)
        except Exception as e:  # une équipe en échec ne bloque pas les autres
            print(f"ÉCHEC {cfg['slug']} : {e}", file=sys.stderr)
            erreurs += 1
            continue
        fichier = DATA / f"{cfg['slug']}.json"
        if fichier.exists():  # garde les observations précédentes tant qu'il n'y en a pas de nouvelles
            ancien = json.loads(fichier.read_text(encoding="utf-8"))
            if ancien.get("coach"):
                d["coach"] = ancien["coach"]
        fichier.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        (TRAVAIL / f"{cfg['slug']}-contexte.json").write_text(
            json.dumps(contexte(d), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{cfg['slug']} : J{d['lastPlayed']}, {len(d['matches'])} matchs, "
              f"{len(d['standings'])} équipes, {len(d['anomalies'])} anomalie(s)")
        for a in d["anomalies"]:
            print("  -", a)
    (DATA / "equipes.json").write_text(json.dumps(
        [{"slug": c["slug"], "titre": c["titre"]} for c in equipes], ensure_ascii=False), encoding="utf-8")
    sys.exit(1 if erreurs == len(equipes) else 0)


if __name__ == "__main__":
    main()

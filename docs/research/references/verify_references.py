"""Verify every reference against Crossref before the manuscript may cite it.

    python docs/research/references/verify_references.py

Each entry in CANDIDATES names a DOI and a few words its title must contain. Crossref's record
for that DOI is fetched; if the DOI does not resolve, or its title lacks the expected words, the
entry is rejected and reported. Only what passes is written to `bibliography.json`, with
Crossref's own metadata - authors, title, venue, volume, issue, pages, year - so no detail of a
citation is typed from memory. Entries without a DOI are not accepted here at all.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "TRIAD-case-study/1.0"   # no contact details are sent to third parties

# key: (doi, words the title must contain, sections that cite it)
CANDIDATES: dict[str, tuple[str, list[str], list[int]]] = {
    # --- agents, LLMs, code generation ---------------------------------------------------
    "wang2024agents": ("10.1007/s11704-024-40231-1", ["autonomous agents"], [1, 3, 6]),
    "xi2025agents": ("10.1007/s11432-024-4222-0", ["agents"], [1, 3, 21]),
    "he2025masse": ("10.1145/3712003", ["multi-agent"], [3, 6]),
    "chen2024mas": ("10.1007/s44336-024-00009-2", ["multi-agent"], [6, 11]),
    "hou2024llm4se": ("10.1145/3695988", ["software engineering"], [3, 8]),
    "wang2024llmtesting": ("10.1109/tse.2024.3368208", ["testing"], [7]),
    "jiang2024selfplanning": ("10.1145/3672456", ["code generation"], [6, 7]),
    "tihanyi2024secure": ("10.1007/s10664-024-10590-1", ["secure"], [12]),
    "das2025llmsecurity": ("10.1145/3712001", ["security and privacy"], [12]),
    "boiko2023chemistry": ("10.1038/s41586-023-06792-0", ["chemical research"], [1, 3]),
    "xue2024dbgpt": ("10.1007/s41019-023-00235-6", ["db-gpt"], [3]),
    "naveed2024llmsurvey": ("10.1007/s10462-024-10888-y", ["large language models"], [3]),
    "pearce2022asleep": ("10.1109/sp46214.2022.9833571", ["asleep at the keyboard"], [12]),
    "greshake2023injection": ("10.1145/3605764.3623985", ["indirect prompt injection"], [12]),
    # --- MLOps, ML engineering ----------------------------------------------------------------
    "kreuzberger2023mlops": ("10.1109/access.2023.3262138", ["mlops"], [1, 3, 14]),
    "paleyes2022deploying": ("10.1145/3533378", ["deploying machine learning"], [2, 3]),
    "ashmore2021assuring": ("10.1145/3453444", ["assuring"], [10, 17]),
    "zhang2022mltesting": ("10.1109/tse.2019.2962027", ["machine learning testing"], [7, 17]),
    "giray2021se4ml": ("10.1016/j.jss.2021.111031", ["software engineering perspective"], [2]),
    "wan2020mlchange": ("10.1109/tse.2019.2937083", ["software development practices"], [2]),
    "amershi2019se4ml": ("10.1109/icse-seip.2019.00042", ["software engineering for machine learning"], [2, 3]),
    "bodor2025mlopsrobust": ("10.1145/3708497", ["mlops"], [17]),
    "lwakatare2020industrial": ("10.1016/j.infsof.2020.106368", ["large-scale machine learning systems"], [2]),
    # --- data quality, cleaning, provenance -----------------------------------------------------
    "priestley2023dataquality": ("10.1145/3592616", ["data quality requirements"], [7]),
    "cote2022cleaning": ("10.1145/3506712", ["data cleaning"], [7]),
    "chu2016cleaning": ("10.1145/2882903.2912574", ["data cleaning"], [7]),
    "abedjan2016errors": ("10.14778/2994509.2994518", ["detecting data errors"], [7]),
    "schelter2018dataquality": ("10.14778/3229863.3229867", ["data quality verification"], [7]),
    "werder2022provenance": ("10.1145/3503488", ["data provenance"], [10, 12]),
    "chapman2020provenance": ("10.14778/3415478.3415556", ["provenance capture"], [10, 17]),
    "herschel2017provenance": ("10.1007/s00778-017-0486-1", ["provenance"], [10]),
    "polyzotis2018lifecycle": ("10.1145/3299887.3299891", ["data lifecycle"], [2, 7]),
    "gong2021dataeval": ("10.1109/tr.2021.3070863", ["data evaluation"], [7]),
    # --- Spark, big data, distributed computation ------------------------------------------------
    "zaharia2016spark": ("10.1145/2934664", ["apache spark"], [3, 5, 9]),
    "armbrust2015sparksql": ("10.1145/2723372.2742797", ["spark sql"], [5, 9]),
    "armbrust2020delta": ("10.14778/3415478.3415560", ["delta lake"], [9]),
    "tang2020sparksurvey": ("10.1109/tkde.2020.2975652", ["spark ecosystem"], [3, 9]),
    "herodotou2020tuning": ("10.1145/3381027", ["parameter tuning"], [9, 11]),
    "ahmed2020hadoopspark": ("10.1186/s40537-020-00388-5", ["hadoop and apache spark"], [3, 9]),
    "lin2023sparktuning": ("10.14778/3611540.3611548", ["online tuning for spark"], [9, 11]),
    "cheng2021sparkcloud": ("10.1016/j.jss.2021.111028", ["apache spark"], [9]),
    "islam2020sparkscheduling": ("10.1016/j.jss.2019.110515", ["apache spark"], [9, 11]),
    "dean2008mapreduce": ("10.1145/1327452.1327492", ["mapreduce"], [3]),
    "harby2025lakehouse": ("10.1016/j.is.2024.102460", ["lakehouse"], [5]),
    "verbraeken2020distml": ("10.1145/3377454", ["distributed machine learning"], [3, 9]),
    "ahmad2019churnbigdata": ("10.1186/s40537-019-0191-6", ["churn"], [3, 18]),
    # --- the Python ecosystem ---------------------------------------------------------------------
    "harris2020numpy": ("10.1038/s41586-020-2649-2", ["numpy"], [3, 5]),
    "virtanen2020scipy": ("10.1038/s41592-019-0686-2", ["scipy"], [3, 5]),
    "castro2023hppython": ("10.1145/3617588", ["python"], [3, 5]),
    "chen2016xgboost": ("10.1145/2939672.2939785", ["xgboost"], [8]),
    "akiba2019optuna": ("10.1145/3292500.3330701", ["optuna"], [8]),
    # --- AutoML, tuning, features ---------------------------------------------------------------
    "he2021automl": ("10.1016/j.knosys.2020.106622", ["automl"], [3, 8]),
    "karmaker2021automl": ("10.1145/3470918", ["automl"], [3, 8]),
    "yang2020hpo": ("10.1016/j.neucom.2020.07.061", ["hyperparameter optimization"], [8]),
    "bischl2023hpo": ("10.1002/widm.1484", ["hyperparameter optimization"], [8]),
    "salazar2021fairfeatures": ("10.14778/3461535.3463474", ["automated feature engineering"], [8, 10]),
    "fan2022autofs": ("10.1109/tkde.2021.3115477", ["automated feature selection"], [8]),
    "hutter2019automl": ("10.1007/978-3-030-05318-5", ["automated machine learning"], [3, 8]),
    # --- tabular learning ------------------------------------------------------------------------
    "shwartz2022tabular": ("10.1016/j.inffus.2021.11.011", ["tabular data"], [8]),
    "borisov2024tabular": ("10.1109/tnnls.2022.3229161", ["tabular data"], [8]),
    "breiman2001rf": ("10.1023/a:1010933404324", ["random forests"], [8]),
    "friedman2001gbm": ("10.1214/aos/1013203451", ["greedy function approximation"], [8]),
    "he2009imbalanced": ("10.1109/tkde.2008.239", ["imbalanced data"], [8]),
    # --- calibration, evaluation, statistics -----------------------------------------------------
    "silvafilho2023calibration": ("10.1007/s10994-023-06336-7", ["classifier calibration"], [8, 10, 14]),
    "bocker2021platt": ("10.1016/j.is.2020.101641", ["platt scaling"], [8]),
    "niculescu2005probabilities": ("10.1145/1102351.1102430", ["good probabilities"], [8, 14]),
    "zadrozny2002calibration": ("10.1145/775047.775151", ["multiclass probability estimates"], [8]),
    "vancalster2019calibration": ("10.1186/s12916-019-1466-7", ["calibration"], [10, 14]),
    "saito2015prplot": ("10.1371/journal.pone.0118432", ["precision-recall"], [14]),
    "brier1950": ("10.1175/1520-0493(1950)078<0001:vofeit>2.0.co;2", ["verification of forecasts"], [14]),
    "efron1979bootstrap": ("10.1214/aos/1176344552", ["bootstrap"], [14]),
    "cerqueira2020tsvalidation": ("10.1007/s10994-020-05910-7", ["performance estimation"], [8, 14]),
    # --- leakage and reproducibility -------------------------------------------------------------
    "kapoor2023leakage": ("10.1016/j.patter.2023.100804", ["leakage"], [2, 8, 17]),
    "kaufman2012leakage": ("10.1145/2382577.2382579", ["leakage in data mining"], [8]),
    "rosenblatt2024leakage": ("10.1038/s41467-024-46150-w", ["data leakage"], [8]),
    # --- drift -------------------------------------------------------------------------------------
    "lu2019drift": ("10.1109/tkde.2018.2876857", ["concept drift"], [10, 11]),
    "gama2014drift": ("10.1145/2523813", ["concept drift adaptation"], [10, 11]),
    "hu2021drift": ("10.1109/tcyb.2020.2983962", ["concept drift detection"], [11]),
    # --- explainability, trust, fairness, governance ------------------------------------------------
    "arrieta2020xai": ("10.1016/j.inffus.2019.12.012", ["explainable artificial intelligence"], [3, 10]),
    "tjoa2021xai": ("10.1109/tnnls.2020.3027314", ["explainable artificial intelligence"], [10]),
    "lundberg2020trees": ("10.1038/s42256-019-0138-9", ["explainable ai for trees"], [10]),
    "ahmed2022xai40": ("10.1109/tii.2022.3146552", ["industry 4.0"], [10, 18]),
    "kaur2022trustworthy": ("10.1145/3491209", ["trustworthy artificial intelligence"], [10, 17]),
    "li2023trustworthy": ("10.1145/3555803", ["trustworthy ai"], [10, 17]),
    "thiebes2021trustworthy": ("10.1007/s12525-020-00441-4", ["trustworthy artificial intelligence"], [10]),
    "mehrabi2021fairness": ("10.1145/3457607", ["bias and fairness"], [10]),
    "pessach2022fairness": ("10.1145/3494672", ["fairness in machine learning"], [10]),
    "caton2024fairness": ("10.1145/3616865", ["fairness in machine learning"], [10]),
    "lu2024rai": ("10.1145/3626234", ["responsible ai pattern"], [12]),
    "shneiderman2020hcai": ("10.1145/3419764", ["ethics and practice"], [10, 12]),
    "mitchell2019modelcards": ("10.1145/3287560.3287596", ["model cards"], [10, 12]),
    "gebru2021datasheets": ("10.1145/3458723", ["datasheets for datasets"], [10]),
    "raji2020accountability": ("10.1145/3351095.3372873", ["accountability gap"], [12, 17]),
    "nist2023airmf": ("10.6028/nist.ai.100-1", ["risk management framework"], [12]),
    "mosqueira2023hitl": ("10.1007/s10462-022-10246-w", ["human-in-the-loop"], [11, 12]),
    "wu2022hitl": ("10.1016/j.future.2022.05.014", ["human-in-the-loop"], [11]),
    # --- security, isolation, audit ---------------------------------------------------------------
    "sultan2019container": ("10.1109/access.2019.2911732", ["container security"], [12]),
    "jarkas2025container": ("10.1145/3715001", ["container security"], [12]),
    "wong2023container": ("10.1016/j.cose.2023.103490", ["container security"], [12]),
    "schneier1999auditlogs": ("10.1145/317087.317089", ["audit logs"], [12]),
    # --- churn ---------------------------------------------------------------------------------------
    "lalwani2022churn": ("10.1007/s00607-021-00908-y", ["churn"], [2, 8, 18]),
    "manzoor2024churnreview": ("10.1109/access.2024.3402092", ["churn"], [3, 18]),
    "wu2021churntelco": ("10.1109/access.2021.3073776", ["churn"], [18]),
    "ecommerce2023churn": ("10.1016/j.measen.2023.100728", ["churn"], [18]),
    "verbeke2012churn": ("10.1016/j.ejor.2011.09.031", ["churn"], [8, 18]),
    "decaigny2018churn": ("10.1016/j.ejor.2018.02.009", ["churn"], [8]),
    "fader2005rfm": ("10.1509/jmkr.2005.42.4.415", ["rfm"], [8]),
    "rental2023churn": ("10.1186/s40537-023-00721-8", ["churn"], [18]),
    # --- domains (Section 18, conceptual transfer) -----------------------------------------------------
    "batko2022healthcare": ("10.1186/s40537-021-00553-4", ["healthcare"], [18]),
    "smartfactory2022": ("10.1016/j.jmsy.2022.03.010", ["smart factories"], [18]),
    "smartcities2020": ("10.1016/j.cosrev.2020.100303", ["smart cities"], [18]),
    "kandt2021cities": ("10.1016/j.cities.2020.102992", ["smart cities"], [18]),
    "fraud2023banking": ("10.1109/access.2022.3232287", ["fraud detection"], [18]),
    "weber2024xaifinance": ("10.1007/s10462-024-10854-8", ["finance"], [18]),
    "chou2021nids": ("10.1145/3472753", ["intrusion detection"], [18]),
    "wang2020xaiids": ("10.1109/access.2020.2988359", ["intrusion detection"], [18]),
    "smartgrid2021": ("10.1109/access.2020.3041178", ["smart grid"], [18]),
    "sarker2021datascience": ("10.1007/s42979-021-00765-8", ["data science"], [1, 3]),
}


def fetch(doi: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["message"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main() -> None:
    cache_path = HERE / "crossref_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    accepted, rejected = {}, {}
    for key, (doi, words, sections) in CANDIDATES.items():
        record = cache.get(doi)
        if record is None:
            record = fetch(doi)
            time.sleep(0.5)
            if record is not None:
                cache[doi] = record
                cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        if record is None:
            rejected[key] = f"{doi} does not resolve in Crossref"
            continue
        title = " ".join(record.get("title") or [""])
        subtitle = " ".join(record.get("subtitle") or [])
        if subtitle and subtitle.lower() not in title.lower():
            title = f"{title}: {subtitle}"
        missing = [w for w in words if w.lower() not in title.lower()]
        if missing:
            rejected[key] = f"{doi} is titled {title!r}; expected {missing}"
            continue
        issued = (record.get("issued") or record.get("published") or {}).get("date-parts", [[None]])
        accepted[key] = {
            "doi": record["DOI"].lower(),
            "type": record.get("type"),
            "title": title,
            "authors": [
                {"given": a.get("given", ""), "family": a.get("family", a.get("name", ""))}
                for a in record.get("author", [])
            ],
            "container": " ".join(record.get("container-title") or []),
            "publisher": record.get("publisher"),
            "volume": record.get("volume"),
            "issue": record.get("issue"),
            "pages": record.get("page") or record.get("article-number"),
            "year": issued[0][0],
            "sections": sections,
        }
    (HERE / "bibliography.json").write_text(json.dumps(accepted, indent=1, ensure_ascii=False),
                                            encoding="utf-8")
    journals = sum(1 for e in accepted.values() if e["type"] == "journal-article")
    recent = sum(1 for e in accepted.values() if e["year"] and 2020 <= e["year"] <= 2025)
    print(f"accepted {len(accepted)}, rejected {len(rejected)}; "
          f"journal articles {journals} ({journals / max(len(accepted), 1):.0%}); "
          f"2020-2025 {recent} ({recent / max(len(accepted), 1):.0%})")
    for key, why in rejected.items():
        print("REJECTED", key, "-", why)


if __name__ == "__main__":
    main()

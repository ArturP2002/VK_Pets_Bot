"""Brand → INN resolver and confident search picking."""
from __future__ import annotations

from services.drug_alias_resolver import resolve_brand
from services.dosage_service import pick_search_hits
from services.formulary_search import DrugHit


def test_dict_resolves_screenshot_brands():
    cases = {
        "Доксифин": "doxycycline",
        "Энроксил": "enrofloxacin",
        "Марфлоксин": "marbofloxacin",
        "Мелоксивет": "meloxicam",
        "Эпокрин": "epoetin",
        "Бисептол": "trimethoprim",
    }
    for query, inn in cases.items():
        found = resolve_brand(query, use_llm=False)
        assert found is not None, query
        blob = " ".join(found.inn_terms).lower()
        assert inn in blob, (query, found.inn_terms)


def test_pick_search_hits_drops_weak_fuzzy():
    hits = [
        DrugHit(
            drug_id=1,
            canonical_name_en="Digoxin",
            canonical_name_ru="Дигоксин",
            matched_alias="Дигоксин",
            score=72.0,
            sources=["carpenter"],
            is_exact=False,
        ),
        DrugHit(
            drug_id=2,
            canonical_name_en="Doxepin",
            canonical_name_ru="Доксепин",
            matched_alias="Доксепин",
            score=74.0,
            sources=["carpenter"],
            is_exact=False,
        ),
    ]
    assert pick_search_hits(hits) == []


def test_pick_search_hits_keeps_exact():
    hits = [
        DrugHit(
            drug_id=3,
            canonical_name_en="Doxycycline",
            canonical_name_ru="Доксициклин",
            matched_alias="Доксициклин",
            score=100.0,
            sources=["bsava"],
            is_exact=True,
        ),
        DrugHit(
            drug_id=2,
            canonical_name_en="Doxepin",
            canonical_name_ru="Доксепин",
            matched_alias="Доксепин",
            score=74.0,
            sources=["carpenter"],
            is_exact=False,
        ),
    ]
    picked = pick_search_hits(hits)
    assert len(picked) == 1
    assert picked[0].drug_id == 3
    assert "доксициклин" in picked[0].display_name.lower()

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from persona_prompts import build_research_partner_prompt


def _research(phase):
    return build_research_partner_prompt(
        passage="Romans 3:25-26", genre="epistle", session_elapsed_seconds=0,
        current_phase=phase, card_work_summary="", coaching_context="")


def test_research_prompt_mentions_list_datasets():
    p = _research("p04_exegesis")
    assert "list_datasets" in p, "research prompt must mention list_datasets() so the agent knows the catalog exists"


def test_p04_exegesis_cues_syntax_graph():
    p = _research("p04_exegesis")
    assert "lookup_syntax_graph" in p


def test_p04_exegesis_uses_real_phrase_concordance_id():
    p = _research("p04_exegesis")
    assert "DB:PHRASE-CONCORDANCE" in p
    assert "query_dataset('PhraseConcordance'" not in p


def test_p10_patristics_cues_church_history_ontology():
    p = _research("p10_patristics")
    # The cue should name ChurchHistory (the Lexham Church History Ontology)
    # as a phase-relevant dataset reachable via query_dataset.
    assert "DB:CHURCH-HISTORY-ONTOLOGY" in p


def test_p13_sermon_work_cues_bullinger():
    p = _research("p13_sermon_work")
    assert "lookup_bullinger_figure" in p or "bullinger" in p.lower()


def test_phase_hints_use_real_dataset_ids_for_known_prompt_cues():
    expected_ids = [
        "DB:BIBLICAL-EVENTS",
        "DB:BIBLICAL-PLACES",
        "DB:PERICOPESETS",
        "DB:SD-JESUS-OT-PROPHECY",
        "DB:LEXHAM-ST-ONTOLOGY",
        "DB:SD-PRAYERS",
    ]
    prompts = "\n".join(_research(phase) for phase in [
        "p06_context", "p07_big_picture", "p08_christ",
        "p09_systematics", "p13_sermon_work",
    ])
    for dataset_id in expected_ids:
        assert dataset_id in prompts

import os, sys, tempfile, json
import importlib.util
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from companion_db import CompanionDB


def _load_sermon_pack():
    """Import tools/sermon_pack.py without requiring sys.path gymnastics."""
    path = Path(__file__).parent.parent.parent / 'sermon_pack.py'
    spec = importlib.util.spec_from_file_location('sermon_pack', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # required so dataclasses can resolve PEP-563 annotations
    spec.loader.exec_module(mod)
    return mod


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    return cdb, tmp.name


def test_sermon_pack_metrics_table_exists_and_save_round_trips():
    cdb, _ = _fresh_db()
    session_id = cdb.create_session('Rom 3:21-26', 45, 3, 21, 26, 'epistle')
    cdb.save_sermon_pack_metric(
        session_id=session_id, artifact='manuscript',
        source_breakdown={'decisions': 3, 'mined': 2, 'missing': 1},
        mining_percentage=0.4,
    )
    rows = cdb.get_latest_sermon_pack_metrics(session_id)
    assert 'manuscript' in rows
    assert rows['manuscript']['mining_percentage'] == 0.4
    assert json.loads(rows['manuscript']['source_breakdown_json']) == {
        'decisions': 3, 'mined': 2, 'missing': 1
    }


def test_field_result_dataclass_and_sources():
    sp = _load_sermon_pack()
    fr = sp.FieldResult(value='X', source='decisions')
    assert fr.value == 'X'
    assert fr.source == 'decisions'
    assert sp.SOURCES == ('decisions', 'outline', 'notepad', 'card', 'mined', 'missing')


def test_missing_field_helper_renders_marker():
    sp = _load_sermon_pack()
    fr = sp.missing_field('subject')
    assert fr.value == '[missing: subject]'
    assert fr.source == 'missing'


def _seed_decisions(db_path, session_id, decisions):
    """Insert session_decisions rows. The table must exist already
    (SessionDecisionStore.init_db ran in the fixture)."""
    from session_decisions import SessionDecisionStore
    store = SessionDecisionStore(db_path)
    store.init_db()
    for k, v in decisions.items():
        store.set_decision(session_id=session_id, key=k, value=v)


def _seed_session(cdb, passage='Romans 3:25-26'):
    return cdb.create_session(
        passage_ref=passage, book=45, chapter=3,
        verse_start=25, verse_end=26, genre='epistle',
    )


def test_load_decisions_returns_keyed_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'Your God has proven His righteousness.',
        'target_length_min': '28',
    })
    sp = _load_sermon_pack()
    d = sp.load_decisions(path, sid)
    assert d['subject'] == 'God is righteous.'
    assert d['hp_tht'] == 'Your God has proven His righteousness.'
    assert d['target_length_min'] == '28'
    assert 'theme' not in d


def test_load_decisions_empty_session_returns_empty_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    assert sp.load_decisions(path, sid) == {}

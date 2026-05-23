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

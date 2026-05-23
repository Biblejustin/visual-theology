import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from companion_db import CompanionDB


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

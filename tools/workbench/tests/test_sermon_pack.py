import os, sys, tempfile, json, sqlite3
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
    """Create a temp DB with all stores the generator may read from.

    Matches conftest.init_all_app_globals so tests exercise the same
    schema set the live app initializes at startup. Add new store inits
    here as the generator's source coverage expands."""
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    cdb = CompanionDB(tmp.name)
    cdb.init_db()
    from session_decisions import SessionDecisionStore
    SessionDecisionStore(tmp.name).init_db()
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


def test_load_outline_tree_groups_main_points_with_subs():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    root = cdb.add_outline_node(sid, 'main_point', 'God displayed Jesus as mercy seat',
                                 verse_ref='Rom 3:25')
    cdb.add_outline_node(sid, 'sub_point', 'Tabernacle was a throne room',
                          parent_id=root)
    cdb.add_outline_node(sid, 'sub_point', 'Propitiation = mercy seat',
                          parent_id=root)
    cdb.add_outline_node(sid, 'cross_ref', 'Lev 16',
                          parent_id=root, verse_ref='Lev 16')
    sp = _load_sermon_pack()
    structured = sp.load_outline_points(path, sid)
    assert len(structured) == 1
    pt = structured[0]
    assert pt['title'] == 'God displayed Jesus as mercy seat'
    assert pt['verse_ref'] == 'Rom 3:25'
    assert [s['content'] for s in pt['subs']] == [
        'Tabernacle was a throne room',
        'Propitiation = mercy seat',
    ]
    assert [c['content'] for c in pt['cross_refs']] == ['Lev 16']


def test_load_outline_tree_ignores_title_only_node():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.add_outline_node(sid, 'title', 'Romans 3:25-26')
    sp = _load_sermon_pack()
    assert sp.load_outline_points(path, sid) == []


def test_load_notepads_returns_phase_keyed_dict():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.save_card_notepad(sid, 'card_digestion', 'The text wrestles with...')
    cdb.save_card_notepad(sid, 'card_read_translate', 'My translation work...')
    sp = _load_sermon_pack()
    notepads = sp.load_notepads(path, sid)
    assert notepads['card_digestion'] == 'The text wrestles with...'
    assert notepads['card_read_translate'] == 'My translation work...'
    assert 'card_prayer' not in notepads


def test_load_card_responses_flattens_to_phase_keyed_lists():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    # Use raw insert because save_response signature varies.
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO card_responses (session_id, phase, question_id, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, 'observation', 1, 'I notice the word propitiation.'),
    )
    conn.execute(
        "INSERT INTO card_responses (session_id, phase, question_id, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, 'observation', 2, 'Verb tenses shift in v.26.'),
    )
    conn.commit(); conn.close()
    sp = _load_sermon_pack()
    by_phase = sp.load_card_responses(path, sid)
    assert by_phase['observation'] == [
        'I notice the word propitiation.',
        'Verb tenses shift in v.26.',
    ]


def _insert_msg(path, sid, role, content, phase='study'):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO conversation_messages "
        "(session_id, phase, role, content, created_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (sid, phase, role, content),
    )
    conn.commit(); conn.close()


def test_mine_largest_outline_message_picks_richest_assistant_turn():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _insert_msg(path, sid, 'user', 'short user note')
    _insert_msg(path, sid, 'assistant',
                'Brief acknowledgement, no headings here.')
    big = ('# Sermon outline\n\n## INTRODUCTION\n\nOpening prose.\n\n'
           '## POINT 1: God displayed Jesus\n\nBody text body text.\n\n'
           '## POINT 2: God proved His righteousness\n\nMore body.\n\n'
           '## CONCLUSION\n\nClosing prose.')
    _insert_msg(path, sid, 'assistant', big)
    _insert_msg(path, sid, 'assistant', 'Quick follow-up, no structure.')
    sp = _load_sermon_pack()
    chosen = sp.mine_outline_message(path, sid)
    assert chosen is not None
    assert chosen.startswith('# Sermon outline')


def test_mine_outline_message_returns_none_when_no_qualifying_message():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _insert_msg(path, sid, 'assistant', 'No markdown headings here at all.')
    _insert_msg(path, sid, 'user', '## a heading from a user does not count')
    sp = _load_sermon_pack()
    assert sp.mine_outline_message(path, sid) is None


def test_mine_is_deterministic_across_calls():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    a = '# A\n\n## Heading 1\n## Heading 2\n## Heading 3\n\nBody.'
    b = '# B\n\n## Heading 1\n## Heading 2\n## Heading 3\n\nBody.'
    _insert_msg(path, sid, 'assistant', a)
    _insert_msg(path, sid, 'assistant', b)
    sp = _load_sermon_pack()
    assert sp.mine_outline_message(path, sid) == sp.mine_outline_message(path, sid)


def test_parse_mined_outline_extracts_sections():
    sp = _load_sermon_pack()
    md = (
        '# Sermon outline\n\n'
        '## INTRODUCTION\n\n'
        'Opening prose paragraph.\n\n'
        'Second intro paragraph.\n\n'
        '## POINT 1: God displayed Jesus as mercy seat\n\n'
        'Body for point 1.\n\n'
        '### Sub-section\n\nMore body.\n\n'
        '## POINT 2: God proved His righteousness\n\n'
        'Body for point 2.\n\n'
        '## CONCLUSION\n\nClosing prose.'
    )
    parsed = sp.parse_mined_outline(md)
    assert parsed['intro'].startswith('Opening prose paragraph.')
    assert 'Second intro paragraph.' in parsed['intro']
    assert len(parsed['points']) == 2
    assert parsed['points'][0]['title'] == 'POINT 1: God displayed Jesus as mercy seat'
    assert 'Body for point 1.' in parsed['points'][0]['body']
    assert '### Sub-section' in parsed['points'][0]['body']
    assert parsed['points'][1]['title'].startswith('POINT 2')
    assert parsed['conclusion'].startswith('Closing prose.')


def test_parse_mined_outline_missing_sections_return_empty_string():
    sp = _load_sermon_pack()
    parsed = sp.parse_mined_outline('# Title\n\nSome prose with no H2 anywhere.')
    assert parsed['intro'] == ''
    assert parsed['points'] == []
    assert parsed['conclusion'] == ''


def test_resolve_artifact_fields_uses_decision_when_present():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'Your God has proven His righteousness.',
        'target_length_min': '28',
    })
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert ctx['subject'].value == 'God is righteous.'
    assert ctx['subject'].source == 'decisions'
    assert ctx['take_home_truth'].value.startswith('Your God')
    assert ctx['target_length_min'].value == '28'


def test_resolve_artifact_falls_back_to_mined_for_intro_when_no_structured():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    big = ('# Sermon\n\n## INTRODUCTION\n\nMined intro paragraph.\n\n'
           '## POINT 1: A\n\nBody.\n\n## POINT 2: B\n\nBody.\n\n'
           '## CONCLUSION\n\nMined conclusion.')
    _insert_msg(path, sid, 'assistant', big)
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert 'Mined intro paragraph.' in ctx['intro'].value
    assert ctx['intro'].source == 'mined'
    assert ctx['points'].source == 'mined'
    assert ctx['conclusion'].source == 'mined'


def test_resolve_artifact_marks_missing_fields_with_marker():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    ctx = sp.resolve_artifact_fields(path, sid, artifact='manuscript')
    assert ctx['subject'].value == '[missing: subject]'
    assert ctx['subject'].source == 'missing'
    assert ctx['intro'].source == 'missing'


def test_compute_source_breakdown_and_mining_pct():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'intro': sp.FieldResult('I', 'mined'),
        'points': sp.FieldResult('P', 'outline'),
        'conclusion': sp.FieldResult('[missing: conclusion]', 'missing'),
    }
    br = sp.compute_source_breakdown(ctx)
    assert br == {'decisions': 2, 'outline': 1, 'mined': 1, 'missing': 1,
                  'notepad': 0, 'card': 0}
    assert sp.compute_mining_percentage(br) == 0.25  # 1 mined / 4 non-missing

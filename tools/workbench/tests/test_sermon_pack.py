import os, sys, tempfile, json, sqlite3
import importlib.util
import re as _re
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


def _assert_no_em_dash_and_no_stray_double_hyphen(out: str) -> None:
    """The no-em-dash rule applies everywhere; the no-double-hyphen rule
    targets the AI-typography tell, not standard markdown horizontal rules.
    A markdown HR is a line of only hyphens and whitespace; anywhere else
    a `--` would be a substitute we want to catch."""
    assert '—' not in out
    for line in out.splitlines():
        if _re.fullmatch(r'-{3,}\s*', line):
            continue
        assert '--' not in line, f'unexpected double-hyphen in: {line!r}'


def test_render_manuscript_substitutes_fields_and_contains_no_em_dash():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('God is righteous.', 'decisions'),
        'take_home_truth': sp.FieldResult('Your God has proven His righteousness.',
                                          'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('Opening prose paragraph.', 'mined'),
        'points': sp.FieldResult('## POINT 1: A\n\nBody one.', 'mined'),
        'conclusion': sp.FieldResult('Closing prose.', 'mined'),
    }
    out = sp.render('manuscript', ctx)
    assert 'Romans 3:25-26' in out
    assert 'Your God has proven His righteousness.' in out
    assert 'Opening prose paragraph.' in out
    assert '## POINT 1: A' in out
    _assert_no_em_dash_and_no_stray_double_hyphen(out)


def test_render_manuscript_with_missing_fields_emits_markers():
    sp = _load_sermon_pack()
    ctx = {k: sp.missing_field(k) for k in
           ('subject', 'take_home_truth', 'target_length_min', 'passage_ref',
            'preach_date', 'intro', 'points', 'conclusion')}
    out = sp.render('manuscript', ctx)
    assert '[missing: subject]' in out
    assert '[missing: take_home_truth]' in out
    assert '[missing: intro]' in out
    _assert_no_em_dash_and_no_stray_double_hyphen(out)


def test_render_manuscript_is_deterministic_byte_identical():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Rom 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('I', 'mined'),
        'points': sp.FieldResult('## POINT 1\n\nbody', 'mined'),
        'conclusion': sp.FieldResult('C', 'mined'),
    }
    a = sp.render('manuscript', ctx)
    b = sp.render('manuscript', ctx)
    assert a == b


def test_render_outline_uses_bullet_style_and_no_em_dash():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('God is righteous.', 'decisions'),
        'take_home_truth': sp.FieldResult('THT here.', 'decisions'),
        'target_length_min': sp.FieldResult('28', 'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'intro': sp.FieldResult('FCF question. Bridge. THT.', 'mined'),
        'points': sp.FieldResult('## POINT 1: A\n* sub one\n* sub two', 'outline'),
        'conclusion': sp.FieldResult('EP recap. THT. Silence.', 'mined'),
    }
    out = sp.render('outline', ctx)
    assert 'Preaching Outline' in out
    assert 'God is righteous.' in out
    assert '## POINT 1: A' in out
    assert '* sub one' in out
    _assert_no_em_dash_and_no_stray_double_hyphen(out)  # reuse the helper from Task 9


def test_render_outline_with_missing_target_length_emits_marker():
    sp = _load_sermon_pack()
    ctx = {
        'subject': sp.FieldResult('S', 'decisions'),
        'take_home_truth': sp.FieldResult('T', 'decisions'),
        'target_length_min': sp.missing_field('target_length_min'),
        'passage_ref': sp.FieldResult('Rom 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('', 'decisions'),
        'intro': sp.missing_field('intro'),
        'points': sp.missing_field('points'),
        'conclusion': sp.missing_field('conclusion'),
    }
    out = sp.render('outline', ctx)
    assert '[missing: target_length_min]' in out
    assert '[missing: intro]' in out
    _assert_no_em_dash_and_no_stray_double_hyphen(out)


def test_render_handout_inserts_fill_in_blanks_and_page_break():
    sp = _load_sermon_pack()
    points_md = ('## Point 1: God ________ Jesus as your ________ ________\n'
                 '* The tabernacle was a royal throne room.\n'
                 '\n'
                 '## Point 2: God ________ His righteousness in dealing with ________ ________\n'
                 '* God dealt with their sins by His forbearance.')
    ctx = {
        'subject': sp.FieldResult('God\'s Righteousness, Proven', 'decisions'),
        'take_home_truth': sp.FieldResult('Your God has proven His righteousness.',
                                          'decisions'),
        'passage_ref': sp.FieldResult('Romans 3:25-26', 'decisions'),
        'preach_date': sp.FieldResult('2026-05-24', 'decisions'),
        'points': sp.FieldResult(points_md, 'outline'),
        'conclusion': sp.FieldResult('God is righteous.', 'mined'),
    }
    out = sp.render('handout', ctx)
    assert 'Romans 3:25-26' in out
    assert 'God ________' in out  # 4+ underscores survives substitution
    assert '<!--PAGEBREAK-->' in out
    # No em-dashes anywhere.
    assert '—' not in out
    # The only legal `--` is in the PAGEBREAK marker; everything else must be `---` HR or no `--`.
    for line in out.splitlines():
        if '<!--PAGEBREAK-->' in line:
            continue
        if _re.fullmatch(r'-{3,}\s*', line):
            continue
        assert '--' not in line, f'unexpected double-hyphen in: {line!r}'


def test_handout_template_filename_triggers_tight_print_profile():
    # Smoke: md_to_html's _pick_css selects the handout profile when the
    # stem contains "handout". This test pins the filename convention.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
    from md_to_html import _pick_css, _HANDOUT_CSS
    assert _pick_css('romans-3-25-26-congregation-handout') == _HANDOUT_CSS


def test_generate_writes_three_files_and_logs_metrics():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {
        'subject': 'God is righteous.',
        'hp_tht': 'THT here.',
        'target_length_min': '28',
    })
    _insert_msg(path, sid, 'assistant',
                '# X\n\n## INTRODUCTION\n\nIntro.\n\n'
                '## POINT 1: A\n\nBody.\n\n## POINT 2: B\n\nBody.\n\n'
                '## CONCLUSION\n\nEnd.')
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = sp.generate(sid, db_path=path, out_dir=td)
        assert set(result) == {'manuscript', 'outline', 'handout'}
        for artifact in result:
            p = Path(result[artifact]['path'])
            assert p.exists()
            assert p.read_text(encoding='utf-8') == result[artifact]['content']
            # no em-dashes
            assert '—' not in result[artifact]['content']
            assert isinstance(result[artifact]['mining_percentage'], float)
            assert isinstance(result[artifact]['source_breakdown'], dict)
    # metrics row was logged
    metrics = cdb.get_latest_sermon_pack_metrics(sid)
    assert set(metrics) == {'manuscript', 'outline', 'handout'}


def test_generate_is_byte_identical_on_consecutive_runs():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {'subject': 'X', 'hp_tht': 'Y',
                                'target_length_min': '28'})
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        a = sp.generate(sid, db_path=path, out_dir=td1)
        b = sp.generate(sid, db_path=path, out_dir=td2)
        for art in ('manuscript', 'outline', 'handout'):
            assert a[art]['content'] == b[art]['content']


def test_generate_subset_of_artifacts():
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        result = sp.generate(sid, db_path=path, out_dir=td, artifacts=('handout',))
        assert set(result) == {'handout'}


def test_generate_raises_for_missing_session():
    cdb, path = _fresh_db()
    sp = _load_sermon_pack()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        try:
            sp.generate(99999, db_path=path, out_dir=td)
        except sp.SermonPackError as e:
            assert '99999' in str(e)
        else:
            assert False, 'expected SermonPackError'


def test_cli_generates_artifacts_to_out_dir(tmp_path):
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    _seed_decisions(path, sid, {'subject': 'S', 'hp_tht': 'T',
                                'target_length_min': '28'})
    sp = _load_sermon_pack()
    rc = sp.main([str(sid), '--db', path, '--out', str(tmp_path)])
    assert rc == 0
    md_files = sorted(p.name for p in tmp_path.glob('*.md'))
    assert any('manuscript' in n for n in md_files)
    assert any('preaching-outline' in n for n in md_files)
    assert any('congregation-handout' in n for n in md_files)


def test_cli_report_prints_recent_mining_percentages(capsys):
    cdb, path = _fresh_db()
    sid = _seed_session(cdb)
    cdb.save_sermon_pack_metric(
        session_id=sid, artifact='manuscript',
        source_breakdown={'decisions': 2, 'mined': 1},
        mining_percentage=0.333,
    )
    sp = _load_sermon_pack()
    rc = sp.main(['--report', '--db', path])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(sid) in out
    assert 'manuscript' in out
    assert '0.333' in out or '33.3' in out

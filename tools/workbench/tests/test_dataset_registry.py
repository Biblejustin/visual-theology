import os, sys, tempfile, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import dataset_registry as dr


def _make_fake_catalog(tmpdir):
    """Build a tiny catalog.db + ResourceManager.db with a handful of resource
    rows, mirroring the live join (rm.Resources.Location ~ Records.ResourceId)."""
    cat = os.path.join(tmpdir, 'catalog.db')
    rm = os.path.join(tmpdir, 'rm.db')
    c = sqlite3.connect(cat)
    c.executescript("""
        CREATE TABLE Records (ResourceId TEXT PRIMARY KEY, Title TEXT, Type TEXT);
        INSERT INTO Records VALUES
          ('LLS:BULLINGER', "Bullinger's Figures of Speech", 'lbx.supplementaldata'),
          ('LLS:THXREF',    'Systematic Theology Cross References Dataset', 'lbx.crossreferences'),
          ('LLS:CASCADIA',  'Cascadia Syntax Graphs of the NT', 'lbx.syntaxdatabase'),
          ('LLS:ESVNT',     'ESV NT reverse interlinear', 'lbx.reverse-interlinear'),
          ('LLS:INTERACT',  'Some Interactive', 'lbx.interactive'),
          ('LLS:LSTO',      'Lexham Systematic Theology Ontology', 'lbx.lexhamsystematictheologyontology'),
          ('LLS:UNKNOWN',   'Something Weird', 'lbx.somethingelse');
    """)
    c.commit(); c.close()
    r = sqlite3.connect(rm)
    r.executescript("""
        CREATE TABLE Resources (ResourceId TEXT PRIMARY KEY, Location TEXT);
        INSERT INTO Resources VALUES
          ('LLS:BULLINGER', '/x/Resources/BULLINGER-FIGURES.lbssd'),
          ('LLS:THXREF',    '/x/Resources/THEOLOGY-XREFS.lbsxrf'),
          ('LLS:CASCADIA',  '/x/Resources/CSGNT.lbssyn'),
          ('LLS:ESVNT',     '/x/Resources/ESVNT.lbsrvi'),
          ('LLS:INTERACT',  '/x/Resources/INTERACTIVE.lbshtm'),
          ('LLS:LSTO',      '/x/Resources/LSTO.lbslsto'),
          ('LLS:UNKNOWN',   '/x/Resources/WEIRD.lbszzz');
    """)
    r.commit(); r.close()
    return cat, rm


def test_registry_loads_and_excludes_denylisted_types(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm)
    reg.load()
    ids = {d.dataset_id for d in reg.list_all()}
    assert 'LLS:BULLINGER' in ids
    assert 'LLS:THXREF' in ids
    assert 'LLS:CASCADIA' in ids
    assert 'LLS:LSTO' in ids
    assert 'LLS:UNKNOWN' in ids  # 'misc' family, still listed
    # Denylisted types must NOT appear
    assert 'LLS:ESVNT' not in ids
    assert 'LLS:INTERACT' not in ids


def test_registry_classifies_schema_family(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm); reg.load()
    by_id = {d.dataset_id: d for d in reg.list_all()}
    assert by_id['LLS:BULLINGER'].schema_family == 'supplemental'  # .lbssd
    assert by_id['LLS:THXREF'].schema_family    == 'xref'          # .lbsxrf
    assert by_id['LLS:CASCADIA'].schema_family  == 'syntax'        # .lbssyn
    assert by_id['LLS:LSTO'].schema_family      == 'ontology'      # .lbslsto
    assert by_id['LLS:UNKNOWN'].schema_family   == 'misc'          # unknown ext


def test_registry_find_and_by_family(tmp_path):
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm); reg.load()
    assert reg.find('LLS:BULLINGER').title.startswith("Bullinger")
    assert reg.find('NOPE') is None
    fam = reg.by_family('xref')
    assert len(fam) == 1 and fam[0].dataset_id == 'LLS:THXREF'


def test_registry_survives_missing_dbs(tmp_path):
    # If catalog or RM file is missing, registry boots empty rather than crashes.
    reg = dr.DatasetRegistry(catalog_path=str(tmp_path / 'nope1.db'),
                              resource_manager_path=str(tmp_path / 'nope2.db'))
    reg.load()  # must not raise
    assert reg.list_all() == []


def test_get_registry_is_thread_safe(monkeypatch):
    """Two concurrent first-calls must produce ONE registry instance."""
    import threading
    import dataset_registry as dr
    # Reset the singleton for this test.
    monkeypatch.setattr(dr, '_singleton', None, raising=False)
    instances = []
    barrier = threading.Barrier(8)
    def grab():
        barrier.wait()
        instances.append(dr.get_registry())
    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    # All 8 threads must see the same singleton (same id).
    assert len({id(x) for x in instances}) == 1


def test_registry_tolerates_oserror_during_load(monkeypatch, tmp_path):
    """A vanished mount / disk error must NOT crash the registry — it boots empty."""
    import dataset_registry as dr
    cat, rm = _make_fake_catalog(str(tmp_path))
    reg = dr.DatasetRegistry(catalog_path=cat, resource_manager_path=rm)
    import sqlite3
    def boom(*a, **k):
        raise OSError("simulated vanished mount")
    monkeypatch.setattr(sqlite3, 'connect', boom)
    reg.load()  # must not raise
    assert reg.list_all() == []

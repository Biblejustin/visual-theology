"""Companion tools — focused implementations for the sermon study companion.

Each tool wraps study.py functions and returns structured data.
The companion agent formats results for display.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Add parent tools dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from resource_index import ResourceIndex
from study import (
    parse_reference, find_bibles, find_commentaries_for_ref,
    read_bible_chapter, extract_verses, clean_bible_text,
    read_annotations, get_interlinear_for_chapter, find_word_in_interlinear,
    find_commentary_section, resolve_bible_files,
    get_resource_articles, read_article_text,
)
from logos_search import get_searcher
from dataset_registry import get_registry
from dataset_adapters import adapter_for

# ── Lexicon/Grammar resource registry ──────────────────────────────────────
# Resources confirmed readable, organized by use case.

NT_LEXICONS = [
    {"abbrev": "BDAG", "file": "BDAG.logos4", "desc": "Greek-English Lexicon of the NT (Bauer-Danker)"},
    {"abbrev": "EDNT", "file": "EXGDCTNT.logos4", "desc": "Exegetical Dictionary of the NT"},
    {"abbrev": "TDNTA", "file": "TDNTA.logos4", "desc": "Theological Dictionary of the NT (Abridged)"},
    {"abbrev": "Louw-Nida", "file": "LOUWNIDA.logos4", "desc": "Greek-English Lexicon by Semantic Domain"},
    {"abbrev": "TLNT", "file": "TLNT.logos4", "desc": "Theological Lexicon of the NT"},
    {"abbrev": "LSJ", "file": "LSJ.logos4", "desc": "Liddell-Scott-Jones Greek-English Lexicon"},
    {"abbrev": "ANLEX", "file": "ANLEX.logos4", "desc": "Analytical Lexicon of the Greek NT"},
    {"abbrev": "M-M", "file": "MMVGT.logos4", "desc": "Moulton & Milligan Vocabulary of Greek Testament"},
]

OT_LEXICONS = [
    {"abbrev": "BDB", "file": "BDB.logos4", "desc": "Brown-Driver-Briggs Hebrew Lexicon"},
    {"abbrev": "HALOT", "file": "HAL.logos4", "desc": "Hebrew and Aramaic Lexicon of the OT"},
    {"abbrev": "TDOT", "file": "TDOT.logos4", "desc": "Theological Dictionary of the OT"},
    {"abbrev": "TLOT", "file": "TLOT.logos4", "desc": "Theological Lexicon of the OT"},
    {"abbrev": "DCH", "file": "DICHEBREW.logos4", "desc": "Dictionary of Classical Hebrew"},
    {"abbrev": "AnLexHeb", "file": "LXHEBANLEX.logos4", "desc": "Analytical Lexicon of Hebrew"},
]

ALL_LEXICONS = NT_LEXICONS + OT_LEXICONS

NT_GRAMMARS = [
    {"abbrev": "Wallace", "file": "GRKGRAMBBWALLACE.logos4", "desc": "Greek Grammar beyond the Basics (Exegetical Syntax)"},
    {"abbrev": "Robertson", "file": "GGNTLHR.logos4", "desc": "Grammar of the Greek NT in Light of Historical Research"},
    {"abbrev": "Blass-Debrunner", "file": "GRNTGRKBLASS.logos4", "desc": "Grammar of NT Greek (Blass)"},
    {"abbrev": "Discourse", "file": "DISCGRMRGRKNT.logos4", "desc": "Discourse Grammar of the Greek NT"},
    {"abbrev": "Morphology", "file": "MORPHBBCLGRK.logos4", "desc": "Morphology of Biblical Greek"},
    {"abbrev": "Verbal Aspect", "file": "BASVERBALGRK.logos4", "desc": "Basics of Verbal Aspect in Biblical Greek"},
    {"abbrev": "Idioms", "file": "IDIOMGREEKNT.logos4", "desc": "Idioms of the Greek New Testament"},
    {"abbrev": "Intermediate", "file": "NTRMDTGRKGRMMR.logos4", "desc": "Intermediate Greek Grammar: Syntax for NT Students"},
]

OT_GRAMMARS = [
    {"abbrev": "GKC", "file": "GESENGRAM.logos4", "desc": "Gesenius' Hebrew Grammar"},
    {"abbrev": "Waltke-OConnor", "file": "WLTKHEBSYN.logos4", "desc": "Introduction to Biblical Hebrew Syntax"},
]

ALL_GRAMMARS = NT_GRAMMARS + OT_GRAMMARS

# Module-level resource index singleton
_resource_index = None
_INDEX_DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'resource_index.db')


def _get_index():
    global _resource_index
    if _resource_index is None:
        _resource_index = ResourceIndex(_INDEX_DB_PATH)
    return _resource_index


def _ensure_indexed(resource_file, resource_type="lexicon"):
    """Ensure a resource is indexed. Build index on first use."""
    idx = _get_index()
    if not idx.is_indexed(resource_file):
        idx.build_index_for_resource(resource_file, resource_type=resource_type)
    return idx


TOOL_DEFINITIONS = [
    {
        "name": "read_bible_passage",
        "description": "Read a Bible passage in one or more translations. Returns clean text for each version.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference, e.g. 'Romans 1:18-23' or 'John 3:16'"
                },
                "versions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bible version names. For original language: 'THGNT' (Greek NT), 'BHS' (Hebrew OT). For English: 'NKJV', 'NET', 'ESV', 'NASB95', 'KJV'. Defaults to ['THGNT'] for NT, ['BHS'] for OT. Always prefer original language unless English is specifically requested."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "find_commentary_paragraph",
        "description": "Find the relevant commentary section for a passage from a specific commentary. Returns the commentary text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference, e.g. 'Romans 1:18-23'"
                },
                "commentary_title": {
                    "type": "string",
                    "description": "Optional: specific commentary to search. If omitted, searches available commentaries."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "lookup_lexicon",
        "description": "Look up a Greek or Hebrew word in lexical resources from Bryan's library. Searches BDAG (gold standard), EDNT, Louw-Nida, TDNTA, TLNT, LSJ, BDB, HALOT, TDOT, and more. Use this for word studies, semantic range, morphological analysis, and theological significance of terms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {
                    "type": "string",
                    "description": "Greek or Hebrew word to look up (in original script or transliterated). Can also be an English gloss to search for."
                },
                "lexicons": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: specific lexicons to search by abbreviation (e.g. ['EDNT', 'Louw-Nida']). If omitted, searches the most relevant ones."
                },
                "testament": {
                    "type": "string",
                    "enum": ["nt", "ot"],
                    "description": "Whether to search NT lexicons (Greek) or OT lexicons (Hebrew). Defaults based on the word script."
                }
            },
            "required": ["word"]
        }
    },
    {
        "name": "lookup_grammar",
        "description": "Search Greek and Hebrew grammars in Bryan's library by FULL TEXT (not headword). Available: Wallace (Exegetical Syntax), Robertson, Blass-Debrunner, Discourse Grammar, Morphology of Biblical Greek, Verbal Aspect, Idioms of the GNT, GKC (Gesenius Hebrew), Waltke-O'Connor. Use for questions about syntax, morphology, verbal aspect, clause structure, and grammatical constructions — including discursive topics like 'subjective vs. objective genitive' that headword lookups would miss. Returns up to 2 matching sections with full text suitable for direct quotation. If `grammars` omitted, searches all NT grammars (Greek) when testament='nt', or all OT (Hebrew) when testament='ot'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — a grammatical concept, Greek/Hebrew form, or construction (e.g. 'middle passive', 'genitive absolute', 'aorist aspect', 'conditional clause')"
                },
                "grammars": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: specific grammars by abbreviation (e.g. ['Wallace', 'Robertson']). If omitted, searches Wallace first for NT, GKC for OT."
                },
                "testament": {
                    "type": "string",
                    "enum": ["nt", "ot"],
                    "description": "Whether to search NT grammars (Greek) or OT grammars (Hebrew). Defaults to NT."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "word_study_lookup",
        "description": "Look up interlinear data for a word in the current passage. Returns lemma, morphology, Strong's number. Note: interlinear data may not be available for all passages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {
                    "type": "string",
                    "description": "Word to look up (English, Greek, or Hebrew)"
                },
                "reference": {
                    "type": "string",
                    "description": "Bible reference for context, e.g. 'Romans 1:18'"
                }
            },
            "required": ["word", "reference"]
        }
    },
    {
        "name": "expand_cross_references",
        "description": "Get cross-reference annotations for a passage and fetch the actual text of referenced verses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference to get cross-references for"
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "save_to_outline",
        "description": "Save a node to the sermon outline. Use for main points, sub-points, cross-references, notes, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "enum": ["title", "theme", "main_point", "sub_point", "bullet", "cross_ref", "note"],
                    "description": "Type of outline node"
                },
                "content": {
                    "type": "string",
                    "description": "The text content of the node"
                },
                "parent_id": {
                    "type": "integer",
                    "description": "Optional parent node ID for nesting"
                },
                "verse_ref": {
                    "type": "string",
                    "description": "Optional verse reference for this point"
                }
            },
            "required": ["node_type", "content"]
        }
    },
    # ── Encrypted Volume Dataset Tools ──────────────────────────────────
    {
        "name": "get_passage_data",
        "description": "Get structured study data for a passage from Logos's pre-indexed datasets. Returns figurative language, grammatical constructions, literary typing, wordplay, propositional outlines, important words, preaching themes, and more. Use this proactively when Bryan enters a new passage or phase.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference, e.g. 'Romans 1:18-23'"
                },
                "datasets": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["figurative_language", "greek_constructions", "hebrew_constructions",
                                 "literary_typing", "wordplay", "propositional_outline",
                                 "important_words", "preaching_themes", "nt_use_of_ot",
                                 "cultural_concepts", "thematic_outlines"]
                    },
                    "description": "Which datasets to query. If omitted, queries the most relevant ones for the current phase."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "get_cross_reference_network",
        "description": "Get curated cross-references from Logos's cross-reference databases. Much richer than inline Bible annotations. Can also query theology, biblical theology, confessional, and grammar cross-references.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference"
                },
                "xref_type": {
                    "type": "string",
                    "enum": ["curated", "systematic", "biblical", "confessional", "grammar"],
                    "description": "Type of cross-references. 'curated' = Bible cross-refs, 'systematic' = systematic theology sections, etc."
                }
            },
            "required": ["reference"]
        }
    },
    {
        "name": "get_passage_context",
        "description": "Get contextual data for a passage: biblical places, people, things, events, ancient literature references, and timeline data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Bible reference"
                },
                "context_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["places", "people", "things", "ancient_literature"]
                    },
                    "description": "Which context data to retrieve. If omitted, returns all available."
                }
            },
            "required": ["reference"]
        }
    },
    # ── Coaching Bridge Tools ──────────────────────────────────────────
    {
        "name": "get_active_commitment",
        "description": "Get Bryan's current coaching commitment — the specific, measurable change he agreed to work on. Returns the commitment text, target date, and progress status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_sermon_patterns",
        "description": "Get aggregate sermon analysis patterns over a time window. Returns trends in timing, structure, delivery, and recurring strengths/weaknesses across multiple sermons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "window_days": {
                    "type": "integer",
                    "description": "Number of days to look back for pattern analysis. Defaults to 90."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_representative_moments",
        "description": "Get specific timestamped moments from past sermons that illustrate a pattern. Use to ground coaching feedback in concrete evidence rather than abstract claims.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "The sermon dimension to find moments for (e.g. 'pacing', 'illustration', 'application', 'transitions', 'exegetical_density')."
                },
                "valence": {
                    "type": "string",
                    "enum": ["positive", "negative"],
                    "description": "Whether to find positive examples (strengths) or negative examples (areas for growth)."
                }
            },
            "required": ["dimension", "valence"]
        }
    },
    {
        "name": "get_counterexamples",
        "description": "Get sermons where a typically weak area was actually strong. Use to show Bryan he CAN do something well — it's not a fixed limitation, just inconsistent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "The sermon dimension to find counterexamples for (e.g. 'pacing', 'illustration', 'application')."
                }
            },
            "required": ["dimension"]
        }
    },
    {
        "name": "get_coaching_insights",
        "description": "Get structured insights from past coaching conversations. Returns patterns the coaching agent has observed, what's been tried, and what's working.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_library",
        "description": (
            "Full-text search across the ENTIRE Logos library (all 4,652 books) using "
            "Logos' own search engine and 6.6 GB inverted index. Returns one result per "
            "resource (deduplicated) with title, author, type, score, total hit count, and "
            "a list of relevant sections — each section includes a ~500-char snippet with "
            "the query terms marked in «guillemets». "
            "Unlike find_commentary_paragraph, this reaches background monographs, "
            "dictionaries, encyclopedias, study bibles, etc. — anything indexed by Logos. "
            "Use this for topical/background research (e.g. 'patronage in the Greco-Roman "
            "world', 'imputation in Reformed theology', 'Adam-Christ typology'). For "
            "verse-keyed commentary on a specific passage, find_commentary_paragraph is "
            "still the right tool. "
            "Query syntax supports Logos operators: quoted phrases (\"\"propitiation\"\"), "
            "AND, OR, NOT, NEAR/N, WITHIN, BEFORE. Plain words are AND'd by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Plain words or Logos operators."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max resources to return (1-25). Default 10."
                },
                "resource_type": {
                    "type": "string",
                    "description": (
                        "Optional Logos Type prefix filter. Common values: "
                        "'text.monograph' (any monograph), "
                        "'text.monograph.commentary.bible' (verse commentaries), "
                        "'text.monograph.encyclopedia' (dictionaries), "
                        "'text.monograph.study.bible' (study Bibles)."
                    )
                },
                "author_contains": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive substring on the Authors field. "
                        "Useful to narrow to a specific author or editor."
                    )
                },
                "include_workspaces": {
                    "type": "boolean",
                    "description": (
                        "If true, include Logos user-workspace docs (LLS:WS_* IDs — "
                        "usually personal notes). Default false."
                    )
                },
                "verse_filter": {
                    "type": "string",
                    "description": (
                        "Optional Bible reference to scope the search to sections "
                        "that intersect this passage. Format: 'Romans 3:21-26' or "
                        "'John 1:1'. Composes with the text query as "
                        "'(<query>) INTERSECTS <verse_filter>' in Logos syntax."
                    )
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_library_section",
        "description": (
            "Fetch the FULL, exact text of a library section found via search_library. "
            "search_library returns only a ~500-char snippet; to quote a source verbatim "
            "you MUST call this with the result's `filename` and the section's `article_num`. "
            "Never quote a source from the snippet alone, and never quote anything this tool "
            "(or another retrieval tool) did not return."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "The `filename` from a search_library result, e.g. 'WALLACEGGBB.logos4'."},
                "article_num": {"type": "integer", "description": "The section's `article_num` from a search_library result."},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)."},
            },
            "required": ["filename", "article_num"],
        },
    },
    {
        "name": "suggest_search_terms",
        "description": (
            "Ask Logos' query parser for completions/refinements for a partial query. "
            "Useful when search_library returns few results — the suggestions may "
            "reveal alternative spellings, Greek/Hebrew lemmas, or related operators."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Partial query text to expand."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "find_illustration",
        "description": (
            "Find sermon illustrations for a theme. Searches the library for "
            "monographs and biographies, then ranks candidates by narrative "
            "concreteness using Opus. Returns 1-3 insights each with a snippet, "
            "source resource, and one-line rationale. Use when an outline point "
            "lacks a story or vivid image."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "Theme or topic to illustrate."},
                "count": {"type": "integer", "description": "How many illustrations to return (1-5). Default 3."},
            },
            "required": ["theme"],
        },
    },
    {
        "name": "find_objections",
        "description": (
            "Find likely objections to a claim the sermon is making. Combines a "
            "library search with an Opus pass over four categories: theological, "
            "pastoral, cultural, honest_doubt. Returns insights each with the "
            "objection in the objector's voice and a one-line rebuttal pointer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The sermon claim under examination."},
                "count": {"type": "integer", "description": "Number of objections (1-6). Default 3."},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "find_counseling_applications",
        "description": (
            "Map the session's passage to common pastoral-counseling situations "
            "(guilt, anxiety, marriage_conflict, etc.). Each insight names the "
            "situation, gives a one-paragraph application, and tags a tier "
            "(preventative / remedial / diagnostic). Uses the session's passage; "
            "no extra input required."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of applications (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "find_wisdom_principles",
        "description": (
            "Distill 2-3 transferable wisdom principles from the session's passage. "
            "Each principle is a single proverbial sentence with rationale, "
            "topical tags, and 1-3 cross-references. Use when the outline needs "
            "preachable take-home sentences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of principles (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "suggest_christ_thread",
        "description": (
            "Propose 1-3 Christ-thread angles for the session's passage from a "
            "Reformed homiletical perspective. Each angle is tagged by category "
            "(typology / promise_fulfillment / contrast / redemptive_historical / "
            "direct_reference) with supporting refs. Optionally focus on a specific "
            "point already in the outline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "point": {"type": "string", "description": "Optional outline point to focus the Christ-thread."},
                "count": {"type": "integer", "description": "Number of angles (1-5). Default 3."},
            },
            "required": [],
        },
    },
    {
        "name": "gauge_outline_balance",
        "description": (
            "Tag every node in the session's outline by homiletical type "
            "(exegetical_claim / application / illustration / christ_thread / etc.) "
            "and return the exegesis-vs-rest ratio plus flags for sections that "
            "are nearly pure exegesis or nearly pure application. Tag results are "
            "cached on outline_nodes; subsequent calls only re-tag changed nodes."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_sermon_coherence",
        "description": (
            "Find prior sermon outlines that made a similar claim to this one. "
            "Uses local embedding similarity. Useful for catching repetition "
            "('you preached this same Christ-thread 3 weeks ago') or unintentional "
            "drift from prior positions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The claim or proposition to check against prior sermons."},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "lookup_mentor_position",
        "description": (
            "Look up what a named homiletical authority actually says on a topic, "
            "to test Bryan's invoked-version against the actual source. Use when "
            "Bryan attributes a standard or requirement to Prutow, Chapell, Robinson, "
            "Beeke, Perkins, York, Macartney, Carrick, Micah Ramsey, or another "
            "named pastor — especially when the standard sounds harsh, absolute, or "
            "memory-shaped (the accuser-voice pattern often distorts authorities into "
            "more-punitive-than-actual standards). Returns matching reference rows "
            "with the cited position and source citation; empty result means we don't "
            "have a verified reference yet for that mentor / topic combination."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mentor_key": {
                    "type": "string",
                    "description": "Lowercase mentor key — e.g., 'prutow', 'chapell', 'robinson', 'beeke', 'perkins', 'york', 'micah'.",
                },
                "topic": {
                    "type": "string",
                    "description": "Optional topic filter — e.g., 'main_point', 'fcf', 'sermon_length'. Omit to get all positions for the mentor.",
                },
            },
            "required": ["mentor_key"],
        },
    },
    {
        "name": "list_datasets",
        "description": (
            "List the research datasets present in Bryan's Logos library. "
            "Use this BEFORE picking a dataset to query — there are ~115 datasets "
            "covering topical sets ('All the Prayers in the Bible'), syntax graphs, "
            "ontologies, cross-references, biblical entities, and more. "
            "Optional `category` filters by schema_family (supplemental, xref, entity, "
            "syntax, clauses, grammar_ds, ontology, thematic, misc) or by lbx type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Optional schema_family or lbx.* type filter."},
            },
        },
    },
    {
        "name": "query_dataset",
        "description": (
            "Query any dataset in the registry by ID for a Bible passage. Returns "
            "{dataset, dataset_type, title, results, note}. Use this for the long tail "
            "of datasets that don't have a named tool. Get dataset_id from list_datasets()."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string", "description": "Dataset id (e.g. 'LLS:BULLINGER') from list_datasets()."},
                "reference":  {"type": "string", "description": "Bible reference, e.g. 'Romans 3:25-26'."},
                "limit":      {"type": "integer", "description": "Max rows to return (default 25, max 100)."},
            },
            "required": ["dataset_id", "reference"],
        },
    },
    {
        "name": "lookup_syntax_graph",
        "description": (
            "Look up the syntactic phrase structure for a Bible passage from "
            "Cascadia Syntax Graphs (NT) or Andersen-Forbes Phrase Markers (OT). "
            "Returns digested head/dependents/relation info — directly useful for "
            "genitive, participle, and clause-structure questions. Pair with "
            "lookup_grammar (Wallace prose) for the textual + structural picture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "Bible reference, e.g. 'Romans 3:22'."},
                "testament": {"type": "string", "enum": ["nt", "ot"], "description": "Default 'nt'."},
            },
            "required": ["reference"],
        },
    },
    {
        "name": "lookup_bullinger_figure",
        "description": (
            "Look up Bullinger's *Figures of Speech Used in the Bible* for a passage. "
            "Returns the rhetorical figures (metaphor, metonymy, hendiadys, chiasm, etc.) "
            "Bullinger catalogues at this reference. Classic homiletical mine."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "Bible reference, e.g. 'Romans 3:25'."},
            },
            "required": ["reference"],
        },
    },
]


def get_tool_names():
    """Return list of available tool names."""
    return [t["name"] for t in TOOL_DEFINITIONS]


def execute_tool(tool_name, tool_input, session_context):
    """Dispatch a tool call and return structured result.

    session_context is a dict with: session_id, passage_ref, book, chapter,
    verse_start, verse_end, genre, db (CompanionDB instance)
    """
    try:
        if tool_name == "read_bible_passage":
            return _read_bible_passage(tool_input)
        elif tool_name == "find_commentary_paragraph":
            return _find_commentary_paragraph(tool_input, session_context)
        elif tool_name == "lookup_lexicon":
            return _lookup_lexicon(tool_input, session_context)
        elif tool_name == "lookup_grammar":
            return _lookup_grammar(tool_input, session_context)
        elif tool_name == "word_study_lookup":
            return _word_study_lookup(tool_input, session_context)
        elif tool_name == "expand_cross_references":
            return _expand_cross_references(tool_input, session_context)
        elif tool_name == "save_to_outline":
            return _save_to_outline(tool_input, session_context)
        elif tool_name == "get_passage_data":
            return _get_passage_data(tool_input, session_context)
        elif tool_name == "get_cross_reference_network":
            return _get_cross_reference_network(tool_input, session_context)
        elif tool_name == "get_passage_context":
            return _get_passage_context(tool_input, session_context)
        elif tool_name == "search_library":
            return _search_library(tool_input, session_context)
        elif tool_name == "read_library_section":
            return _read_library_section(tool_input)
        elif tool_name == "list_datasets":
            return _list_datasets(tool_input)
        elif tool_name == "query_dataset":
            return _query_dataset(tool_input)
        elif tool_name == "lookup_syntax_graph":
            return _lookup_syntax_graph(tool_input)
        elif tool_name == "lookup_bullinger_figure":
            return _lookup_bullinger_figure(tool_input)
        elif tool_name == "suggest_search_terms":
            return _suggest_search_terms(tool_input, session_context)
        elif tool_name == "find_illustration":
            return _find_illustration(tool_input, session_context)
        elif tool_name == "find_objections":
            return _find_objections(tool_input, session_context)
        elif tool_name == "find_counseling_applications":
            return _find_counseling_applications(tool_input, session_context)
        elif tool_name == "find_wisdom_principles":
            return _find_wisdom_principles(tool_input, session_context)
        elif tool_name == "suggest_christ_thread":
            return _suggest_christ_thread(tool_input, session_context)
        elif tool_name == "gauge_outline_balance":
            return _gauge_outline_balance(tool_input, session_context)
        elif tool_name == "check_sermon_coherence":
            return _check_sermon_coherence(tool_input, session_context)
        elif tool_name == "lookup_mentor_position":
            return _lookup_mentor_position(tool_input, session_context)
        # ── Coaching Bridge Tools ──────────────────────────────────────
        elif tool_name == "get_active_commitment":
            from coaching_bridge import load_active_commitment
            return load_active_commitment(session_context['db']) or {'active': False}
        elif tool_name == "get_sermon_patterns":
            from sermon_coach_tools import get_sermon_patterns
            return get_sermon_patterns(session_context['db'], window_days=tool_input.get('window_days', 90))
        elif tool_name == "get_representative_moments":
            from meta_coach_tools import get_representative_moments
            return {'moments': get_representative_moments(session_context['db'], dimension=tool_input['dimension'], valence=tool_input['valence'])}
        elif tool_name == "get_counterexamples":
            from meta_coach_tools import get_counterexamples
            return {'counterexamples': get_counterexamples(session_context['db'], dimension=tool_input['dimension'])}
        elif tool_name == "get_coaching_insights":
            from coaching_bridge import load_coaching_insights
            return {'insights': load_coaching_insights(session_context['db'])}
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}


def _read_bible_passage(tool_input):
    """Read a Bible passage in one or more translations."""
    ref_str = tool_input["reference"]
    ref = parse_reference(ref_str)
    # Default to original language: THGNT for NT, BHS for OT.
    # parse_reference returns Logos native numbering where NT is 61-87.
    if "versions" not in tool_input or not tool_input["versions"]:
        versions = ["THGNT"] if ref["book"] >= 61 else ["BHS"]
    else:
        versions = tool_input["versions"]

    bible_files = resolve_bible_files(versions)

    results = []
    for bf in bible_files:
        chapter_text, metadata = read_bible_chapter(bf, ref["book"], ref["chapter"])
        if chapter_text:
            if ref["verse_start"]:
                text = extract_verses(chapter_text, ref["verse_start"], ref["verse_end"])
            else:
                text = chapter_text
            text = clean_bible_text(text)
            results.append({
                "version": bf.replace(".logos4", "").replace(".lbxlls", ""),
                "text": text
            })

    return {
        "reference": ref_str,
        "translations": results
    }


def _search_library(tool_input, session_context):
    """Full-text search across the entire Logos library.

    Wraps the LogosSearcher subprocess. Returns one entry per resource (deduplicated)
    with title/author/type, a normalized score, total hit count, and a list of
    relevant sections — each with a snippet showing query terms in «guillemets».
    """
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    limit = max(1, min(int(tool_input.get("limit", 10)), 25))
    type_prefix = (tool_input.get("resource_type") or "").strip() or None
    author_contains = (tool_input.get("author_contains") or "").strip() or None
    include_workspaces = bool(tool_input.get("include_workspaces", False))
    verse_filter = (tool_input.get("verse_filter") or "").strip() or None

    try:
        from logos_search import get_searcher
        searcher = get_searcher()
        result = searcher.search(
            query,
            limit=limit,
            resource_type_prefix=type_prefix,
            author_contains=author_contains,
            exclude_workspaces=not include_workspaces,
            verse_filter=verse_filter,
        )
    except Exception as e:
        return {"error": f"search_failed: {type(e).__name__}: {e}"}

    if not result.get("ok"):
        return {"error": result.get("error", "unknown"),
                "message": result.get("message", "")}

    results = result.get("results") or []
    return {
        "query": query,
        "result_count": len(results),
        "results": results,
    }


def _list_datasets(tool_input):
    """List datasets in the registry, optionally filtered by schema_family or lbx type."""
    cat = (tool_input.get("category") or "").strip().lower() or None
    reg = get_registry()
    all_ds = reg.list_all()
    if cat:
        ds = [d for d in all_ds if d.schema_family == cat or d.type.lower() == cat]
    else:
        ds = all_ds
    return {
        "count": len(ds),
        "datasets": [
            {"dataset_id": d.dataset_id, "title": d.title,
             "family": d.schema_family, "type": d.type}
            for d in ds
        ],
    }


def _query_dataset(tool_input):
    """Look up a dataset by id and route to the adapter for its schema family."""
    dataset_id = (tool_input.get("dataset_id") or "").strip()
    ref_str = (tool_input.get("reference") or "").strip()
    if not dataset_id or not ref_str:
        return {"error": "dataset_id and reference are required"}
    limit = max(1, min(int(tool_input.get("limit", 25)), 100))
    ds = get_registry().find(dataset_id)
    if ds is None:
        return {"error": "unknown_dataset", "dataset_id": dataset_id,
                "message": "call list_datasets() to discover valid ids"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    try:
        return adapter_for(ds.schema_family).query(ref, ds, limit=limit)
    except Exception as e:
        return {"error": f"query_failed: {type(e).__name__}: {e}",
                "dataset_id": dataset_id, "reference": ref_str}


def _lookup_syntax_graph(tool_input):
    """Pre-select a syntax dataset by testament, then delegate to the adapter."""
    ref_str = (tool_input.get("reference") or "").strip()
    testament = (tool_input.get("testament") or "nt").lower()
    if not ref_str:
        return {"error": "reference is required"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    reg = get_registry()
    syntax_ds = reg.by_family("syntax")
    # Heuristic dataset pick: NT → first 'Cascadia'-titled; OT → first 'Andersen-Forbes'-titled.
    needle = "cascadia" if testament == "nt" else "andersen-forbes"
    chosen = next((d for d in syntax_ds if needle in (d.title or "").lower()), None)
    if chosen is None and syntax_ds:
        chosen = syntax_ds[0]  # last resort
    if chosen is None:
        return {"error": "no_syntax_dataset"}
    try:
        return adapter_for("syntax").query(ref, chosen)
    except Exception as e:
        return {"error": f"syntax_query_failed: {type(e).__name__}: {e}"}


def _lookup_bullinger_figure(tool_input):
    """Pre-select Bullinger's Figures of Speech, then delegate to the adapter."""
    ref_str = (tool_input.get("reference") or "").strip()
    if not ref_str:
        return {"error": "reference is required"}
    ref = parse_reference(ref_str)
    if not ref:
        return {"error": "bad_reference", "reference": ref_str}
    reg = get_registry()
    chosen = next((d for d in reg.list_all() if "bullinger" in (d.title or "").lower()), None)
    if chosen is None:
        return {"error": "bullinger_not_in_library"}
    try:
        return adapter_for(chosen.schema_family).query(ref, chosen)
    except Exception as e:
        return {"error": f"bullinger_query_failed: {type(e).__name__}: {e}"}


def _read_library_section(tool_input):
    """Return the full exact text of a library section (resolves to read_article_text)."""
    filename = (tool_input.get("filename") or "").strip()
    article_num = tool_input.get("article_num")
    if not filename or article_num is None:
        return {"error": "filename and article_num are required (from a search_library result)"}
    max_chars = max(500, min(int(tool_input.get("max_chars", 8000)), 30000))
    try:
        text = read_article_text(filename, int(article_num), max_chars=max_chars)
    except Exception as e:
        return {"error": f"read_failed: {type(e).__name__}: {e}"}
    if not text:
        return {"error": "no_text", "message": f"No text for {filename} article {article_num}."}
    return {"filename": filename, "article_num": int(article_num), "text": text}


def _suggest_search_terms(tool_input, session_context):
    """Get query completions/refinements from Logos' query parser."""
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        from logos_search import get_searcher
        searcher = get_searcher()
        result = searcher.suggest(query)
    except Exception as e:
        return {"error": f"suggest_failed: {type(e).__name__}: {e}"}
    if not result.get("ok"):
        return {"error": result.get("error", "unknown"),
                "message": result.get("message", "")}
    return {
        "query": query,
        "suggestions": result.get("suggestions") or [],
    }


def _find_commentary_paragraph(tool_input, session_context):
    """Find relevant commentary section for a passage."""
    ref_str = tool_input["reference"]
    ref = parse_reference(ref_str)

    commentaries = find_commentaries_for_ref(ref, limit=5)

    if not commentaries:
        return {"error": "No commentaries found for this reference"}

    results = []
    for comm in commentaries[:3]:
        resource_file = comm.get("filename") or comm.get("location", "")
        if not resource_file:
            continue
        text = find_commentary_section(resource_file, ref)
        if text:
            if len(text) > 5000:
                text = text[:5000] + "\n\n[... truncated ...]"
            results.append({
                "title": comm.get("abbrev") or comm.get("title", resource_file),
                "text": text
            })

    return {
        "reference": ref_str,
        "commentaries": results
    }


def _lookup_lexicon(tool_input, session_context):
    """Look up a word in lexical resources by searching article IDs and text."""
    word = tool_input["word"]
    requested_lexicons = tool_input.get("lexicons")
    testament = tool_input.get("testament")

    # Auto-detect testament from word script if not specified
    if not testament:
        # Check if word contains Greek characters
        if any('\u0370' <= c <= '\u03FF' or '\u1F00' <= c <= '\u1FFF' for c in word):
            testament = "nt"
        elif any('\u0590' <= c <= '\u05FF' for c in word):
            testament = "ot"
        else:
            # Default based on session context
            book = session_context.get("book", 66)
            testament = "ot" if book and book <= 39 else "nt"

    # Select lexicons to search
    if requested_lexicons:
        lexicons = [l for l in ALL_LEXICONS if l["abbrev"] in requested_lexicons]
        if not lexicons:
            lexicons = NT_LEXICONS[:3] if testament == "nt" else OT_LEXICONS[:3]
    else:
        lexicons = NT_LEXICONS[:4] if testament == "nt" else OT_LEXICONS[:4]

    results = []
    for lex in lexicons:
        entry = _search_lexicon(lex["file"], lex["abbrev"], word)
        if entry:
            results.append(entry)
        if len(results) >= 3:
            break

    return {
        "word": word,
        "testament": testament,
        "entries": results,
        "searched": [l["abbrev"] for l in lexicons],
    }


def _search_lexicon(resource_file, abbrev, word, resource_type="lexicon"):
    """Search a lexicon/grammar using the pre-built index."""
    try:
        idx = _ensure_indexed(resource_file, resource_type=resource_type)
        results = idx.lookup(word, resource_file, limit=3)

        if not results:
            return None

        # Read the best match article
        best = results[0]
        text = read_article_text(resource_file, best["article_num"], max_chars=8000)
        if not text:
            return None

        return {
            "lexicon": abbrev,
            "article_id": best["article_id"],
            "headword": best.get("headword", ""),
            "text": text[:6000] if len(text) > 6000 else text,
        }
    except Exception:
        return None


def _lookup_grammar(tool_input, session_context):
    """Search Greek/Hebrew grammars by full text (not headword index), then
    return the matching section's full text. The headword index missed
    discursive sections like Wallace on the subjective/objective genitive."""
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    testament = tool_input.get("testament", "nt")
    requested = tool_input.get("grammars")
    pool = ALL_GRAMMARS if requested else (NT_GRAMMARS if testament == "nt" else OT_GRAMMARS)
    if requested:
        pool = [g for g in ALL_GRAMMARS if g["abbrev"] in requested] or pool
    grammar_files = {g["file"] for g in pool}

    try:
        result = get_searcher().search(query, limit=15)
    except Exception as e:
        return {"error": f"grammar_search_failed: {type(e).__name__}: {e}", "query": query}
    if result and result.get("ok") is False:
        return {
            "error": result.get("error", "search_unavailable"),
            "message": result.get("message", ""),
            "query": query,
            "testament": testament,
        }
    hits = (result or {}).get("results") or []

    entries = []
    for h in hits:
        if h.get("filename") not in grammar_files:
            continue
        sections = h.get("sections") or []
        if not sections:
            continue
        art = sections[0].get("article_num")
        if art is None:
            continue
        try:
            text = read_article_text(h["filename"], int(art), max_chars=6000)
        except Exception:
            logger.warning(
                "grammar lookup failed for %s (%s)",
                h.get("abbrev", "?"),
                h.get("filename", "?"),
                exc_info=True,
            )
            continue
        if not text:
            continue
        entries.append({
            "abbrev": h.get("abbrev") or "",
            "title": h.get("title") or "",
            "filename": h.get("filename"),
            "article_num": int(art),
            "text": text,
        })
        if len(entries) >= 2:
            break

    return {"query": query, "testament": testament, "entries": entries,
            "searched": sorted(grammar_files)}


def _word_study_lookup(tool_input, session_context):
    """Look up interlinear/morphological data for a word in the passage.

    Uses MorphGNT (authoritative) for NT, ESV interlinear fallback for OT.
    """
    word = tool_input["word"]
    ref_str = tool_input["reference"]
    ref = parse_reference(ref_str)
    book = ref["book"]

    # NT: use MorphGNT for authoritative morphology
    if book >= 40:
        from morphgnt_cache import get_cache
        import unicodedata
        cache = get_cache()

        verse_start = ref.get("verse_start", 1)
        verse_end = ref.get("verse_end", verse_start)
        word_nfc = unicodedata.normalize("NFC", word.strip().rstrip(".,;·"))

        all_words = []
        for v in range(verse_start, verse_end + 1):
            all_words.extend(cache.lookup_verse(book, ref["chapter"], v))

        if all_words:
            # Find matches: search surface text, normalized, and lemma
            matches = []
            for w in all_words:
                w_text = w["text"].rstrip(".,;·")
                if (word_nfc.lower() == w_text.lower()
                        or word_nfc.lower() == w["normalized"].lower()
                        or word_nfc.lower() == w["lemma"].lower()
                        or word.lower() in w.get("parsing_human", "").lower()):
                    matches.append({
                        "surface": w["text"],
                        "lemma": w["lemma"],
                        "morphology": w["parsing_human"],
                        "pos": w["pos"],
                    })

            if matches:
                return {
                    "word": word,
                    "reference": ref_str,
                    "source": "morphgnt",
                    "matches": matches,
                }

            # No exact match — return all verse words for context
            return {
                "word": word,
                "reference": ref_str,
                "source": "morphgnt",
                "matches": [],
                "verse_words": [
                    {"surface": w["text"], "lemma": w["lemma"],
                     "morphology": w["parsing_human"]}
                    for w in all_words
                ],
                "note": f"No exact match for '{word}'. Showing all words in the verse for reference."
            }

    # OT or MorphGNT miss: fall back to ESV interlinear
    bible_files = resolve_bible_files(["ESV"])
    if not bible_files:
        return {"error": "No Bible available for interlinear data"}

    interlinear = get_interlinear_for_chapter(bible_files[0], book, ref["chapter"])
    if not interlinear:
        return {"note": "Interlinear data not available for this passage. Use lookup_lexicon for word studies."}

    matches = find_word_in_interlinear(interlinear, word)

    if not matches:
        return {
            "word": word,
            "reference": ref_str,
            "matches": [],
            "note": f"No interlinear matches for '{word}'. Use lookup_lexicon for detailed word study."
        }

    structured = []
    for m in matches:
        entry = {
            "surface": m.get("Surface") or m.get("Word", ""),
            "lemma": m.get("Lemma", ""),
            "morphology": m.get("Morphology", ""),
            "strongs": m.get("Strong's", m.get("Strongs", "")),
        }
        for key in ["Manuscript", "Louw-Nida", "Gloss"]:
            if key in m and m[key]:
                entry[key.lower().replace("-", "_")] = m[key]
        structured.append(entry)

    return {
        "word": word,
        "reference": ref_str,
        "matches": structured
    }


def _expand_cross_references(tool_input, session_context):
    """Get cross-reference annotations and fetch referenced verse text."""
    ref_str = tool_input["reference"]
    ref = parse_reference(ref_str)

    bible_files = resolve_bible_files(["ESV"])
    if not bible_files:
        return {"error": "No Bible available"}

    annotations = read_annotations(
        bible_files[0], ref["book"], ref["chapter"],
        verses=list(range(ref["verse_start"] or 1, (ref["verse_end"] or ref["verse_start"] or 1) + 1))
    )

    return {
        "reference": ref_str,
        "cross_references": annotations.get("xrefs", {}),
        "footnotes": annotations.get("footnotes", {})
    }


def _save_to_outline(tool_input, session_context):
    """Save a node to the sermon outline."""
    db = session_context.get("db")
    session_id = session_context.get("session_id")

    if not db or not session_id:
        return {"error": "No active session for outline"}

    node_id = db.add_outline_node(
        session_id=session_id,
        node_type=tool_input["node_type"],
        content=tool_input["content"],
        parent_id=tool_input.get("parent_id"),
        verse_ref=tool_input.get("verse_ref"),
        source_type="companion",
    )

    return {
        "saved": True,
        "node_id": node_id,
        "node_type": tool_input["node_type"],
        "content": tool_input["content"]
    }


# ── Dataset Tool Implementations ─────────────────────────────────────────

def _logos_to_protestant_book(book):
    """Convert Logos native book number (NT: 61-87) to Protestant (NT: 40-66).

    dataset_tools functions expect Protestant numbering — they internally
    convert back to Logos via _protestant_to_logos (book + 21 for NT).
    parse_reference() returns Logos native, so all dataset_tools callers
    must convert first or they'll query the wrong book.
    """
    return book - 21 if book >= 61 else book


def _get_passage_data(tool_input, session_context):
    """Query pre-indexed passage datasets.

    When no datasets are specified, selects defaults based on the current
    study phase so Claude gets the most relevant data automatically.
    """
    from dataset_tools import (
        query_figurative_language, query_greek_constructions,
        query_hebrew_constructions, query_literary_typing,
        query_wordplay, query_propositional_outline,
        query_important_words, query_preaching_themes,
        query_nt_use_of_ot, query_cultural_concepts,
        query_thematic_outlines,
    )

    ref = parse_reference(tool_input["reference"])
    book, chapter = _logos_to_protestant_book(ref["book"]), ref["chapter"]
    testament = "ot" if book <= 39 else "nt"
    phase = session_context.get("phase", "")

    requested = tool_input.get("datasets")
    if not requested:
        # Phase-aware defaults
        constructions = "greek_constructions" if testament == "nt" else "hebrew_constructions"
        if phase in ("text_work", ""):
            requested = ["figurative_language", constructions, "literary_typing"]
        elif phase == "word_study":
            requested = ["important_words", constructions]
        elif phase == "context":
            requested = ["thematic_outlines", "nt_use_of_ot", "cultural_concepts"]
        elif phase == "theological":
            requested = ["nt_use_of_ot", "cultural_concepts", "preaching_themes"]
        elif phase in ("exegetical_point", "fcf_homiletical"):
            requested = ["propositional_outline", "preaching_themes"]
        elif phase == "sermon_construction":
            requested = ["preaching_themes", "cultural_concepts"]
        else:
            requested = ["preaching_themes", constructions]

    dispatch = {
        "figurative_language": lambda: query_figurative_language(book, chapter),
        "greek_constructions": lambda: query_greek_constructions(book, chapter),
        "hebrew_constructions": lambda: query_hebrew_constructions(book, chapter),
        "literary_typing": lambda: query_literary_typing(book, chapter),
        "wordplay": lambda: query_wordplay(book, chapter),
        "propositional_outline": lambda: query_propositional_outline(book, chapter, testament),
        "important_words": lambda: query_important_words(book, chapter),
        "preaching_themes": lambda: query_preaching_themes(book, chapter),
        "nt_use_of_ot": lambda: query_nt_use_of_ot(book, chapter),
        "cultural_concepts": lambda: query_cultural_concepts(book, chapter),
        "thematic_outlines": lambda: query_thematic_outlines(book, chapter),
    }

    results = {}
    for ds in requested:
        if ds in dispatch:
            try:
                data = dispatch[ds]()
                results[ds] = data if data else []
            except Exception:
                results[ds] = []

    return {"reference": tool_input["reference"], "datasets": results}


def _get_cross_reference_network(tool_input, session_context):
    """Query cross-reference databases."""
    from dataset_tools import query_curated_cross_refs, query_theology_xrefs

    ref = parse_reference(tool_input["reference"])
    book = _logos_to_protestant_book(ref["book"])
    xref_type = tool_input.get("xref_type", "curated")

    if xref_type == "curated":
        rows = query_curated_cross_refs(book, ref["chapter"],
                                        ref.get("verse_start"), ref.get("verse_end"))
    else:
        rows = query_theology_xrefs(book, ref["chapter"], xref_type=xref_type)

    return {
        "reference": tool_input["reference"],
        "xref_type": xref_type,
        "cross_references": rows[:50]
    }


def _get_passage_context(tool_input, session_context):
    """Query contextual data for a passage."""
    from dataset_tools import (
        query_biblical_places, query_biblical_people,
        query_biblical_things, query_ancient_literature
    )

    ref = parse_reference(tool_input["reference"])
    book, chapter = _logos_to_protestant_book(ref["book"]), ref["chapter"]

    requested = tool_input.get("context_types")
    if not requested:
        requested = ["places", "people", "things"]

    dispatch = {
        "places": lambda: query_biblical_places(book, chapter),
        "people": lambda: query_biblical_people(book, chapter),
        "things": lambda: query_biblical_things(book, chapter),
        "ancient_literature": lambda: query_ancient_literature(book, chapter),
    }

    results = {}
    for ct in requested:
        if ct in dispatch:
            try:
                data = dispatch[ct]()
                results[ct] = data if data else []
            except Exception:
                results[ct] = []

    return {"reference": tool_input["reference"], "context": results}


# ── Homiletics Engine tools ──────────────────────────────────────────────────


def _ctx_passage(ctx):
    return {
        'book': ctx.get('book'),
        'chapter': ctx.get('chapter'),
        'verse_start': ctx.get('verse_start'),
        'verse_end': ctx.get('verse_end'),
        'passage_ref': ctx.get('passage_ref'),
    }


def _serialize_insights(insights):
    return [
        {
            'kind': i.kind,
            'title': i.title,
            'body': i.body,
            'citations': list(i.citations),
            'confidence': i.confidence,
            'metadata': dict(i.metadata),
        }
        for i in insights
    ]


def _find_illustration(tool_input, ctx):
    theme = (tool_input.get('theme') or '').strip()
    if not theme:
        return {'error': 'theme is required'}
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import IllustrationFinder
        insights = IllustrationFinder().run(
            ctx['db'], session_id=ctx['session_id'],
            theme=theme, passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'illustration_failed: {type(e).__name__}: {e}'}


def _find_objections(tool_input, ctx):
    claim = (tool_input.get('claim') or '').strip()
    if not claim:
        return {'error': 'claim is required'}
    count = max(1, min(int(tool_input.get('count', 3)), 6))
    try:
        from homiletics_engine import ObjectionFinder
        insights = ObjectionFinder().run(
            ctx['db'], session_id=ctx['session_id'],
            claim=claim, count=count, passage=_ctx_passage(ctx),
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'objection_failed: {type(e).__name__}: {e}'}


def _find_counseling_applications(tool_input, ctx):
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import CounselingApplications
        insights = CounselingApplications().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'counseling_failed: {type(e).__name__}: {e}'}


def _find_wisdom_principles(tool_input, ctx):
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import WisdomPrinciples
        insights = WisdomPrinciples().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'wisdom_failed: {type(e).__name__}: {e}'}


def _suggest_christ_thread(tool_input, ctx):
    point = tool_input.get('point')
    count = max(1, min(int(tool_input.get('count', 3)), 5))
    try:
        from homiletics_engine import ChristThreadProposer
        insights = ChristThreadProposer().run(
            ctx['db'], session_id=ctx['session_id'],
            passage=_ctx_passage(ctx), point=point, count=count,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'christ_thread_failed: {type(e).__name__}: {e}'}


def _gauge_outline_balance(tool_input, ctx):
    try:
        from homiletics_engine import BalanceGauger
        insights = BalanceGauger().run(ctx['db'], session_id=ctx['session_id'])
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'balance_failed: {type(e).__name__}: {e}'}


def _check_sermon_coherence(tool_input, ctx):
    claim = (tool_input.get('claim') or '').strip()
    if not claim:
        return {'error': 'claim is required'}
    try:
        from homiletics_engine import CoherenceChecker
        insights = CoherenceChecker().run(
            ctx['db'], session_id=ctx['session_id'], claim=claim,
        )
        return {'insights': _serialize_insights(insights)}
    except Exception as e:
        return {'error': f'coherence_failed: {type(e).__name__}: {e}'}


def _lookup_mentor_position(tool_input, ctx):
    """Test the invoked-version of an authority against the actual source.

    Returns matching reference rows from mentor_references. Empty result
    means we don't have a verified reference yet — the LLM should NOT
    fabricate; it should tell Bryan we don't have a verified source and
    ask him to provide the actual quote or chapter so it can be captured.
    """
    mentor_key = (tool_input.get('mentor_key') or '').strip().lower()
    topic = (tool_input.get('topic') or '').strip() or None
    if not mentor_key:
        return {'error': 'mentor_key is required'}
    try:
        from mentor_reference_store import MentorReferenceStore
        store = MentorReferenceStore(ctx['db'].db_path)
        rows = store.get_references(mentor_key=mentor_key, topic=topic)
    except Exception as e:
        return {'error': f'lookup_failed: {type(e).__name__}: {e}'}
    if not rows:
        return {
            'mentor_key': mentor_key,
            'topic': topic,
            'references': [],
            'note': (
                f'No verified reference found for mentor_key={mentor_key!r}'
                + (f' topic={topic!r}' if topic else '')
                + '. Do NOT fabricate a position. Tell Bryan we lack a verified '
                  'source for this and offer to capture the actual quote / '
                  'chapter / page when he can provide it.'
            ),
        }
    return {
        'mentor_key': mentor_key,
        'topic': topic,
        'references': [
            {
                'source_title': r['source_title'],
                'topic': r['topic'],
                'position_text': r['position_text'],
                'citation': r['citation'],
                'verified': bool(r['verified']),
            }
            for r in rows
        ],
    }

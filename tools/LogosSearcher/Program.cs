// LogosSearcher — persistent JSON-line search subprocess against the local LibraryIndex.
//
// Hosts Libronix.DigitalLibrary.LibraryIndex + Libronix.SearchEngine.Searcher via
// reflection (no compile-time refs to Logos assemblies — they live in Logos.app
// and we load them at runtime from there).
//
// Protocol: one JSON object per line on stdin, one JSON object per line on stdout.
//   request:  {"op":"search","q":"patronage","limit":10,"order":"Relevance"}
//   response: {"ok":true,"hits":[{"resource_id":"LLS:...","title":"...","hit_count":3,"section_offset":42224,"section_length":483}],"total_documents":639,"total_hits":3637}
//   request:  {"op":"ping"}
//   response: {"ok":true,"pong":true}
//   request:  {"op":"shutdown"}  → process exits.
//
// Errors: {"ok":false,"error":"<machine-readable>","message":"<human-readable>"}.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;

namespace LogosSearcher;

public static class Program
{
    const string LIB_DIR = "/Applications/Logos.app/Contents/Frameworks/FaithlifeDesktop.framework/Versions/Current/Frameworks/ApplicationBundle.framework/Versions/Current/Resources/lib";
    const string DEFAULT_INDEX_PATH = "/Volumes/External/Logos4/Data/e3txalek.5iq/LibraryIndex-snapshot";
    const string CATALOG_DB = "/Volumes/External/Logos4/Data/e3txalek.5iq/LibraryCatalog/catalog.db";

    static object _index;
    static object _searcher;
    static Type _libraryIndexType;
    static Type _searcherType;
    static Type _queryOptionsType;
    static Type _queryParserType;
    static Type _searchResultOrderType;
    static Assembly _searchEngine;
    static object _workStateNone;
    static MethodInfo _parseMethod;
    static MethodInfo _executeQueryMethod;

    public static int Main(string[] args)
    {
        string indexPath = args.Length > 0 ? args[0] : DEFAULT_INDEX_PATH;

        // Assemblies first (so transitive Libronix.* loads from Logos lib dir)
        AppDomain.CurrentDomain.AssemblyResolve += (sender, e) =>
        {
            var name = new AssemblyName(e.Name).Name + ".dll";
            var path = Path.Combine(LIB_DIR, name);
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        };

        // Preload native dylibs. Once loaded by absolute path, subsequent P/Invokes
        // that reference them by short name find them via the already-loaded image
        // table. We cannot register our own DllImportResolver because
        // Libronix.SearchEngine and System.Data.SQLite install theirs at module init.
        foreach (var dylib in new[] { "libsqlite3-logos.dylib", "libSinaiInterop.dylib", "libSinaiBookBuilderAndIndexer.dylib" })
        {
            var p = Path.Combine(LIB_DIR, dylib);
            if (File.Exists(p))
            {
                try { System.Runtime.InteropServices.NativeLibrary.Load(p); }
                catch (Exception ex) { Console.Error.WriteLine($"[preload-fail] {dylib}: {ex.Message}"); }
            }
        }

        try { Initialize(indexPath); }
        catch (Exception ex)
        {
            EmitError("init_failed", ex);
            return 1;
        }

        Console.Error.WriteLine($"[ready] LogosSearcher index={indexPath}");

        var inputOptions = new JsonDocumentOptions { AllowTrailingCommas = true };
        string line;
        while ((line = Console.In.ReadLine()) != null)
        {
            line = line.Trim();
            if (line.Length == 0) continue;
            try
            {
                using var doc = JsonDocument.Parse(line, inputOptions);
                var root = doc.RootElement;
                var op = root.TryGetProperty("op", out var opEl) ? opEl.GetString() : "search";
                switch (op)
                {
                    case "ping":
                        EmitJson(new Dictionary<string, object> { { "ok", true }, { "pong", true } });
                        break;
                    case "shutdown":
                        EmitJson(new Dictionary<string, object> { { "ok", true }, { "shutdown", true } });
                        return 0;
                    case "search":
                        HandleSearch(root);
                        break;
                    case "suggest":
                        HandleSuggest(root);
                        break;
                    default:
                        EmitJson(new Dictionary<string, object> { { "ok", false }, { "error", "unknown_op" }, { "message", $"Unknown op: {op}" } });
                        break;
                }
            }
            catch (Exception ex)
            {
                EmitError("request_failed", ex);
            }
        }
        return 0;
    }

    static void Initialize(string indexPath)
    {
        var digLib = Assembly.LoadFrom(Path.Combine(LIB_DIR, "Libronix.DigitalLibrary.dll"));
        _searchEngine = Assembly.LoadFrom(Path.Combine(LIB_DIR, "Libronix.SearchEngine.dll"));
        var utilDll = Assembly.LoadFrom(Path.Combine(LIB_DIR, "Libronix.Utility.dll"));

        _libraryIndexType = digLib.GetType("Libronix.DigitalLibrary.LibraryIndex")
            ?? throw new InvalidOperationException("LibraryIndex type missing");
        _searcherType = _searchEngine.GetType("Libronix.SearchEngine.Searcher")
            ?? throw new InvalidOperationException("Searcher type missing");
        _queryOptionsType = _searchEngine.GetType("Libronix.SearchEngine.QueryOptions")
            ?? throw new InvalidOperationException("QueryOptions type missing");
        _queryParserType = _searchEngine.GetType("Libronix.SearchEngine.Parser.QueryParser")
            ?? throw new InvalidOperationException("QueryParser type missing");
        _searchResultOrderType = _searchEngine.GetType("Libronix.SearchEngine.SearchResultOrder")
            ?? throw new InvalidOperationException("SearchResultOrder type missing");

        var workStateType = utilDll.GetType("Libronix.Utility.Threading.WorkState")
            ?? throw new InvalidOperationException("WorkState type missing");
        _workStateNone = workStateType.GetProperty("None", BindingFlags.Public | BindingFlags.Static).GetValue(null);

        _parseMethod = _queryParserType.GetMethod("Parse", new[] { typeof(string) })
            ?? throw new InvalidOperationException("QueryParser.Parse(string) missing");
        _executeQueryMethod = _searcherType.GetMethods().First(m => m.Name == "ExecuteQuery");

        var ctor = _libraryIndexType.GetConstructor(new[] { typeof(string) });
        _index = ctor.Invoke(new object[] { indexPath });
        _searcher = _libraryIndexType.GetMethod("CreateSearcher", Type.EmptyTypes).Invoke(_index, null);
    }

    static void HandleSearch(JsonElement root)
    {
        var q = root.GetProperty("q").GetString() ?? "";
        if (string.IsNullOrWhiteSpace(q))
        {
            EmitJson(new Dictionary<string, object> { { "ok", false }, { "error", "empty_query" } });
            return;
        }
        int limit = root.TryGetProperty("limit", out var lEl) ? lEl.GetInt32() : 10;
        string orderName = root.TryGetProperty("order", out var oEl) ? oEl.GetString() : "Relevance";

        var queryNode = _parseMethod.Invoke(null, new object[] { q });
        if (queryNode == null)
        {
            EmitJson(new Dictionary<string, object> { { "ok", false }, { "error", "parse_failed" }, { "message", $"Could not parse query: {q}" } });
            return;
        }

        var queryOptions = Activator.CreateInstance(_queryOptionsType);
        // Best-effort order setting; fall back silently if name is unknown.
        try
        {
            var orderValue = Enum.Parse(_searchResultOrderType, orderName);
            _queryOptionsType.GetProperty("ResultOrder").SetValue(queryOptions, orderValue);
        }
        catch { /* leave default */ }
        _queryOptionsType.GetProperty("ResultCount").SetValue(queryOptions, (int?)limit);
        _queryOptionsType.GetProperty("DocumentCount").SetValue(queryOptions, (int?)limit);
        _queryOptionsType.GetProperty("ReturnTermFrequency").SetValue(queryOptions, true);

        var result = _executeQueryMethod.Invoke(_searcher, new[] { _workStateNone, queryNode, queryOptions });
        var resultType = result.GetType();
        var hits = ((System.Collections.IEnumerable)resultType.GetProperty("Results").GetValue(result))
            .Cast<object>()
            .Take(limit)
            .Select(MapHit)
            .ToList();
        var totalDocuments = (int)resultType.GetProperty("DocumentCount").GetValue(result);
        var totalResults = (int)resultType.GetProperty("ResultCount").GetValue(result);
        var totalHits = (int)resultType.GetProperty("HitCount").GetValue(result);

        EmitJson(new Dictionary<string, object>
        {
            { "ok", true },
            { "query", q },
            { "order", orderName },
            { "total_documents", totalDocuments },
            { "total_results", totalResults },
            { "total_hits", totalHits },
            { "hits", hits },
        });
    }

    static void HandleSuggest(JsonElement root)
    {
        // QueryParser.GetCompleterContexts(query, settings) returns a list of
        // CompleterContext objects representing possible completions at the end
        // of the query string. We turn each into a flat suggestion record.
        var q = root.GetProperty("q").GetString() ?? "";
        if (string.IsNullOrWhiteSpace(q))
        {
            EmitJson(new Dictionary<string, object> { { "ok", false }, { "error", "empty_query" } });
            return;
        }

        var getCompleter = _queryParserType.GetMethods()
            .FirstOrDefault(m => m.Name == "GetCompleterContexts" && m.GetParameters().Length == 2);
        if (getCompleter == null)
        {
            EmitJson(new Dictionary<string, object> { { "ok", false }, { "error", "no_completer" } });
            return;
        }
        var settingsType = getCompleter.GetParameters()[1].ParameterType;
        var settings = Activator.CreateInstance(settingsType);
        object contexts;
        try
        {
            contexts = getCompleter.Invoke(null, new object[] { q, settings });
        }
        catch (Exception ex)
        {
            EmitError("suggest_failed", ex);
            return;
        }
        var suggestions = new List<object>();
        if (contexts is System.Collections.IEnumerable e)
        {
            foreach (var ctx in e.Cast<object>().Take(20))
            {
                var ct = ctx.GetType();
                var entry = new Dictionary<string, object> { { "kind", ct.Name } };
                foreach (var p in ct.GetProperties())
                {
                    try
                    {
                        var v = p.GetValue(ctx);
                        if (v == null) continue;
                        if (v is string s) entry[p.Name] = s;
                        else if (v is int i) entry[p.Name] = i;
                        else if (v is bool b) entry[p.Name] = b;
                        else if (v is System.Collections.IEnumerable ee && !(v is string))
                        {
                            var list = ee.Cast<object>().Take(10).Select(x => x?.ToString()).ToList();
                            if (list.Count > 0) entry[p.Name] = list;
                        }
                        else entry[p.Name] = v.ToString();
                    }
                    catch { }
                }
                suggestions.Add(entry);
            }
        }
        EmitJson(new Dictionary<string, object>
        {
            { "ok", true },
            { "query", q },
            { "suggestions", suggestions },
        });
    }

    static Dictionary<string, object> MapHit(object hit)
    {
        var t = hit.GetType();
        var docId = t.GetProperty("DocumentId").GetValue(hit) as string;
        var sectionOffset = (int)t.GetProperty("SectionOffset").GetValue(hit);
        var sectionLength = (int)t.GetProperty("SectionLength").GetValue(hit);
        var hitCount = (int)t.GetProperty("HitCount").GetValue(hit);
        var sectionPostingCount = (int)t.GetProperty("SectionPostingCount").GetValue(hit);

        var hitsObj = t.GetProperty("Hits").GetValue(hit) as System.Collections.IEnumerable;
        var hitPositions = new List<object>();
        if (hitsObj != null)
        {
            foreach (var h in hitsObj.Cast<object>().Take(20))
            {
                var ht = h.GetType();
                hitPositions.Add(new Dictionary<string, object>
                {
                    { "offset", ht.GetProperty("Offset").GetValue(h) },
                    { "length", ht.GetProperty("Length").GetValue(h) },
                    { "word_offset", ht.GetProperty("WordOffset").GetValue(h) },
                });
            }
        }
        return new Dictionary<string, object>
        {
            { "resource_id", docId },
            { "section_offset", sectionOffset },
            { "section_length", sectionLength },
            { "hit_count", hitCount },
            { "section_posting_count", sectionPostingCount },
            { "hit_positions", hitPositions },
        };
    }

    static void EmitJson(object payload)
    {
        Console.WriteLine(JsonSerializer.Serialize(payload));
        Console.Out.Flush();
    }

    static void EmitError(string error, Exception ex)
    {
        var msg = ex.InnerException?.Message ?? ex.Message;
        EmitJson(new Dictionary<string, object> {
            { "ok", false }, { "error", error }, { "message", msg },
            { "exception", ex.GetType().FullName },
        });
    }
}

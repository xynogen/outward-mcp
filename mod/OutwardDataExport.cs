// Read-only BepInEx plugin: dumps game data (items, recipes, statuses) and live state as JSON.
// Never touches save files. Files land in BepInEx/outward-mcp/. status.json tells readers what worked.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;

[BepInPlugin("xynogen.outward.dataexport", "Outward Data Export", "1.3.0")]
public class OutwardDataExport : BaseUnityPlugin
{
    const int Schema = 3;  // bump when a JSON shape changes
    const float StaticRetrySeconds = 10f;
    const int StaticMaxTries = 5;

    string dir;
    bool staticDone;
    int staticTries;
    float nextStatic, nextLive;
    readonly Dictionary<string, string> results = new Dictionary<string, string>();  // file -> "ok N" / error
    ConfigEntry<bool> liveEnabled;
    ConfigEntry<float> liveInterval;
    ConfigEntry<float> scanRadius;

    void Awake()
    {
        dir = Path.Combine(Paths.BepInExRootPath, "outward-mcp");
        Directory.CreateDirectory(dir);
        Logger.LogInfo("Output: " + dir);
        liveEnabled = Config.Bind("Live", "Enabled", true, "Write live game state JSON periodically.");
        liveInterval = Config.Bind("Live", "IntervalSeconds", 15f,
            new ConfigDescription("Seconds between live state dumps.", new AcceptableValueRange<float>(1f, 300f)));
        scanRadius = Config.Bind("Live", "ScanRadius", 50f,
            new ConfigDescription("Meters around you to list enemies in live.json.", new AcceptableValueRange<float>(5f, 500f)));
    }

    void Update()
    {
        if (!staticDone && Time.unscaledTime >= nextStatic && StaticReady()) DumpStatic();
        if (liveEnabled.Value && Time.unscaledTime >= nextLive)
        {
            nextLive = Time.unscaledTime + liveInterval.Value;
            if (!Loading()) Export("live.json", LiveExport);
        }
    }

    static bool StaticReady()
    {
        return ResourcesPrefabManager.Instance != null && ResourcesPrefabManager.Instance.Loaded
            && LocalizationManager.Instance != null && LocalizationManager.Instance.Loaded
            && RecipeManager.Instance != null;
    }

    static bool Loading()
    {
        var l = NetworkLevelLoader.Instance;
        return l == null || l.IsGameplayLoading || l.IsSceneLoading;
    }

    // Items, recipes and statuses never change in a session. Retried a few times: one may load later.
    void DumpStatic()
    {
        staticTries++;
        nextStatic = Time.unscaledTime + StaticRetrySeconds;
        var ok = Export("items.json", ItemDump.Build) & Export("recipes.json", RecipeDump.Recipes)
               & Export("statuses.json", RecipeDump.Statuses);
        staticDone = ok || staticTries >= StaticMaxTries;
    }

    int LiveExport(Json j)
    {
        var c = CharacterManager.Instance == null ? null : CharacterManager.Instance.GetFirstLocalCharacter();
        if (c == null || c.Inventory == null) throw new InvalidOperationException("no local character yet");
        return LiveDump.Build(j, c, scanRadius.Value);
    }

    // Build into memory, then swap the file in: a failed export never leaves a broken or half-written file.
    bool Export(string name, Func<Json, int> build)
    {
        string result;
        try
        {
            var j = new Json();
            var count = build(j);
            Write(name, j.ToString());
            result = "ok " + count + (j.Skipped > 0 ? ", skipped " + j.Skipped : "");
        }
        catch (Exception e) { result = "error: " + e.GetType().Name + ": " + e.Message; }
        var ok = result.StartsWith("ok");
        string before;
        results.TryGetValue(name, out before);
        results[name] = result;
        if (before == null || before.StartsWith("ok") != ok)  // log and report changes only, not every dump
        {
            if (ok) Logger.LogInfo(name + ": " + result);
            else Logger.LogWarning(name + ": " + result);
            WriteStatus();
        }
        return ok;
    }

    void WriteStatus()
    {
        var j = new Json();
        j.Open('{');
        j.Field("schema", Schema);
        j.Field("plugin_version", Info.Metadata.Version.ToString());
        j.Field("written_utc", DateTime.UtcNow.ToString("o"));
        j.Obj("files");
        foreach (var kv in results) j.Field(kv.Key, kv.Value);
        j.Close('}');
        j.Close('}');
        try { Write("status.json", j.ToString()); }
        catch (Exception e) { Logger.LogError("status.json: " + e.Message); }
    }

    void Write(string name, string text)
    {
        var path = Path.Combine(dir, name);
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, text, new UTF8Encoding(false));
        try
        {
            if (File.Exists(path)) File.Replace(tmp, path, null);  // atomic swap: readers never see a missing file
            else File.Move(tmp, path);
        }
        catch (IOException)  // a reader holds the file open: keep the old one, next dump retries
        {
            File.Delete(tmp);
            throw;
        }
    }
}

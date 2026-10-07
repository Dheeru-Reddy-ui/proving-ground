// Proving Ground: runtime switches for the seeded bugs in the instrumented TrashCat build.
// Our own code, not Asset Store content. Tests turn bugs on through AltTester's
// CallStaticMethod("PGBugFlags", "Configure", ...); each hook in the game asks On("<flag>").
// Every hook point is listed in game/HOOKS.md.

using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Scripting;

[Preserve]
public static class PGBugFlags
{
    const string k_PrefsKey = "PGBugFlags.active";

    // Replaced as a whole on every change and never mutated, so a reader always sees one set.
    static HashSet<string> s_Active = new HashSet<string>(StringComparer.Ordinal);

    // Some hooks run while the game reads its save at startup (PlayerData.Read), before a driver
    // can connect, so the active set survives an app restart in PlayerPrefs. `pm clear` deletes
    // PlayerPrefs with the rest of the app's data, so every reset starts with no flags.
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    static void LoadPersisted()
    {
        string csv = PlayerPrefs.GetString(k_PrefsKey, "");
        if (csv.Length > 0)
        {
            Apply(csv, false);
        }
    }

    /// Replaces the active set with the comma-separated flag IDs; an empty string clears it.
    [Preserve]
    public static void Configure(string csvFlagIds)
    {
        Apply(csvFlagIds ?? "", true);
    }

    /// True when the flag is active.
    [Preserve]
    public static bool On(string id)
    {
        return s_Active.Contains(id);
    }

    /// The active flag IDs, sorted and comma-separated ("" when none).
    [Preserve]
    public static string Active()
    {
        List<string> ids = new List<string>(s_Active);
        ids.Sort(StringComparer.Ordinal);
        return string.Join(",", ids);
    }

    static void Apply(string csv, bool persist)
    {
        HashSet<string> next = new HashSet<string>(StringComparer.Ordinal);
        foreach (string part in csv.Split(','))
        {
            string id = part.Trim();
            if (id.Length > 0)
            {
                next.Add(id);
            }
        }
        s_Active = next;

        string active = Active();
        if (persist)
        {
            PlayerPrefs.SetString(k_PrefsKey, active);
            PlayerPrefs.Save();
        }
        Debug.Log("PGFLAGS " + active);
    }
}

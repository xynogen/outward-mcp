using System;
using System.Collections.Generic;

// Crafting recipes and status effects straight from the game: exact item ids, follows patches and mods.
static class RecipeDump
{
    public static int Recipes(Json j)
    {
        var all = ItemDump.Private<Dictionary<string, Recipe>>(typeof(RecipeManager), RecipeManager.Instance, "m_recipes");
        if (all == null || all.Count == 0) throw new InvalidOperationException("RecipeManager.m_recipes missing or empty");
        int n = 0;
        j.Open('[');
        foreach (var r in all.Values)
            if (r != null && j.Try(() => Recipe(j, r))) n++;
        j.Close(']');
        return n;
    }

    // Any null inside throws, and Json.Try drops the whole recipe: no half-known ingredients.
    static void Recipe(Json j, Recipe r)
    {
        j.Open('{');
        j.Field("uid", r.UID.ToString());
        j.Field("station", r.CraftingStationType.ToString());
        j.Key("results"); j.Open('[');
        foreach (var res in r.Results)
        {
            j.Open('{'); j.Field("id", res.ItemID); j.Safe("name", () => res.RefItem.Name); j.Field("qty", res.Quantity); j.Close('}');
        }
        j.Close(']');
        if (r.Results.Length == 0) throw new InvalidOperationException("recipe without result");
        j.Key("ingredients"); j.Open('[');
        foreach (var ing in r.Ingredients)
        {
            j.Open('{');
            if (ing.ActionType == RecipeIngredient.ActionTypes.AddSpecificIngredient)
            {
                j.Field("id", ing.AddedIngredient.ItemID);
                j.Safe("name", () => ing.AddedIngredient.Name);
            }
            else j.Field("tag", ing.AddedIngredientType.Tag.TagName);  // any item with this tag: "Meat", "Egg"...
            j.Close('}');
        }
        j.Close(']');
        j.Close('}');
    }

    static string Describe(StatusEffect s)
    {
        var data = s.StatusData == null ? null : s.StatusData.EffectsData;
        return Placeholders.Fill(s.Description, (e, v) =>
            data != null && e >= 1 && e <= data.Length && data[e - 1].Data != null  // EffectData is a struct
            && v >= 1 && v <= data[e - 1].Data.Length ? data[e - 1].Data[v - 1] : null);
    }

    public static int Statuses(Json j)
    {
        var all = ItemDump.Private<Dictionary<string, StatusEffect>>(typeof(ResourcesPrefabManager), null, "STATUSEFFECT_PREFABS");
        if (all == null || all.Count == 0) throw new InvalidOperationException("STATUSEFFECT_PREFABS missing or empty");
        int n = 0;
        j.Open('{');
        foreach (var kv in all)
        {
            var s = kv.Value;
            if (s == null || !j.Try(() =>
            {
                j.Obj(kv.Key);
                j.Safe("name", () => s.StatusName);
                j.Safe("description", () => Describe(s));
                j.Safe("negative", () => s.IsMalusEffect);
                j.Safe("purgeable", () => s.Purgeable);
                var life = s.StatusData == null ? 0f : s.StatusData.LifeSpan;
                if (life < 0) j.Field("permanent", true);  // -1 = lasts until removed
                else if (life > 0) j.Field("duration_s", life);
                j.Safe("family", () => s.EffectFamily == null ? null : s.EffectFamily.Name);
                j.Close('}');
            })) continue;
            n++;
        }
        j.Close('}');
        return n;
    }
}

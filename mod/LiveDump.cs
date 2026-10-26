using System;
using System.Collections.Generic;
using UnityEngine;

// Live state of the first local character: vitals, every item it carries, enemies around it.
// Each section is a Json.Try: one broken game object drops that section, not the whole file.
static class LiveDump
{
    static readonly string[] Compass = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" };

    public static int Build(Json j, Character c, float scanRadius)
    {
        var inv = c.Inventory;
        int items = 0;
        j.Open('{');
        j.Field("time_utc", DateTime.UtcNow.ToString("o"));
        j.Field("character", c.Name);
        j.Safe("area", () => AreaManager.Instance.CurrentArea.SceneName);
        j.Safe("silver", () => inv.AvailableMoney);
        j.Try(() =>
        {
            var s = c.PlayerStats;
            j.Obj("vitals");
            j.Field("health", s.CurrentHealth); j.Field("burnt_health", s.BurntHealth); j.Field("max_health", s.MaxHealth);
            j.Field("stamina", s.CurrentStamina); j.Field("burnt_stamina", s.BurntStamina); j.Field("max_stamina", s.MaxStamina);
            j.Field("mana", s.CurrentMana); j.Field("burnt_mana", s.BurntMana); j.Field("max_mana", s.MaxMana);
            j.Close('}');
        });
        j.Key("items"); j.Open('[');
        items += Box(j, "pouch", () => inv.Pouch);
        items += Box(j, "backpack", () => inv.EquippedBag == null ? null : inv.EquippedBag.Container);
        items += Box(j, "stash", () => inv.Stash);
        foreach (var slot in inv.Equipment.EquipmentSlots)
            if (slot != null && slot.EquippedItem != null && j.Try(() => Row(j, "equipped", slot.EquippedItem))) items++;
        j.Close(']');
        j.Try(() => Nearby(j, c, scanRadius));
        j.Close('}');
        return items;
    }

    // Living AI the player can target (not allies, guards or merchants), nearest first.
    static void Nearby(Json j, Character me, float radius)
    {
        var here = me.transform.position;
        var near = new List<Character>();
        foreach (var o in CharacterManager.Instance.Characters.Values)
            if (o != null && o != me && o.IsAI && o.Alive && Vector3.Distance(o.transform.position, here) <= radius
                && me.TargetingSystem.IsTargetable(o)) near.Add(o);
        near.Sort((a, b) => Vector3.Distance(a.transform.position, here).CompareTo(Vector3.Distance(b.transform.position, here)));
        j.Key("nearby"); j.Open('[');
        foreach (var o in near)
            j.Try(() =>
            {
                var d = o.transform.position - here;
                var bearing = (Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg + 360f) % 360f;  // Unity: +z north, +x east
                j.Open('{');
                j.Field("name", o.Name);
                j.Field("faction", o.Faction.ToString());
                j.Field("distance_m", (float)Math.Round(d.magnitude, 1));
                j.Field("direction", Compass[(int)((bearing + 22.5f) / 45f) % 8]);
                j.Field("in_combat", o.InCombat);
                j.Safe("targets_you", () => o.TargetingSystem.IsTargetable(me));
                j.Safe("health", () => (float)Math.Round(o.Stats.CurrentHealth, 1));
                j.Safe("max_health", () => (float)Math.Round(o.Stats.ActiveMaxHealth, 1));
                j.Close('}');
            });
        j.Close(']');
    }

    static int Box(Json j, string where, Func<ItemContainer> get)
    {
        ItemContainer box;
        try { box = get(); } catch (Exception) { return 0; }
        if (box == null) return 0;
        int n = 0;
        foreach (var it in box.GetContainedItems())
            if (it != null && j.Try(() => Row(j, where, it))) n++;
        return n;
    }

    static void Row(Json j, string where, Item it)
    {
        j.Open('{');
        j.Field("location", where); j.Field("id", it.ItemID); j.Field("name", it.Name);
        j.Field("qty", it.IsStackable ? it.RemainingAmount : 1);
        j.Safe("durability", () => it.MaxDurability > 0 ? (object)it.CurrentDurability : null);
        j.Safe("max_durability", () => it.MaxDurability > 0 ? (object)it.MaxDurability : null);
        j.Close('}');
    }
}

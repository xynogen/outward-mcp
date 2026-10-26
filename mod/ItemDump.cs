using System;
using System.Collections.Generic;
using System.Reflection;

// Static item/skill database, straight from the game's prefabs.
static class ItemDump
{
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

    public static Dictionary<string, Item> Prefabs() { return Private<Dictionary<string, Item>>(typeof(ResourcesPrefabManager), null, "ITEM_PREFABS"); }

    // Game fields that are private: read by name, null if a patch renames them.
    public static T Private<T>(Type type, object instance, string field) where T : class
    {
        var f = type.GetField(field, All);
        return f == null ? null : f.GetValue(instance) as T;
    }

    public static int Build(Json j)
    {
        var prefabs = Prefabs();
        if (prefabs == null || prefabs.Count == 0) throw new InvalidOperationException("ITEM_PREFABS missing or empty");
        int n = 0;
        j.Open('{');
        foreach (var it in prefabs.Values)
            if (it != null && j.Try(() => Row(j, it))) n++;
        j.Close('}');
        return n;
    }

    static void Row(Json j, Item it)
    {
        j.Obj(it.ItemID.ToString());
        j.Safe("name", () => it.Name);
        j.Safe("description", () => it.Description);
        j.Safe("kind", () => it.GetType().Name);  // not "class": wiki uses that for Mace/Sword
        j.Safe("type", () => it.TypeDisplay);
        j.Field("is_skill", it is Skill);
        j.Field("stackable", it.IsStackable);
        j.Safe("tags", () =>
        {
            var names = new System.Collections.Generic.List<string>();
            foreach (var t in it.Tags) if (t != null) names.Add(t.TagName);
            return string.Join(";", names.ToArray());  // ingredient tags such as "Meat" match recipe tags
        });
        if (it.Stats != null)
        {
            j.Safe("value", () => it.Stats.BaseValue);
            j.Safe("weight", () => it.Stats.RawWeight);
            j.Safe("max_durability", () => it.Stats.MaxDurability);
        }
        var eq = it as Equipment;
        if (eq != null && eq.Stats != null) Equip(j, eq);
        var w = it as Weapon;
        if (w != null) Weapon(j, w);
        var sk = it as Skill;
        if (sk != null)
        {
            j.Field("mana_cost", sk.ManaCost); j.Field("stamina_cost", sk.StaminaCost);
            j.Field("health_cost", sk.HealthCost); j.Field("cooldown", sk.Cooldown);
            j.Field("passive", sk.IsPassive);
        }
        var per = it.GetComponent<Perishable>();
        if (per != null) j.Safe("perish_depletion_rate", () => per.DepletionRate);
        j.Close('}');
    }

    static void Equip(Json j, Equipment eq)
    {
        var s = eq.Stats;
        j.Field("equip_slot", eq.EquipSlot.ToString());
        j.Safe("two_handed", () => eq.TwoHanded);
        j.Damage("damage_protection", Get<float[]>(s, "m_damageProtection"));
        j.Damage("damage_resistance", Get<float[]>(s, "m_damageResistance"));
        j.Damage("damage_bonus", Get<float[]>(s, "m_damageAttack"));
        j.Safe("barrier", () => s.BarrierProtection);
        j.Safe("impact_resistance", () => s.ImpactResistance);
        j.Safe("heat_protection", () => s.HeatProtection);
        j.Safe("cold_protection", () => s.ColdProtection);
        j.Safe("corruption_resistance", () => s.CorruptionResistance);
        j.Safe("movement_penalty", () => s.MovementPenalty);
        j.Safe("stamina_use_penalty", () => s.StaminaUsePenalty);
        j.Safe("stamina_cost_reduction", () => s.StaminaCostReduction);
        j.Safe("mana_use_modifier", () => s.ManaUseModifier);
        j.Safe("pouch_capacity_bonus", () => s.PouchCapacityBonus);
        j.Safe("max_health_bonus", () => s.MaxHealthBonus);
    }

    static void Weapon(Json j, Weapon w)
    {
        j.Field("weapon_type", w.Type.ToString());
        var ws = w.Stats;
        if (ws == null) return;
        j.Field("impact", ws.Impact); j.Field("attack_speed", ws.AttackSpeed);
        j.Field("stamina_cost", ws.StamCost); j.Field("reach", ws.Reach);
        if (ws.BaseDamage == null) return;
        j.Obj("damage");
        foreach (var d in ws.BaseDamage.List) j.Field(d.Type.ToString(), d.Damage);
        j.Close('}');
    }

    static T Get<T>(object o, string field) where T : class
    {
        for (var t = o.GetType(); t != null; t = t.BaseType)
        {
            var f = t.GetField(field, All);
            if (f != null) return f.GetValue(o) as T;
        }
        return null;
    }
}

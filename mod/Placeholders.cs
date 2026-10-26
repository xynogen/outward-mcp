using System;
using System.Globalization;
using System.Text.RegularExpressions;

// Status descriptions hold [E<effect>V<value>] slots (1-based). The game fills them from
// StatusData.EffectsData[effect].Data[value] when it shows the text; prefabs keep them raw.
static class Placeholders
{
    static readonly Regex Slot = new Regex(@"\[E(\d+)V(\d+)\]");

    // value(effect, index) returns null when unknown; that slot stays as it was.
    public static string Fill(string text, Func<int, int, string> value)
    {
        if (string.IsNullOrEmpty(text)) return text;
        return Slot.Replace(text, m =>
        {
            var v = value(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value));
            return string.IsNullOrEmpty(v) ? m.Value : Number(v);
        });
    }

    // Raw effect data -> display number: "2:Fire" -> "2" (damage keeps its type after ':'), "-.5" -> "-0.5".
    public static string Number(string raw)
    {
        var head = raw.Split(':')[0];
        float f;
        return float.TryParse(head, NumberStyles.Float, CultureInfo.InvariantCulture, out f)
            ? f.ToString("0.##", CultureInfo.InvariantCulture) : raw;
    }
}

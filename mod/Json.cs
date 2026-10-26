using System;
using System.Globalization;
using System.Text;

// Minimal JSON writer (C# 5, no deps). Safe() skips a field whose getter throws: one game API mismatch must not kill a dump.
class Json
{
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    static readonly string[] DamageNames = { "Physical", "Ethereal", "Decay", "Electric", "Frost", "Fire" };
    readonly StringBuilder sb = new StringBuilder();
    bool first = true;

    public int Skipped { get; private set; }

    // Run one row/section; if it throws, undo what it wrote so the document stays valid JSON.
    public bool Try(Action write)
    {
        int length = sb.Length;
        bool wasFirst = first;
        try { write(); return true; }
        catch (Exception)
        {
            sb.Length = length;
            first = wasFirst;
            Skipped++;
            return false;
        }
    }

    public void Open(char c) { Sep(); sb.Append(c); first = true; }
    public void Close(char c) { sb.Append(c); first = false; }
    public void Key(string k) { Sep(); Str(k); sb.Append(':'); first = true; }
    public void Field(string k, object v) { Key(k); Sep(); Val(v); first = false; }
    public void Obj(string k) { Key(k); Open('{'); }

    public void Safe(string key, Func<object> g)
    {
        object v;
        try { v = g(); } catch (Exception) { return; }
        if (key != null && v != null) Field(key, v);
    }

    public void Damage(string key, float[] a)
    {
        if (a == null) return;
        Obj(key);
        for (int i = 0; i < a.Length && i < DamageNames.Length; i++) if (a[i] != 0f) Field(DamageNames[i], a[i]);
        Close('}');
    }

    void Sep() { if (!first) sb.Append(','); first = false; }

    void Val(object v)
    {
        if (v == null) sb.Append("null");
        else if (v is bool) sb.Append((bool)v ? "true" : "false");
        else if (v is float) { var f = (float)v; sb.Append(float.IsNaN(f) || float.IsInfinity(f) ? "null" : f.ToString("R", Inv)); }
        else if (v is int || v is long || v is double) sb.Append(Convert.ToString(v, Inv));
        else Str(v.ToString());
    }

    void Str(string s)
    {
        sb.Append('"');
        foreach (var ch in s)
        {
            if (ch == '"' || ch == '\\') sb.Append('\\').Append(ch);
            else if (ch < ' ') sb.Append("\\u").Append(((int)ch).ToString("x4"));
            else sb.Append(ch);
        }
        sb.Append('"');
    }

    public override string ToString() { return sb.ToString(); }
}

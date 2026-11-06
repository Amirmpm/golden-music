"""Offline tests for the Golden Music 3.0 lyrics modules.

Covers: gm/lyrics_query.py, gm/lyrics_quality.py, gm/lyrics_search.py,
gm/lyrics_translate.py, gm/lyrics_io.py, gm/rating_tags.py.

Run:  python3 scripts/test_lyrics3.py
Constraints: NO Qt, NO network, NO pytest — plain script with
[OK]/[FAIL] lines, a SUMMARY and a non-zero exit code on failure.
The canonical regression: "01 vaveyla" (untagged Persian track) must
resolve to the query "vaveyla" and be found by the aggregator.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gm import lyrics_query as lq            # noqa: E402
from gm import lyrics_quality as lqual       # noqa: E402
from gm import lyrics_search as lsrch        # noqa: E402
from gm import lyrics_translate as ltr       # noqa: E402
from gm import lyrics_io as lio              # noqa: E402
from gm import rating_tags as rt             # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  [FAIL] {name} {extra}")


# Minimal local LRC parser (parse_lrc-compatible) for round-trip tests —
# deliberately NOT importing the legacy repo lyrics.py.
_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc_min(text):
    entries = {}
    plain = []
    for raw in text.splitlines():
        stamps = _LRC_TS.findall(raw)
        content = _LRC_TS.sub("", raw).strip()
        if not content:
            continue
        if stamps:
            for m in _LRC_TS.finditer(raw):
                mm, ss, frac = m.groups()
                ms = int(mm) * 60_000 + int(ss) * 1000 + \
                    int((frac or "0").ljust(3, "0")[:3])
                entries.setdefault(ms, content)
        else:
            plain.append(content)
    if entries:
        return sorted(entries.items()), True
    return [(None, p) for p in plain], False


TMP = Path(tempfile.mkdtemp(prefix="gmlyrics3_"))

GOOD_LYRICS = "\n".join([
    "I was walking down the street one day in the rain",
    "Thinking about the things we used to say",
    "Every corner holds a memory of your name",
    "And the shadows keep on calling me the same",
    "So I carry on until the morning light",
    "Hoping everything will turn out right",
])


# ===========================================================================
print("\n=== 1. lyrics_query: cleanup, mojibake, fuzzy matching ===")


def t_query():
    check("strip leading track number (01 vaveyla)",
          lq.clean_title("01 vaveyla") == "vaveyla",
          repr(lq.clean_title("01 vaveyla")))
    check("strip junk from 01_vaveyla_(320kbps)_[ulsan]",
          lq.clean_title("01_vaveyla_(320kbps)_[ulsan]") == "vaveyla",
          repr(lq.clean_title("01_vaveyla_(320kbps)_[ulsan]")))
    check("strip '1.' style track number",
          lq.clean_title("1. vaveyla") == "vaveyla")
    check("strip '07 -' style track number",
          lq.clean_title("07 - Gol-e Sang") == "Gol-e Sang")
    check("paren junk (Official Video)",
          lq.clean_title("Song (Official Video)") == "Song")
    check("paren junk (Audio) [HD]",
          lq.clean_title("Song (Audio) [HD]") == "Song")
    check("paren junk (Remastered 2011)",
          lq.clean_title("Song (Remastered 2011)") == "Song")
    check("quality tag 320kbps",
          lq.clean_title("Nightfall 320kbps") == "Nightfall")
    check("quality tag flac in brackets",
          lq.clean_title("Song [flac]") == "Song")
    check("site pollution words",
          lq.clean_title("vaveyla www.keepyourmusic.com") == "vaveyla",
          repr(lq.clean_title("vaveyla www.keepyourmusic.com")))
    check("numbered titles survive ('7 Rings')",
          lq.clean_title("7 Rings") == "7 Rings")
    check("numbered titles survive ('99 Luftballons')",
          lq.clean_title("99 Luftballons") == "99 Luftballons")
    check("numbered titles survive ('24K Magic')",
          lq.clean_title("24K Magic") == "24K Magic")
    check("empty input -> empty",
          lq.clean_title("") == "" and lq.clean_title(None) == "")

    # --- artist cleanup ---
    check("clean_artist strips ft tail",
          lq.clean_artist("Googoosh ft. Someone") == "Googoosh")
    check("clean_artist strips feat tail",
          lq.clean_artist("Googoosh feat. Sasy & X") == "Googoosh")
    check("clean_artist collapses junk artists",
          lq.clean_artist("12") == "" and
          lq.clean_artist("unknown artist") == "")

    # --- mojibake ---
    persian = "گل سنگ"
    moji = persian.encode("cp1256").decode("latin-1")
    check("fix_mojibake restores cp1256-as-latin1 Persian",
          lq.fix_mojibake(moji) == persian, repr(lq.fix_mojibake(moji)))
    check("fix_mojibake leaves French intact",
          lq.fix_mojibake("Élève à l'école") == "Élève à l'école")
    check("fix_mojibake leaves plain ASCII intact",
          lq.fix_mojibake("vaveyla") == "vaveyla")
    broken = "\ufffd" + "گل".encode("cp1256").decode("latin-1")
    check("fix_mojibake tolerates U+FFFD replacement chars",
          "گل" in lq.fix_mojibake(broken), repr(lq.fix_mojibake(broken)))

    # --- normalization ---
    check("normalize folds kehef/kaf",
          lq.normalize_for_match("ک") == lq.normalize_for_match("ك"))
    check("normalize folds farsi/arabic yeh",
          lq.normalize_for_match("ی") == lq.normalize_for_match("ي"))
    check("normalize strips diacritics and ZWNJ",
          lq.normalize_for_match("گُُل") == lq.normalize_for_match("گل") and
          lq.normalize_for_match("می\u200cروم") == lq.normalize_for_match("میروم"))
    check("normalize casefolds and drops punctuation",
          lq.normalize_for_match("Vaveyla!") == lq.normalize_for_match("vaveyla"))

    # --- fuzzy ---
    check("fuzzy_score exact after case/space normalization",
          lq.fuzzy_score("vaveyla", "Vaveyla ") == 1.0)
    check("fuzzy_score empty semantics",
          lq.fuzzy_score("", "") == 1.0 and lq.fuzzy_score("a", "") == 0.0)
    near = lq.fuzzy_score("vaveyla", "vaveylaa")
    check("fuzzy_score near-miss in (0,1)",
          0.3 < near < 1.0, str(near))

    # --- pick_best ---
    good_cand = {"title": "Vaveyla", "artist": "", "duration": 210,
                 "synced": True, "lines": [(0, "x")], "source": "net"}
    spam_cand = {"title": "vaveyla ringtone download now", "artist": "",
                 "duration": 321, "synced": False,
                 "lines": [(None, "x")], "source": "spam"}
    best, score = lq.pick_best([spam_cand, good_cand], "vaveyla", "", 210)
    check("pick_best prefers duration-matching synced candidate",
          best is good_cand, f"score={score}")
    check("pick_best score reaches GOOD threshold",
          score >= lq.GOOD, str(score))
    check("pick_best thresholds exported",
          lq.GOOD == 0.75 and lq.OK == 0.55)
    check("pick_best empty input",
          lq.pick_best([], "x", "y") == (None, 0.0))
    check("pick_best ignores non-dict entries",
          lq.pick_best([None, "junk"], "x", "y") == (None, 0.0))

    # --- filename parsing ---
    check("title_from_filename 'Artist - N - Title'",
          lq.title_from_filename("Googoosh - 02 - Gol-e Sang") ==
          ("Googoosh", "Gol-e Sang"))
    check("title_from_filename 'Artist - Title'",
          lq.title_from_filename("Googoosh - Gol-e Sang") ==
          ("Googoosh", "Gol-e Sang"))
    check("title_from_filename 'N Title'",
          lq.title_from_filename("01 vaveyla") == ("", "vaveyla"))
    check("title_from_filename 'N - Title'",
          lq.title_from_filename("01 - vaveyla") == ("", "vaveyla"))
    check("title_from_filename plain",
          lq.title_from_filename("Gol-e Sang") == ("", "Gol-e Sang"))

    # --- variants ---
    vs = lq.title_variants("Song (Live Edit)", "Artist A")
    check("title_variants ordering",
          vs == [("Song (Live Edit)", "Artist A"),
                 ("Song", "Artist A"),
                 ("Artist A - Song (Live Edit)", ""),
                 ("Song (Live Edit)", "")], repr(vs))
    check("title_variants dedupes plain cleanup ('01 vaveyla')",
          lq.title_variants("01 vaveyla", "") == [("vaveyla", "")],
          repr(lq.title_variants("01 vaveyla", "")))
    long_title = ("A Very Long Song Title That Goes On And On Forever "
                  "And Ever In The Night")
    vs2 = lq.title_variants(long_title, "X")
    check("title_variants truncates very long titles",
          vs2 and vs2[-1] == ("A Very Long Song Title", "X"), repr(vs2[-1]))
    vs3 = lq.title_variants("Song ft. Backup", "A")
    check("title_variants includes feat-stripped variant",
          ("Song", "A") in vs3, repr(vs3))


t_query()


# ===========================================================================
print("\n=== 2. lyrics_quality: verdict + needs_confirmation ===")


def t_quality():
    v = lqual.verdict("la la\nla la la")
    check("short lyrics invalid + action search",
          not v["valid"] and v["reason"] == "too_short" and
          v["action"] == "search", repr(v))
    three_lines = "\n".join(["word " * 12] * 3)
    v2 = lqual.verdict(three_lines)
    check("under 4 non-empty lines invalid",
          not v2["valid"] and v2["reason"] == "too_short", repr(v2))

    ad = "\n".join([
        "This song is available as a ringtone for your phone",
        "Download it today and enjoy the sound every morning",
        "Many people love this melody around the whole world",
        "It brings joy to everyone who hears it playing loud",
    ])
    check("ad-word lyrics invalid",
          not lqual.verdict(ad)["valid"], repr(lqual.verdict(ad)))

    url_lyr = "\n".join([
        "These lyrics were copied from another website sadly",
        "Visit https://example.invalid/lyrics for the full text",
        "The rest of the song continues in much the same way here",
        "And nobody really knows who wrote these lines at all",
    ])
    check("URL-bearing lyrics invalid",
          not lqual.verdict(url_lyr)["valid"] and
          lqual.verdict(url_lyr)["reason"] == "spam_url")

    rep = "\n".join(
        ["the same old line keeps playing in my head all night long"] * 9 +
        ["one different line here"])
    check("repeated-line walls invalid",
          lqual.verdict(rep)["reason"] == "spam_repeated" and
          not lqual.verdict(rep)["valid"])

    shout = "\n".join([
        "DOWNLOAD OUR NEW PRODUCT TODAY AND ENJOY THE BEST MUSIC FOREVER",
        "THE BEST OFFERS AND DEALS ARE WAITING FOR YOU RIGHT NOW TODAY",
        "CLICK THE BIG BUTTON AND GET STARTED WITH YOUR FREE TRIAL NOW",
        "THOUSANDS OF HAPPY USERS ALREADY JOINED THE SERVICE THIS WEEK",
        "DO NOT WAIT ANY LONGER BECAUSE THE OFFER ENDS VERY SOON MY FRIEND",
    ])
    check("all-caps shouting invalid",
          lqual.verdict(shout)["reason"] == "spam_shouting" and
          not lqual.verdict(shout)["valid"])

    contact = "\n".join([
        "Follow the singer for updates on new releases",
        "Contact us anytime you like for more details",
        "The office line is 0912 345 6789 call now ok",
        "We are always happy to talk about the music",
        "And answer questions about the lyrics and songs",
    ])
    check("phone numbers flagged as spam",
          lqual.verdict(contact)["reason"] == "spam_contact" and
          not lqual.verdict(contact)["valid"])

    ok_v = lqual.verdict(GOOD_LYRICS)
    check("normal lyrics valid + action keep",
          ok_v["valid"] and ok_v["reason"] == "ok" and
          ok_v["action"] == "keep", repr(ok_v))

    inst = lqual.verdict("[Instrumental]")
    check("instrumental special-cased as valid",
          inst["valid"] and inst["reason"] == "instrumental", repr(inst))
    check("instrumental '(inst)' variant",
          lqual.verdict("(inst)")["reason"] == "instrumental")
    check("None/empty text invalid",
          not lqual.verdict(None)["valid"] and
          not lqual.verdict("")["valid"])

    other_good = "\n".join([
        "Morning comes and takes the night away",
        "I can see the sunlight on the bay",
        "Nothing left of yesterday",
        "But the promise of another day",
    ])
    check("needs_confirmation: existing invalid -> True",
          lqual.needs_confirmation("short", other_good) is True)
    check("needs_confirmation: empty existing -> True",
          lqual.needs_confirmation("", other_good) is True)
    check("needs_confirmation: both valid, similar size -> False",
          lqual.needs_confirmation(GOOD_LYRICS, other_good) is False)
    check("needs_confirmation: candidate 2x longer -> True",
          lqual.needs_confirmation(other_good, other_good + "\n" +
                                   other_good) is True)


t_quality()


# ===========================================================================
print("\n=== 3. lyrics_search: fake providers, aggregator, planning ===")


class FakeProvider(lsrch.LyricsProvider):
    """Offline provider matching a canned catalog (title+artist aware)."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.calls = []

    def name(self):
        return "fake"

    def search(self, query_title, query_artist, duration_s=None):
        self.calls.append((query_title, query_artist))
        qtn = lq.normalize_for_match(query_title)
        qan = lq.normalize_for_match(query_artist)
        out = []
        for item in self.catalog:
            itn = lq.normalize_for_match(item["title"])
            ian = lq.normalize_for_match(item.get("artist", ""))
            if itn == qtn and (not qan or not ian or ian == qan):
                out.append(dict(item))
        return out


class AlwaysProvider(lsrch.LyricsProvider):
    """Returns the same items for every query (dedupe stress)."""

    def __init__(self, items):
        self.items = items
        self.calls = 0

    def name(self):
        return "always"

    def search(self, query_title, query_artist, duration_s=None):
        self.calls += 1
        return [dict(i) for i in self.items]


class ExplodingProvider(lsrch.LyricsProvider):
    def name(self):
        return "boom"

    def search(self, query_title, query_artist, duration_s=None):
        raise RuntimeError("provider exploded")


def t_search():
    # --- the canonical "01 vaveyla" end-to-end case ---
    track = TMP / "01 vaveyla.mp3"
    track.write_bytes(b"")
    plan = lsrch.plan_searches(str(track))
    check("plan_searches on untagged '01 vaveyla.mp3'",
          plan == [("vaveyla", "", None)], repr(plan))
    plan2 = lsrch.plan_searches(
        {"title": "01 vaveyla", "artist": "", "duration": 210})
    check("plan_searches from metadata dict",
          plan2 == [("vaveyla", "", 210)], repr(plan2))

    catalog = [{"title": "Vaveyla", "artist": "", "duration": 210,
                "synced": True,
                "lines": [(0, "line one"), (5000, "line two")],
                "source": "fake"}]
    provider = FakeProvider(catalog)
    agg = lsrch.Aggregator([provider])
    ranked = agg.search_all("01 vaveyla", "", 210)
    check("'01 vaveyla' found end-to-end with score >= GOOD",
          bool(ranked) and ranked[0][1] >= lq.GOOD,
          repr(ranked[:1]))
    check("found candidate is the synced 'vaveyla' one",
          bool(ranked) and lq.normalize_for_match(ranked[0][0]["title"]) ==
          "vaveyla" and ranked[0][0]["synced"] is True)

    # --- mojibake tags are fixed before querying ---
    persian = "گل سنگ"
    moji = persian.encode("cp1256").decode("latin-1")
    moji_catalog = [{"title": persian, "artist": "", "duration": 220,
                     "synced": True, "lines": [(0, "x")], "source": "fake"}]
    moji_ranked = lsrch.Aggregator([FakeProvider(moji_catalog)]) \
        .search_all(moji, "", 220)
    check("mojibake title fixed and found",
          bool(moji_ranked) and moji_ranked[0][1] >= lq.GOOD and
          lq.normalize_for_match(moji_ranked[0][0]["title"]) ==
          lq.normalize_for_match(persian), repr(moji_ranked[:1]))

    # --- wrong artist still matches via the title-only variant ---
    wrong_catalog = [{"title": "Gol-e Sang", "artist": "Googoosh",
                      "duration": 200, "synced": True,
                      "lines": [(0, "x")], "source": "fake"}]
    wrong_ranked = lsrch.Aggregator([FakeProvider(wrong_catalog)]) \
        .search_all("Gol-e Sang", "Wrong Artist", 200)
    check("wrong-artist query still matched via title-only variant",
          bool(wrong_ranked) and wrong_ranked[0][1] >= lq.GOOD,
          repr(wrong_ranked[:1]))

    # --- early exit ---
    early_catalog = [{"title": "Vaveyla (Live) ft. Sasy",
                      "artist": "Googoosh", "duration": 210,
                      "synced": True, "lines": [(0, "x")],
                      "source": "fake"}]
    early_provider = FakeProvider(early_catalog)
    multi_variant = "Vaveyla (Live) ft. Sasy"
    check("early-exit fixture really has multiple variants",
          len(lq.title_variants(multi_variant, "Googoosh")) >= 3)
    early_ranked = lsrch.Aggregator([early_provider]) \
        .search_all(multi_variant, "Googoosh", 210)
    check("early exit after first GOOD variant",
          len(early_provider.calls) == 1 and
          bool(early_ranked) and early_ranked[0][1] >= lq.GOOD,
          repr(early_provider.calls))

    # --- dedupe ---
    items = [
        {"title": "vaveyla", "artist": "", "duration": 999,
         "synced": False, "lines": [], "source": "always"},
        {"title": "other song", "artist": "", "duration": 999,
         "synced": False, "lines": [], "source": "always"},
    ]
    always = AlwaysProvider(items)
    dedup_ranked = lsrch.Aggregator([always]) \
        .search_all(multi_variant, "A", 210)
    n_variants = len(lq.title_variants(multi_variant, "A"))
    check("dedupe collapses repeated candidates across variants",
          len(dedup_ranked) == 2,
          f"got {len(dedup_ranked)} entries")
    check("no early exit below GOOD (all variants queried)",
          always.calls == n_variants,
          f"{always.calls} vs {n_variants}")

    # --- robustness ---
    check("exploding provider never crashes the aggregator",
          lsrch.Aggregator([ExplodingProvider()])
          .search_all("anything", "x", 100) == [])
    events = []
    lsrch.Aggregator([FakeProvider(catalog)]) \
        .search_all("01 vaveyla", "", 210, progress_cb=lambda s, d: events.append(s))
    check("progress_cb receives query/hit stages",
          "query" in events and "hit" in events, repr(events))
    check("aggregator with no providers returns []",
          lsrch.Aggregator([]).search_all("t", "a", 1) == [])

    # --- LrclibProvider with injected fetchers ---
    urls = []

    def fake_fetch(url, timeout=8):
        urls.append(url)
        return {"trackName": "Vaveyla", "artistName": "Sasy",
                "duration": 210, "instrumental": False,
                "syncedLyrics": "[00:01.00]line one\n[00:02.50]line two",
                "plainLyrics": "line one\nline two"}

    prov = lsrch.LrclibProvider(fetch_json=fake_fetch)
    cands = prov.search("Vaveyla", "Sasy", 210)
    check("LrclibProvider parses /api/get hit",
          len(cands) == 1 and cands[0]["title"] == "Vaveyla" and
          cands[0]["artist"] == "Sasy" and cands[0]["duration"] == 210 and
          cands[0]["synced"] is True and
          cands[0]["lines"] == [(1000, "line one"), (2500, "line two")] and
          cands[0]["source"] == "lrclib.net", repr(cands))
    check("LrclibProvider /api/get URL is https + right endpoint",
          urls and urls[0].startswith("https://lrclib.net/api/get?") and
          "artist_name=Sasy" in urls[0] and "duration=210" in urls[0],
          repr(urls[:1]))

    captured = []
    coerced = lsrch.LrclibProvider(
        fetch_json=lambda u, t: captured.append(u) or [],
        base_url="http://lrclib.net")
    coerced.search("T", "", None)
    check("http base_url coerced to https",
          captured and captured[0].startswith("https://lrclib.net/api/search?q="),
          repr(captured[:1]))

    def boom(url, timeout=8):
        raise OSError("network down")

    check("LrclibProvider swallows fetch errors",
          lsrch.LrclibProvider(fetch_json=boom)
          .search("Vaveyla", "Sasy", 210) == [])

    def garbage(url, timeout=8):
        return "not-a-json-object"

    check("LrclibProvider ignores malformed payloads",
          lsrch.LrclibProvider(fetch_json=garbage)
          .search("Vaveyla", "Sasy", 210) == [])

    bare_urls = []

    def bare(url, timeout=8):
        bare_urls.append(url)
        return []

    lsrch.LrclibProvider(fetch_json=bare).search("Vaveyla", "", None)
    check("no artist -> single bare /api/search?q= call",
          len(bare_urls) == 1 and "q=Vaveyla" in bare_urls[0],
          repr(bare_urls))

    both_urls = []

    def both(url, timeout=8):
        both_urls.append(url)
        return []

    lsrch.LrclibProvider(fetch_json=both).search("Vaveyla", "Sasy", None)
    check("artist without duration -> artist search then bare search",
          len(both_urls) == 2 and "artist_name=Sasy" in both_urls[0] and
          "q=Vaveyla" in both_urls[1], repr(both_urls))

    def http_raises():
        try:
            lsrch._default_fetch_json("http://lrclib.net/api/search?q=x")
            return False
        except ValueError:
            return True

    check("default fetcher rejects non-https URLs",
          http_raises())


t_search()


# ===========================================================================
print("\n=== 4. lyrics_translate: alignment + inline pairs ===")


def t_translate():
    merged = ltr.merge_translations(
        [(0, "a"), (5000, "b"), (10000, "c")],
        [(0, "تا"), (5000, "تب")])
    check("exact timestamp alignment",
          merged == [(0, "a", "تا"), (5000, "b", "تب"),
                     (10000, "c", None)], repr(merged))

    near = ltr.merge_translations([(5000, "b")], [(5600, "تب")])
    check("nearest match within 800 ms",
          near == [(5000, "b", "تب")], repr(near))
    far = ltr.merge_translations([(5000, "b")], [(7000, "تب")])
    check("no match beyond 800 ms window",
          far == [(5000, "b", None)], repr(far))

    once = ltr.merge_translations([(5000, "a"), (5400, "b")], [(5200, "t")])
    check("each translation used at most once",
          once == [(5000, "a", "t"), (5400, "b", None)], repr(once))

    untimed = ltr.merge_translations(
        [(None, "intro"), (1000, "a")], [(1000, "t")])
    check("untimed original lines keep text with no translation",
          untimed == [(None, "intro", None), (1000, "a", "t")], repr(untimed))
    check("empty inputs merge to []",
          ltr.merge_translations([], []) == [] and
          ltr.merge_translations(None, None) == [])

    inline = ltr.extract_inline_translation(
        "[00:01.00]hello ||| سلام\nplain line\n[00:02.00]two /// dos\n")
    check("inline '|||' separator parse",
          (1000, "hello", "سلام") in inline, repr(inline))
    check("inline '///' separator parse",
          (2000, "two", "dos") in inline, repr(inline))
    check("plain lines parsed without translation",
          (None, "plain line", None) in inline, repr(inline))
    tagged = ltr.extract_inline_translation(
        "[00:03.50]hello [translation: bonjour]")
    check("inline [translation: ...] tag parse",
          tagged == [(3500, "hello", "bonjour")], repr(tagged))

    side = TMP / "song.lrc"
    side.write_text("[00:12.00]ترجمه خط\n[00:17.30]second line\n",
                    encoding="utf-8")
    entries = ltr.parse_translation_sidecar(str(side))
    check("sidecar parse from temp file",
          entries == [(12000, "ترجمه خط"), (17300, "second line")],
          repr(entries))
    side2 = TMP / "song2.lrc"
    side2.write_text("[00:20.00][translation: hello]\n[00:25.00]plain\n",
                     encoding="utf-8")
    entries2 = ltr.parse_translation_sidecar(str(side2))
    check("sidecar supports inline [translation:] tags",
          entries2 == [(20000, "hello"), (25000, "plain")], repr(entries2))
    check("missing sidecar returns []",
          ltr.parse_translation_sidecar(str(TMP / "nope.lrc")) == [])


t_translate()


# ===========================================================================
print("\n=== 5. lyrics_io: LRC serialization, sidecar, embedded ===")


def t_io():
    lines = [(1234, "hi"), (61000, "bye"), (None, "untimed")]
    text = lio.to_lrc_text(lines, True)
    check("to_lrc_text synced serialization",
          text == "[00:01.234]hi\n[01:01.000]bye\n", repr(text))
    parsed, synced = parse_lrc_min(text)
    check("to_lrc_text round trips through an LRC parser",
          parsed == [(1234, "hi"), (61000, "bye")] and synced is True,
          repr(parsed))
    plain = lio.to_lrc_text([(None, "one"), (None, "  "), (None, "two")],
                            False)
    check("to_lrc_text plain text skips blanks",
          plain == "one\ntwo\n", repr(plain))

    shifted = lio.apply_offset([(1000, "a"), (2000, "b")], 500)
    check("apply_offset positive shift",
          shifted == [(1500, "a"), (2500, "b")], repr(shifted))
    clamped = lio.apply_offset([(100, "a"), (5000, "b")], -1000)
    check("apply_offset clamps negatives at 0",
          clamped == [(0, "a"), (4000, "b")], repr(clamped))
    mixed = lio.apply_offset([(5000, "b"), (1000, "a"), (None, "meta")], 0)
    check("apply_offset keeps monotonic order, untimed last",
          mixed == [(1000, "a"), (5000, "b"), (None, "meta")], repr(mixed))
    check("apply_offset of empty list",
          lio.apply_offset([], 100) == [])
    check("apply_offset tolerates bad offset",
          lio.apply_offset([(1000, "a")], None) == [(1000, "a")])

    audio = TMP / "track.mp3"
    audio.write_bytes(b"")
    lio.save_to_sidecar(str(audio), [(1234, "hi"), (61000, "bye")], True)
    sidecar = TMP / "track.lrc"
    sparsed, ssynced = parse_lrc_min(sidecar.read_text(encoding="utf-8"))
    check("save_to_sidecar writes a parseable .lrc next to audio",
          sidecar.is_file() and sparsed == [(1234, "hi"), (61000, "bye")] and
          ssynced is True, repr(sparsed))
    try:
        lio.save_to_sidecar(str(audio), [(0, "x")], True)
        exists_raised = False
    except lio.LyricsIOError as e:
        exists_raised = e.reason == "exists"
    check("save_to_sidecar refuses to overwrite by default", exists_raised)
    lio.save_to_sidecar(str(audio), [(2000, "new")], True, overwrite=True)
    sparsed2, _ = parse_lrc_min(sidecar.read_text(encoding="utf-8"))
    check("save_to_sidecar overwrite=True replaces content",
          sparsed2 == [(2000, "new")], repr(sparsed2))
    try:
        lio.save_to_sidecar(str(audio), [], True)
        empty_raised = False
    except lio.LyricsIOError as e:
        empty_raised = e.reason == "empty"
    check("save_to_sidecar with no lines raises 'empty'", empty_raised)

    # --- embedded errors ---
    try:
        lio.save_embedded(str(TMP / "missing.mp3"), [(0, "x")], True)
        missing_raised = False
    except lio.LyricsIOError as e:
        missing_raised = e.reason == "not_found"
    check("save_embedded missing path raises 'not_found'", missing_raised)

    empty_audio = TMP / "empty.mp3"
    empty_audio.write_bytes(b"")
    try:
        lio.save_embedded(str(empty_audio), [], True)
        no_lines_raised = False
    except lio.LyricsIOError as e:
        no_lines_raised = e.reason == "empty"
    check("save_embedded with no lines raises 'empty'",
          no_lines_raised and empty_audio.stat().st_size == 0)

    garbage = TMP / "garbage.bin"
    garbage.write_bytes(b"NOTAUDIO")
    try:
        lio.save_embedded(str(garbage), [(0, "x")], True)
        unsupported = False
    except lio.LyricsIOError as e:
        unsupported = e.reason == "unsupported_format"
    check("save_embedded unknown format raises 'unsupported_format'",
          unsupported)

    garbage_mp3 = TMP / "garbage.mp3"
    garbage_mp3.write_bytes(b"NOTAUDIO")
    try:
        lio.save_embedded(str(garbage_mp3), [(0, "x")], True)
        bad_mp3 = False
    except lio.LyricsIOError as e:
        bad_mp3 = e.reason == "unsupported_format"
    check("save_embedded non-MPEG bytes with .mp3 name rejected",
          bad_mp3 and garbage_mp3.read_bytes() == b"NOTAUDIO")

    # --- pure ID3 frame construction ---
    uslt = lio.build_uslt([(1234, "hi"), (61000, "bye")], False)
    check("build_uslt carries plain text",
          uslt.text == "hi\nbye\n", repr(uslt.text))
    uslt2 = lio.build_uslt([(1234, "hi")], True)
    check("build_uslt carries LRC text when synced",
          "[00:01.234]hi" in uslt2.text, repr(uslt2.text))
    sylt = lio.build_sylt([(1234, "hi"), (61000, "bye")])
    from mutagen.id3 import Encoding
    check("build_sylt carries (text, ms) list, format=2, type=1, UTF-8",
          sylt.text == [("hi", 1234), ("bye", 61000)] and
          sylt.format == 2 and sylt.type == 1 and
          sylt.encoding == Encoding.UTF8, repr(sylt.text))

    # --- real embedded round trip on an ID3-only file ---
    tagged = TMP / "tagged.mp3"
    tagged.write_bytes(b"")
    from mutagen.id3 import ID3
    ID3().save(str(tagged))
    ok = lio.save_embedded(str(tagged), [(1234, "hi"), (61000, "bye")], True)
    tags = ID3(str(tagged))
    sylt_frames = tags.getall("SYLT")
    uslt_frames = tags.getall("USLT")
    check("save_embedded writes SYLT + USLT into a real ID3 tag",
          ok is True and len(sylt_frames) == 1 and len(uslt_frames) == 1 and
          sylt_frames[0].text == [("hi", 1234), ("bye", 61000)] and
          "[00:01.234]hi" in uslt_frames[0].text,
          repr((sylt_frames and sylt_frames[0].text,
                uslt_frames and uslt_frames[0].text)))


t_io()


# ===========================================================================
print("\n=== 6. rating_tags: POPM/vorbis/MP4 mapping + file round trip ===")


def t_rating():
    check("stars_to_popm standard mapping",
          [rt.stars_to_popm(n) for n in range(6)] ==
          [0, 1, 64, 128, 192, 255])
    check("popm_to_stars tolerance edges",
          rt.popm_to_stars(0) == 0 and rt.popm_to_stars(1) == 1 and
          rt.popm_to_stars(63) == 1 and rt.popm_to_stars(64) == 2 and
          rt.popm_to_stars(100) == 2 and rt.popm_to_stars(128) == 3 and
          rt.popm_to_stars(191) == 3 and rt.popm_to_stars(192) == 4 and
          rt.popm_to_stars(224) == 4 and rt.popm_to_stars(225) == 5 and
          rt.popm_to_stars(255) == 5)
    check("POPM round trip for all star values",
          all(rt.popm_to_stars(rt.stars_to_popm(n)) == n for n in range(6)))
    check("stars_to_popm clamps out-of-range",
          rt.stars_to_popm(9) == 255 and rt.stars_to_popm(-3) == 0)
    check("MP4 star mapping round trip",
          rt.stars_to_mp4(3) == 60 and rt.mp4_to_stars(60) == 3 and
          rt.mp4_to_stars(100) == 5 and rt.mp4_to_stars(0) == 0 and
          rt.mp4_to_stars("80") == 4)
    check("vorbis rating tolerance",
          rt.vorbis_to_stars(5) == 5 and rt.vorbis_to_stars("3") == 3 and
          rt.vorbis_to_stars(80) == 4 and rt.vorbis_to_stars(100) == 5 and
          rt.vorbis_to_stars(0) == 0)

    rated = TMP / "rated.mp3"
    rated.write_bytes(b"")
    check("write_rating on a fresh .mp3 succeeds",
          rt.write_rating(str(rated), 4) is True)
    check("read_rating reads the written stars back",
          rt.read_rating(str(rated)) == 4)
    from mutagen.id3 import ID3
    popm = ID3(str(rated)).getall("POPM")[0]
    check("POPM frame carries the standard email + value",
          popm.email == "goldenmusic@local" and popm.rating == 192,
          repr((popm.email, popm.rating)))
    rt.write_rating(str(rated), 2)
    check("write_rating updates an existing rating",
          rt.read_rating(str(rated)) == 2)
    rt.write_rating(str(rated), 9)
    check("write_rating clamps stars to 5",
          rt.read_rating(str(rated)) == 5)
    rt.write_rating(str(rated), -3)
    check("write_rating clamps negative stars to 0",
          rt.read_rating(str(rated)) == 0)

    check("write/read on missing path degrade gracefully",
          rt.write_rating(str(TMP / "nope.mp3"), 3) is False and
          rt.read_rating(str(TMP / "nope.mp3")) == 0)
    bad_flac = TMP / "bad.flac"
    bad_flac.write_bytes(b"NOTAUDIO")
    check("garbage .flac never raises (write False, read 0)",
          rt.write_rating(str(bad_flac), 3) is False and
          rt.read_rating(str(bad_flac)) == 0)
    bad_bin = TMP / "bad.bin"
    bad_bin.write_bytes(b"NOTAUDIO")
    check("unknown format returns False",
          rt.write_rating(str(bad_bin), 3) is False and
          rt.read_rating(str(bad_bin)) == 0)


t_rating()


# ===========================================================================
print("\n=== 7. hygiene ===")
check("no Qt was imported by the lyrics modules",
      not any(mod.startswith("PyQt6") for mod in sys.modules),
      repr([m for m in sys.modules if m.startswith("PyQt6")][:4]))
check("mojibake helper is idempotent",
      lq.fix_mojibake(lq.fix_mojibake("گل سنگ".encode("cp1256")
                                      .decode("latin-1"))) == "گل سنگ")

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)

print(f"\nSUMMARY: {PASS} passed, {FAIL} failed")
if FAILURES:
    print("Failed checks:", ", ".join(FAILURES))
sys.exit(1 if FAIL else 0)

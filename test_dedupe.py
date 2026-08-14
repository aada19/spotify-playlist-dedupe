"""Exercise the dedupe logic against a mocked Spotify API.

NOTE: these assert on the payload we send. They cannot verify what Spotify
does with it -- that gap is exactly what let the `positions` regression through.
The DELETE stub therefore models the *real* documented API semantics: every
occurrence of a listed URI is removed, and `positions` is ignored.
"""
import builtins
import importlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

captured = {}


def make_track(name, artists, uri):
    return {"track": {"name": name,
                      "artists": [{"name": a} for a in artists],
                      "album": {"name": "Alb", "release_date": "2020-01-01"},
                      "uri": uri}}


def install_stubs(all_items):
    pages = [all_items[n:n + 100] for n in range(0, len(all_items), 100)] or [[]]

    def page_obj(idx):
        return {"items": pages[idx],
                "total": len(all_items),
                "next": "url" if idx + 1 < len(pages) else None,
                "_idx": idx}

    class FakeSpotify:
        def __init__(self, auth_manager=None):
            pass

        def playlist(self, link):
            return {"tracks": page_obj(0), "snapshot_id": "SNAP123"}

        def next(self, result):
            if not result["next"]:
                return None
            return page_obj(result["_idx"] + 1)

    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = FakeSpotify
    oauth2 = types.ModuleType("spotipy.oauth2")

    class FakeOAuth:
        def __init__(self, **kw):
            pass

        def get_access_token(self, as_dict=False):
            return "faketoken"

    oauth2.SpotifyOAuth = FakeOAuth
    spotipy.oauth2 = oauth2

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **k: None

    requests_mod = types.ModuleType("requests")

    def fake_delete(url, headers=None, json=None):
        captured["json"] = json
        # Model the documented API: remove ALL occurrences of each URI.
        doomed = {t["uri"] for t in json["tracks"]}
        captured["survivors"] = [
            it["track"]["uri"] for it in all_items
            if it["track"]["uri"] not in doomed
        ]

        class R:
            status_code = 200

            def json(self_inner):
                return {"snapshot_id": "NEWSNAP"}
        return R()
    requests_mod.delete = fake_delete

    for name, mod in (("spotipy", spotipy), ("spotipy.oauth2", oauth2),
                      ("dotenv", dotenv), ("requests", requests_mod)):
        sys.modules[name] = mod


def run(all_items):
    captured.clear()
    install_stubs(all_items)
    os.environ.update(SPOTIFY_CLIENT_ID="a", SPOTIFY_CLIENT_SECRET="b",
                      SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback")
    builtins.input = lambda *a: "https://open.spotify.com/playlist/PLID"
    sys.modules.pop("no_duplicates_spotify", None)
    mod = importlib.import_module("no_duplicates_spotify")
    mod.main()
    return mod


failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got:  {got}\n        want: {want}")
        failures.append(label)


def sent():
    return captured.get("json", {}).get("tracks", [])


print("=== Test 1: same track ID twice -> must NOT be deleted ===")
items = [make_track("Song A", ["X"], "spotify:track:AAA"),
         make_track("Song B", ["Y"], "spotify:track:BBB"),
         make_track("Song A", ["X"], "spotify:track:AAA")]
m = run(items)
check("nothing sent to DELETE", sent(), [])
check("recorded as a same-ID repeat", [s[0] for s in m.same_id_repeats], ["Song A"])
check("still reported as a duplicate", len(m.duplicates), 1)

print("\n=== Test 2: cross-ID duplicate (the case the tool exists for) ===")
items = [make_track("Song A", ["X"], "spotify:track:SINGLE"),
         make_track("Song A", ["X"], "spotify:track:ALBUMCUT")]
m = run(items)
check("removes the later ID, uri only, no positions",
      sent(), [{"uri": "spotify:track:ALBUMCUT"}])
check("kept copy survives the real API semantics",
      captured["survivors"], ["spotify:track:SINGLE"])
check("no same-ID repeats", m.same_id_repeats, [])

print("\n=== Test 3: mixed - one cross-ID dupe and one same-ID dupe ===")
items = [make_track("Song A", ["X"], "spotify:track:AAA"),
         make_track("Song A", ["X"], "spotify:track:BBB"),
         make_track("Song A", ["X"], "spotify:track:AAA")]
m = run(items)
check("only the differing URI is sent", sent(), [{"uri": "spotify:track:BBB"}])
check("keeper AAA survives", "spotify:track:AAA" in captured["survivors"], True)
check("same-ID copy left in place", len(m.same_id_repeats), 1)

print("\n=== Test 4: exactly 100 tracks (the pagination crash case) ===")
items = [make_track(f"S{n}", ["X"], f"spotify:track:{n}") for n in range(100)]
try:
    m = run(items)
    check("no duplicates flagged", m.duplicates, [])
    print("PASS  did not raise TypeError on final page")
except TypeError as e:
    print(f"FAIL  still crashes: {e}")
    failures.append("100-track crash")

print("\n=== Test 5: exactly 200 tracks, cross-ID dupe on last page ===")
items = [make_track(f"S{n}", ["X"], f"spotify:track:{n}") for n in range(199)]
items.append(make_track("S5", ["X"], "spotify:track:OTHER"))
try:
    m = run(items)
    check("dupe found across page boundary", sent(), [{"uri": "spotify:track:OTHER"}])
    check("original S5 survives", "spotify:track:5" in captured["survivors"], True)
except TypeError as e:
    print(f"FAIL  crashed: {e}")
    failures.append("200-track crash")

print("\n=== Test 6: 250 tracks, same URI repeated 3x -> all left in place ===")
items = [make_track(f"S{n}", ["X"], f"spotify:track:{n}") for n in range(250)]
items[120] = make_track("S5", ["X"], "spotify:track:5")
items[240] = make_track("S5", ["X"], "spotify:track:5")
m = run(items)
check("nothing sent", sent(), [])
check("both extra copies left in place", len(m.same_id_repeats), 2)

print("\n=== Test 7: 101 tracks, no dupes (regression) ===")
items = [make_track(f"S{n}", ["X"], f"spotify:track:{n}") for n in range(101)]
m = run(items)
check("no false positives", m.duplicates, [])

print("\n=== Test 8: non-200 from Spotify must raise, not pass silently ===")
items = [make_track("Song A", ["X"], "spotify:track:SINGLE"),
         make_track("Song A", ["X"], "spotify:track:ALBUMCUT")]
captured.clear()
install_stubs(items)


def failing_delete(url, headers=None, json=None):
    class R:
        status_code = 403

        def json(self_inner):
            return {}
        text = "Forbidden"
    return R()


sys.modules["requests"].delete = failing_delete
os.environ.update(SPOTIFY_CLIENT_ID="a", SPOTIFY_CLIENT_SECRET="b",
                  SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback")
builtins.input = lambda *a: "https://open.spotify.com/playlist/PLID"
sys.modules.pop("no_duplicates_spotify", None)
mod = importlib.import_module("no_duplicates_spotify")
try:
    mod.main()
    print("FAIL  a 403 was swallowed silently")
    failures.append("403 swallowed")
except RuntimeError as e:
    print(f"PASS  raised on 403: {str(e)[:60]}...")

print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)

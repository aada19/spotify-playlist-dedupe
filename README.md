# spotify-playlist-dedupe

Spotify's built-in duplicate detection matches on track ID. That catches the case where the exact same catalog entry was added to a playlist twice, but the same recording routinely exists in Spotify's catalog under several different IDs — a single release and the album cut are separate entries, a remaster or re-release gets its own entry, and regional catalogs can carry their own copies of the same record. Adding "the same song" from search on two different occasions can easily produce two different track IDs, so the exact-ID check sees two unrelated tracks and stays quiet. This script matches on track title plus artist instead, so those cross-ID repeats are visible.

## What it does

- Prompts for a Spotify playlist link on stdin.
- Reads the playlist through the Spotify Web API, paging through it 100 tracks at a time.
- Builds a key from each track's title and artist credits and records which keys it has already seen.
- Prints every track it considers a duplicate, with the album name and release date, so you can see what matched what.
- Removes repeats that carry a *different* track ID from the copy being kept — the ones Spotify's own duplicate check does not flag.
- Leaves same-track-ID repeats in place, reporting them instead. Spotify already warns about those at add time, so they were a deliberate choice.
- Asks for confirmation before deleting anything, and aborts on any answer other than `y`.
- Issues a single `DELETE` to the Web API for the tracks it does remove.

## How it works

The matching key is a tuple of two strings taken straight from the API response:

1. `track.name` — the track title.
2. Every credited artist's name, joined with `,` in the order the API returns them.

**No normalization is applied to either field.** The comparison is an exact, case-sensitive string match. Titles are not lowercased, whitespace is not trimmed, punctuation is not folded, and parenthetical or dash suffixes such as `(Remastered 2011)`, `- Live`, or `(feat. …)` are not stripped. The artist field is the full credit list, not just the primary artist, so the same song credited as `Artist A` in one entry and `Artist A,Artist B` in another produces two different keys and will not be flagged.

This matters in practice: it means the script catches repeats where the title and full artist credit happen to be byte-identical across two different track IDs, which is the common case for a single vs. an album cut. It does not catch a repeat where one entry says `Song (Remastered)` and the other says `Song`.

The script walks the playlist in order. The **first** occurrence of a given key is the copy that is kept; every later occurrence is added to the removal list. There is no preference logic — it does not try to keep the earliest release, the most popular version, or the explicit version. Position in the playlist is the only tiebreaker.

Removal is done with a direct `DELETE https://api.spotify.com/v1/playlists/{id}/tracks` request, sending each flagged track as `{"uri": …}` along with the playlist's `snapshot_id` from the initial read.

The critical constraint is that this endpoint removes **every** occurrence of each URI it is given. The API accepts no way to target one copy of a track and spare another — the request body is `tracks: [{uri}]` and `snapshot_id`, nothing more.

This lines up with the scope decision below. A duplicate is only queued for removal when the copy being kept has a *different* URI, which is exactly the cross-ID case: removing every occurrence of the album cut leaves the single untouched, because they are different URIs. The script records the URI of the first copy it keeps for each key and compares against it.

### Same-ID repeats are left alone on purpose

If the identical catalog entry appears in a playlist twice, Spotify already warned you about it at add time — the client shows a "this is already in this playlist" prompt and makes you confirm. Getting two copies of the same URI into a playlist therefore takes a deliberate override.

This tool does not undo that decision. Its purpose is the duplicate Spotify gives you *no* warning about: the same recording under a different track ID, where the built-in check sees two unrelated entries and stays silent. Removing a repeat you were already warned about and chose to keep would be the tool overruling an explicit choice.

Same-ID repeats are still detected and printed, with their playlist positions, so you can remove one yourself if it was a mistake. They are simply never deleted automatically.

(The Spotify API independently forbids it anyway — with no way to target one occurrence of a URI, deleting the repeat would take the kept copy with it. But the product reason is the governing one.)

## Limitations

These are real and worth reading before pointing this at a playlist you care about.

- **Title+artist collides on genuinely different recordings.** A studio version and a live version, an original and a re-recording (e.g. a "Taylor's Version"), an acoustic take, or a remaster released under an identical title and artist credit will be treated as the same track. The script does nothing to distinguish them — it collects the album name and release date for the printout, but does not use either in the matching decision. This is the reason for the confirmation prompt: the album name is shown for every proposed removal precisely so a live cut or remaster can be spotted before you approve.
- **There is no dry-run mode.** The script prints everything it found and waits for confirmation before deleting, so no removal happens without an explicit `y`, but there is no way to inspect the result without also being offered the delete.
- **The delete call is unbatched.** The Spotify API accepts at most 100 entries per removal request. The script sends every flagged track in one request, so a playlist with more than 100 distinct duplicated tracks will fail.
- **Error handling is minimal.** The removal response status is checked and a non-200 raises rather than passing silently, but no request is wrapped in `try`/`except` and there is no retry or rate-limit handling. A network failure or an expired token surfaces as an unhandled traceback.
- **The cached authorization eventually expires.** Access tokens are refreshed automatically, but Spotify refresh tokens now expire after about six months. When that happens the next run reopens the browser approval prompt. That is expected behavior, not a failure.

### Paging

The Spotify API returns playlist tracks 100 at a time. The script tracks an `offset` and calls `spotipy`'s `next()` after every 100th track, guarding on whether a next page actually exists:

```python
if (i + 1) % 100 == 0 and tracks['next']:
    tracks = spot.next(tracks)
    items = tracks['items']
    offset = i + 1
```

The guard is load-bearing. Without it, a playlist whose length is an exact multiple of 100 triggers the fetch on its final track, `next()` returns `None`, and the following line raises `TypeError: 'NoneType' object is not subscriptable` before anything is deleted.

## Running locally

### Prerequisites

- Python 3.8 or newer (developed against 3.10).
- A Spotify app registered at the [developer dashboard](https://developer.spotify.com/dashboard), for the client ID and secret.
- A redirect URI registered on that app. Use `http://127.0.0.1:8888/callback` — Spotify now rejects `http://localhost`, so the loopback IP has to be spelled out.
- Your own Spotify account added to the app's user list in the dashboard. Apps created since February 2026 start in development mode, which only permits accounts on that list.

### Environment variables

Copy the example file and fill in the values:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | Client ID of your Spotify app. |
| `SPOTIFY_CLIENT_SECRET` | Client secret of your Spotify app. |
| `SPOTIFY_REDIRECT_URI` | Must match a redirect URI registered on the app exactly. |

The script exits with a message naming the missing variables if any of the three is unset.

### Authorization

Authentication uses the authorization code flow via `spotipy`'s `SpotifyOAuth`. On the first run a browser window opens asking you to approve the requested scopes (`playlist-modify-public`, `playlist-modify-private`, `playlist-read-private`). After you approve, the token is cached in `.spotify_token_cache` and refreshed automatically, so subsequent runs do not prompt. That cache file holds a live token and is gitignored — do not commit it.

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
python no_duplicates_spotify.py
```

It prompts for a playlist link, prints everything it found, and asks before removing anything:

```
Spotify Playlist Link:
https://open.spotify.com/playlist/<your-playlist-id>
Duplicates found:
('Song Title', 'Artist Name', 'Album Name', '2019-04-05')

Left in place - same track ID as the copy being kept. Spotify warns about
these when you add them, so they were added on purpose:
  position 7: Song Title - Artist Name [Album Name]

About to remove 1 track(s) from the playlist.
Proceed? [y/N]
```

Anything other than `y` or `yes` aborts without touching the playlist. Check the album names in that list before answering — a live version or remaster sharing a title with the studio cut will appear as a duplicate, and the album is what distinguishes them.

Run the tests with:

```bash
python test_dedupe.py
```

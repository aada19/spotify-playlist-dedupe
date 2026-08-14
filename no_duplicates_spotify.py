import os
import sys

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI")

_missing = [
    name
    for name, value in (
        ("SPOTIFY_CLIENT_ID", CLIENT_ID),
        ("SPOTIFY_CLIENT_SECRET", CLIENT_SECRET),
        ("SPOTIFY_REDIRECT_URI", REDIRECT_URI),
    )
    if not value
]
if _missing:
    sys.exit(
        "Missing environment variable(s): "
        + ", ".join(_missing)
        + "\nCopy .env.example to .env and fill in the values."
    )

SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"

auth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path=".spotify_token_cache",
)
spot = spotipy.Spotify(auth_manager=auth)

play_link = input("Spotify Playlist Link: \n")
play_id = play_link.split("/")[-1].split("?")[0]
play_dic = spot.playlist(play_link)

num_songs = play_dic['tracks']['total']
snapshot_id = play_dic['snapshot_id']

tracks = play_dic['tracks']
items = tracks['items']
i = 0
offset = 0
# (title, artists) -> uri of the first copy seen, which is the one we keep
seen = {}
duplicates = []
removable_uris = []
same_id_repeats = []
while i < num_songs:
    if items[i - offset]['track'] is not None:
        song = items[i - offset]['track']['name']
        artists = ",".join([k['name']
                            for k in items[i - offset]['track']['artists']])
        album = items[i - offset]['track']['album']['name']
        release_date = items[i - offset]['track']['album']['release_date']
        uri = items[i - offset]['track']['uri']

        key = (song, artists)
        if key in seen:
            duplicates.append((song, artists, album, release_date))
            if uri == seen[key]:
                # Same track ID as the copy we keep. Spotify already warns
                # about this at add time, so it was added deliberately -- not
                # ours to undo. Left in place and reported.
                same_id_repeats.append((song, artists, album, i))
            elif uri not in removable_uris:
                removable_uris.append(uri)
        else:
            seen[key] = uri

    # Only page forward if there is a next page. On a playlist whose length is
    # an exact multiple of 100 this condition is true on the final track, and
    # spot.next() returns None there.
    if (i + 1) % 100 == 0 and tracks['next']:
        tracks = spot.next(tracks)
        items = tracks['items']
        offset = i + 1
    i += 1

# The API removes every occurrence of each URI listed. That is safe here only
# because a URI reaches this list solely when the copy being kept has a
# different URI; same-URI repeats are diverted into `skipped` above.
objects = [{"uri": uri} for uri in removable_uris]

SPOTIFY_REMOVE_ITEM_URL = f"https://api.spotify.com/v1/playlists/{play_id}/tracks"


def delete_duplicates(objects):
    access_token = auth.get_access_token(as_dict=False)
    response = requests.delete(
        SPOTIFY_REMOVE_ITEM_URL,
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        json={
            "tracks": objects,
            "snapshot_id": snapshot_id
        }
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Spotify returned {response.status_code} for the removal request: "
            f"{response.text}"
        )

    return response.json()


def main():
    if not duplicates:
        print("No duplicates found.")
        return

    print("Duplicates found: ")
    for dup in duplicates:
        print(dup)

    if same_id_repeats:
        print("\nLeft in place - same track ID as the copy being kept. Spotify "
              "warns about these when you add them, so they were added on "
              "purpose:")
        for song, artists, album, position in same_id_repeats:
            print(f"  position {position}: {song} - {artists} [{album}]")

    if not objects:
        print("\nNothing to remove.")
        return

    result = delete_duplicates(objects=objects)
    print(f"\nRemoved {len(objects)} track(s). "
          f"New snapshot: {result.get('snapshot_id')}")


if __name__ == '__main__':
    main()

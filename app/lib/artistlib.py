import os
import urllib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, PngImagePlugin, UnidentifiedImageError
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import ReadTimeout

from app import settings
from app.models import Album, Artist, Track
from app.store import artists as artist_store
from app.utils.hashing import create_hash
from app.utils.progressbar import tqdm

CHECK_ARTIST_IMAGES_KEY = ""

LARGE_ENOUGH_NUMBER = 100
PngImagePlugin.MAX_TEXT_CHUNK = LARGE_ENOUGH_NUMBER * (1024**2)
# https://stackoverflow.com/a/61466412


def get_artist_image_link(artist: str):
    """
    Returns an artist image url.
    """

    try:
        query = urllib.parse.quote(artist)  # type: ignore

        url = f"https://api.deezer.com/search/artist?q={query}"
        response = requests.get(url, timeout=30)
        data = response.json()

        for res in data["data"]:
            res_hash = create_hash(res["name"], decode=True)
            artist_hash = create_hash(artist, decode=True)

            if res_hash == artist_hash:
                return str(res["picture_big"])

        return None
    except (RequestConnectionError, ReadTimeout, IndexError, KeyError):
        return None


# TODO: Move network calls to utils/network.py
class DownloadImage:
    def __init__(self, url: str, name: str) -> None:
        sm_path = Path(settings.Paths.get_artist_img_sm_path()) / name
        lg_path = Path(settings.Paths.get_artist_img_lg_path()) / name

        img = self.download(url)

        if img is not None:
            self.save_img(img, sm_path, lg_path)

    @staticmethod
    def download(url: str) -> Image.Image | None:
        """
        Downloads the image from the url.
        """
        try:
            res = requests.get(url, timeout=20, proxies={
                "http": "socks5h://192.168.11.7:1080",
                "https": "socks5h://192.168.11.7:1080",
            })
            return Image.open(BytesIO(res.content))
        except UnidentifiedImageError:
            return None

    @staticmethod
    def save_img(img: Image.Image, sm_path: Path, lg_path: Path):
        """
        Saves the image to the destinations.
        """
        img.save(lg_path, format="webp")

        sm_size = settings.Defaults.SM_ARTIST_IMG_SIZE
        img.resize((sm_size, sm_size), Image.ANTIALIAS).save(sm_path, format="webp")


class CheckArtistImages:
    def __init__(self, instance_key: str):
        global CHECK_ARTIST_IMAGES_KEY
        CHECK_ARTIST_IMAGES_KEY = instance_key

        # read all files in the artist image folder
        path = settings.Paths.get_artist_img_sm_path()
        processed = "".join(os.listdir(path)).replace("webp", "")

        # filter out artists that already have an image
        artists = filter(
            lambda a: a.artisthash not in processed, artist_store.ArtistStore.artists
        )
        artists = list(artists)

        # process the rest
        key_artist_map = ((instance_key, artist) for artist in artists)

        with ThreadPoolExecutor(max_workers=20) as executor:
            res = list(
                tqdm(
                    executor.map(self.download_image, key_artist_map),
                    total=len(artists),
                    desc="Downloading missing artist images",
                )
            )

            list(res)

    @staticmethod
    def download_image(_map: tuple[str, Artist]):
        """
        Checks if an artist image exists and downloads it if not.

        :param artist: The artist name
        """
        instance_key, artist = _map

        if CHECK_ARTIST_IMAGES_KEY != instance_key:
            return

        img_path = (
                Path(settings.Paths.get_artist_img_sm_path()) / f"{artist.artisthash}.webp"
        )

        if img_path.exists():
            return

        url = get_artist_image_link(artist.name)

        if url is not None:
            return DownloadImage(url, name=f"{artist.artisthash}.webp")


# def fetch_album_bio(title: str, albumartist: str) -> str | None: """ Returns the album bio for a given album. """
# last_fm_url = "http://ws.audioscrobbler.com/2.0/?method=album.getinfo&api_key={}&artist={}&album={
# }&format=json".format( settings.Paths.LAST_FM_API_KEY, albumartist, title )

#     try:
#         response = requests.get(last_fm_url)
#         data = response.json()
#     except:
#         return None

#     try:
#         bio = data["album"]["wiki"]["summary"].split('<a href="https://www.last.fm/')[0]
#     except KeyError:
#         bio = None

#     return bio


# class FetchAlbumBio:
#     """
#     Returns the album bio for a given album.
#     """

#     def __init__(self, title: str, albumartist: str):
#         self.title = title
#         self.albumartist = albumartist

#     def __call__(self):
#         return fetch_album_bio(self.title, self.albumartist)


def get_artists_from_tracks(tracks: list[Track]) -> set[str]:
    """
    Extracts all artists from a list of tracks. Returns a list of Artists.
    """
    artists = set()

    master_artist_list = [[x.name for x in t.artists] for t in tracks]
    artists = artists.union(*master_artist_list)

    return artists


def get_albumartists(albums: list[Album]) -> set[str]:
    artists = set()

    for album in albums:
        albumartists = [a.name for a in album.albumartists]

        artists.update(albumartists)

    return artists


def get_all_artists(tracks: list[Track], albums: list[Album]) -> list[Artist]:
    artists_from_tracks = get_artists_from_tracks(tracks=tracks)
    artist_from_albums = get_albumartists(albums=albums)

    artists = list(artists_from_tracks.union(artist_from_albums))
    artists.sort()

    # Remove duplicates
    artists_dup_free = set()
    artist_hashes = set()

    for artist in artists:
        artist_hash = create_hash(artist, decode=True)

        if artist_hash not in artist_hashes:
            artists_dup_free.add(artist)
            artist_hashes.add(artist_hash)

    return [Artist(a) for a in artists_dup_free]

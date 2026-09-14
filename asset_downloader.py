"""
asset_downloader.py
Interactive CLI for downloading and decrypting IDOLY PRIDE assets.

Built entirely on top of the IdolyPrideObjectManager package:
the manifest is fetched (or loaded) and decrypted by the library,
and every download goes through the library's deobfuscation and
media conversion pipeline. No library code is modified.

Offers a full download of the entire manifest, or a selective download
across fine-grained categories (character models/motions, spine2d SD
characters, props, showcase goods, NPCs & mascots, maps, effects, cards,
costumes, hair, accessories, items, gacha/shop art, banners, UI, movies,
music, voices, story scripts, ...), each optionally filtered by character
using the library's character database.

Manifest maintenance is delegated to update_manifest.py, the script
merged unmodified from the upstream 'manifest-update' branch: the
downloader can check the server for a newer revision, build or refresh
a local manifests/ archive through its do_update(), rebuild the wayback
object log through its rebuild_log(), and load any archived revision.

Usage:
    python asset_downloader.py                  # fully interactive
    python asset_downloader.py --src octocacheevai
    python asset_downloader.py --revision 900 --output out/
    python asset_downloader.py --archive manifests/
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import IdolyPrideObjectManager as ipom

console = Console()

# Selective download categories, mapped onto the asset naming scheme
# observed in the decrypted manifest. Each entry is
# (group, label, [regex, ...], description); regexes follow
# PrideManifest.search() semantics (re.match, case-insensitive).
CATEGORIES = [
    # -------- 3D --------
    (
        "3D",
        "Character models",
        [r"mdl_chr_.*"],
        "character meshes: body / face / hair per costume",
    ),
    (
        "3D",
        "Character motions (animations)",
        [r"mot_.*"],
        "motion data: live stages, adv scenes, photo poses, home idles",
    ),
    (
        "3D",
        "Props",
        [r"mdl_prp_.*"],
        "handheld & stage props (fans, flowers, flyers, ...)",
    ),
    (
        "3D",
        "Showcase goods (merch)",
        [r"mdl_shw_.*"],
        "3D merch models: badges, acrylic boards, ...",
    ),
    (
        "3D",
        "Other 3D models",
        [r"mdl_env_.*", r"mdl_oth_.*"],
        "environment and miscellaneous meshes",
    ),
    # -------- Spine2D / SD --------
    (
        "Spine2D",
        "SD characters (spine2d)",
        [r"spi_sd_.*"],
        "chibi skeletons (.skl), atlas layouts (.atlas), and textures",
    ),
    (
        "Spine2D",
        "NPCs & mascots",
        [r"sd_npc.*", r"img_mob_.*"],
        "SD NPC spines/mascots and NPC/mob portraits",
    ),
    # -------- World --------
    (
        "World",
        "Maps & environments",
        [r"env_.*", r"scl_.*", r"m_sky.*", r"pfb_panorama.*", r"lok_.*"],
        "stages, rooms, panoramas, skyboxes, scene layouts",
    ),
    (
        "World",
        "Effects",
        [r"eff_.*", r"efp_.*"],
        "particle systems, flares, cutin/movie effects",
    ),
    # -------- Images --------
    (
        "Images",
        "Card art",
        [r"img_card_.*"],
        "character cards: full / rect / upper / thumb",
    ),
    (
        "Images",
        "Photo art",
        [r"img_photo_.*"],
        "photo mode shots",
    ),
    (
        "Images",
        "Story art",
        [r"img_story_.*"],
        "story stills, episode covers, thumbnails",
    ),
    (
        "Images",
        "Character portraits & sprites",
        [r"img_chr_.*"],
        "portraits, adv sprites, signs, icons per character",
    ),
    (
        "Images",
        "Costume thumbnails",
        [r"img_cos_.*"],
        "costume catalogue thumbnails",
    ),
    (
        "Images",
        "Hair thumbnails",
        [r"img_hair_.*"],
        "hairstyle catalogue thumbnails",
    ),
    (
        "Images",
        "Accessory thumbnails",
        [r"img_acc_.*"],
        "accessory catalogue thumbnails",
    ),
    (
        "Images",
        "Items, deco & toys",
        [r"img_item_.*", r"img_deco_.*", r"img_toy_.*", r"img_shelf_.*",
         r"img_ornament.*"],
        "item icons, room decorations, toys, shelves",
    ),
    (
        "Images",
        "Gacha & shop art",
        [r"img_gacha_.*", r"img_shop_.*", r"img_dokan_.*", r"img_exchange_.*"],
        "gacha screens/buttons, shop items, exchange art",
    ),
    (
        "Images",
        "Banners",
        [r"img_banner_.*"],
        "event / notice banners (large and small)",
    ),
    (
        "Images",
        "UI & icons",
        [r"img_ui_.*", r"img_icon.*", r"img_loading.*", r"img_tutorial.*",
         r"img_help.*", r"img_message_.*", r"img_music_.*"],
        "interface art, icons, loading screens, stamps, jackets",
    ),
    (
        "Images",
        "All images",
        [r"img_.*"],
        "every img_* texture (superset of the img groups above)",
    ),
    # -------- Video --------
    (
        "Video",
        "Music videos (MVs)",
        [r"mov_mv.*"],
        "full music videos (mp4)",
    ),
    (
        "Video",
        "Card & gacha movies",
        [r"mov_card_.*", r"mov_gacha.*"],
        "animated cards and gacha movies",
    ),
    (
        "Video",
        "All movies",
        [r"mov_.*"],
        "every mov_* video (superset of the video groups above)",
    ),
    # -------- Audio --------
    (
        "Audio",
        "BGM & songs",
        [r"sud_bgm.*", r"sud_music.*"],
        "background music and playable songs",
    ),
    (
        "Audio",
        "Voice lines",
        [r"sud_vo.*"],
        "character voice clips (home, adv, live, phone, ...)",
    ),
    (
        "Audio",
        "Sound effects",
        [r"sud_se.*"],
        "UI and gameplay sound effects",
    ),
    # -------- Text --------
    (
        "Text",
        "Story scripts (adventure text)",
        [r"adv_.*"],
        "story/adventure command scripts (.txt)",
    ),
]

# "Everything else": objects matching none of the categories above
# (shaders, live timelines, sun-001 templates, relation tables, ...),
# expressed as a negative lookahead so it stays a plain regex that
# PrideManifest.search() understands.
_KNOWN_UNION = "|".join(
    f"(?:{p})" for _, _, patterns, _ in CATEGORIES for p in patterns
)
CATEGORIES.append(
    (
        "Misc",
        "Everything else",
        [rf"(?!(?:{_KNOWN_UNION})).*"],
        "objects not covered by any category above (shaders, timelines, ...)",
    )
)

# Character database: abbreviations come from the library itself
# (IdolyPrideObjectManager.const.CHARACTER_ABBREVS); display names and
# units below mirror the annotations in that file. Unknown abbreviations
# added by future library updates still show up, just without a pretty name.
CHARACTER_NAMES = {
    "mna": ("Nagase Mana", "Hoshimi Pro"),
    "ktn": ("Nagase Kotono", "Tsuki no Tempest"),
    "ngs": ("Ibuki Nagisa", "Tsuki no Tempest"),
    "ski": ("Shiraishi Saki", "Tsuki no Tempest"),
    "suz": ("Narumiya Suzu", "Tsuki no Tempest"),
    "mei": ("Hayasaka Mei", "Tsuki no Tempest"),
    "skr": ("Kawasaki Sakura", "Sunny Peace"),
    "szk": ("Hyodo Shizuku", "Sunny Peace"),
    "chs": ("Shiraishi Chisa", "Sunny Peace"),
    "rei": ("Ichinose Rei", "Sunny Peace"),
    "hrk": ("Saeki Haruko", "Sunny Peace"),
    "rui": ("Tendo Rui", "TRINITYAiLE"),
    "yu": ("Suzumura Yu", "TRINITYAiLE"),
    "smr": ("Okuyama Sumire", "TRINITYAiLE"),
    "rio": ("Kanzaki Rio", "LizNoir"),
    "aoi": ("Igawa Aoi", "LizNoir"),
    "ai": ("Komiyama Ai", "LizNoir"),
    "kkr": ("Akazaki Kokoro", "LizNoir"),
    "kor": ("Yamada Kaori (fran)", "IIIX"),
    "kan": ("Kojima Kana", "IIIX"),
    "mhk": ("Takeda Mihoko", "IIIX"),
    "mku": ("Hatsune Miku", "Collab"),
    "ymk": ("Yuki Miku", "Collab"),
    "chk": ("Takami Chika", "Collab"),
    "rik": ("Sakurauchi Riko", "Collab"),
    "yo": ("Watanabe You", "Collab"),
    "cca": ("Hoto Cocoa", "Collab"),
    "chn": ("Kafu Chino", "Collab"),
}


def char_token_pattern(abbrevs: list[str]) -> str:
    """
    Regex matching a character abbreviation as a delimited token inside
    an asset name (e.g. 'mna' in 'mdl_chr_mna-fest-10_hair' or 'skr' in
    'sud_vo_adv_hbd_02_skr005', but not 'ai' inside 'adv_main').
    """
    union = "|".join(sorted(abbrevs, key=len, reverse=True))
    return rf"(?:^|(?<=[-_]))(?:{union})(?=$|[-_.\d])"

IMAGE_FORMATS = ["png", "jpeg", "webp", "bmp", "tiff"]
AUDIO_FORMATS = ["wav", "mp3", "ogg", "flac"]

# Manifest archive maintained by update_manifest.py, and the revision
# index that script reads; both come from the upstream 'manifest-update'
# branch merged into this repository.
DEFAULT_ARCHIVE_PATH = "manifests"
WAYBACK_COMMITS_LOCAL = "wayback_commits.json"


# ---------------- prompt helpers ---------------- #


def ask(text: str, default: str = "") -> str:
    suffix = f" [cyan]\\[{default}][/cyan]" if default else ""
    answer = console.input(f"[bold]{text}[/bold]{suffix}: ").strip()
    return answer or default


def ask_yn(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = console.input(f"[bold]{text}[/bold] [cyan]\\[{hint}][/cyan]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        console.print("[red]Please answer 'y' or 'n'.[/red]")


def ask_int(text: str, default: int) -> int:
    while True:
        answer = ask(text, str(default))
        try:
            return int(answer)
        except ValueError:
            console.print("[red]Please enter an integer.[/red]")


def fmt_size(nbytes: int) -> str:
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


# ---------------- manifest archive & updates ---------------- #
# Thin wrappers around update_manifest.py, the maintenance script taken
# unmodified from the upstream 'manifest-update' branch. It is imported
# lazily so that the downloader still runs as a standalone script when
# only the library is present.


def load_update_script():
    """
    [INTERNAL] Imports update_manifest.py from the working directory.
    Returns None, with an explanation, if it cannot be imported.
    """

    try:
        import update_manifest
    except ImportError as e:
        console.print(
            f"[yellow]update_manifest.py is unavailable ({e}).[/yellow]\n"
            "[dim]Run the downloader from the repository root, "
            "with 'tqdm' installed.[/dim]"
        )
        return None

    return update_manifest


def known_revisions() -> list[int]:
    """
    Revisions resolvable through the wayback commit index, preferring the
    local wayback_commits.json and falling back to the upstream copy.
    """

    from IdolyPrideObjectManager.const import (
        WAYBACK_COMMITS_LOG_LOCAL,
        WAYBACK_COMMITS_LOG_REMOTE,
    )
    from IdolyPrideObjectManager.utils import _json_load

    source = (
        WAYBACK_COMMITS_LOG_LOCAL
        if Path(WAYBACK_COMMITS_LOG_LOCAL).is_file()
        else WAYBACK_COMMITS_LOG_REMOTE
    )

    try:
        return sorted(int(revision) for revision in _json_load(source))
    except Exception as e:
        console.print(f"[yellow]Could not read the wayback commit index: {e}[/yellow]")
        return []


def archive_revisions(archive: Path) -> list[int]:
    """Revisions present in a local archive exported by update_manifest.py."""

    if not archive.is_dir():
        return []

    revisions = []
    for entry in archive.glob("v[0-9][0-9][0-9][0-9].json"):
        try:
            revisions.append(int(entry.stem[1:]))
        except ValueError:
            continue

    return sorted(revisions)


def archive_latest_revision(archive: Path) -> Optional[int]:
    """The LATEST_REVISION marker maintained by update_manifest.do_update()."""

    try:
        return int((archive / "LATEST_REVISION").read_text().strip())
    except (OSError, ValueError):
        return None


def load_from_archive(archive: Path) -> Optional[ipom.PrideManifest]:
    """
    Loads one file from the local manifest archive. Note that only
    v0000.json is a complete manifest; every other vNNNN.json is the diff
    against revision NNNN, which is exactly what makes it useful here --
    downloading one selects only the objects changed since that revision.
    """

    revisions = archive_revisions(archive)
    if not revisions:
        console.print(
            f"[yellow]No manifest archive found at '{archive}'.[/yellow]\n"
            "[dim]Build one from the main menu: "
            "Manifest updates -> update the local archive.[/dim]"
        )
        return None

    latest = archive_latest_revision(archive)
    console.print(
        f"Archive [cyan]{archive}[/cyan]: {len(revisions)} files"
        + (f", exported at revision [cyan]v{latest}[/cyan]" if latest else "")
    )
    console.print(
        "[dim]v0000.json is the full manifest; every other vNNNN.json holds "
        "only the objects added or updated since revision NNNN.[/dim]"
    )

    while True:
        raw = ask("Revision to load (0 = full manifest, blank = back)", "0")
        if not raw:
            return None

        try:
            revision = int(raw)
        except ValueError:
            console.print("[red]Please enter a revision number.[/red]")
            continue

        if revision not in revisions:
            console.print(f"[red]v{revision:04d}.json is not in the archive.[/red]")
            continue

        path = archive / f"v{revision:04d}.json"
        console.print(f"Loading [cyan]{path}[/cyan] ...")
        return ipom.load(path)


def check_for_update(
    manifest: ipom.PrideManifest, archive: Path
) -> Optional[ipom.PrideManifest]:
    """
    Compares the server's current revision against what is held locally.
    This mirrors the check at the top of update_manifest.do_update(),
    without exporting anything.
    """

    console.print("Checking the game server for a new manifest revision ...")
    latest = ipom.fetch()

    remote_revision = latest.revision.this
    loaded_revision = manifest.revision.this
    archived = archive_latest_revision(archive)
    indexed = known_revisions()

    table = Table(title="Revisions", box=box.ROUNDED)
    table.add_column("Source", style="bold")
    table.add_column("Revision", justify="right")
    table.add_row("Game server (latest)", f"v{remote_revision}")
    table.add_row("Currently loaded", f"v{loaded_revision}")
    table.add_row("Local archive", f"v{archived}" if archived else "[dim]none[/dim]")
    table.add_row("Wayback index", f"v{indexed[-1]}" if indexed else "[dim]none[/dim]")
    console.print(table)

    if remote_revision == loaded_revision:
        console.print("[green]The loaded manifest is up to date.[/green]")
        return None

    console.print(
        f"[yellow]A newer revision is available: "
        f"v{remote_revision} (loaded: v{loaded_revision}).[/yellow]"
    )
    if ask_yn("Switch to the latest manifest now?", True):
        return latest
    return None


def update_archive(archive: Path) -> Optional[ipom.PrideManifest]:
    """
    Runs update_manifest.do_update(), which exports the full manifest plus
    one diff per past revision and then rebuilds the wayback object log.
    """

    script = load_update_script()
    if script is None:
        return None

    indexed = known_revisions()
    console.print(
        Panel(
            "update_manifest.do_update() exports [bold]v0000.json[/bold] (the "
            "full manifest) plus one diff per past revision — "
            f"{'roughly ' + str(indexed[-1]) if indexed else 'over a thousand'} "
            "files totalling several GB — then rebuilds wayback_objects.json "
            "by refetching every historical manifest.\n\n"
            "[yellow]This is the upstream maintenance job, not a quick "
            "refresh: expect a long run and heavy network use.[/yellow]",
            title="Heads up",
            box=box.ROUNDED,
        )
    )
    if not ask_yn(f"Run it against '{archive}'?", False):
        console.print("[dim]Cancelled.[/dim]")
        return None

    if not Path(WAYBACK_COMMITS_LOCAL).is_file():
        console.print(
            f"[yellow]{WAYBACK_COMMITS_LOCAL} is missing from the working "
            "directory; run the downloader from the repository root.[/yellow]"
        )
        return None

    archive.mkdir(parents=True, exist_ok=True)
    marker = archive / "LATEST_REVISION"
    if not marker.is_file():
        marker.write_text("0")  # do_update reads this before anything else

    try:
        updated = script.do_update(archive)
    except Exception as e:
        console.print(f"[red]update_manifest.do_update() failed: {e}[/red]")
        return None

    if not updated:
        console.print("[green]Already at the newest revision, nothing exported.[/green]")
        return None

    console.print(f"[green]Archive updated to v{archive_latest_revision(archive)}.[/green]")
    if ask_yn("Load the freshly exported manifest?", True):
        return ipom.load(archive / "v0000.json")
    return None


def rebuild_wayback_log(manifest: ipom.PrideManifest):
    """Runs update_manifest.rebuild_log() against a complete manifest."""

    script = load_update_script()
    if script is None:
        return

    if not Path(WAYBACK_COMMITS_LOCAL).is_file():
        console.print(
            f"[yellow]{WAYBACK_COMMITS_LOCAL} is missing from the working "
            "directory; run the downloader from the repository root.[/yellow]"
        )
        return

    console.print(
        "[yellow]rebuild_log() refetches every indexed historical manifest "
        "and writes wayback_objects.json (tens of MB).[/yellow]"
    )
    if not ask_yn("Continue?", False):
        console.print("[dim]Cancelled.[/dim]")
        return

    if manifest.revision.base:  # a diff cannot seed the object list
        console.print("Fetching the full latest manifest first ...")
        manifest = ipom.fetch()

    try:
        script.rebuild_log(manifest)
    except Exception as e:
        console.print(f"[red]update_manifest.rebuild_log() failed: {e}[/red]")
        return

    console.print("[green]Wayback object log rebuilt.[/green]")


def run_manifest_updates(
    args: argparse.Namespace, manifest: ipom.PrideManifest
) -> Optional[ipom.PrideManifest]:
    """
    Manifest maintenance menu, driven by update_manifest.py.
    Returns a manifest to switch to, or None to keep the current one.
    """

    console.print("\n[bold underline]Manifest updates[/bold underline]")
    console.print(
        Panel(
            "[1] Check for a new manifest revision\n"
            "[2] Update the local archive (update_manifest.do_update)\n"
            "[3] Rebuild the wayback object log (update_manifest.rebuild_log)\n"
            "[0] Back",
            title="update_manifest.py",
            box=box.ROUNDED,
        )
    )

    archive = Path(args.archive)
    choice = ask("Select an option", "1")

    if choice == "1":
        return check_for_update(manifest, archive)
    if choice == "2":
        return update_archive(archive)
    if choice == "3":
        rebuild_wayback_log(manifest)
        return None
    if choice not in ("0", "b", "back"):
        console.print("[red]Invalid choice.[/red]")
    return None


# ---------------- manifest handling ---------------- #


def obtain_manifest(args: argparse.Namespace) -> ipom.PrideManifest:
    """Fetch (and decrypt) the manifest, or load it from a local file/archive."""

    if args.src:
        console.print(f"Loading local manifest from [cyan]{args.src}[/cyan] ...")
        return ipom.load(args.src)

    if args.revision is not None:
        console.print(f"Fetching manifest revision [cyan]v{args.revision}[/cyan] ...")
        return ipom.fetch(this_revision=args.revision)

    console.print(
        Panel(
            "[1] Fetch the latest manifest from the game server (default)\n"
            "[2] Fetch a specific past revision (wayback history)\n"
            "[3] Load a local manifest file (octocacheevai / .pdb / .json)\n"
            "[4] Load from the local manifests/ archive (update_manifest.py)",
            title="Manifest source",
            box=box.ROUNDED,
        )
    )

    while True:
        choice = ask("Select source", "1")
        if choice == "1":
            console.print("Fetching and decrypting the latest manifest ...")
            return ipom.fetch()
        if choice == "2":
            indexed = known_revisions()
            if indexed:
                console.print(
                    f"[dim]{len(indexed)} revisions indexed, "
                    f"v{indexed[0]} to v{indexed[-1]}.[/dim]"
                )
            while True:
                revision = ask_int("Revision number", indexed[-1] if indexed else 1)
                if not indexed or revision in indexed:
                    break
                console.print(
                    f"[yellow]v{revision} is not in the wayback index, "
                    "so the fetch will most likely fail.[/yellow]"
                )
                if ask_yn("Try anyway?", False):
                    break
            console.print(f"Fetching manifest revision v{revision} ...")
            return ipom.fetch(this_revision=revision)
        if choice == "3":
            path = ask("Path to manifest file", "octocacheevai")
            console.print(f"Loading and decrypting [cyan]{path}[/cyan] ...")
            return ipom.load(path)
        if choice == "4":
            manifest = load_from_archive(Path(args.archive))
            if manifest is not None:
                return manifest
            continue
        console.print("[red]Invalid choice.[/red]")


def collect_entries(manifest: ipom.PrideManifest) -> list[tuple[str, int]]:
    """
    (name, size) pairs for every object in the manifest, without
    instantiating 50k+ object classes. Names mirror what
    PrideManifest.search() matches against: assetbundle names carry
    a '.unity3d' suffix, resource names are verbatim.
    """
    entries = [
        (info["name"] + ".unity3d", info["size"])
        for info in manifest.assetbundles.infos
    ]
    entries += [(info["name"], info["size"]) for info in manifest.resources.infos]
    return entries


def match_entries(
    entries: list[tuple[str, int]], patterns: list[str]
) -> list[tuple[str, int]]:
    """Preview matches with the same semantics as PrideManifest.search()."""
    joined = "|".join(f"(?:{p})" for p in patterns)
    compiled = re.compile(joined, flags=re.IGNORECASE)
    return [(name, size) for name, size in entries if compiled.match(name)]


def show_summary(manifest: ipom.PrideManifest, entries: list[tuple[str, int]]):
    total = sum(size for _, size in entries)
    console.print(
        Panel(
            f"Revision: [bold cyan]{manifest.revision}[/bold cyan]\n"
            f"Assetbundles: [bold]{len(manifest.assetbundles):,}[/bold]\n"
            f"Resources: [bold]{len(manifest.resources):,}[/bold]\n"
            f"Total size: [bold]{fmt_size(total)}[/bold]",
            title="Decrypted manifest",
            box=box.ROUNDED,
        )
    )


# ---------------- download options ---------------- #


def gather_download_kwargs(args: argparse.Namespace) -> dict:
    """Interactively assemble the kwargs shared by all download calls."""

    kwargs = {}
    kwargs["path"] = ask("Output directory", args.output)
    kwargs["categorize"] = ask_yn("Categorize files into subdirectories?", True)

    if ask_yn("Convert media (Texture2D->image, AudioClip->audio, etc.)?", True):
        image_format = ask(f"Image format ({'/'.join(IMAGE_FORMATS)})", "png").lower()
        if image_format not in IMAGE_FORMATS:
            console.print(f"[yellow]Unknown format '{image_format}', using png.[/yellow]")
            image_format = "png"
        kwargs["image_format"] = image_format

        image_resize = ask("Resize images to ratio, e.g. 16:9 (blank = keep)", "")
        if image_resize:
            if re.fullmatch(r"\d+(\.\d+)?:\d+(\.\d+)?", image_resize):
                kwargs["image_resize"] = image_resize
            else:
                console.print(f"[yellow]Invalid ratio '{image_resize}', keeping original size.[/yellow]")

        audio_format = ask(f"Audio format ({'/'.join(AUDIO_FORMATS)})", "wav").lower()
        if audio_format not in AUDIO_FORMATS:
            console.print(f"[yellow]Unknown format '{audio_format}', using wav.[/yellow]")
            audio_format = "wav"
        kwargs["audio_format"] = audio_format

        kwargs["unpack_subsongs"] = ask_yn(
            "Unpack multi-track audio archives (ZIP) into the output directory?", False
        )
    else:
        kwargs["convert_image"] = False
        kwargs["convert_audio"] = False
        kwargs["convert_video"] = False
        kwargs["convert_text"] = False

    return kwargs


def confirm_download(matched: list[tuple[str, int]]) -> bool:
    total = sum(size for _, size in matched)
    console.print(
        f"\nMatched [bold cyan]{len(matched):,}[/bold cyan] objects, "
        f"[bold cyan]{fmt_size(total)}[/bold cyan] to download "
        "(existing files are skipped automatically)."
    )
    preview = ", ".join(name for name, _ in matched[:5])
    if preview:
        console.print(f"[dim]e.g. {preview}{', ...' if len(matched) > 5 else ''}[/dim]")
    return ask_yn("Proceed with download?", True)


# ---------------- download modes ---------------- #


def run_full_download(
    manifest: ipom.PrideManifest,
    entries: list[tuple[str, int]],
    args: argparse.Namespace,
):
    console.print("\n[bold underline]Full download[/bold underline]")
    console.print(
        "[yellow]This downloads EVERY assetbundle and resource in the manifest.[/yellow]"
    )
    if not confirm_download(entries):
        console.print("[dim]Cancelled.[/dim]")
        return

    kwargs = gather_download_kwargs(args)
    manifest.download_all(**kwargs)
    console.print("[green]Full download pass finished.[/green]")


def pick_categories(entries: list[tuple[str, int]]) -> list[str]:
    """Render the category table and return the chosen regex patterns."""

    table = Table(title="Asset categories", box=box.ROUNDED)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Group", style="magenta")
    table.add_column("Category", style="bold")
    table.add_column("Objects", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Contents", style="dim")

    previous_group = None
    for index, (group, label, patterns, description) in enumerate(CATEGORIES, 1):
        if group != previous_group and previous_group is not None:
            table.add_section()
        matched = match_entries(entries, patterns)
        table.add_row(
            str(index),
            group if group != previous_group else "",
            label,
            f"{len(matched):,}",
            fmt_size(sum(size for _, size in matched)),
            description,
        )
        previous_group = group
    table.add_section()
    table.add_row(str(len(CATEGORIES) + 1), "", "Custom regex", "-", "-",
                  "enter your own name pattern(s)")
    console.print(table)

    while True:
        raw = ask("Select categories (comma-separated, e.g. 1,3,5, or 'all')")
        if not raw:
            return []

        if raw.strip().lower() == "all":
            return [p for _, _, patterns, _ in CATEGORIES for p in patterns]

        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        patterns = []
        valid = True
        for token in tokens:
            if not token.isdigit() or not 1 <= int(token) <= len(CATEGORIES) + 1:
                console.print(f"[red]Invalid selection '{token}'.[/red]")
                valid = False
                break
            index = int(token) - 1
            if index < len(CATEGORIES):
                patterns.extend(CATEGORIES[index][2])
            else:
                patterns.extend(prompt_custom_patterns(entries))
        if valid:
            return patterns


def pick_characters() -> list[str]:
    """
    Render the character table (abbreviations from the library's database,
    display names from CHARACTER_NAMES) and return the chosen abbreviations.
    Empty selection means "no character filter".
    """

    from IdolyPrideObjectManager.const import CHARACTER_ABBREVS

    table = Table(title="Characters", box=box.ROUNDED)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Abbrev", style="bold")
    table.add_column("Name")
    table.add_column("Unit", style="magenta")

    for index, abbrev in enumerate(CHARACTER_ABBREVS, 1):
        name, unit = CHARACTER_NAMES.get(abbrev, (abbrev, "?"))
        table.add_row(str(index), abbrev, name, unit)
    console.print(table)

    while True:
        raw = ask("Select characters (numbers, abbrevs, or names; blank = all)")
        if not raw:
            return []

        chosen = []
        valid = True
        for token in [t.strip().lower() for t in raw.split(",") if t.strip()]:
            if token.isdigit() and 1 <= int(token) <= len(CHARACTER_ABBREVS):
                chosen.append(CHARACTER_ABBREVS[int(token) - 1])
            elif token in CHARACTER_ABBREVS:
                chosen.append(token)
            else:
                by_name = [
                    abbrev
                    for abbrev in CHARACTER_ABBREVS
                    if token in CHARACTER_NAMES.get(abbrev, (abbrev, ""))[0].lower()
                ]
                if by_name:
                    chosen.extend(by_name)  # e.g. 'miku' matches both Mikus
                else:
                    console.print(f"[red]Unknown character '{token}'.[/red]")
                    valid = False
                    break
        if valid and chosen:
            return sorted(set(chosen), key=CHARACTER_ABBREVS.index)
        if valid:
            console.print("[yellow]Nothing selected; try again.[/yellow]")


def prompt_custom_patterns(entries: list[tuple[str, int]]) -> list[str]:
    """Ask for a user-supplied regex, validating and previewing matches."""

    while True:
        pattern = ask("Custom regex (e.g. img_card_full_1.*, sud_music.*inst)")
        if not pattern:
            return []
        try:
            re.compile(pattern)
        except re.error as e:
            console.print(f"[red]Invalid regex: {e}[/red]")
            continue

        matched = match_entries(entries, [pattern])
        if not matched:
            console.print("[yellow]No objects match this pattern; try again.[/yellow]")
            continue

        sample = ", ".join(name for name, _ in matched[:3])
        console.print(f"[dim]{len(matched):,} matches, e.g. {sample}[/dim]")
        return [pattern]


def run_selective_download(
    manifest: ipom.PrideManifest,
    entries: list[tuple[str, int]],
    args: argparse.Namespace,
):
    console.print("\n[bold underline]Selective download[/bold underline]")

    patterns = pick_categories(entries)
    if not patterns:
        console.print("[dim]Nothing selected.[/dim]")
        return

    if ask_yn("Filter by character?", False):
        abbrevs = pick_characters()
        if abbrevs:
            names = ", ".join(
                CHARACTER_NAMES.get(a, (a, ""))[0] for a in abbrevs
            )
            console.print(f"[dim]Filtering for: {names}[/dim]")
            lookahead = f"(?=.*{char_token_pattern(abbrevs)})"
            patterns = [f"{lookahead}(?:{p})" for p in patterns]

    matched = match_entries(entries, patterns)
    if not matched:
        console.print(
            "[yellow]No objects matched the selection "
            "(note: some categories carry no character token in their names).[/yellow]"
        )
        return
    if not confirm_download(matched):
        console.print("[dim]Cancelled.[/dim]")
        return

    kwargs = gather_download_kwargs(args)
    manifest.download(*[f"(?:{p})" for p in patterns], **kwargs)
    console.print("[green]Selective download pass finished.[/green]")


def run_export(manifest: ipom.PrideManifest):
    console.print("\n[bold underline]Export decrypted manifest[/bold underline]")
    path = ask("Export path (.json / .csv / .pdb)", f"manifest_{manifest.revision}.json")
    manifest.export(path)


# ---------------- entry point ---------------- #


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive downloader for IDOLY PRIDE assets, "
        "powered by IdolyPrideObjectManager."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--src", help="load a local manifest file (octocacheevai / .pdb / .json) "
        "instead of asking interactively"
    )
    source.add_argument(
        "--revision", type=int,
        help="fetch a specific manifest revision instead of asking interactively",
    )
    parser.add_argument(
        "--output", default="objects/",
        help="default output directory offered in prompts (default: objects/)",
    )
    parser.add_argument(
        "--archive", default=DEFAULT_ARCHIVE_PATH,
        help="local manifest archive maintained by update_manifest.py "
        f"(default: {DEFAULT_ARCHIVE_PATH}/)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    console.print(
        Panel(
            "[bold magenta]IDOLY PRIDE asset downloader[/bold magenta]\n"
            "[dim]interactive frontend for IdolyPrideObjectManager[/dim]",
            box=box.DOUBLE,
        )
    )

    manifest = obtain_manifest(args)
    entries = collect_entries(manifest)
    show_summary(manifest, entries)

    while True:
        console.print(
            Panel(
                "[1] Full download (everything in the manifest)\n"
                "[2] Selective download (models, spine2d, maps, props, npcs,\n"
                "    accessories, ui, ... — optionally filtered by character)\n"
                "[3] Export decrypted manifest (JSON / CSV / ProtoDB)\n"
                "[4] Manifest updates (check / archive / wayback log)\n"
                "[0] Quit",
                title="Main menu",
                box=box.ROUNDED,
            )
        )
        choice = ask("Select an option", "2")

        if choice == "1":
            run_full_download(manifest, entries, args)
        elif choice == "2":
            run_selective_download(manifest, entries, args)
        elif choice == "3":
            run_export(manifest)
        elif choice == "4":
            switched = run_manifest_updates(args, manifest)
            if switched is not None:
                manifest = switched
                entries = collect_entries(manifest)
                show_summary(manifest, entries)
        elif choice in ("0", "q", "quit", "exit"):
            console.print("Bye!")
            return
        else:
            console.print("[red]Invalid choice.[/red]")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Interrupted, exiting.[/dim]")
        sys.exit(130)

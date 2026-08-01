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

Usage:
    python asset_downloader.py                  # fully interactive
    python asset_downloader.py --src octocacheevai
    python asset_downloader.py --revision 900 --output out/
"""

import argparse
import re
import sys

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


# ---------------- manifest handling ---------------- #


def obtain_manifest(args: argparse.Namespace) -> ipom.PrideManifest:
    """Fetch (and decrypt) the manifest, or load it from a local file."""

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
            "[3] Load a local manifest file (octocacheevai / .pdb / .json)",
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
            revision = ask_int("Revision number", 1)
            console.print(f"Fetching manifest revision v{revision} ...")
            return ipom.fetch(this_revision=revision)
        if choice == "3":
            path = ask("Path to manifest file", "octocacheevai")
            console.print(f"Loading and decrypting [cyan]{path}[/cyan] ...")
            return ipom.load(path)
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
